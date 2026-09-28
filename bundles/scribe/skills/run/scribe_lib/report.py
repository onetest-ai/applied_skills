"""report: the run report (`out/_runs/<date>.json`) and operator metrics
over it (spec A12; PoC finding I7).

This module owns `out/_runs/<date>.json` end to end — `run_report_path` (one
file per replayed day), `append_run` (every row-writer in the codebase:
`publish_task`/`_resume_journal` in `publish.py`, and `scribe.py`'s
`report --status`, funnel through this one function), and `summary`, the
aggregation `scribe.py report --summary` prints.

**Merge counts ride along on every row for free.** `append_run` looks up
`work/<task>/merge.json` for the row's `task` (skipping the synthetic
`"_run"` task and any row with no matching file — nothing ran, or merge
never got that far) and embeds its `sections` dict under `"merge"` before
writing the row. That is the ONE place a task's per-section
`kept/reworded/recited/added/superseded/dropped_by_check/dropped_by_model/
false_stale` counts (`merge._merge_drafted_section`) reach the run report —
`publish_task`'s "published" row and `scribe.py report --status`'s row both
get it automatically, simply by calling `append_run` last, after `merge` has
already run and written `merge.json` for that task this run. `summary`
therefore reads ONLY the run report; it never re-reads `work/`.

A task is **stale** this run if any of its sections has `status: "drafted"`.
It is **false-stale** (A12) if any section came back with `false_stale: true`
— that per-section flag (`merge._merge_drafted_section`: `status ==
"drafted" and added + reworded + recited + superseded == 0`) is read as-is,
never recomputed here (controller ruling R4: a drop is still counted in
`dropped_by_check`/`dropped_by_model` regardless of whether the section is
false-stale)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scribe_lib.config import Config

_SECTION_COUNT_KEYS = (
    "kept", "reworded", "recited", "added", "superseded", "dropped_by_check", "dropped_by_model",
)


def run_report_path(config: Config) -> Path:
    """`out/_runs/<SCRIBE_NOW date>.json` — one run report per (replayed) day."""
    return config.out_root / "_runs" / f"{config.now[:10]}.json"


def merge_counts_for_row(config: Config, task_id: str) -> dict[str, Any] | None:
    """`work/<task_id>/merge.json`'s `sections` dict, or `None` when that
    file doesn't exist for this run (merge never ran, or wrote nothing)."""
    path = config.work_dir / task_id / "merge.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data.get("sections") or {}


def append_run(config: Config, entry: dict[str, Any]) -> None:
    """Append one row to the run report — every writer in the codebase
    (`publish_task`, `_resume_journal`, `scribe.py`'s `report --status`) goes
    through this. A row naming a real task (not the synthetic `"_run"`) has
    that task's `merge.json` sections embedded under `"merge"` when present,
    so the row is self-sufficient for `summary` without touching `work/`."""
    path = run_report_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    row = dict(entry)
    task_id = row.get("task")
    if task_id and task_id != "_run":
        merge = merge_counts_for_row(config, task_id)
        if merge is not None:
            row["merge"] = merge

    rows.append(row)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def record(config: Config, *, task: str | None, status: str, reason: str, stage: str | None = None) -> dict[str, Any]:
    """Build + append a `report --status` row (`scribe.py`'s `cmd_report`
    calls this once it has validated `--task`/`--reason`). Returns the row
    that was appended (before `merge` embedding, which `append_run` adds)."""
    row: dict[str, Any] = {"task": task or "_run", "version": None, "status": status, "reasons": [reason]}
    if stage:
        row["stage"] = stage
    append_run(config, row)
    return row


def rows(config: Config) -> list[dict[str, Any]]:
    """Today's (or the resumed replay date's) run-report rows, as written —
    `scribe.py report` (no `--status`) prints these."""
    path = run_report_path(config)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def last_minutes(config: Config, task_id: str) -> float | None:
    """The most recently recorded `minutes` value for `task_id` across every
    run report under `out/_runs/*.json` (oldest to newest by filename, so
    the last match found is the most recent), or `None` when it has never
    been recorded — `compute_plan`'s budget deferral (task 11) then falls
    back to `default_task_minutes`."""
    runs_dir = config.out_root / "_runs"
    if not runs_dir.is_dir():
        return None
    found: float | None = None
    for path in sorted(runs_dir.glob("*.json")):
        try:
            file_rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for row in file_rows:
            if row.get("task") == task_id and row.get("minutes") is not None:
                found = row["minutes"]
    return found


def _task_entry(row: dict[str, Any]) -> dict[str, Any]:
    """A task-level row for `summary`, folded from its `merge.json` sections
    (embedded on the run-report row by `append_run`). `stale`/`false_stale`
    are read off each section's own `status`/`false_stale` (as
    `merge._merge_drafted_section` computed them — `false_stale = status ==
    "drafted" and added + reworded + recited + superseded == 0`), never
    recomputed here: a task is `stale` if ANY section drafted, and
    `false_stale` if any section came back false-stale — the redraft did
    SOMETHING nowhere near as useful as it looked."""
    merge = row.get("merge") or {}
    totals = dict.fromkeys(_SECTION_COUNT_KEYS, 0)
    stale = False
    false_stale = False
    for sec in merge.values():
        if sec.get("status") == "drafted":
            stale = True
        if sec.get("false_stale"):
            false_stale = True
        for key in _SECTION_COUNT_KEYS:
            totals[key] += sec.get(key, 0)
    return {
        "task": row.get("task"),
        "status": row.get("status"),
        "version": row.get("version"),
        "stale": stale,
        "false_stale": false_stale,
        **totals,
    }


def summary(config: Config, date: str) -> dict[str, Any]:
    """`{"date", "tasks": [...], "totals": {...}}` for `out/_runs/<date>.json`
    — one entry per row, in file order (a `"_run"`-level row is included
    like any other; it has no `merge` and contributes zeros/`False`).
    `totals` sums every task's numeric fields plus counts of
    `stale`/`false_stale` tasks and the row count (`"tasks"`)."""
    path = config.out_root / "_runs" / f"{date}.json"
    report_rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    tasks = [_task_entry(row) for row in report_rows]

    totals: dict[str, Any] = dict.fromkeys(_SECTION_COUNT_KEYS, 0)
    totals["stale"] = 0
    totals["false_stale"] = 0
    totals["tasks"] = len(tasks)
    for t in tasks:
        if t["stale"]:
            totals["stale"] += 1
        if t["false_stale"]:
            totals["false_stale"] += 1
        for key in _SECTION_COUNT_KEYS:
            totals[key] += t[key]

    return {"date": date, "tasks": tasks, "totals": totals}


__all__ = ["run_report_path", "append_run", "merge_counts_for_row", "record", "rows", "last_minutes", "summary"]
