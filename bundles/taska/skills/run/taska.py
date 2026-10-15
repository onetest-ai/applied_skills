#!/usr/bin/env python3
"""taska: keep a manager's Epic > Story > Task tree current from meetings, calendar and files.

Deterministic half of the taska skills. Every subcommand prints JSON; errors print {"status": "error", ...}
and exit 1. Run from the project directory (the one holding taska.toml) or pass --project.
"""
import argparse
import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taska_lib import config as C, ledger as L, media, pack as PK, plan as PL, staleness as ST, store as S, workbench as W  # noqa: E402
from taska_lib.sources import ADAPTERS, msgraph  # noqa: E402

HERE = Path(__file__).resolve().parent


def out(x, code=0):
    print(json.dumps(x, indent=1, ensure_ascii=False, default=str))
    sys.exit(code)


def cfg_store(a):
    cfg = C.load(a.project)
    S.ensure(cfg.store)
    return cfg, cfg.store


def cmd_init(a):
    p = Path(a.project).resolve() / C.CONFIG_NAME
    if p.exists():
        out({"status": "exists", "config": str(p)})
    p.write_text(C.TEMPLATE.format(name=a.name or Path(a.project).resolve().name, timezone=a.timezone))
    out({"status": "created", "config": str(p), "next": "fill [plan], [[people]] and [sources.*], then `setup`"})


def cmd_setup(a):
    cfg, st = cfg_store(a)
    epics = PL.load_epics(cfg.plan)
    have = [e for e in S.events(st) if e["type"] == "create" and e["kind"] == "epic"]
    if have:
        out({"status": "ok", "epics": len(have), "note": "epics already set up; edit them in the workbench"})
    json.dump({"epics": epics}, open(S.P(st, "plan.json"), "w"), indent=1)
    out({"status": "ok", "epics": L.init_epics(st, epics), "store": str(st)})


def cmd_doctor(a):
    cfg, st = cfg_store(a)
    t = media.tools()
    rep = {"project": cfg.name, "store": str(st), "people": len(cfg.people), "tools": t, "sources": {}}
    for k, s in cfg.sources.items():
        ad = s["adapter"]
        r = {"adapter": ad}
        if ad in ("meetings_folder", "files_folder"):
            r["available"] = bool(s.get("path") and os.path.isdir(s["path"]))
            if ad == "files_folder" and not shutil.which("pdftotext"):
                r["note"] = "PDFs are recorded by name only (pdftotext not on PATH)"
            if ad == "meetings_folder":
                r["whisper_model"] = media.whisper_model(cfg.media)
                if not (t["ffmpeg"] and t["whisper"] and r["whisper_model"]):
                    r["note"] = "recordings without a transcript need ffmpeg + whisper-cli + a ggml model"
        elif ad.startswith("msgraph"):
            try:
                r["signed_in_as"] = msgraph.status(s)
                r["available"] = bool(r["signed_in_as"])
            except msgraph.GraphError as e:
                r.update(available=False, reason=str(e))
        elif ad == "code_host":
            from taska_lib.sources import code_host
            tool = code_host.CLI.get(s.get("provider", "github"))
            r["available"] = bool(tool and shutil.which(tool))
            if not r["available"]:
                r["reason"] = f"{tool} not on PATH (provider {s.get('provider', 'github')})"
        if ad not in ADAPTERS:
            r["available"], r["reason"] = False, f"adapter {ad} is not implemented yet"
        rep["sources"][k] = r
    bad = [k for k, r in rep["sources"].items() if not r.get("available")]
    out(dict(rep, status="ok" if not bad else "degraded", unavailable=bad), 0)


def cmd_sync(a):
    cfg, st = cfg_store(a)
    cur = S.cursors(st)
    reports = []
    for k, s in cfg.sources.items():
        if a.source and k != a.source:
            continue
        fn = ADAPTERS.get(s["adapter"])
        if not fn:
            reports.append({"source": k, "status": "skipped", "reason": f"adapter {s['adapter']} not implemented yet"})
            continue
        new_cur, rep = fn(cfg, k, s, st, cur.get(k))
        S.update_cursor(st, k, new_cur)  # per source: a later failure never loses an earlier source's progress
        rep["requeued"] = S.requeue(st, rep.get("changed") or [], "evidence changed after it was processed")
        reports.append(rep)
    pend = _pending(st)
    late = [x for x in pend if x["before_processed"]]
    out({"status": "ok", "sources": reports, "pending": len(pend), "late_arrivals": [x["id"] for x in late]})


