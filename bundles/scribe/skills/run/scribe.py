#!/usr/bin/env python3
"""Scribe CLI — maintain versioned, cited living documents from a Brain.

Deterministic work only; the drafting/citing itself is the agent's job (scribe:run
SKILL.md). Every command prints one JSON object to stdout.

Exit codes: 0 ok · 1 refused/failed (JSON has a "reason") · 2 usage / not implemented.

Usage:
  scribe.py --project PROJ validate
  scribe.py --project PROJ plan [--due] [--task ID]
  scribe.py --project PROJ delta <task>

Implemented: validate, plan, delta, gather-raw, fingerprint, base, prepare,
check-file, check-task, merge, accept, publish, lineage, index, render,
doctor, report, observe, list, status, enable, disable, promote. `publish
--no-render` still works without a render.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scribe_lib.accept import accept_task  # noqa: E402
from scribe_lib.basedoc import base_task  # noqa: E402
from scribe_lib.checkfile import check_file_task  # noqa: E402
from scribe_lib.checktask import check_task_task  # noqa: E402
from scribe_lib.config import (  # noqa: E402
    Config,
    ScribeError,
    compute_brain_delta,
    compute_raw_delta,
    group_membership,
    parse_template_ref,
    raw_root_available,
    read_brain_identity,
    read_state,
    resolve_instance_inputs,
    load_config,
    sha256_file,
    validate_all,
)
from scribe_lib.doctor import run_doctor  # noqa: E402
from scribe_lib.fingerprint import fingerprint_task  # noqa: E402
from scribe_lib.index import build_index, query_index  # noqa: E402
from scribe_lib.lineage import lineage_task  # noqa: E402
from scribe_lib.merge import merge_task  # noqa: E402
from scribe_lib.observe import observe_task  # noqa: E402
from scribe_lib.pack import prepare_task  # noqa: E402
from scribe_lib.publish import publish_task  # noqa: E402
from scribe_lib.raw import gather_raw_task  # noqa: E402
from scribe_lib import registry  # noqa: E402
from scribe_lib import report  # noqa: E402
from scribe_lib.render import render_task  # noqa: E402

NOT_IMPLEMENTED: tuple[str, ...] = ()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe", description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True, help="Scribe project dir (holds scribe.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="Load scribe.toml + tasks/templates; schema check + DAG check")

    p_plan = sub.add_parser("plan", help="Topological task order + which tasks are due")
    p_plan.add_argument("--due", action="store_true", help="List only due tasks")
    p_plan.add_argument("--task", help="Limit to one task id; also forces cadence:manual to run")

    p_delta = sub.add_parser("delta", help="Brain + raw changes since a task's last build")
    p_delta.add_argument("task", help="Task id")

    p_gather = sub.add_parser("gather-raw", help="Select + parse this task's raw-replay files")
    p_gather.add_argument("task", help="Task id")

    p_fp = sub.add_parser("fingerprint", help="Per-section staleness vs. the Brain + raw index")
    p_fp.add_argument("task", help="Task id")

    p_base = sub.add_parser("base", help="Recover the drafting base from the latest published version")
    p_base.add_argument("task", help="Task id")

    p_prepare = sub.add_parser("prepare", help="delta + base + gather-raw + fingerprint -> work/<task>/pack/")
    p_prepare.add_argument("task", help="Task id")

    p_checkfile = sub.add_parser("check-file", help="Verify every [FILE:] claim's quote against the parsed raw text")
    p_checkfile.add_argument("task", help="Task id")

    p_checktask = sub.add_parser("check-task", help="Verify every [TASK:] claim cites a live upstream claim")
    p_checktask.add_argument("task", help="Task id")

    p_merge = sub.add_parser("merge", help="base.md + drafted stale sections -> work/<task>/next.md")
    p_merge.add_argument("task", help="Task id")

    p_observe = sub.add_parser(
        "observe",
        help="Record a noop's observation (raw/brain snapshot, upstream versions, section fingerprints) "
        "without publishing",
    )
    p_observe.add_argument("task", help="Task id")

    p_accept = sub.add_parser("accept", help="Run acceptance checks on work/<task>/next.md")
    p_accept.add_argument("task", help="Task id")

    p_render = sub.add_parser("render", help="work/<task>/next.md -> work/<task>/render/ (docx + pdf + diagrams)")
    p_render.add_argument("task", help="Task id")
    p_render.add_argument("--md", help="Markdown path to render (default work/<task>/next.md)")

    sub.add_parser("doctor", help="Check pandoc/soffice/mermaid are on PATH")

    p_publish = sub.add_parser("publish", help="Version + write work/<task>/next.md into out/<task.out>/")
    p_publish.add_argument("task", help="Task id")
    p_publish.add_argument(
        "--no-render", action="store_true", help="Publish the Markdown version only (skip the render pass)"
    )

    p_lineage = sub.add_parser("lineage", help="Write _src/vNNN.lineage.json + vNNN.sources.json")
    p_lineage.add_argument("task", help="Task id")

    p_index = sub.add_parser("index", help="Rebuild out/_lineage/index.json; --query answers 'what depends on X'")
    p_index.add_argument("--query")

    p_report = sub.add_parser(
        "report",
        help="Append a row to (or, with no --status, show) today's run report out/_runs/<date>.json",
    )
    p_report.add_argument("--task", help="Task id (omit for a run-level row, recorded as task '_run')")
    p_report.add_argument(
        "--status",
        choices=("noop", "failed", "skipped"),
        help="Row status; omit to only print the run report path and its rows",
    )
    p_report.add_argument("--stage", help="Step that ended the task, e.g. prepare, merge, accept, verifier")
    p_report.add_argument("--reason", help="Why (required with --status)")
    p_report.add_argument(
        "--summary", action="store_true",
        help="Print operator metrics (A12): per-task churn + totals, aggregated from the run report",
    )
    p_report.add_argument("--date", help="Date (YYYY-MM-DD) for --summary; default today (SCRIBE_NOW)")

    sub.add_parser("list", help="JSON rows: task, template, group, enabled, cadence, publish, version, last_status")

    p_status = sub.add_parser("status", help="One task's registry row + its last run-report row")
    p_status.add_argument("task", help="Task id")

    p_enable = sub.add_parser("enable", help="Rewrite the task's enabled: frontmatter line to true")
    p_enable.add_argument("task", help="Task id")

    p_disable = sub.add_parser("disable", help="Rewrite the task's enabled: frontmatter line to false")
    p_disable.add_argument("task", help="Task id")

    p_promote = sub.add_parser("promote", help="Write a reusable template from a proven task instance")
    p_promote.add_argument("task", help="Task id")
    p_promote.add_argument("--as", dest="template_id", required=True, help="New template id")
    p_promote.add_argument(
        "--force", action="store_true", help="Overwrite an existing template id instead of refusing"
    )

    return parser


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=False))


def cmd_validate(config: Config) -> int:
    result = validate_all(config)
    _print(
        {
            "status": "ok",
            "templates": sorted(result["templates"]),
            "tasks": result["order"],
        }
    )
    return 0


def _due_reasons(
    config: Config,
    instance: dict[str, Any],
    template: dict[str, Any],
    state: dict[str, Any],
    upstream_versions: dict[str, int],
    *,
    explicit: bool,
    enabled: bool,
) -> tuple[bool, list[str], str, list[str]]:
    """Returns (due, reasons, cadence, notes). `explicit` = this task was
    named via --task. `enabled` (task 11) is the task's effective enabled
    state — the AND of every matching group's `enabled` when it belongs to
    one or more groups (`config.group_membership`), else its own
    `enabled:` frontmatter (default true). `notes` (review fix round 1,
    Important #4) carries `"raw_root_unavailable"` whenever the synced raw
    folder is offline right now — independent of `state`/`due`, so a
    first-run task or one with no other reason to be due still surfaces the
    outage rather than a quiet, misleadingly-normal `plan`."""
    reasons: list[str] = []
    notes: list[str] = []
    cadence = instance.get("cadence", "on-brain-update")
    if not raw_root_available(config):
        notes.append("raw_root_unavailable")
    if not state:
        reasons.append("first_run")
    else:
        brain_delta = compute_brain_delta(config, state)
        if brain_delta["changed"] or brain_delta["added"] or brain_delta["removed"]:
            reasons.append("brain_changed")

        raw_inputs = resolve_instance_inputs(instance, template).get("raw") or {}
        raw_delta = compute_raw_delta(config, raw_inputs, state)
        # m2: the synced raw folder can be offline. `compute_raw_delta` marks
        # that with `unavailable: True` and reports no new/changed/removed —
        # never flag `raw_changed` from an outage, which would look
        # (wrongly) like every raw file had just been deleted.
        if not raw_delta.get("unavailable") and (raw_delta["new"] or raw_delta["changed"] or raw_delta["removed"]):
            reasons.append("raw_changed")

        recorded_upstream: dict[str, int] = state.get("upstream_versions") or {}
        if any(version > recorded_upstream.get(up, -1) for up, version in upstream_versions.items()):
            reasons.append("upstream_published")

        published = state.get("published") or {}
        docx_sha = published.get("docx_sha256")
        out_dir = config.out_root / instance["out"]
        docx_path = out_dir / f"{instance['title']}.docx"
        if docx_sha and docx_path.is_file() and sha256_file(docx_path) != docx_sha:
            reasons.append("base_edited")

        if cadence == "daily":
            # Due when the last time this task was touched at all — either
            # published (`built_at`) or merely checked on a noop
            # (`last_checked`, written by `observe`) — falls on an earlier
            # calendar day than `now`. Using `built_at` alone would re-run a
            # daily task every night forever once it stops finding anything
            # new to draft (a noop never advances `built_at`).
            built_at = state.get("built_at") or ""
            last_checked = state.get("last_checked") or ""
            latest = max(built_at, last_checked)
            if latest[:10] != config.now[:10]:
                reasons.append("cadence")

    due = bool(reasons)
    if cadence == "manual" and not explicit:
        due = False
    if not enabled and not explicit:
        due = False
    return due, reasons, cadence, notes


def compute_plan(
    config: Config, data: dict[str, Any], *, task: str | None = None, due_only: bool = False
) -> dict[str, Any]:
    """Pure core of the `plan` subcommand: topological task order + which
    tasks are due, given already-`validate_all`'d `data`. Exposed under this
    name (rather than kept private to `cmd_plan`) because later tasks (11,
    14) call it directly rather than going through argparse."""
    instances, templates, edges, order = (
        data["instances"],
        data["templates"],
        data["edges"],
        data["order"],
    )

    if task and task not in instances:
        raise ScribeError(f"unknown task '{task}'")

    entries: dict[str, dict[str, Any]] = {}
    for tid in order:
        inst = instances[tid]
        template_id, _ = parse_template_ref(inst["template"])
        template = templates[template_id]
        state = read_state(config, inst)
        upstream_versions = {
            up: (read_state(config, instances[up]).get("version") or 0) for up in edges[tid]
        }
        explicit = task == tid
        group, group_enabled = group_membership(config, tid)
        # Fix round 1, issue 1: a task runs only when its OWN `enabled:` is
        # true AND (it is in no group, or every matching group is enabled)
        # — not one or the other. Ignoring the instance's own `enabled:`
        # whenever any group matched made `scribe.py disable <task>` a
        # silent no-op for every grouped task.
        enabled = inst.get("enabled", True) and (True if group is None else group_enabled)
        due, reasons, cadence, notes = _due_reasons(
            config, inst, template, state, upstream_versions, explicit=explicit, enabled=enabled
        )
        depends_on_due = any(entries[up]["due"] for up in edges[tid])

        not_due_reason = None
        if not due:
            if not enabled and not explicit:
                not_due_reason = "disabled"
            elif cadence == "manual" and not explicit:
                not_due_reason = "cadence manual"
            else:
                not_due_reason = "up to date"

        entries[tid] = {
            "id": tid,
            "task": tid,
            "template": inst["template"],
            "due": due,
            "reasons": reasons,
            "not_due_reason": not_due_reason,
            "depends_on_due": depends_on_due,
            "cadence": cadence,
            "upstream": edges[tid],
            "notes": notes,
            "group": group,
            "enabled": enabled,
        }

    # Budget deferral (task 11; fix round 1, issue 4). A running sum, in
    # topological order, of each DUE task's minutes: `report.last_minutes`
    # when it has run before, else `default_task_minutes`. A task past
    # `budget_minutes` or `max_tasks_per_run` is marked `deferred` — NEVER
    # dropped from `tasks_out`, still due, still runnable with `--task`;
    # only `plan --due` skips a deferred task (it stays due again next
    # `--due` sweep, budget permitting — see `due_only` below).
    #
    # The running sum SEEDS from minutes already spent THIS run (today's
    # run-report rows), not from zero, so re-planning mid-run (the run
    # SKILL's loop calls `plan --due` again after every task) does not
    # forget what already ran. A task with a row in today's report already
    # ran this run — it is never re-admitted or deferred here, regardless
    # of whether it is still `due` (e.g. daily cadence still shows `due`
    # after a noop `observe`).
    #
    # `skipped` rows are excluded (fix round 3, Minor): a task skipped at
    # the PLAN stage — "not due" or "upstream_failed" — never actually ran
    # (`prepare` was never called for it this cycle, per `report.py`'s
    # `_MINUTES_STATUSES`), so it must not consume budget minutes, a
    # `max_tasks_per_run` slot, or turn off the run's first-task
    # no-starvation bypass. It stays eligible to be picked up as a normal
    # not-yet-run due task by a later `plan --due` this same run (e.g. once
    # its upstream unblocks).
    today_rows = report.rows(config)
    ran_today: dict[str, float] = {}
    for row in today_rows:
        row_task = row.get("task")
        if not row_task or row_task == "_run" or row.get("status") == "skipped":
            continue
        ran_today[row_task] = row.get("minutes") if row.get("minutes") is not None else config.default_task_minutes

    running_minutes = sum(ran_today.values())
    admitted = len(ran_today)
    # Fix round 2, issue 4: the no-starvation bypass (below) must fire AT
    # MOST once per RUN, not once per `plan --due` call — the run SKILL
    # loop calls `plan --due` again after every task, so seeding this
    # `False` on every call let every single task bypass the budget in
    # turn. Seeding it from `ran_today` (something already ran THIS run)
    # means the bypass only ever fires for the very first task of the run.
    first_due_admitted = bool(ran_today)
    for tid in order:
        entry = entries[tid]
        if not entry["due"]:
            entry["deferred"] = False
            continue
        if tid in ran_today:
            # Already ran (published/noop/failed/pending) earlier this run —
            # counted in the seed above; never re-admitted or deferred. A
            # `skipped` row does NOT land here (excluded from `ran_today`
            # above) — a task skipped at plan stage falls through to the
            # normal budget check below instead, as if it had not been
            # touched this run yet.
            entry["deferred"] = False
            continue
        task_minutes = report.last_minutes(config, tid)
        if task_minutes is None:
            task_minutes = config.default_task_minutes
        over_budget = admitted >= config.max_tasks_per_run or running_minutes + task_minutes > config.budget_minutes
        if over_budget and first_due_admitted:
            entry["deferred"] = True
        else:
            # The first due-and-not-yet-run task of this plan is always
            # admitted, even over budget — otherwise a single task whose
            # own estimate exceeds the whole budget would be deferred on
            # every run forever, which amounts to dropping it (no
            # starvation, fix round 1 ruling).
            entry["deferred"] = False
            running_minutes += task_minutes
            admitted += 1
            first_due_admitted = True

    # Top-level notes (review fix round 1, Important #4): computed over
    # EVERY task, before `--task`/`--due` filtering, so an offline raw root
    # stays visible even when the filtered `tasks` list would otherwise hide
    # every task that has it. Fan-out notes (fix round 1, issue 2) are
    # folded in the same way.
    top_notes = sorted(
        {n for entry in entries.values() for n in entry["notes"]}
        | {f"fanout_parent_missing: {label}" for label in (data.get("fanout_parent_missing") or [])}
    )

    tasks_out = [entries[tid] for tid in order]
    if task:
        tasks_out = [t for t in tasks_out if t["id"] == task]
    if due_only:
        # Fix round 1, issue 4(b): a deferred task is due but must not be
        # picked up by a `--due` sweep — only the full `plan` (or an
        # explicit `--task`) still shows it, `deferred: true`.
        tasks_out = [t for t in tasks_out if t["due"] and not t["deferred"]]

    return {
        "status": "ok",
        "now": config.now,
        "run_report": str(report.run_report_path(config)),
        "order": order,
        "tasks": tasks_out,
        "notes": top_notes,
        "new_fanout_children": data.get("new_fanout_children") or [],
    }


def cmd_plan(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    result = compute_plan(config, data, task=args.task, due_only=args.due)
    _print(result)
    return 0


def cmd_delta(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates = data["instances"], data["templates"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    inst = instances[args.task]
    template_id, _ = parse_template_ref(inst["template"])
    template = templates[template_id]
    state = read_state(config, inst)

    brain_delta = compute_brain_delta(config, state)
    raw_inputs = resolve_instance_inputs(inst, template).get("raw") or {}
    raw_delta = compute_raw_delta(config, raw_inputs, state)

    _print(
        {
            "status": "ok",
            "task": args.task,
            "first_run": not state,
            "brain": {k: v for k, v in brain_delta.items() if k != "current"},
            "raw": {k: v for k, v in raw_delta.items() if k != "current"},
        }
    )
    return 0


def cmd_gather_raw(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates = data["instances"], data["templates"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    inst = instances[args.task]
    template_id, _ = parse_template_ref(inst["template"])
    template = templates[template_id]
    raw_inputs = resolve_instance_inputs(inst, template).get("raw") or {}
    result = gather_raw_task(config, args.task, inst, raw_inputs)
    _print({"status": "ok", **result})
    return 0


def cmd_fingerprint(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    result = fingerprint_task(config, args.task, instances, templates, edges)
    _print({"status": "ok", **result})
    return 0


def cmd_base(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates = data["instances"], data["templates"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    inst = instances[args.task]
    template_id, _ = parse_template_ref(inst["template"])
    template = templates[template_id]
    result = base_task(config, args.task, inst, template)
    _print({"status": "ok", "task": args.task, **result})
    return 1 if result.get("status") == "failed" else 0


def cmd_prepare(config: Config, args: argparse.Namespace) -> int:
    result = prepare_task(config, args.task)
    _print(result)
    return 0 if result.get("status") == "ok" else 1


def cmd_check_file(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances = data["instances"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    result = check_file_task(config, args.task, instances[args.task])
    _print({"status": "ok", **result})
    return 0


def cmd_check_task(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances = data["instances"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    result = check_task_task(config, args.task, instances)
    _print({"status": "ok", **result})
    return 0


def cmd_merge(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates = data["instances"], data["templates"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    result = merge_task(config, args.task, instances, templates)
    _print(result)
    return 0


def cmd_observe(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    inst = instances[args.task]
    template_id, _ = parse_template_ref(inst["template"])
    template = templates[template_id]
    result = observe_task(config, args.task, inst, template, instances, edges)
    _print({"status": "ok", **result})
    return 0


def cmd_accept(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates = data["instances"], data["templates"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    inst = instances[args.task]
    template_id, _ = parse_template_ref(inst["template"])
    template = templates[template_id]
    result = accept_task(config, args.task, inst, template)
    _print(result)
    return 0 if result["status"] == "ok" else 1


def cmd_render(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances = data["instances"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    inst = instances[args.task]
    result = render_task(config, args.task, inst, instances, md_path=args.md)
    _print({"status": "ok" if result["ok"] else "error", **result})
    return 0 if result["ok"] else 1


def cmd_doctor(config: Config) -> int:
    result = run_doctor()
    # The Brain the scripts read (scribe.toml brain_db, or SCRIBE_BRAIN_DB).
    # scribe:run compares brain.chunks with the MCP server's health
    # counts.chunks, so scripts and the agent's Brain tools can never silently
    # point at two different stores (e.g. Night 5's Brain copy).
    brain = read_brain_identity(config.brain_db)
    result["brain"] = brain
    brain_ok = "error" not in brain
    if not brain_ok:
        result["status"] = "error"
    _print(result)
    return 0 if result["all_found"] and brain_ok else 1


def cmd_report(config: Config, args: argparse.Namespace) -> int:
    if args.summary:
        date = args.date or config.now[:10]
        _print(report.summary(config, date))
        return 0

    path = report.run_report_path(config)
    if args.status:
        if not args.reason:
            raise ScribeError("report --status needs --reason")
        if args.task:
            data = validate_all(config)
            if args.task not in data["instances"]:
                raise ScribeError(f"unknown task '{args.task}'")
        report.record(config, task=args.task, status=args.status, reason=args.reason, stage=args.stage)
    rows = report.rows(config)
    _print({"status": "ok", "run_report": str(path), "rows": rows})
    return 0


def cmd_publish(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    inst = instances[args.task]
    template_id, _ = parse_template_ref(inst["template"])
    template = templates[template_id]
    result = publish_task(config, args.task, inst, template, instances, edges, no_render=args.no_render)
    _print(result)
    return 0 if result["status"] == "ok" else 1


def cmd_lineage(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates = data["instances"], data["templates"]
    if args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")
    inst = instances[args.task]
    template_id, _ = parse_template_ref(inst["template"])
    template = templates[template_id]
    result = lineage_task(config, args.task, inst, template, instances)
    _print(result)
    return 0


def cmd_index(config: Config, args: argparse.Namespace) -> int:
    if args.query:
        entries = query_index(config, args.query)
        _print({"status": "ok", "query": args.query, "results": entries})
    else:
        index = build_index(config)
        _print({"status": "ok", "keys": len(index)})
    return 0


def cmd_list(config: Config) -> int:
    result = registry.list_rows(config)
    _print({"status": "ok", **result})
    return 0


def cmd_status(config: Config, args: argparse.Namespace) -> int:
    row = registry.status_row(config, args.task)
    _print({"status": "ok", **row})
    return 0


def cmd_enable(config: Config, args: argparse.Namespace) -> int:
    path = registry.set_enabled(config, args.task, True)
    _print({"status": "ok", "task": args.task, "enabled": True, "path": str(path)})
    return 0


def cmd_disable(config: Config, args: argparse.Namespace) -> int:
    path = registry.set_enabled(config, args.task, False)
    _print({"status": "ok", "task": args.task, "enabled": False, "path": str(path)})
    return 0


def cmd_promote(config: Config, args: argparse.Namespace) -> int:
    path = registry.promote(config, args.task, args.template_id, force=args.force)
    _print({"status": "ok", "task": args.task, "template": args.template_id, "path": str(path)})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in NOT_IMPLEMENTED:
        print(json.dumps({"status": "error", "reason": f"'{args.command}' not implemented yet"}))
        return 2

    try:
        config = load_config(args.project)
        if args.command == "validate":
            return cmd_validate(config)
        if args.command == "plan":
            return cmd_plan(config, args)
        if args.command == "delta":
            return cmd_delta(config, args)
        if args.command == "gather-raw":
            return cmd_gather_raw(config, args)
        if args.command == "fingerprint":
            return cmd_fingerprint(config, args)
        if args.command == "base":
            return cmd_base(config, args)
        if args.command == "prepare":
            return cmd_prepare(config, args)
        if args.command == "check-file":
            return cmd_check_file(config, args)
        if args.command == "check-task":
            return cmd_check_task(config, args)
        if args.command == "merge":
            return cmd_merge(config, args)
        if args.command == "observe":
            return cmd_observe(config, args)
        if args.command == "accept":
            return cmd_accept(config, args)
        if args.command == "publish":
            return cmd_publish(config, args)
        if args.command == "lineage":
            return cmd_lineage(config, args)
        if args.command == "index":
            return cmd_index(config, args)
        if args.command == "render":
            return cmd_render(config, args)
        if args.command == "doctor":
            return cmd_doctor(config)
        if args.command == "report":
            return cmd_report(config, args)
        if args.command == "list":
            return cmd_list(config)
        if args.command == "status":
            return cmd_status(config, args)
        if args.command == "enable":
            return cmd_enable(config, args)
        if args.command == "disable":
            return cmd_disable(config, args)
        if args.command == "promote":
            return cmd_promote(config, args)
    except ScribeError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 1

    print(json.dumps({"status": "error", "reason": f"unknown command '{args.command}'"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
