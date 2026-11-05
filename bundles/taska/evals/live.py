#!/usr/bin/env python3
"""taska's producer of the generic live view (evalview.py, eval-live/1): while an eval runs, each key meeting is a
unit, the scorer's buckets are the metrics, `done` refusals are the gates, all read from the copy's own ledger, the
key, the best run so far on the same key and tier, and this run's own session file.

  live.py --copy DIR --key FILE --out DIR [--baseline REC] [--every 15] [--once] [--since EPOCH]

run.py writes the page during a tier-2 run (<out_root>/live/) and keeps a copy in the run's record; this CLI is for a run
started some other way. Reads only."""
import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
for p in (RUN_DIR, EVALS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import evalview as V  # noqa: E402
import record as REC  # noqa: E402
import relabel  # noqa: E402
import trajectory  # noqa: E402
from record import TIERS  # noqa: E402
from taska_lib import config as C, evalscore as ES, store as S  # noqa: E402

EVERY = 15
SESSIONS = Path.home() / ".claude" / "projects"
FINDING = ("B7", "B13")
METRICS = ("B7", "B13", "B15", "B7c")
STAGE = {"not_proposed": "read, never proposed", "card": "only a proposal card", "applied_not_live": "applied, then undone or merged away",
         "proposed_not_applied": "proposed, refused by apply", "meeting_not_processed": "meeting not processed yet", "matched": "found"}
ANSWER_FLAGS = re.compile(r'--(?:no-decisions|ack|quote|note)\s+(?:"((?:[^"\\]|\\.)*)"|\'([^\']*)\')')
write = V.write


def load_baseline(rec: Path) -> dict:
    """What a live page compares with: a record's per-item verdicts, its provenance, and its run's time and cost."""
    rec = Path(rec)
    summary = (json.loads((rec / "trajectory.json").read_text(encoding="utf-8")).get("summary") or {}
               if (rec / "trajectory.json").exists() else {})
    meta = json.loads((rec / "meta.json").read_text(encoding="utf-8")) if (rec / "meta.json").exists() else {}
    meta = {**meta, **REC.run_provenance(rec.parent, meta.get("project") or "")}   # the run's own code, not a rescorer's
    return {"record": rec.name, "rows": json.loads((rec / "score.json").read_text(encoding="utf-8")).get("rows") or [],
            "duration_s": summary.get("duration_s"), "cost_usd": summary.get("cost_usd"), "meta": meta}


def _provenance(meta: dict) -> dict[str, str]:
    commit = (meta.get("git_commit") or "")[:10] + (" + uncommitted changes" if meta.get("git_dirty") else "")
    return {"code": commit, "prompts": (meta.get("prompts_sha256") or "")[:12], "model": " + ".join(meta.get("models") or [])}


def _gates(run: trajectory.Run, evid: str) -> list[dict]:
    """Every `done` on one meeting: what it answered, which parts, and what the agent gave it."""
    out = []
    for c in run.calls:
        cmd = c.input.get("command", "") if c.name == "Bash" else ""
        if " done " not in cmd or f"--evidence {evid}" not in cmd:
            continue
        a = trajectory._json_answer(c.output)
        parts = [p if isinstance(p, int) else p.get("part") for p in a.get("parts") or []
                 if isinstance(p, int) or (isinstance(p, dict) and not p.get("events"))]
        out.append({"status": a.get("status") or ("ok" if not c.is_error else "failed"), "parts": parts,
                    "answer": " · ".join(x or y for x, y in ANSWER_FLAGS.findall(cmd))})
    return out


def snapshot(copy: Path, key: list[dict], started: float, now: float, root: Path | None = None,
             exit_code: int | None = None, record: str | None = None, model_tier: str | None = None,
             baseline: dict | None = None, out_root: Path | None = None) -> dict:
    """The eval so far as an eval-live/1 document. `exit_code` set means the run has ended (0 finished, else stopped)."""
    store = C.load(copy).store
    processed = S.processed(S.events(store))
    session = trajectory.find_session(copy, started, root or SESSIONS)
    run = trajectory.load_run(session) if session else None
    traces = {t.evidence: t for t in trajectory.evidence_traces(run, store / "evidence")} if run else {}
    if not started and run and run.started:   # a run started elsewhere: dated by its own session
        started = dt.datetime.fromisoformat(run.started.replace("Z", "+00:00")).timestamp()
    used = sorted({a.model for a in run.agents if a.model}) if run else []
    model_tier = model_tier or next((t for t, m in TIERS.items() if set(used) == {m}), None)
    ended = exit_code is not None or bool(run and run.complete)   # the harness's exit code, else the session's own end
    status = "running" if not ended else "stopped" if exit_code not in (None, 0) else "finished"
    if exit_code is None and ended and run.ended:   # a session that ended on its own: its own span
        now = dt.datetime.fromisoformat(run.ended.replace("Z", "+00:00")).timestamp()

    score = ES.score(store, REC.neutral(key))
    key_by, nodes, titles = {g["cid"]: g for g in key}, relabel._nodes(copy), S.evidence(store)
    by_bucket = {b: {r["key_id"]: r for r in score["rows"] if r["bucket"] == b and r.get("key_id")} for b in METRICS}
    base_rows = (baseline or {}).get("rows") or []
    base_v = {b: {r["key_id"]: r["verdict"] == "hit" for r in base_rows if r.get("bucket") == b and r.get("key_id")} for b in METRICS}

    def item(cid: str, bucket: str) -> dict:
        g, r = key_by[cid], by_bucket[bucket][cid]
        near = relabel._similar(g, nodes)
        return {"id": cid, "title": g.get("title") or "", "quote": g.get("quote") or "", "where": f"{g['evidence']} {g.get('ts') or ''}".strip(),
                "stage": STAGE.get(r.get("stage") or "", r.get("stage") or ""), "nearby": f"{near['id']} · {near['title']}" if near else ""}

    metrics = []
    for b in METRICS:
        m = {"code": b, "name": ES.BUCKET_NAMES[b], "ok": score[b]["ok"], "n": score[b]["n"], "baseline_ok": None, "baseline_n": None}
        if baseline:
            b15 = [r for r in base_rows if r.get("bucket") == "B15"]
            m["baseline_ok"] = sum(r["verdict"] == "hit" for r in b15) if b == "B15" else sum(base_v[b].values())
            m["baseline_n"] = len(b15) if b == "B15" else score[b]["n"]
        metrics.append(m)
    compared = [V.compare_items(b, ES.BUCKET_NAMES[b], {c: r["verdict"] == "hit" for c, r in by_bucket[b].items()}, base_v[b],
                                {c: item(c, b) for c in by_bucket[b]}) for b in FINDING] if baseline else []

    names = {a.agent_id: a.description or a.agent_id for a in run.agents} if run else {}
    units, reasons = [], {}
    for evid in sorted({g["evidence"] for g in key}):
        t = traces.get(evid)
        mine = {c: (b, r) for b in FINDING for c, r in by_bucket[b].items() if r["meeting"] == evid}
        gates = _gates(run, evid) if run else []
        for g in gates:
            if g["status"] != "ok":
                reasons[g["status"]] = reasons.get(g["status"], 0) + 1
        state = ("done" if evid in processed else "at the gate" if gates and gates[-1]["status"] != "ok"
                 else "writing" if t and t.applied else "reading" if t and t.lines_seen else "queued")
        units.append({"id": evid, "title": (titles.get(evid) or {}).get("title") or "", "state": state,
                      "found": sum(r["verdict"] == "hit" for _, r in mine.values()), "expected": len(mine),
                      "baseline_found": sum(base_v[b].get(c, False) for c, (b, _) in mine.items()) if baseline else None,
                      "missed": [item(c, b) for c, (b, r) in mine.items() if r["verdict"] == "miss"], "gate": gates,
                      "agent": names.get(t.reader, t.reader) if t and t.reader else ""})
    refusals = sum(reasons.values())
    errors = sum(a.errors for a in run.agents) if run else 0
    blocked = trajectory.blocked_commands(run) if run else 0
    agents = [{"name": "main" if a.agent_id == "main" else a.description or a.agent_id, "model": a.model, "turns": a.turns,
               "units": [u["id"][:10] for u in units if u["agent"] == (a.description or a.agent_id)],
               "last": next((trajectory.step_words(c) for c in reversed(run.calls) if c.agent_id == a.agent_id), "")} for a in run.agents] if run else []
    minutes = lambda s: f"{round(s / 60)} min" if s else None
    stamp = lambda x: dt.datetime.fromtimestamp(x).isoformat(timespec="seconds")
    known = REC.run_provenance(out_root, str(copy)) if record and out_root else {}   # recorded: the run's own record
    this = _provenance(known or dict(REC.provenance(RUN_DIR), models=used))
    return {"schema": V.SCHEMA, "title": "taska cold re-extraction", "unit_label": "meeting", "status": status,
            "started": stamp(started), "updated": stamp(now), "elapsed_s": int(now - started), "model_tier": model_tier,
            "record": record, "source": str(copy), "session": str(session) if session else None,
            "metrics": metrics,
            "baseline": {"record": baseline["record"], "of": baseline.get("of"), "compared": compared} if baseline else None,
            "verdict": V.verdict(compared, baseline["record"] if baseline else None, (baseline or {}).get("of")) if ended else None,
            "provenance": {"this": this, "baseline": _provenance(baseline.get("meta") or {}) if baseline else {}},
            "units": units, "stuck": {"reasons": reasons},
            "health": [{"name": "Time", "value": minutes(now - started) or "0 min", "baseline": minutes((baseline or {}).get("duration_s") or 0)},
                       {"name": "Meetings done", "value": f"{sum(u['state'] == 'done' for u in units)}/{len(units)}", "baseline": None},
                       {"name": "Gate refusals (by design)", "value": refusals, "baseline": None},
                       {"name": "Tool errors (not gates)", "value": max(0, errors - refusals), "baseline": None},
                       {"name": "Blocked commands", "value": blocked, "baseline": None},
                       {"name": "Cost", "value": f"${run.cost_usd:.2f}" if run and run.complete else "—",
                        "baseline": f"${baseline['cost_usd']}" if baseline and baseline.get("cost_usd") else None}],
            "agents": agents}


def precision_tile(metrics: list[dict], quality: dict | None, href: str) -> tuple[list[dict], list[dict]]:
    """One precision tile: the judged precision once the judge is validated, else key-match precision (B15) with the
    reason it is only a lower bound; the numbers behind it as a caveat."""
    p = ((quality or {}).get("summary") or {}).get("precision")
    if not p:
        return metrics, []
    if not p.get("validated"):
        return metrics, [{"text": f"Key-match precision is a lower bound: {p.get('flag') or 'not validated'}.", "href": href}]
    pct = lambda v: "—" if v is None else f"{round(100 * v)}%"
    tile = {"code": "precision", "name": "Judged precision", "ok": p["correct"], "n": p["n"], "baseline_ok": None, "baseline_n": None}
    burden = "—" if p.get("per_meeting_to_delete") is None else p["per_meeting_to_delete"]
    text = (f"Judged precision {pct(p.get('rate'))} (at least {pct(p.get('lb95'))}), {p['correct']} of {p['n']} judged; "
            f"{burden} items to delete per meeting ({p.get('meetings')} meetings).")
    return [tile if m["code"] == "B15" else m for m in metrics], [{"text": text, "href": href}]


def caveats(quality: dict, href: str) -> list[dict]:
    """What the run's quality check says about its counts, for eval-live/1's caveats: matches nobody has judged, created
    items not yet judged, and how much of the run the independent notes cover."""
    s = quality.get("summary") or {}
    found, (answered, asked) = s.get("found") or [0, 0], s.get("judged_items") or [0, 0]
    pos = sum(i["produced"].get("matched_by") == "position" for u in quality.get("units") or [] for i in u.get("items") or [])
    out = []
    if s.get("found_correct") and answered >= asked:
        out.append({"text": f"Judged: {s['found_correct'][0]} of {s['found_correct'][1]} found correctly (a plain count says "
                            f"{found[0]}/{found[1]}).", "href": href})
    else:
        out.append({"text": f"Found {found[0]}/{found[1]} counts every match, {pos} only by position; {answered} of {asked} "
                            f"judge questions are answered, so a found item may still be different work.", "href": href})
    if s.get("not_in_key_unjudged"):
        out.append({"text": f"{s['not_in_key_unjudged']} created items are not in the key and not yet judged (real work, "
                            f"a duplicate or not work).", "href": href})
    have, of = s.get("independent_coverage") or [0, 0]
    if of and have < of:
        out.append({"text": f"Independent notes cover {have} of {of} meetings.", "href": href})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--copy", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=int, default=EVERY)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--since", type=float, default=0.0, help="epoch seconds the run started (default: any session)")
    ap.add_argument("--baseline", help="a record folder to compare with (same key and model tier)")
    a = ap.parse_args(argv)
    key = ES.load_gold(a.key)[1]
    baseline = load_baseline(Path(a.baseline)) if a.baseline else None
    while True:
        doc = snapshot(Path(a.copy), key, a.since, time.time(), baseline=baseline)
        write(Path(a.out), doc)
        if a.once or doc["status"] != "running":   # the session ended: the last write stands, the watcher stops
            print(json.dumps({"status": doc["status"], "metrics": {m["code"]: f"{m['ok']}/{m['n']}" for m in doc["metrics"]}}))
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