def cmd_transcribe(a):
    """Transcribe up to --limit recordings that came without a transcript (ffmpeg + whisper.cpp; slow)."""
    cfg, st = cfg_store(a)
    model = media.whisper_model(cfg.media)
    if not (media.tools()["whisper"] and model):
        out({"status": "error", "error": "needs whisper-cli on PATH and a ggml model ([media].whisper_model)"}, 1)
    cfg.asr_budget = a.limit
    reps = []
    for k, s in cfg.sources.items():
        if s["adapter"] == "meetings_folder" and (not a.source or a.source == k) and cfg.asr_budget > 0:
            new_cur, rep = ADAPTERS["meetings_folder"](cfg, k, s, st, S.cursors(st).get(k))
            S.update_cursor(st, k, new_cur)
            rep["requeued"] = S.requeue(st, rep.get("changed") or [], "re-transcribed after it was processed")
            reps.append(rep)
    left = sum(1 for r in reps for p in r.get("pending", []) if "transcription" in p["reason"])
    out({"status": "ok", "model": model, "transcribed": a.limit - cfg.asr_budget, "still_to_transcribe": left,
         "sources": reps})


def _pending(st):
    ev, evs = S.evidence(st), S.events(st)
    done = S.processed(evs)
    order = S.order(ev)
    last_done = max([i for i, c in enumerate(order) if c in done] or [-1])
    return [{"id": c, "at": ev[c]["at"], "kind": ev[c]["kind"], "meeting_kind": ev[c].get("meeting_kind"),
             "title": ev[c]["title"], "md": str(S.P(st, "evidence", c + ".md")),
             "before_processed": i < last_done} for i, c in enumerate(order) if c not in done]


def cmd_pending(a):
    _, st = cfg_store(a)
    p = _pending(st)
    out({"status": "ok", "count": len(p), "items": p[:a.limit] if a.limit else p})


def cmd_context(a):
    cfg, st = cfg_store(a)
    print(json.dumps(L.context(st, cfg), ensure_ascii=False, separators=(",", ":")))  # compact: agents re-read it often


def cmd_apply(a):
    cfg, st = cfg_store(a)
    try:
        props = json.load(open(a.proposals))
    except (OSError, json.JSONDecodeError) as e:
        out({"status": "error", "error": f"proposals: {e}"}, 1)
    if not isinstance(props, list):
        out({"status": "error", "error": "proposals must be a JSON array"}, 1)
    out(dict(L.apply(st, cfg, a.evidence, props), status="ok"))


def cmd_done(a):
    _, st = cfg_store(a)
    if a.evidence not in S.evidence(st):
        out({"status": "error", "error": f"unknown evidence {a.evidence}"}, 1)
    L.mark_processed(st, a.evidence, a.note or "")
    out({"status": "ok", "processed": a.evidence, "pending": len(_pending(st))})


def cmd_ingest(a):
    """Evidence fetched by an agent (e.g. through an MCP server) enters the store with the same rules as an adapter's:
    a JSON array of {source, kind, at, title, text|turns, url?, meta?}; ids are derived, duplicates skipped."""
    cfg, st = cfg_store(a)
    items = json.load(open(a.file))
    if not isinstance(items, list):
        out({"status": "error", "error": "ingest takes a JSON array of evidence items"}, 1)
    new, bad = [], []
    for i, x in enumerate(items):
        if not isinstance(x, dict) or x.get("kind") not in S.KINDS or not isinstance(x.get("at"), str) \
                or not x.get("title") or not (x.get("text") or x.get("turns")):
            bad.append({"i": i, "why": f"needs kind in {list(S.KINDS)}, at, title and text or turns"})
            continue
        try:
            dt.datetime.fromisoformat(x["at"][:19])
        except ValueError:
            bad.append({"i": i, "why": f"at must be an ISO date or datetime, got {x['at']!r}"})
            continue
        if x.get("turns") and not (isinstance(x["turns"], list) and all(
                isinstance(t, dict) and str(t.get("text") or "").strip() for t in x["turns"])):
            bad.append({"i": i, "why": "turns must be a list of {speaker, text, ts?} with text"})
            continue
        src = x.get("source") or a.source
        item = {"id": S.make_id(x["at"], src, x.get("key") or x["title"] + x["at"]), "source": src, "kind": x["kind"],
                "at": x["at"][:19], "title": x["title"], "url": x.get("url"), "meta": dict(x.get("meta") or {}, ingested=True)}
        if x.get("turns"):
            item["turns"] = [dict(t, ts=t.get("ts") or "00:00:00", speaker=cfg.canonical(t.get("speaker")) or "?",
                                  bc=bool(t.get("bc"))) for t in x["turns"]]
            item["meeting_kind"] = x.get("meeting_kind", "internal")
        else:
            item["text"] = x["text"]
        if S.write_evidence(st, item):
            new.append(item["id"])
    out({"status": "ok", "new": new, "rejected": bad, "pending": len(_pending(st))})


