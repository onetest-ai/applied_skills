"""The task registry (task 11): `list`/`status`/`enable`/`disable`/`promote`
over the tasks a project's `scribe.toml` + `tasks/*.task.md` declare.

`set_enabled` and `promote` are the two mutating operations here; `list_rows`
and `status_row` are read-only views scribe.py's `list`/`status` subcommands
print as-is.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from scribe_lib.config import (
    Config,
    ScribeError,
    deep_merge,
    group_membership,
    parse_frontmatter,
    parse_template_ref,
    read_state,
    validate_all,
)
from scribe_lib import report as report_mod


def _instance_path(instances: dict[str, dict[str, Any]], task_id: str) -> Path:
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    path = instances[task_id].get("_path")
    if not path:
        raise ScribeError(f"{task_id}: no source file on record")
    return Path(path)


def _rewrite_frontmatter_line(path: Path, key: str, new_line: str) -> None:
    """Replace the line beginning `key:` inside `path`'s YAML frontmatter (or
    insert one just before the closing `---` when the key is absent). Used
    for both `enabled:` (a scalar) and `approved_children:` (a flow list) —
    the only two frontmatter keys the registry ever rewrites in place, and
    both are anchored at column 0 so a nested key of the same name is never
    touched."""
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

    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    for i in range(1, end_idx):
        if pattern.match(lines[i]):
            lines[i] = new_line
            break
    else:
        lines.insert(end_idx, new_line)

    path.write_text("".join(lines), encoding="utf-8")


def _add_approved_child(path: Path, node_id: str) -> None:
    fm, _ = parse_frontmatter(path)
    approved = sorted(set(fm.get("approved_children") or []) | {node_id})
    _rewrite_frontmatter_line(path, "approved_children", f"approved_children: {json.dumps(approved)}\n")


def set_enabled(config: Config, task_id: str, value: bool) -> Path:
    """Rewrite the `enabled:` frontmatter line of `task_id`'s `.task.md` file
    in place — replacing an existing line, or inserting a new one just
    before the closing `---` when the file never declared one (the schema
    default is `true`, so most tasks never need the line until toggled).

    Fix round 1, ruling on issue 2: a fan-out CHILD (`instances[task_id]`
    carries `_fanout_parent`/`_fanout_node` — see `config.expand_instances`)
    has no `.task.md` file of its own to rewrite. `enable`-ing one instead
    records the approval into its PARENT instance's `approved_children:`
    frontmatter list, which is what makes `expand_instances` stop forcing
    that child `enabled: false` on the next load. `disable`-ing a fan-out
    child is refused — there is nothing to disable directly; the parent's
    `for_each`/its group is what controls it."""
    data = validate_all(config)
    instances = data["instances"]
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    inst = instances[task_id]
    parent_tid = inst.get("_fanout_parent")
    if parent_tid is not None:
        if not value:
            raise ScribeError(
                f"{task_id}: a fan-out child cannot be disabled directly — "
                f"its parent's for_each/group controls it; 'enable' records approval instead"
            )
        node_id = inst["_fanout_node"]
        # The parent instance ("fan") no longer exists as its own key in
        # `instances` once expanded — only its children do. Each child's
        # dict is a copy of the parent's (`config.expand_instances`), so its
        # own `_path` still names the parent's `.task.md` file; that is the
        # one file to rewrite.
        parent_path = Path(inst["_path"])
        _add_approved_child(parent_path, node_id)
        return parent_path

    path = _instance_path(instances, task_id)
    _rewrite_frontmatter_line(path, "enabled", f"enabled: {'true' if value else 'false'}\n")
    return path


# Frontmatter keys a promoted template's string VALUES may be de-literalised
# in (fix round 1, ruling on issue 6): never an id, a key, `kind`, `lanes` or
# `acceptance` — those are structure/identifiers, not prose, and a param
# value that happens to equal one (e.g. a section id) must never turn into a
# live `{{param}}` there.
_SECTION_TEXT_KEYS = ("title", "intent")
_SECTION_LIST_TEXT_KEYS = ("queries", "must")


def _delitteralize_str(value: Any, params: dict[str, Any]) -> Any:
    """Substring (not just exact-match) replacement of every `params` value
    found in `value` with `{{param}}`, longest value first so a value that
    is itself a substring of a longer one never shadows it (fix round 1:
    the brief's exact-match-only version missed `"Overview of Widget"` when
    `params["name"] == "Widget"`)."""
    if not isinstance(value, str):
        return value
    items = sorted(
        ((name, str(v)) for name, v in params.items() if not isinstance(v, list) and str(v)),
        key=lambda kv: -len(kv[1]),
    )
    for name, sval in items:
        if sval in value:
            value = value.replace(sval, f"{{{{{name}}}}}")
    return value


def _delitteralize_tree(value: Any, params: dict[str, Any]) -> Any:
    """`_delitteralize_str` applied to every string VALUE (never a dict key)
    in a nested dict/list — used for `goal` and `inputs`, which are free
    prose/config, never identifiers."""
    if isinstance(value, str):
        return _delitteralize_str(value, params)
    if isinstance(value, list):
        return [_delitteralize_tree(v, params) for v in value]
    if isinstance(value, dict):
        return {k: _delitteralize_tree(v, params) for k, v in value.items()}
    return value


def _delitteralize_output(output: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Only `output.sections[].{title,intent,queries,must}` are prose; `id`,
    `kind` and `lanes` are identifiers/enums and are copied verbatim (fix
    round 1, ruling on issue 6)."""
    output = dict(output or {})
    sections = output.get("sections")
    if isinstance(sections, list):
        new_sections = []
        for sec in sections:
            new_sec = dict(sec)
            for key in _SECTION_TEXT_KEYS:
                if key in new_sec:
                    new_sec[key] = _delitteralize_str(new_sec[key], params)
            for key in _SECTION_LIST_TEXT_KEYS:
                if key in new_sec and isinstance(new_sec[key], list):
                    new_sec[key] = [_delitteralize_str(v, params) for v in new_sec[key]]
            new_sections.append(new_sec)
        output["sections"] = new_sections
    return output


