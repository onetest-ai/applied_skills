#!/usr/bin/env python3
"""taska: keep a manager's Epic > Story > Task tree current from meetings, calendar and files.

Deterministic half of the taska skills. Every subcommand prints JSON; errors print {"status": "error", ...}
and exit 1. Run from the project directory (the one holding taska.toml) or pass --project.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taska_lib import ai as AI, config as C, deliver as DL, gate as GT, ledger as L, media, report as RP, runlog as RL, runner as RN, schedules as SCH, pack as PK, plan as PL, reads as RD, staleness as ST, store as S, workbench as W  # noqa: E402
from taska_lib.sources import ADAPTERS, msgraph  # noqa: E402

HERE = Path(__file__).resolve().parent


CONTEXT_HINT = "taska context --ids S001,T002 (the nodes you touch), or taska context --compact (to find them)"
NEXT_AT = 20_000   # over this, the harness saves the output to a file: the answer names the filter that makes it small


def _json(x):
    """One value per line (a saved output can be Read in parts) without indentation (+33 % characters on a real tree,
    re-read on every later turn); `indent=0` is +4 % over one line."""
    return json.dumps(x, indent=0, separators=(",", ":"), ensure_ascii=False, default=str)


def out(x, code=0, next_hint=""):
    s = _json(x)
    if next_hint and len(s) > NEXT_AT:
        s = _json(x | {"next": f"narrow with: {next_hint}"})
    print(s)
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
    from taska_lib import setup as SU
    t = SU.tools()
    rep = {"project": cfg.name, "store": str(st), "people": len(cfg.people), "platform": sys.platform,
           "scheduler": SU.scheduler(), "tools": t, "install": SU.install_hints(t), "sources": {}}
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
        elif ad in ("teams_chat", "teams_channel"):
            from taska_lib.sources import teams_common
            r["available"], why = teams_common.ready(s)
            if why:
                r["reason"] = why
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
    rep["portability"] = portability((cfg.project_dir / "taska.toml").read_text())
    rep["handover_check"] = handover_check(cfg, st)
    _, gaps = _queue(st, cfg)
    rep["reads"] = {k: dict(g, ok=not g["waiting"] and not g["outside_window"]) for k, g in gaps.items()}
    if cfg.run.get("decisions_since"):
        rep["deprecated"] = ["[run].decisions_since is deprecated: use `taska backfill --read decisions --since DATE` "
                             "(or --days N, --evidence ID...); remove [run].decisions_since; window backfill is off while decisions_since is set"]
    out(dict(rep, status="ok" if not bad else "degraded", unavailable=bad), 0)


def portability(toml_text, home=None):
    """Paths written inside one person's home folder: they break when the project moves to another machine
    (handover). Write them as "~/..." instead; taska expands "~" on load."""
    home = home or os.path.expanduser("~")
    return [f"{line.strip()}: write it as {line.strip().replace(home + '/', '~/')}"
            for line in toml_text.splitlines() if home + "/" in line and not line.lstrip().startswith("#")]


def handover_check(cfg, st):
    """Per source: does a cursor say what was read? Items with no cursor would be read again on this machine (they
    are still recognised as the same items, but the run does the work twice). Shown before anything is applied."""
    cur, ev = S.cursors(st), S.evidence(st)
    out_ = {}
    for k in cfg.sources:
        n = sum(1 for x in ev.values() if x.get("source") == k)
        out_[k] = {"items": n, "cursor": k in cur, "would_reread": bool(n) and k not in cur}
    return out_


def cmd_handover(a):
    """Pack the project for its next owner: taska.toml and the store (ledger, evidence, cursors) in one zip, with a
    note. Read-only: nothing in the project is moved or removed (a project is handed over, never rebuilt)."""
    import zipfile
    cfg, st = cfg_store(a)
    outdir = Path(os.path.expanduser(a.out)); outdir.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() else "-" for c in cfg.name.lower()).strip("-")
    day = dt.date.today().isoformat()
    zp, note = outdir / f"{slug}-{day}.zip", outdir / f"{slug}-{day}-handover.md"
    files = [cfg.project_dir / "taska.toml"] + [p for p in Path(st).rglob("*") if p.is_file()]
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(cfg.project_dir))
    N = L.fold(st)[0]
    ev = S.evidence(st)
    counts = {"evidence": len(ev), "meetings": sum(1 for x in ev.values() if x.get("kind") == "meeting"),
              "events": sum(1 for _ in open(Path(st) / "events.jsonl")) if (Path(st) / "events.jsonl").exists() else 0,
              "nodes": len(N), "pending_cards": sum(1 for n in N.values() for s in n.get("suggestions", []) if s["status"] == "pending")}
    warn = portability((cfg.project_dir / "taska.toml").read_text())
    lines = [f"# Handover: {cfg.name} ({day})", "",
             f"- {counts['evidence']} evidence items ({counts['meetings']} meetings), {counts['events']} events in the ledger, "
             f"{counts['nodes']} tree items, {counts['pending_cards']} cards waiting for a Confirm.",
             "- Sources: " + ", ".join(f"{k} ({s['adapter']})" for k, s in cfg.sources.items()) + ".", "",
             "## On the new machine", "1. Unpack this zip into the project folder (the old owner stops their scheduled runs first: one runner at a time).",
             "2. Run `taska doctor`: every source should be available, and no source should say would_reread.",
             "3. Run `/taska:run`, then `/taska:workbench`."]
    if warn:
        lines += ["", "## Paths to check (tied to the old owner's home folder)"] + [f"- {w}" for w in warn]
    note.write_text("\n".join(lines) + "\n")
    out({"status": "ok", "zip": str(zp), "note": str(note), "counts": counts, "files": len(files), "portability": warn}, 0)


def cmd_sync(a):
    cfg, st = cfg_store(a)
    dry = None
    if a.dry:  # run every source against a scratch copy: findings and proposals land there, the live store is untouched
        import tempfile
        dry = os.path.join(tempfile.mkdtemp(prefix="taska-dry-"), "store")
        shutil.copytree(st, dry) if os.path.isdir(st) else S.ensure(dry)
        st = dry
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
    pend = _pending(st, cfg)
    late = [x for x in pend if x["before_processed"]]
    out({"status": "ok", "sources": reports, "pending": len(pend), "late_arrivals": [x["id"] for x in late],
         **({"dry_store": dry} if dry else {})})


def cmd_infer(a):
    from taska_lib import infer as IN, judge as J
    cfg, st = cfg_store(a)
    url = next((s.get("judge_url") for s in cfg.sources.values() if s.get("judge_url")), None) or J.DEFAULT_URL
    out(IN.run(st, cfg, url=url))


def cmd_repair_evidence(a):
    from taska_lib import repair as R
    cfg, st = cfg_store(a)
    cal = None
    if a.calendar:
        cal = json.load(open(a.calendar))
        cal = cal if isinstance(cal, list) else cal.get("events", [])
    r = R.repair_evidence(st, apply=a.apply, calendar=cal, tz=cfg.timezone)
    out({"status": "ok", **r})


def cmd_audit_ingest(a):
    """Audit the taska store for missing, misdated or truncated meetings.

    --expected FILE : JSON array of {"at", "title", "source"} from an oracle (e.g. echo-oracle.py)
    --calendar FILE : JSON array of {"start", "subject"[, "end"]} from a calendar export
    Either file may be omitted; the corresponding list is treated as empty.
    """
    from taska_lib import ingest_audit as IA
    cfg, st = cfg_store(a)
    if a.expected:
        with open(a.expected) as f:
            expected = json.load(f)
    else:
        expected = []
    if a.calendar:
        with open(a.calendar) as f:
            calendar = json.load(f)
    else:
        calendar = []
    ev = S.evidence(st)
    result = IA.audit_ingest(ev, expected, calendar, cfg.timezone)
    out({"status": "ok", **result})


def cmd_lint(a):
    from taska_lib import lint as LT
    _, st = cfg_store(a)
    N, *_ = L.fold(st)
    result = LT.lint(N)
    payload = {"status": "ok", **result}
    if a.report:
        out(payload, 0)
    has_findings = any(result[k] for k in result)
    out(payload, 1 if has_findings else 0)


def cmd_similar(a):
    from taska_lib import similar as SM
    _, st = cfg_store(a)
    N, *_ = L.fold(st)
    results = SM.similar(N, a.title, a.kind, k=a.k)
    out({"status": "ok", "results": results})


def cmd_snapshot(a):
    from taska_lib import snapshot as SN
    _, st = cfg_store(a)
    r = SN.snapshot(st, a.before, Path(a.out))
    out({"status": "ok", **r})


def cmd_teams_report(a):
    from taska_lib import teams_report as TR
    _, st = cfg_store(a)
    out({"status": "ok", "page": TR.write(st, a.date or dt.date.today().isoformat())})


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


def _queue(st, cfg):
    """(rows in time order, read gaps). Rows: unprocessed evidence (`need` extract) and processed items a read is
    pending for (`need` the read's name, `why` extracted | requested | backfill: taska_lib/reads.py)."""
    ev, evs = S.evidence(st), S.events(st)
    done = S.processed(evs)
    reads, gaps = RD.plan(ev, evs, cfg.run)
    order = S.order(ev)
    last_done = max([i for i, c in enumerate(order) if c in done] or [-1])
    rows = []
    for i, c in enumerate(order):
        why = None
        if c not in done and ev[c]["kind"] != "message":  # Teams messages are judged in sync, never extracted
            need = "extract"
        elif c in reads:
            need, why = reads[c]
        else:
            continue
        row = {"id": c, "at": ev[c]["at"], "kind": ev[c]["kind"], "meeting_kind": ev[c].get("meeting_kind"),
               "title": ev[c]["title"], "md": str(S.P(st, "evidence", c + ".md")),
               "before_processed": need == "extract" and i < last_done, "need": need}
        text = S.full_text(ev[c])
        row |= {"words": len(text.split()), "chars": len(text)}   # to plan batches without measuring files
        rows.append(row | ({"why": why} if why else {}))
    return rows, gaps


def _pending(st, cfg):
    return _queue(st, cfg)[0]


def cmd_pending(a):
    cfg, st = cfg_store(a)
    p, gaps = _queue(st, cfg)
    out({"status": "ok", "count": len(p), "items": p[:a.limit] if a.limit else p, "gaps": gaps})


def cmd_backfill(a):
    """Ask for a read on processed items, whatever the window and cap: one `backfill` event (nothing with --dry-run)."""
    cfg, st = cfg_store(a)
    read = RD.READS[a.read]
    ev, evs = S.evidence(st), S.events(st)
    done, had, asked = S.processed(evs), L.reads_of(evs), RD.requested(evs, read)
    if a.evidence:
        unknown = [c for c in a.evidence if c not in ev]
        if unknown:
            out({"status": "error", "error": f"unknown evidence {unknown}"}, 1)
        chosen = [c for c in S.order(ev) if c in set(a.evidence)]
    else:
        if a.since:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.since):
                    raise ValueError
                since = dt.date.fromisoformat(a.since).isoformat()
            except ValueError:
                out({"status": "error", "error": f"--since must be a date written YYYY-MM-DD, got {a.since!r}"}, 1)
        else:
            if not re.fullmatch(r"\d+", a.days):
                out({"status": "error", "error": f"--days must be a whole number of 0 or more, got {a.days!r}"}, 1)
            since = RD.window_start(RD.anchor(ev, evs), int(a.days))
        chosen = [c for c in S.order(ev) if since is not None and ev[c]["date"] >= since and ev[c]["kind"] in read.kinds]
    requested, skipped = [], {}
    for c in chosen:
        if ev[c]["kind"] not in read.kinds:
            skipped[c] = f"not a {'/'.join(sorted(read.kinds))}"
        elif c not in done:
            skipped[c] = "not processed"
        elif had.get(c, {}).get(read.name, 0) >= read.version:
            skipped[c] = f"read at version {read.version}"
        elif c in asked:
            skipped[c] = "already requested"
        else:
            requested.append(c)
    if requested and not a.dry_run:
        S.append(st, [{"type": "backfill", "read": read.name, "version": read.version, "evidence_ids": requested,
                       "by": "agent", "at": dt.datetime.now().isoformat(timespec="seconds")}])
    out({"status": "ok", "read": read.name, "dry_run": a.dry_run, "requested": requested, "skipped": skipped})


