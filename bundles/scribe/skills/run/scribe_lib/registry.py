"""The task registry (task 11): `list`/`status`/`enable`/`disable`/`promote`
over the tasks a project's `scribe.toml` + `tasks/*.task.md` declare.

`set_enabled` and `promote` are the two mutating operations here; `list_rows`
and `status_row` are read-only views scribe.py's `list`/`status` subcommands
print as-is.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scribe_lib.config import (
    Config,
    ScribeError,
    deep_merge,
    group_membership,
    parse_template_ref,
    read_state,
    validate_all,
)
from scribe_lib import report as report_mod

_ENABLED_RE = re.compile(r"^enabled\s*:")


def _instance_path(instances: dict[str, dict[str, Any]], task_id: str) -> Path:
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    path = instances[task_id].get("_path")
    if not path:
        raise ScribeError(f"{task_id}: no source file on record")
    return Path(path)


def set_enabled(config: Config, task_id: str, value: bool) -> Path:
    """Rewrite the `enabled:` frontmatter line of `task_id`'s `.task.md` file
    in place — replacing an existing line, or inserting a new one just
    before the closing `---` when the file never declared one (the schema
    default is `true`, so most tasks never need the line until toggled)."""
    data = validate_all(config)
    path = _instance_path(data["instances"], task_id)

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise ScribeError(f"{path}: file must start with '---' YAML frontmatter")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ScribeError(f"{path}: unterminated YAML frontmatter (no closing '---')")

    new_line = f"enabled: {'true' if value else 'false'}\n"
    for i in range(1, end_idx):
        if _ENABLED_RE.match(lines[i]):
            lines[i] = new_line
            break
    else:
        lines.insert(end_idx, new_line)

    path.write_text("".join(lines), encoding="utf-8")
    return path


def _delitteralize(value: Any, params: dict[str, Any]) -> Any:
    """The reverse of `substitute_params`: a string that exactly equals one
    of `params`' (non-list) values becomes `{{param}}`, so a template
    written by `promote` from a proven task instance never bakes that
    instance's own literal param values back in."""
    if isinstance(value, str):
        for name, p_value in params.items():
            if isinstance(p_value, list):
                continue
            if value == str(p_value):
                return f"{{{{{name}}}}}"
        return value
    if isinstance(value, list):
        return [_delitteralize(v, params) for v in value]
    if isinstance(value, dict):
        return {k: _delitteralize(v, params) for k, v in value.items()}
    return value


def promote(config: Config, task_id: str, template_id: str) -> Path:
    """Task templates and task instances — promote: turn a proven task
    instance into a reusable template, `templates/<template_id>.tmpl.md`
    (the project templates dir; created if absent) version 1. The new
    template is the instance's own template's frontmatter, with `id`/
    `version` replaced, `inputs` = the template's `inputs` deep-merged with
    the instance's `inputs` overrides, and every literal value across the
    whole frontmatter that matches one of the instance's param values
    replaced by `{{param}}` — so nothing the instance filled in (e.g. a
    fan-out child's own name) leaks into the reusable template."""
    data = validate_all(config)
    instances, templates = data["instances"], data["templates"]
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    inst = instances[task_id]
    src_template_id, _ = parse_template_ref(inst["template"])
    template = templates[src_template_id]
    params = dict(inst.get("params") or {})

    merged_inputs = deep_merge(template.get("inputs") or {}, inst.get("inputs") or {})

    new_fm: dict[str, Any] = {k: v for k, v in template.items() if not k.startswith("_")}
    new_fm["id"] = template_id
    new_fm["version"] = 1
    new_fm["inputs"] = merged_inputs
    if params:
        new_fm["params"] = sorted(params.keys())
    new_fm = _delitteralize(new_fm, params)

    body = template.get("_body") or ""
    text = "---\n" + yaml.safe_dump(new_fm, sort_keys=False, allow_unicode=True) + "---\n\n" + body.lstrip("\n")

    templates_dir = config.project_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    path = templates_dir / f"{template_id}.tmpl.md"
    path.write_text(text, encoding="utf-8")
    return path


def list_rows(config: Config) -> list[dict[str, Any]]:
    """One row per task: `{task, template, group, enabled, cadence, publish,
    version, last_status}` — `scribe.py list`'s JSON output."""
    data = validate_all(config)
    instances = data["instances"]
    rows: list[dict[str, Any]] = []
    for tid in data["order"]:
        rows.append(_row(config, instances, tid))
    return rows


def status_row(config: Config, task_id: str) -> dict[str, Any]:
    """One task's registry row plus its last run-report row (or `None` when
    it has never appeared in a run report) — `scribe.py status <task>`."""
    data = validate_all(config)
    instances = data["instances"]
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    row = _row(config, instances, task_id)
    last_row = None
    for r in report_mod.rows(config):
        if r.get("task") == task_id:
            last_row = r
    row["last_run"] = last_row
    return row


def _row(config: Config, instances: dict[str, dict[str, Any]], tid: str) -> dict[str, Any]:
    inst = instances[tid]
    group, group_enabled = group_membership(config, tid)
    enabled = group_enabled if group is not None else inst.get("enabled", True)
    state = read_state(config, inst)
    last_status = None
    for r in report_mod.rows(config):
        if r.get("task") == tid:
            last_status = r.get("status")
    return {
        "task": tid,
        "template": inst.get("template"),
        "group": group,
        "enabled": enabled,
        "cadence": inst.get("cadence", "on-brain-update"),
        "publish": inst.get("publish"),
        "version": state.get("version"),
        "last_status": last_status,
    }


__all__ = ["set_enabled", "promote", "list_rows", "status_row"]