def _template_exists(config: Config, templates: dict[str, Any], template_id: str) -> bool:
    if template_id in templates:
        return True
    for tdir in config.templates_dirs:
        if (tdir / f"{template_id}.tmpl.md").is_file():
            return True
    return (config.project_dir / "templates" / f"{template_id}.tmpl.md").is_file()


def promote(config: Config, task_id: str, template_id: str, *, force: bool = False) -> Path:
    """Task templates and task instances — promote: turn a proven task
    instance into a reusable template, `templates/<template_id>.tmpl.md`
    (the project templates dir; created if absent) version 1. The new
    template is the instance's own template's frontmatter, with `id`/
    `version` replaced, `inputs` = the template's `inputs` deep-merged with
    the instance's `inputs` overrides, and `{{param}}` substituted for every
    substring match of one of the instance's param values — but ONLY inside
    `goal`, `inputs`, and each section's `title`/`intent`/`queries`/`must`
    (fix round 1, ruling on issue 6). Every id (`id`, `output.sections[].id`),
    `kind`, `lanes` and `acceptance` is copied byte-for-byte — a param value
    that happens to equal a section id (e.g. `params.name == "overview"`)
    must never turn that id into a parameter.

    Refuses (`ScribeError`, a `ValueError`) when `template_id` already names
    a loaded template or an existing `.tmpl.md` file, unless `force=True` —
    `promote` used to silently overwrite an existing library template,
    resetting it to v1 under every instance still pinned to its real
    version."""
    data = validate_all(config)
    instances, templates = data["instances"], data["templates"]
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    if not force and _template_exists(config, templates, template_id):
        raise ScribeError(f"template '{template_id}' already exists; pass force=True to overwrite")

    inst = instances[task_id]
    src_template_id, _ = parse_template_ref(inst["template"])
    template = templates[src_template_id]
    params = dict(inst.get("params") or {})

    merged_inputs = deep_merge(template.get("inputs") or {}, inst.get("inputs") or {})

    new_fm: dict[str, Any] = {k: v for k, v in template.items() if not k.startswith("_")}
    new_fm["id"] = template_id
    new_fm["version"] = 1
    new_fm["goal"] = _delitteralize_tree(template.get("goal"), params)
    new_fm["inputs"] = _delitteralize_tree(merged_inputs, params)
    if "output" in new_fm:
        new_fm["output"] = _delitteralize_output(new_fm["output"], params)
    if params:
        new_fm["params"] = sorted(params.keys())

    body = template.get("_body") or ""
    text = "---\n" + yaml.safe_dump(new_fm, sort_keys=False, allow_unicode=True) + "---\n\n" + body.lstrip("\n")

    templates_dir = config.project_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    path = templates_dir / f"{template_id}.tmpl.md"
    path.write_text(text, encoding="utf-8")
    return path


def list_rows(config: Config) -> dict[str, Any]:
    """`{"tasks": [...], "notes": [...], "new_fanout_children": [...]}` —
    `scribe.py list`'s JSON output. `notes`/`new_fanout_children` surface the
    same fan-out visibility `plan` carries (fix round 1, ruling on issue 2)
    so a project with no scheduled run still shows a newly-appeared
    taxonomy child, or a `for_each` whose `taxonomy_under` label no longer
    resolves, from `list` alone."""
    data = validate_all(config)
    instances = data["instances"]
    today_rows = report_mod.rows(config)
    rows: list[dict[str, Any]] = [_row(config, instances, tid, today_rows) for tid in data["order"]]
    notes = sorted(f"fanout_parent_missing: {label}" for label in (data.get("fanout_parent_missing") or []))
    return {"tasks": rows, "notes": notes, "new_fanout_children": data.get("new_fanout_children") or []}


def status_row(config: Config, task_id: str) -> dict[str, Any]:
    """One task's registry row plus its last run-report row (or `None` when
    it has never appeared in a run report) — `scribe.py status <task>`."""
    data = validate_all(config)
    instances = data["instances"]
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    today_rows = report_mod.rows(config)
    row = _row(config, instances, task_id, today_rows)
    last_row = None
    for r in today_rows:
        if r.get("task") == task_id:
            last_row = r
    row["last_run"] = last_row
    return row


def _row(config: Config, instances: dict[str, dict[str, Any]], tid: str, today_rows: list[dict[str, Any]]) -> dict[str, Any]:
    inst = instances[tid]
    group, group_enabled = group_membership(config, tid)
    # Fix round 1, issue 1 — same AND as scribe.py compute_plan: a task's
    # own `enabled:` must hold even when every matching group is enabled.
    enabled = inst.get("enabled", True) and (True if group is None else group_enabled)
    state = read_state(config, inst)
    last_status = None
    for r in today_rows:
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