def cmd_context(a):
    cfg, st = cfg_store(a)
    out(L.context(st, cfg, ids=[i.strip() for i in a.ids.split(",") if i.strip()] if a.ids else None, compact=a.compact),
        next_hint=CONTEXT_HINT)


def cmd_apply(a):
    from taska_lib import coverage as CV
    cfg, st = cfg_store(a)
    try:
        props = json.load(open(a.proposals))
    except (OSError, json.JSONDecodeError) as e:
        out({"status": "error", "error": f"proposals: {e}"}, 1)
    if not isinstance(props, list):
        out({"status": "error", "error": "proposals must be a JSON array"}, 1)
    if a.section is None:
        out(dict(L.apply(st, cfg, a.evidence, props), status="ok"))
    ev = S.evidence(st)
    if a.evidence not in ev:
        out({"status": "error", "error": f"unknown evidence {a.evidence}"}, 1)
    item = ev[a.evidence]
    secs = CV.sections(item)
    if not 1 <= a.section <= len(secs):
        out({"status": "error", "error": f"section must be 1..{len(secs)} for {a.evidence}"}, 1)
    keep: list[int] = []
    outside: list[dict] = []
    gone: set[str] = set()
    for i, p in enumerate(props):
        q = p.get("quote") if isinstance(p, dict) else None
        n = CV.section_of(item, q, secs) if q else 0
        if n and n != a.section:
            outside.append({"i": i, "why": "outside_section", "section": n})
            gone |= {f"#{i}", *([p["ref"]] if p.get("ref") else [])}
            continue
        deps = p.get("depends_on") if isinstance(p, dict) else None
        used = [p.get(k) for k in ("parent", "node", "into")] + ([deps] if isinstance(deps, str) else list(deps or [])) \
            if isinstance(p, dict) else []
        hit = next((h for h in used if isinstance(h, str) and h in gone), None)
        if hit:
            outside.append({"i": i, "why": "outside_section", "section": n or a.section, "refers_to": hit})
            gone |= {f"#{i}", *([p["ref"]] if p.get("ref") else [])}
            continue
        keep.append(i)
    sub = [dict(props[i], ref=props[i].get("ref") or f"#{i}") if isinstance(props[i], dict) else props[i] for i in keep]
    res = L.apply(st, cfg, a.evidence, sub)
    rej = [dict(r, i=keep[r["i"]]) if isinstance(r.get("i"), int) and r["i"] < len(keep) else r for r in res["rejected"]]
    out(dict(res, rejected=sorted(outside + rej, key=lambda r: r["i"] if isinstance(r.get("i"), int) else -1), status="ok"))


