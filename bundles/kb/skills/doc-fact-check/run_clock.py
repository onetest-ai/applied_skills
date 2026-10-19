"""Record per-stage timings for /kb:doc-fact-check and /kb:deck-fact-check (spec §4.7).

    python run_clock.py <work dir> <stage> start|end [--workers N]

Writes <work dir>/timings.json ({timings: {stage: {start, end}}, workers: {stage: N}, minutes_total,
missing_start}) and, when <work dir>/../run.json exists (written by merge_findings.py), mirrors timings,
workers and minutes_total into it, so the findings page and baseline show the measured time.
Stages: extract, figures, chunk, verify, merge, verify6b, annotate, report. `extract start` begins a new
run: it discards the timings and workers an earlier run left in timings.json. Never fails a run over a
missing start: the stage is listed in missing_start instead; a malformed timings.json is read as empty.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

STAGES = ("extract", "figures", "chunk", "verify", "merge", "verify6b", "review", "annotate", "report")


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _stamp(v) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def _minutes(timings: dict) -> float | None:
    stamps = [x for t in timings.values() if isinstance(t, dict) for v in t.values() if (x := _stamp(v))]
    if len(stamps) < 2:
        return None
    return round((max(stamps) - min(stamps)).total_seconds() / 60, 1)


def record(work_dir: Path, stage: str, edge: str, workers: int | None = None, now: str | None = None) -> dict:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r} (one of {', '.join(STAGES)})")
    if edge not in ("start", "end"):
        raise ValueError("edge must be start or end")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "timings.json"
    doc = {} if (stage, edge) == ("extract", "start") else _load(path)   # extract start begins a new run
    timings, wk = doc.get("timings"), doc.get("workers")
    timings = {k: t for k, t in timings.items() if isinstance(t, dict)} if isinstance(timings, dict) else {}
    wk = wk if isinstance(wk, dict) else {}
    timings.setdefault(stage, {})[edge] = now or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if workers is not None:
        wk[stage] = workers
    doc = {"timings": timings, "workers": wk, "minutes_total": _minutes(timings),
           "missing_start": sorted(s for s, t in timings.items() if "end" in t and "start" not in t)}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    run_path = work_dir.parent / "run.json"
    if run_path.exists():
        run = _load(run_path)
        run.update(timings=timings, workers=wk, minutes_total=doc["minutes_total"])
        run_path.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def stamp(work_dir, stage: str, edge: str, workers: int | None = None) -> None:
    """A script's own clock (`--clock <work dir>`): records the edge, and never fails the script that calls it."""
    try:
        record(Path(work_dir), stage, edge, workers)
    except (OSError, ValueError) as e:
        print(f"run_clock: {stage} {edge} not recorded ({e})", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("work_dir")
    ap.add_argument("stage")
    ap.add_argument("edge", choices=("start", "end"))
    ap.add_argument("--workers", type=int)
    a = ap.parse_args(argv)
    try:
        doc = record(Path(a.work_dir), a.stage, a.edge, a.workers)
    except ValueError as e:
        print(f"run_clock.py: {e}", file=sys.stderr)
        return 2
    total = doc["minutes_total"]
    print(f"{a.stage} {a.edge}" + (f" · {total} min so far" if total is not None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