def cmd_people(a):
    """Speakers seen in the evidence who are not in [[people]], with how often and in which kind of meeting: the
    list onboarding (or the manager) confirms sides from. Someone heard only in client meetings is likely client."""
    cfg, st = cfg_store(a)
    seen = {}
    for x in S.evidence(st).values():
        for t in x.get("turns") or []:
            sp = t.get("speaker")
            if not sp or sp == "?" or cfg.person(sp):
                continue
            r = seen.setdefault(sp, {"name": sp, "turns": 0, "meetings": set(), "client_meetings": 0})
            r["turns"] += 1
            if x["id"] not in r["meetings"]:
                r["meetings"].add(x["id"])
                r["client_meetings"] += x.get("meeting_kind") == "client"
    rows = sorted(seen.values(), key=lambda r: -r["turns"])
    out({"status": "ok", "known": len(cfg.people), "unknown": [
        {"name": r["name"], "turns": r["turns"], "meetings": len(r["meetings"]), "client_meetings": r["client_meetings"],
         "likely_side": "client" if r["client_meetings"] == len(r["meetings"]) else "internal or client"}
        for r in rows[: a.limit or len(rows)]]})


def cmd_redo(a):
    """Put a processed evidence item back in the queue (after its transcript was fixed, or to re-extract it)."""
    cfg, st = cfg_store(a)
    if a.evidence not in S.evidence(st):
        out({"status": "error", "error": f"unknown evidence {a.evidence}"}, 1)
    if a.drop:  # the item itself was wrong (e.g. a garbled transcript): forget it so its source re-reads it
        item = S.evidence(st)[a.evidence]
        rel = (item.get("meta") or {}).get("file") or (item.get("meta") or {}).get("path")
        for suf in (".json", ".md"):
            S.P(st, "evidence", a.evidence + suf).unlink(missing_ok=True)
        S.P(st, "media", a.evidence + ".vtt").unlink(missing_ok=True)
        cur = S.cursors(st).get(item["source"])
        if isinstance(cur, dict) and rel in cur:
            cur.pop(rel)
            S.update_cursor(st, item["source"], cur)
        out({"status": "ok", "dropped": a.evidence, "requeued": S.requeue(st, [a.evidence], a.why or "dropped"),
             "next": "the next sync (or transcribe, for a recording) re-reads it"})
    out({"status": "ok", "requeued": S.requeue(st, [a.evidence], a.why or "redo requested")})


def cmd_verify_prep(a):
    _, st = cfg_store(a)
    pk = L.verify_prep(st, a.weak)
    dest = a.out or str(S.P(st, "verify", "packets.json"))
    json.dump(pk, open(dest, "w"), indent=1, ensure_ascii=False)
    out({"status": "ok", "packets": len(pk), "out": dest})


def cmd_verify_apply(a):
    _, st = cfg_store(a)
    out(dict(L.verify_apply(st, json.load(open(a.file))), status="ok"))


def cmd_asks(a):
    cfg, st = cfg_store(a)
    rows, N = ST.classify(st, cfg)
    pick = [r for r in rows if (r["top"] if not a.all else not r["shadow"])]
    by = {}
    for r in pick:
        by.setdefault(r["ask"], []).append({"node": r["node"]["id"], "flags": r["flags"], "tier": r["tier"],
                                            "q": ST.question(r, N)})
    close = [{"node": r["node"]["id"], "title": r["node"]["title"]} for r in rows if r["tier"] == "close" and not r["shadow"]]
    out({"status": "ok", "asks": by, "confirm_or_close": close})