def cmd_sections(a):
    from taska_lib import coverage as CV
    cfg, st = cfg_store(a)
    ev = S.evidence(st)
    if a.evidence not in ev:
        out({"status": "error", "error": f"unknown evidence {a.evidence}"}, 1)
    item = ev[a.evidence]
    md = S.P(st, "evidence", a.evidence + ".md")
    lines = md.read_text().split("\n") if md.exists() else []
    rows = CV.section_lines(lines, item, CV.sections(item))
    out({"status": "ok", "evidence": a.evidence, "md": str(md),
         "sections": [{k: r[k] for k in ("n", "first_line", "last_line", "start_ts", "words")} for r in rows]})


def reread_token(st, evidence, part, n=None):
    n = sum(1 for e in S.events(st) if e.get("evidence") == evidence) if n is None else n
    return hashlib.sha256(f"{evidence}|{part}|{n}".encode()).hexdigest()[:12]


def cmd_reread(a):
    from taska_lib import coverage as CV
    _, st = cfg_store(a)
    item = S.evidence(st).get(a.evidence)
    if not item:
        out({"status": "error", "error": f"unknown evidence {a.evidence}"}, 1)
    parts = CV.parts_for(item)
    if not 1 <= a.part <= parts:
        out({"status": "error", "error": f"part must be 1..{parts} for {a.evidence}"}, 1)
    md = S.P(st, "evidence", a.evidence + ".md")
    first, last = CV.part_lines(md.read_text().split("\n") if md.exists() else [], item, a.part, parts)
    r = {"status": "ok", "evidence": a.evidence, "part": a.part, "starts": CV.part_starts(item, parts)[a.part - 1],
         "md": str(md), "first_line": first, "last_line": last, "token": reread_token(st, a.evidence, a.part)}
    if first is None:
        r["hint"] = "the .md is stale: run `render-evidence`, then reread again"
    out(r)


