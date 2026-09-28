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
doctor, report, observe. `publish --no-render` still works without a render.
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
    parse_template_ref,
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
from scribe_lib.publish import append_run, publish_task, run_report_path  # noqa: E402
from scribe_lib.raw import gather_raw_task  # noqa: E402
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
) -> tuple[bool, list[str], str]:
    """Returns (due, reasons, cadence). `explicit` = this task was named via --task."""
    reasons: list[str] = []
    cadence = instance.get("cadence", "on-brain-update")
    if not state:
        reasons.append("first_run")
    else:
        brain_delta = compute_brain_delta(config, state)
        if brain_delta["changed"] or brain_delta["added"] or brain_delta["removed"]:
            reasons.append("brain_changed")

        raw_inputs = resolve_instance_inputs(instance, template).get("raw") or {}
        raw_delta = compute_raw_delta(config, raw_inputs, state)
        if raw_delta["new"] or raw_delta["changed"] or raw_delta["removed"]:
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
            built_at = state.get("built_at") or ""
            if built_at[:10] != config.now[:10]:
                reasons.append("cadence")

    due = bool(reasons)
    if cadence == "manual" and not explicit:
        due = False
    if not instance.get("enabled", True) and not explicit:
        due = False
    return due, reasons, cadence


def cmd_plan(config: Config, args: argparse.Namespace) -> int:
    data = validate_all(config)
    instances, templates, edges, order = (
        data["instances"],
        data["templates"],
        data["edges"],
        data["order"],
    )

    if args.task and args.task not in instances:
        raise ScribeError(f"unknown task '{args.task}'")

    entries: dict[str, dict[str, Any]] = {}
    for tid in order:
        inst = instances[tid]
        template_id, _ = parse_template_ref(inst["template"])
        template = templates[template_id]
        state = read_state(config, inst)
        upstream_versions = {
            up: (read_state(config, instances[up]).get("version") or 0) for up in edges[tid]
        }
        explicit = args.task == tid
        due, reasons, cadence = _due_reasons(
            config, inst, template, state, upstream_versions, explicit=explicit
        )
        depends_on_due = any(entries[up]["due"] for up in edges[tid])

        not_due_reason = None
        if not due:
            if not inst.get("enabled", True) and not explicit:
                not_due_reason = "disabled"
            elif cadence == "manual" and not explicit:
                not_due_reason = "cadence manual"
            else:
                not_due_reason = "up to date"

        entries[tid] = {
            "id": tid,
            "template": inst["template"],
            "due": due,
            "reasons": reasons,
            "not_due_reason": not_due_reason,
            "depends_on_due": depends_on_due,
            "cadence": cadence,
            "upstream": edges[tid],
        }

    tasks_out = [entries[tid] for tid in order]
    if args.task:
        tasks_out = [t for t in tasks_out if t["id"] == args.task]
    if args.due:
        tasks_out = [t for t in tasks_out if t["due"]]

    _print(
        {
            "status": "ok",
            "now": config.now,
            "run_report": str(run_report_path(config)),
            "order": order,
            "tasks": tasks_out,
        }
    )
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
    path = run_report_path(config)
    if args.status:
        if not args.reason:
            raise ScribeError("report --status needs --reason")
        task_id = args.task or "_run"
        if args.task:
            data = validate_all(config)
            if args.task not in data["instances"]:
                raise ScribeError(f"unknown task '{args.task}'")
        row: dict[str, Any] = {"task": task_id, "version": None, "status": args.status, "reasons": [args.reason]}
        if args.stage:
            row["stage"] = args.stage
        append_run(config, row)
    rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
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
    except ScribeError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 1

    print(json.dumps({"status": "error", "reason": f"unknown command '{args.command}'"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