def cmd_status(a):
    cfg, st = cfg_store(a)
    N, _, order, ev = L.fold(st)
    rows, _ = ST.classify(st, cfg)
    cnt = {}
    for n in N.values():
        if n["state"] not in ("merged", "deleted"):
            cnt.setdefault(n["kind"], {}).setdefault(n["state"], 0)
            cnt[n["kind"]][n["state"]] += 1
    out({"status": "ok", "project": cfg.name, "evidence_processed": len(order), "pending": len(_pending(st)),
         "nodes": cnt, "asks": sum(r["top"] for r in rows),
         "last": ev[order[-1]]["date"] if order else None})


def cmd_pack(a):
    cfg, st = cfg_store(a)
    md = PK.build(st, cfg, a.days)
    dest = Path(a.out) if a.out else S.P(st, "out", f"pack-{dt.date.today().isoformat()}.md")
    dest.write_text(md)
    out({"status": "ok", "out": str(dest), "words": len(md.split())})


def cmd_serve(a):
    cfg, st = cfg_store(a)
    W.serve(st, cfg, a.port)


def cmd_export(a):
    cfg, st = cfg_store(a)
    out({"status": "ok", "out": W.export(st, cfg, a.out or S.P(st, "out", "workbench.html"))})


def cmd_schedule(a):
    """Print (never install) a cron line or launchd plist for an unattended `/taska:run --due`."""
    pd = C.find_project(a.project)
    claude = shutil.which("claude") or "claude"
    cmd = f"cd {shlex.quote(str(pd))} && mkdir -p logs && {shlex.quote(claude)} -p \"/taska:run --due\" " \
          f">> logs/taska-$(date +\\%F).log 2>&1"
    hh, mm = a.time.split(":")
    if not a.launchd:
        out({"status": "ok", "kind": "cron", "line": f"{int(mm)} {int(hh)} * * 1-5 PATH={os.environ.get('PATH', '')} {cmd}"})
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.applied.taska</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string><string>{cmd.replace('&', '&amp;').replace('>', '&gt;')}</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{os.environ.get('PATH', '')}</string></dict>
  <key>StartCalendarInterval</key><array>{''.join(f'<dict><key>Weekday</key><integer>{d}</integer><key>Hour</key><integer>{int(hh)}</integer><key>Minute</key><integer>{int(mm)}</integer></dict>' for d in range(1, 6))}</array>
</dict></plist>"""
    out({"status": "ok", "kind": "launchd", "path": "~/Library/LaunchAgents/ai.applied.taska.plist", "plist": plist})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=".")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("init"); p.add_argument("--name"); p.add_argument("--timezone", default="UTC")
    sp.add_parser("setup"); sp.add_parser("doctor"); sp.add_parser("context"); sp.add_parser("status")
    p = sp.add_parser("sync"); p.add_argument("--source")
    p = sp.add_parser("transcribe"); p.add_argument("--limit", type=int, default=3); p.add_argument("--source")
    p = sp.add_parser("pending"); p.add_argument("--limit", type=int, default=0)
    p = sp.add_parser("apply"); p.add_argument("--evidence", required=True); p.add_argument("--proposals", required=True)
    p = sp.add_parser("done"); p.add_argument("--evidence", required=True); p.add_argument("--note")
    p = sp.add_parser("ingest"); p.add_argument("--file", required=True); p.add_argument("--source", default="ingest")
    p = sp.add_parser("people"); p.add_argument("--limit", type=int, default=0)
    p = sp.add_parser("redo"); p.add_argument("--evidence", required=True); p.add_argument("--why")
    p.add_argument("--drop", action="store_true")
    p = sp.add_parser("verify-prep"); p.add_argument("--out"); p.add_argument("--weak", action="store_true")
    p = sp.add_parser("verify-apply"); p.add_argument("--file", required=True)
    p = sp.add_parser("asks"); p.add_argument("--all", action="store_true")
    p = sp.add_parser("pack"); p.add_argument("--days", type=int, default=7); p.add_argument("--out")
    p = sp.add_parser("serve"); p.add_argument("--port", type=int, default=8766)
    p = sp.add_parser("export"); p.add_argument("--out")
    p = sp.add_parser("schedule"); p.add_argument("--time", default="18:30"); p.add_argument("--launchd", action="store_true")
    a = ap.parse_args()
    fn = globals()["cmd_" + a.cmd.replace("-", "_")]
    try:
        fn(a)
    except (C.ConfigError, ValueError, OSError) as e:
        out({"status": "error", "error": str(e)}, 1)


if __name__ == "__main__":
    main()