def cmd_done(a):
    from taska_lib import coverage as CV, leftovers as LO
    cfg, st = cfg_store(a)
    ev = S.evidence(st)
    if a.evidence not in ev:
        out({"status": "error", "error": f"unknown evidence {a.evidence}"}, 1)
    note = a.note or ""
    if ev[a.evidence].get("kind") == "meeting":
        # extraction checks coverage and leftovers and leaves the meeting to its decision pass; a meeting already
        # processed is in that pass, which checks decisions only (coverage and leftovers were met when it was extracted)
        if a.evidence not in S.processed(S.events(st)):
            c = CV.coverage(ev[a.evidence], S.events(st))
            gap = sorted(set(c["empty_parts"]) | set(c["thin_parts"]))
            if gap and not a.ack:
                out({"status": "coverage", "evidence": a.evidence, "empty_parts": c["empty_parts"], "thin_parts": c["thin_parts"],
                     "parts": c["parts"], "starts": c["starts"],
                     "hint": "re-read those parts of the transcript (each starts at the ts in `starts`) and add what was said "
                             "(or nothing), then run done again; if they really hold no work, pass --ack \"<why>\" with one "
                             "--quote \"<8+ contiguous words from that part>\" per part"}, 1)
            if gap:
                item = ev[a.evidence]
                short = [q for q in a.quote or [] if CV.too_short(q)]
                found = {q: CV.part_of(item, q) for q in a.quote or [] if q not in short}
                missing = [p for p in gap if p not in found.values()]
                if missing:
                    out({"status": "ack_unquoted", "evidence": a.evidence, "parts_without_quote": missing,
                         "quotes_found": {q: p for q, p in found.items() if p},
                         "quotes_not_found": [q for q, p in found.items() if not p], "quotes_too_short": short,
                         "starts": c["starts"],
                         "hint": "an --ack needs proof of reading: one --quote of 8+ contiguous words taken from each part in "
                                 "parts_without_quote (each part starts at the ts in `starts`), then run the same done command again"}, 1)
                count = sum(1 for e in S.events(st) if e.get("evidence") == a.evidence)
                stale = [p for p in gap if reread_token(st, a.evidence, p, count) not in (a.reread or [])]
                if stale:
                    out({"status": "reread", "evidence": a.evidence, "parts_without_reread": stale, "starts": c["starts"],
                         "hint": "run `reread --evidence <id> --part N` for each, read what it prints, apply what was said, "
                                 "then run done again with the new tokens; a token changes with every apply on the meeting, so run reread "
                                 "again after your last apply"}, 1)
                note = (note + " " if note else "") + f"coverage acknowledged: {a.ack} (parts {gap})"
            lo = LO.leftovers(ev[a.evidence], S.events(st))
            if lo["gated"] and lo["count"] and a.leftovers != lo["token"]:
                out({"status": "leftovers", "evidence": a.evidence, "count": lo["count"],
                     "hint": f"run `leftovers --evidence {a.evidence}`, decide each listed sentence (apply an op, "
                             "or not work by the tracking rule), then run the same done command again, adding --leftovers <its token>"}, 1)
            L.mark_processed(st, a.evidence, note, decisions_pending=True)
        else:
            d = LO.decisions(ev[a.evidence], S.events(st))   # every meeting: decisions are said, not committed to
            read, missing, bad = LO.decision_acks(ev[a.evidence], d, a.no_decisions or [])
            if bad:
                out({"status": "ack_unquoted", "evidence": a.evidence, "quotes_not_found": bad, "starts": d["starts"],
                     "hint": "each --no-decisions needs 8+ contiguous words found in the meeting's text (each part starts at the "
                             "ts in `starts`); fix or drop those quotes, then run the same done command again"}, 1)
            if missing:
                out({"status": "decisions", "evidence": a.evidence, "parts": missing, "starts": d["starts"],
                     "hint": "read each listed part whole for decisions: any choice the group settled that changes what someone "
                             "does next (a recorded risk does not count); record them with apply, or pass --no-decisions "
                             "\"<8+ words from that part>\" per part that has none; then run the same done command again"}, 1)
            L.mark_processed(st, a.evidence, note, decisions_read=read, reads={"decisions": RD.READS["decisions"].version})
    else:
        L.mark_processed(st, a.evidence, note)
    out({"status": "ok", "processed": a.evidence, "pending": len(_pending(st, cfg))})


def cmd_leftovers(a):
    from taska_lib import leftovers as LO
    _, st = cfg_store(a)
    ev = S.evidence(st)
    if a.evidence not in ev:
        out({"status": "error", "error": f"unknown evidence {a.evidence}"}, 1)
    item, events = ev[a.evidence], S.events(st)
    r, d = LO.leftovers(item, events), LO.decisions(item, events)
    if a.part:   # token and count stay the whole meeting's: done checks them
        r["parts"] = [p for p in r["parts"] if p["part"] == a.part]
        d["parts"] = [p for p in d["parts"] if p["part"] == a.part]
    if a.section:
        from taska_lib import coverage as CV
        secs = CV.sections(item)
        if not 1 <= a.section <= len(secs):
            out({"status": "error", "error": f"section must be 1..{len(secs)} for {a.evidence}"}, 1)
        s = secs[a.section - 1]
        r["parts"] = [dict(p, leftovers=[x for x in p["leftovers"] if s["start"] <= x["offset"] < s["end"]]) for p in r["parts"]]
        d["parts"] = [dict(p, hints=[x for x in p["hints"] if s["start"] <= x["offset"] < s["end"]]) for p in d["parts"]]
        r["section"] = a.section
    out(dict(r, decisions=d, status="ok"),
        next_hint=f"taska leftovers --evidence {a.evidence} --part 1")


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
    out({"status": "ok", "new": new, "rejected": bad, "pending": len(_pending(st, cfg))})


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
    out({"status": "ok", "project": cfg.name, "evidence_processed": len(order), "pending": len(_pending(st, cfg)),
         "nodes": cnt, "asks": sum(r["top"] for r in rows),
         "last": ev[order[-1]]["date"] if order else None})


def cmd_pack(a):
    cfg, st = cfg_store(a)
    if getattr(a, "json", False):
        data = PK.build_client(st, cfg, a.days)
        dest = Path(a.out) if a.out else S.P(st, "out", f"pack-client-{dt.date.today().isoformat()}.json")
        dest.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        out({"status": "ok", "out": str(dest), "week": data["week"]})
    else:
        md = PK.build(st, cfg, a.days)
        dest = Path(a.out) if a.out else S.P(st, "out", f"pack-{dt.date.today().isoformat()}.md")
        dest.write_text(md)
        out({"status": "ok", "out": str(dest), "words": len(md.split())})


def cmd_serve(a):
    cfg, st = cfg_store(a)
    W.serve(st, cfg, a.port, register_opts={"agents_dir": a.agents_dir} if a.agents_dir else None)


def cmd_export(a):
    cfg, st = cfg_store(a)
    out({"status": "ok", "out": W.export(st, cfg, a.out or S.P(st, "out", "workbench.html"))})


def cmd_compare_external(a):
    """Compare an external note-taker's actions (e.g. Echo) against the taska tree.

    --file FILE : JSON file produced by echo-actions.py (required)
    --gold FILE : optional gold JSONL for gold_items counts (one annotation per line)

    Prints per-meeting counts (echo_actions, taska_items, gold_items, tracked) and
    the gap candidates (actions with a transcript span but no matching taska event).
    """
    from taska_lib import compare_external as CE
    with open(a.file) as f:
        external = json.load(f)
    from taska_lib import evalscore as ES
    gold = ES.load_gold(a.gold)[1] if a.gold else None   # same reader as `score`: header + fingerprint checked
    cfg, st = cfg_store(a)
    result = CE.compare_external(st, external, gold, cfg.timezone)
    out({"status": "ok", **result})


def cmd_score(a):
    """Score an extraction run against a gold JSONL file (one JSON object per line).

    Blank lines are skipped. A first line starting with '#' is treated as a header comment
    and skipped. All other lines must be valid JSON objects containing an 'evidence' key;
    a malformed line raises ValueError with the 1-based line number.
    """
    from taska_lib import evalscore as ES
    gold = []
    with open(a.gold) as f:
        for i, line in enumerate(f):
            raw = line.strip()
            if not raw:
                continue
            if i == 0 and raw.startswith("#"):
                continue  # header comment
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"gold line {i + 1}: {exc}") from exc
            if "evidence" not in item:
                raise ValueError(f"gold line {i + 1}: missing 'evidence' key")
            gold.append(item)
    ES.load_gold(a.gold)   # the header's n and sha256, when present, must match: a key edited in place is refused
    cfg, st = cfg_store(a)
    result = ES.score(st, gold)
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1, ensure_ascii=False))
    out({"status": "ok", **result})


def cmd_ai(a):
    """Reach Claude for an unattended run (taska_lib/ai.py): --probe says which route answers, or how to fix each."""
    r = AI.run(a.args[1:] if a.args[:1] == ["--"] else a.args, probe=a.probe)
    if a.probe:
        out({k: v for k, v in r.items() if v or k == "ok"}, 0 if r["ok"] else 1)
    if not r["ok"]:
        print(json.dumps({"status": "error", "why": r["why"]}, indent=1), file=sys.stderr)
    sys.exit(0 if r["ok"] else 1)


def cmd_render_evidence(a):
    """Rewrite every evidence .md from its .json in the current format (lines short enough to be read whole)."""
    cfg, st = cfg_store(a)
    out({"status": "ok", "rewritten": S.rerender(st)})


def cmd_schedules(a):
    """The project's [[schedules]] with each one's state, next run and what the OS holds; --set FILE replaces them
    from a JSON list (saved to taska.toml); --register writes one OS job per schedule; --delete NAME removes a schedule
    and its job; --retire LABEL unloads a job outside taska.toml and moves its file aside; --preview NAME says
    what a run will do. The same functions as the Schedules page: the onboarding skill drives these."""
    cfg = C.load(a.project)
    opts = dict({"agents_dir": a.agents_dir} if a.agents_dir else {}, **({"force": True} if a.force else {}))
    reg = None
    if a.preview:
        out(dict(W.run_preview(cfg, a.preview), status="ok"))
    if a.delete:
        reg = W.delete_schedule(cfg, a.delete, opts)
    elif a.retire:
        reg = [W.retire_job(cfg, a.retire, opts)]
    elif a.set:
        reg = W.set_schedules(cfg, json.loads(Path(a.set).read_text()), opts)
    elif a.register:
        reg = SCH.register(cfg, **opts)
    out(dict(W.schedules_view(cfg, opts), status="ok", **({"registered": reg} if reg is not None else {})))


def cmd_run_schedule(a):
    """One scheduled run (taska_lib/runner.py): the project's steps for the schedule's kind, then the report, the gate
    and the delivery; a send is HELD when something failed. --dry-run lists the commands."""
    cfg, st = cfg_store(a)
    s = next((x for x in cfg.schedules if x.name == a.name), None)
    if not s:
        out({"status": "error", "error": f"no schedule named {a.name!r}; known: {[x.name for x in cfg.schedules]}"}, 2)
    r = RN.run(cfg, s, week=a.week, dry_run=a.dry_run)
    out(dict(r, status="ok" if r["verdict"] != "busy" else "busy"), 0 if r["verdict"] in ("done", "ready") else 1)


def cmd_report(a):
    """The weekly report (taska_lib/report.py): select --week W --out F | apply --prose P --sel S | build --week W --prose P --out F."""
    cfg, st = cfg_store(a)
    reports = Path(a.reports) if a.reports else C.find_project(a.project) / "reports"
    if a.what == "select":
        sel = RP.select(cfg, a.week, reports, today=dt.date.fromisoformat(a.today) if a.today else None)
        Path(a.out).write_text(json.dumps(sel, indent=1, ensure_ascii=False))
        out({"status": "ok", "items": len(sel["items"]), "priorities": len(sel["priorities"]),
             "cached": sum(1 for x in sel["items"] + sel["priorities"] if x["sentence"]), "milestones": len(sel["milestones"]),
             "risks": len(sel["risks"]), "asks": len(sel["slide5"]), "slots": len(sel["slots"])})
    if a.what == "apply":
        out(RP.apply(cfg, a.prose, a.sel))
    r = RP.build(cfg, a.week, a.prose, a.out, snapshot=a.snapshot, upto=a.upto)
    out({"out": a.out, "slots": len(r["slots"]), "failed": r["errors"], "withheld": r["withheld"]})


def cmd_deliver(a):
    """A week's mail content (taska_lib/deliver.py): reports/WEEK/deliver.json and a ready-to-send mail.md."""
    cfg, st = cfg_store(a)
    reports = Path(a.reports) if a.reports else C.find_project(a.project) / "reports"
    sched = next((s for s in cfg.schedules if s.name == a.schedule), None) if a.schedule else None
    d = DL.deliver(cfg, reports / a.week, a.to, schedule=sched, status=a.status, kind=a.kind)
    out({"deliver": str(reports / a.week / "deliver.json"), "mail": str(reports / a.week / "mail.md"),
         "failed": sum(1 for g in d["gates"] if not g["ok"])})


def cmd_gate(a):
    """The pre-send gate on a week's draft (taska_lib/gate.py): presend.json + presend.html, exit 1 when a blocking gate fails."""
    cfg, st = cfg_store(a)
    reports = Path(a.reports) if a.reports else C.find_project(a.project) / "reports"
    docx = Path(a.docx) if a.docx else GT.latest(reports / a.week, "*.docx")
    if not docx:
        out({"status": "error", "error": f"no draft .docx in {reports / a.week}"}, 2)
    r = GT.run_gate(cfg, a.week, docx, reports, choices=a.choices, baseline=a.baseline, key=a.key, calendar=a.calendar,
                    external=a.external, reader=a.reader, port=a.port, progress=a.progress)
    out({"status": "ok" if not r["exit"] else "blocked", "page": r["page"], "failed": r["failed"], "coverage": r["coverage"]},
        r["exit"])


def cmd_run_log(a):
    """A run's step record (taska_lib/runlog.py): begin | step ID RC [DETAIL] | finish | failed [--this-run] | unfinished."""
    keep = tuple(a.keep.split(",")) if a.keep else RL.KEEP
    if a.what == "begin":
        RL.begin(a.week_dir, a.report_only, keep)
    elif a.what == "step":
        RL.step(a.week_dir, a.args[0], a.args[1], a.args[2] if len(a.args) > 2 else "")
    elif a.what == "finish":
        RL.finish(a.week_dir)
    elif a.what == "unfinished":
        out({"status": "ok", "unfinished": RL.unfinished(a.week_dir)})
    elif a.what == "failed":
        f = RL.failed(a.week_dir, a.this_run)
        out({"status": "ok", "failed": f}, 1 if f else 0)
    out({"status": "ok"})


def cmd_schedule(a):
    """Print (never install) a cron line or launchd plist for an unattended `/taska:run --due`, run through `taska ai`
    so it finds a working sign-in (a stored token, then CodeMie)."""
    pd = C.find_project(a.project)
    me = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} --project {shlex.quote(str(pd))}"
    cmd = f"cd {shlex.quote(str(pd))} && mkdir -p logs && PATH={shlex.quote(os.environ.get('PATH', ''))} {me} ai -- -p \"/taska:run --due\" " \
          f">> logs/taska-$(date +\\%F).log 2>&1"
    hh, mm = a.time.split(":")
    if not a.launchd:
        out({"status": "ok", "kind": "cron", "line": f"{int(mm)} {int(hh)} * * 1-5 {cmd}"})
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.applied.taska</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string><string>{cmd.replace('&', '&amp;').replace('>', '&gt;')}</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{os.environ.get('PATH', '')}</string></dict>
  <key>StartCalendarInterval</key><array>{''.join(f'<dict><key>Weekday</key><integer>{d}</integer><key>Hour</key><integer>{int(hh)}</integer><key>Minute</key><integer>{int(mm)}</integer></dict>' for d in range(1, 6))}</array>
</dict></plist>"""
    out({"status": "ok", "kind": "launchd", "path": "~/Library/LaunchAgents/ai.applied.taska.plist", "plist": plist})


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=".")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("init"); p.add_argument("--name"); p.add_argument("--timezone", default="UTC")
    sp.add_parser("setup"); sp.add_parser("doctor"); sp.add_parser("status")
    p = sp.add_parser("context", help="the live tree, people and what has been processed")
    p.add_argument("--ids", help="only these nodes (comma-separated), each with its children")
    p.add_argument("--compact", action="store_true", help="the tree with id, title, state, owners and due only")
    p = sp.add_parser("sync"); p.add_argument("--source"); p.add_argument("--dry", action="store_true",
                                                                          help="run against a scratch copy of the store")
    p = sp.add_parser("teams-report"); p.add_argument("--date")
    sp.add_parser("render-evidence", help="rewrite every evidence .md from its .json (the .json is never changed)")
    sp.add_parser("infer", help="propose the closures a done/held task implies (each waits for Confirm)")
    p = sp.add_parser("repair-evidence", help="re-date meetings dated only by file arrival; mark re-synced copies")
    p.add_argument("--apply", action="store_true", help="write the repair (default: report only)")
    p.add_argument("--calendar", help="JSON file [{subject, start, end}]: re-date file-dated meetings to their event")
    p = sp.add_parser("audit-ingest", help="audit meetings for missing, misdated or truncated items")
    p.add_argument("--expected", help="JSON file: [{at, title, source}] from an oracle (optional)")
    p.add_argument("--calendar", help="JSON file: [{start, subject[, end]}] from a calendar export (optional)")
    p = sp.add_parser("lint", help="tree health checks: duplicates, open-under-closed, cycles, zombie stories")
    p.add_argument("--report", action="store_true", help="print results and exit 0 (default: exit 1 when any finding)")
    p = sp.add_parser("similar", help="find nodes similar to a given title")
    p.add_argument("--title", required=True, help="title to search against")
    p.add_argument("--kind", required=True, help="node kind (task, story, decision, risk, epic)")
    p.add_argument("--k", type=int, default=3, help="maximum results (default: 3)")
    p = sp.add_parser("snapshot", help="copy the store up to a given evidence item for a re-extraction run")
    p.add_argument("--before", required=True, help="evidence id: the first item NOT included in the copy")
    p.add_argument("--out", required=True, help="output project directory (must not exist or be empty)")
    p = sp.add_parser("transcribe"); p.add_argument("--limit", type=int, default=3); p.add_argument("--source")
    p = sp.add_parser("pending"); p.add_argument("--limit", type=int, default=0)
    p = sp.add_parser("backfill", help="ask for a read on processed meetings whatever the window and cap (one backfill event)")
    p.add_argument("--read", required=True, choices=sorted(RD.READS))
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--since", help="meetings dated YYYY-MM-DD or later")
    g.add_argument("--days", help="meetings within N days of the newest processed item")
    g.add_argument("--evidence", nargs="+", help="these evidence ids")
    p.add_argument("--dry-run", action="store_true", help="list what would be requested; write nothing")
    p = sp.add_parser("apply"); p.add_argument("--evidence", required=True); p.add_argument("--proposals", required=True)
    p.add_argument("--section", type=int, help="apply only entries quoted from this section (see `sections`); others come back rejected")
    p = sp.add_parser("sections", help="a meeting's reading sections: the lines of its .md and the first ts of each")
    p.add_argument("--evidence", required=True)
    p = sp.add_parser("done"); p.add_argument("--evidence", required=True); p.add_argument("--note")
    p.add_argument("--ack", help="why a third of a long meeting produced nothing (else done refuses: status coverage)")
    p.add_argument("--quote", action="append", help="with --ack: verbatim words from each acknowledged part (proof it was read)")
    p.add_argument("--reread", action="append", help="with --ack: the token `reread` printed for each acknowledged part; the token changes with every apply on the meeting, so run reread again after your last apply")
    p.add_argument("--leftovers", help="the token `leftovers` printed for this gated (6,000+ characters) meeting's current state")
    p.add_argument("--no-decisions", action="append", help="decision pass (a meeting already processed): 8+ verbatim words "
                   "from each part of it with no decision recorded in it (proof it was read for decisions; the part is found "
                   "from the quote)")
    p = sp.add_parser("reread", help="the lines of a meeting's .md that hold one third of its text, with a token for done --reread")
    p.add_argument("--evidence", required=True)
    p.add_argument("--part", type=int, required=True)
    p = sp.add_parser("leftovers", help="sentences that sound like work and that no applied event quotes, per third; with a token for done")
    p.add_argument("--evidence", required=True)
    p.add_argument("--part", type=int, choices=(1, 2, 3), help="only this third of the meeting")
    p.add_argument("--section", type=int, help="only the sentences of this section (see `sections`); token and count stay the whole meeting's")
    p = sp.add_parser("ingest"); p.add_argument("--file", required=True); p.add_argument("--source", default="ingest")
    p = sp.add_parser("people"); p.add_argument("--limit", type=int, default=0)
    p = sp.add_parser("redo"); p.add_argument("--evidence", required=True); p.add_argument("--why")
    p.add_argument("--drop", action="store_true")
    p = sp.add_parser("verify-prep"); p.add_argument("--out"); p.add_argument("--weak", action="store_true")
    p = sp.add_parser("verify-apply"); p.add_argument("--file", required=True)
    p = sp.add_parser("asks"); p.add_argument("--all", action="store_true")
    p = sp.add_parser("pack"); p.add_argument("--days", type=int, default=7); p.add_argument("--out"); p.add_argument("--json", action="store_true", help="output ClientWeeklyData JSON instead of markdown")
    p = sp.add_parser("serve"); p.add_argument("--port", type=int, default=8766)
    p.add_argument("--agents-dir", help="where the Schedules view registers jobs (default ~/Library/LaunchAgents)")
    p = sp.add_parser("export"); p.add_argument("--out")
    p = sp.add_parser("score"); p.add_argument("--gold", required=True); p.add_argument("--out")
    p = sp.add_parser("compare-external", help="compare an external note-taker's actions against the taska tree")
    p.add_argument("--file", required=True, help="JSON file from echo-actions.py")
    p.add_argument("--gold", help="gold JSONL file (one annotation per line) for gold_items counts")
    p = sp.add_parser("report", help="the weekly report: select (what goes in) | apply (fixed content into prose.json) | build (the docx)")
    p.add_argument("what", choices=["select", "apply", "build"]); p.add_argument("--week"); p.add_argument("--out")
    p.add_argument("--prose"); p.add_argument("--sel"); p.add_argument("--reports"); p.add_argument("--today")
    p.add_argument("--snapshot", help="build: the workbench file name published next to the docx"); p.add_argument("--upto")
    p = sp.add_parser("schedules", help="the project's [[schedules]] with each one's next run; --set FILE | --register")
    p.add_argument("--set", help="a JSON list of schedules that replaces [[schedules]] in taska.toml and registers them")
    p.add_argument("--register", action="store_true", help="write and (re)load one launchd agent per schedule")
    p.add_argument("--agents-dir", help="where the agents go (default ~/Library/LaunchAgents)")
    p.add_argument("--force", action="store_true", help="take the schedules over from a machine that is gone (the one-runner lock)")
    p.add_argument("--delete", metavar="NAME", help="remove this schedule from taska.toml and its OS job")
    p.add_argument("--retire", metavar="LABEL", help="unload a job outside taska.toml (or left behind) and move its file to .taska/retired-jobs/")
    p.add_argument("--preview", metavar="NAME", help="what a run of this schedule will do now: week, command, steps, who gets mail")
    p = sp.add_parser("run-schedule", help="one scheduled run: the project's steps, then report, gate, delivery")
    p.add_argument("name"); p.add_argument("--week"); p.add_argument("--dry-run", action="store_true")
    p = sp.add_parser("deliver", help="a week's mail content: reports/WEEK/deliver.json and a ready-to-send mail.md")
    p.add_argument("week"); p.add_argument("--to", required=True); p.add_argument("--reports"); p.add_argument("--status")
    p.add_argument("--schedule", help="the [[schedules]] entry whose cc and Include checklist apply"); p.add_argument("--kind", default="preview")
    p = sp.add_parser("gate", help="the pre-send gate on a week's draft: presend.json + presend.html under reports/WEEK/")
    p.add_argument("week"); p.add_argument("--docx"); p.add_argument("--reports"); p.add_argument("--choices")
    p.add_argument("--baseline"); p.add_argument("--key"); p.add_argument("--calendar"); p.add_argument("--external")
    p.add_argument("--reader", default="the reader"); p.add_argument("--port", type=int, default=8766)
    p.add_argument("--progress", help="append one line per run to this jsonl")
    p = sp.add_parser("run-log", help="a run's step record: begin | step ID RC [DETAIL] | finish | failed | unfinished")
    p.add_argument("what", choices=["begin", "step", "finish", "failed", "unfinished"]); p.add_argument("week_dir")
    p.add_argument("args", nargs="*"); p.add_argument("--report-only", action="store_true")
    p.add_argument("--this-run", action="store_true", help="failed: leave out the steps a report-only rebuild kept")
    p.add_argument("--keep", help="begin --report-only: step ids kept from the last finished run (comma-separated)")
    p = sp.add_parser("schedule"); p.add_argument("--time", default="18:30"); p.add_argument("--launchd", action="store_true")
    p = sp.add_parser("ai", help="reach Claude for an unattended run: a stored token, then CodeMie, then Bedrock if allowed")
    p.add_argument("--probe", action="store_true", help="one tiny call: which route answers, or how to fix each")
    p.add_argument("args", nargs=argparse.REMAINDER, help="claude's arguments, after --")
    p = sp.add_parser("handover", help="pack taska.toml + the store into a zip with a note for the next owner (read-only)")
    p.add_argument("--out", required=True, help="folder to write <project>-<date>.zip and its note into")
    return ap


def main():
    a = build_parser().parse_args()
    fn = globals()["cmd_" + a.cmd.replace("-", "_")]
    try:
        fn(a)
    except (C.ConfigError, ValueError, OSError) as e:
        out({"status": "error", "error": str(e)}, 1)


if __name__ == "__main__":
    main()
