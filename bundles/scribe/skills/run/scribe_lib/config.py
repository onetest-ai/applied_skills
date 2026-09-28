"""Config, task/template loading, and brain/raw delta helpers for Scribe.

Kept deliberately small and dependency-light (stdlib tomllib + pyyaml only) so
later tasks (fingerprint, merge, render, lineage) can import it directly.

Layout assumed (see poc-design.md):
  PROJ/scribe.toml
  PROJ/tasks/*.task.md          -- task instances
  <templates_dir>/*.tmpl.md     -- library templates (REPO), + optional PROJ/templates
  PROJ/out/<instance.out>/_src/state.json  -- per-task build state (absent -> {})
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

FRONTMATTER_DELIM = "---"
PARAM_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

REQUIRED_TEMPLATE_FIELDS = {"id", "version", "goal", "params", "inputs", "output"}
REQUIRED_INSTANCE_FIELDS = {"template", "id", "title", "params", "out"}


class ScribeError(ValueError):
    """A refusal with a human-readable reason. Callers turn this into exit 1.

    Subclasses `ValueError` (controller ruling R1, task 11) so tests can
    assert refusals with `pytest.raises(ValueError, match=...)` without
    importing `ScribeError` itself; `scribe.py`'s CLI still catches
    `ScribeError` specifically and turns it into the JSON `{"status":
    "error", "reason": ...}` / exit 1 shape.
    """


# --------------------------------------------------------------------- config --

@dataclass
class Config:
    project_dir: Path
    brain_db: Path
    brain_catalog: Path
    brain_skills: Path
    brain_mcp_dir: Path
    out_root: Path
    tasks_dir: Path
    templates_dirs: list[Path]
    raw_root: Path
    work_dir: Path
    top_k: int
    now: str
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_tasks_per_run: int = 20
    budget_minutes: int = 90
    default_task_minutes: int = 10


def _resolve_path(project_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    return path.resolve()


def load_config(project_dir: str | Path) -> Config:
    """Read PROJ/scribe.toml, applying SCRIBE_NOW / SCRIBE_BRAIN_DB env overrides."""
    project_dir = Path(project_dir).expanduser().resolve()
    toml_path = project_dir / "scribe.toml"
    if not toml_path.is_file():
        raise ScribeError(f"scribe.toml not found under {project_dir}")
    with toml_path.open("rb") as fh:
        raw = tomllib.load(fh)
    proj = raw.get("project", {})
    run = raw.get("run", {})

    def required(key: str, default: str | None = None) -> str:
        value = proj.get(key, default)
        if value is None:
            raise ScribeError(f"scribe.toml missing [project].{key}")
        return value

    brain_db_value = os.environ.get("SCRIBE_BRAIN_DB") or required("brain_db")
    templates_dirs = [_resolve_path(project_dir, required("templates_dir"))]
    proj_templates = project_dir / "templates"
    if proj_templates.is_dir():
        templates_dirs.append(proj_templates)

    now = os.environ.get("SCRIBE_NOW") or date.today().isoformat()

    groups_raw = raw.get("groups") or {}
    groups = {
        name: {"enabled": bool(g.get("enabled", True)), "tasks": list(g.get("tasks") or [])}
        for name, g in groups_raw.items()
    }

    return Config(
        project_dir=project_dir,
        brain_db=_resolve_path(project_dir, brain_db_value),
        brain_catalog=_resolve_path(project_dir, required("brain_catalog")),
        brain_skills=_resolve_path(project_dir, required("brain_skills")),
        brain_mcp_dir=_resolve_path(project_dir, required("brain_mcp_dir")),
        out_root=_resolve_path(project_dir, required("out_root", "out")),
        tasks_dir=_resolve_path(project_dir, required("tasks_dir", "tasks")),
        templates_dirs=templates_dirs,
        raw_root=_resolve_path(project_dir, required("raw_root", "raw-replay")),
        work_dir=_resolve_path(project_dir, required("work_dir", "work")),
        top_k=int(run.get("top_k", 8)),
        now=now,
        groups=groups,
        max_tasks_per_run=int(run.get("max_tasks_per_run", 20)),
        budget_minutes=int(run.get("budget_minutes", 90)),
        default_task_minutes=int(run.get("default_task_minutes", 10)),
    )


# --------------------------------------------------------------- frontmatter --

def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """Split a `.tmpl.md` / `.task.md` file into (YAML frontmatter dict, body)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != FRONTMATTER_DELIM:
        raise ScribeError(f"{path}: file must start with '---' YAML frontmatter")
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == FRONTMATTER_DELIM:
            fm_text = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            data = yaml.safe_load(fm_text) or {}
            if not isinstance(data, dict):
                raise ScribeError(f"{path}: frontmatter must be a YAML mapping")
            return data, body
    raise ScribeError(f"{path}: unterminated YAML frontmatter (no closing '---')")


def load_templates(config: Config) -> dict[str, dict[str, Any]]:
    """id -> template dict, `_body` (drafting guidance) and `_path` attached.

    Later dirs override earlier ones by id, so an optional PROJ/templates can
    shadow (or add to) the library templates under templates_dir.
    """
    templates: dict[str, dict[str, Any]] = {}
    for tdir in config.templates_dirs:
        if not tdir.is_dir():
            continue
        for path in sorted(tdir.glob("*.tmpl.md")):
            fm, body = parse_frontmatter(path)
            tid = fm.get("id")
            if not tid:
                raise ScribeError(f"{path}: template missing 'id'")
            fm = dict(fm)
            fm["_body"] = body
            fm["_path"] = str(path)
            templates[tid] = fm
    return templates


def load_instances(config: Config) -> dict[str, dict[str, Any]]:
    """id -> task instance dict, `_body` and `_path` attached."""
    if not config.tasks_dir.is_dir():
        raise ScribeError(f"tasks_dir not found: {config.tasks_dir}")
    instances: dict[str, dict[str, Any]] = {}
    for path in sorted(config.tasks_dir.glob("*.task.md")):
        fm, body = parse_frontmatter(path)
        tid = fm.get("id")
        if not tid:
            raise ScribeError(f"{path}: task instance missing 'id'")
        if tid in instances:
            raise ScribeError(f"duplicate task id '{tid}' ({path})")
        fm = dict(fm)
        fm["_body"] = body
        fm["_path"] = str(path)
        instances[tid] = fm
    return instances


def group_membership(config: "Config", task_id: str) -> tuple[str | None, bool | None]:
    """`(group, group_enabled)` — `config.groups` whose `tasks` glob patterns
    (fnmatch) match `task_id`, so groups filter which tasks run (task 11).
    A task can be in no group (`(None, None)`; the caller falls back to the
    instance's own `enabled:`), or in one or more; when several match, a
    task runs only when EVERY matching group is enabled — so `group_enabled`
    is the AND of them, and `group` (the name shown in `plan`/`list`) is the
    alphabetically-first match, deterministic regardless of dict order."""
    matching = sorted(
        name
        for name, g in (config.groups or {}).items()
        if any(fnmatch.fnmatch(task_id, pattern) for pattern in (g.get("tasks") or []))
    )
    if not matching:
        return None, None
    enabled = all(config.groups[name].get("enabled", True) for name in matching)
    return matching[0], enabled


def _taxonomy_children(brain_db: Path, label: str) -> list[tuple[str, str]]:
    """`[(child_id, child_label), ...]` — rows of the Brain's `graph_nodes`
    whose `parent` is the id of the node whose `label` equals `label`,
    opened strictly read-only (`mode=ro`), ordered by id for a deterministic
    fan-out order."""
    if not brain_db.is_file():
        raise ScribeError(f"brain_db not found: {brain_db}")
    con = sqlite3.connect(f"file:{brain_db.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        con.execute("PRAGMA query_only=ON")
        row = con.execute("SELECT id FROM graph_nodes WHERE label = ?", (label,)).fetchone()
        if row is None:
            raise ScribeError(f"taxonomy node not found: '{label}'")
        parent_id = row[0]
        children = con.execute(
            "SELECT id, label FROM graph_nodes WHERE parent = ? ORDER BY id", (parent_id,)
        ).fetchall()
    finally:
        con.close()
    return [(cid, clabel) for cid, clabel in children]


def expand_instances(config: "Config", instances: dict[str, dict[str, Any]], brain_db: Path) -> dict[str, dict[str, Any]]:
    """Task templates and task instances — fan-out: an instance whose
    frontmatter has `for_each: {taxonomy_under: "<label>"}` becomes one
    instance per child of that taxonomy node (`<id>[<child-node-id>]`), each
    with `params.name = <child label>`, `params.tags = [<child label>]` and
    `out = <out>/<child label>`, and no `for_each` key of its own (so it is
    an ordinary instance from here on). An instance with no `for_each` is
    passed through unchanged. Runs AFTER `validate_all`'s required-field /
    declared-params check on the un-expanded instances — the base instance's
    own `params` (e.g. just `name`) is what gets checked against the
    template's declared params; the auto-injected `tags` param is never
    checked against that declaration."""
    result: dict[str, dict[str, Any]] = {}
    for tid, inst in instances.items():
        for_each = inst.get("for_each")
        if not for_each:
            result[tid] = inst
            continue
        label = for_each.get("taxonomy_under")
        if not label:
            raise ScribeError(f"{tid}: for_each missing 'taxonomy_under'")
        for child_id, child_label in _taxonomy_children(brain_db, label):
            new_id = f"{tid}[{child_id}]"
            new_inst = dict(inst)
            new_inst.pop("for_each", None)
            new_inst["id"] = new_id
            params = dict(inst.get("params") or {})
            params["name"] = child_label
            params["tags"] = [child_label]
            new_inst["params"] = params
            base_out = str(inst.get("out") or "")
            new_inst["out"] = f"{base_out}/{child_label}" if base_out else child_label
            result[new_id] = new_inst
    return result


def _validate_out_paths(config: "Config", instances: dict[str, dict[str, Any]]) -> None:
    """`out` that is absolute, or resolves outside `out_root`, is refused;
    so is a pair of tasks whose `out` paths are equal or one is a prefix
    (an ancestor directory) of the other — two tasks would otherwise publish
    into (or over) each other's files."""
    resolved: dict[str, Path] = {}
    for tid, inst in instances.items():
        raw_out = str(inst.get("out") or "")
        if Path(raw_out).is_absolute():
            raise ScribeError(f"{tid}: out path '{raw_out}' is absolute")
        candidate = (config.out_root / raw_out).resolve()
        try:
            candidate.relative_to(config.out_root)
        except ValueError:
            raise ScribeError(f"{tid}: out path '{raw_out}' resolves outside out_root")
        resolved[tid] = candidate

    ids = sorted(resolved)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            pa, pb = resolved[a], resolved[b]
            if pa == pb or pa in pb.parents or pb in pa.parents:
                raise ScribeError(f"out paths overlap: '{a}' ({pa}) and '{b}' ({pb})")


# --------------------------------------------------------------- substitution --

def substitute_params(value: Any, params: dict[str, Any]) -> Any:
    """Replace `{{param}}` in strings/lists/dicts.

    A string that IS exactly "{{p}}" (no other characters) becomes the param
    value verbatim (so a list param stays a list). Inside a longer string every
    `{{p}}` is replaced by str(value), joining a list param with ", ".
    """
    if isinstance(value, str):
        stripped = value.strip()
        exact = re.fullmatch(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", stripped)
        if exact and stripped == value:
            name = exact.group(1)
            if name not in params:
                raise ScribeError(f"unknown param '{{{{{name}}}}}'")
            return params[name]

        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in params:
                raise ScribeError(f"unknown param '{{{{{name}}}}}'")
            v = params[name]
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            return str(v)

        return PARAM_RE.sub(_sub, value)
    if isinstance(value, list):
        return [substitute_params(v, params) for v in value]
    if isinstance(value, dict):
        return {k: substitute_params(v, params) for k, v in value.items()}
    return value


def deep_merge(base: Any, override: Any) -> Any:
    """Dict-recursive merge; lists and scalars in `override` fully replace `base`."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value) if key in merged else value
        return merged
    return override


def parse_template_ref(ref: str) -> tuple[str, int]:
    if not isinstance(ref, str) or "@" not in ref:
        raise ScribeError(f"template ref '{ref}' must be '<id>@<version>'")
    tid, _, ver = ref.rpartition("@")
    if not tid or not ver.isdigit():
        raise ScribeError(f"template ref '{ref}' must be '<id>@<version>'")
    return tid, int(ver)


def resolve_instance_inputs(instance: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    """Template `inputs` deep-merged with the instance's `inputs` overrides, with
    `{{param}}` substitution applied using the instance's `params`."""
    merged = deep_merge(template.get("inputs") or {}, instance.get("inputs") or {})
    params = instance.get("params") or {}
    return substitute_params(merged, params)


# ------------------------------------------------------------------ validate --

def topological_order(edges: dict[str, list[str]]) -> list[str]:
    """edges[tid] = upstream task ids tid depends on. Returns an order where every
    upstream precedes its downstream; raises ScribeError on a cycle."""
    state: dict[str, int] = {}
    order: list[str] = []

    def visit(node: str, stack: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = " -> ".join(stack[stack.index(node) :] + [node])
            raise ScribeError(f"cycle in inputs.tasks: {cycle}")
        state[node] = 1
        stack.append(node)
        for upstream in edges.get(node, []):
            visit(upstream, stack)
        stack.pop()
        state[node] = 2
        order.append(node)

    for node in sorted(edges):
        visit(node, [])
    return order


def validate_all(config: Config) -> dict[str, Any]:
    """Load templates + instances; schema-check, reject unknown params/templates,
    build the upstream DAG and topologically order it. Raises ScribeError on the
    first batch of problems found (all collected, then raised together)."""
    templates = load_templates(config)
    instances = load_instances(config)

    errors: list[str] = []
    for tid, inst in instances.items():
        missing = REQUIRED_INSTANCE_FIELDS - inst.keys()
        if missing:
            errors.append(f"{tid}: instance missing fields {sorted(missing)}")
            continue
        try:
            template_id, template_version = parse_template_ref(inst["template"])
        except ScribeError as exc:
            errors.append(f"{tid}: {exc}")
            continue
        template = templates.get(template_id)
        if template is None:
            errors.append(f"{tid}: unknown template '{template_id}'")
            continue
        if template.get("version") != template_version:
            errors.append(
                f"{tid}: template version mismatch (instance wants "
                f"{template_id}@{template_version}, found @{template.get('version')})"
            )
            continue
        missing_t = REQUIRED_TEMPLATE_FIELDS - template.keys()
        if missing_t:
            errors.append(f"{template_id}: template missing fields {sorted(missing_t)}")
            continue
        declared_params = set(template.get("params") or [])
        given_params = set((inst.get("params") or {}).keys())
        unknown = given_params - declared_params
        if unknown:
            errors.append(
                f"{tid}: unknown params {sorted(unknown)} "
                f"(template declares {sorted(declared_params)})"
            )
        missing_params = declared_params - given_params
        if missing_params:
            errors.append(f"{tid}: missing required params {sorted(missing_params)}")

    if errors:
        raise ScribeError("; ".join(errors))

    instances = expand_instances(config, instances, config.brain_db)
    _validate_out_paths(config, instances)

    edges: dict[str, list[str]] = {}
    for tid, inst in instances.items():
        template_id, _ = parse_template_ref(inst["template"])
        template = templates[template_id]
        merged_inputs = resolve_instance_inputs(inst, template)
        upstream = list(merged_inputs.get("tasks") or [])
        for up in upstream:
            if up not in instances:
                raise ScribeError(f"{tid}: unknown upstream task '{up}' in inputs.tasks")
            if up == tid:
                raise ScribeError(f"{tid}: inputs.tasks cannot reference itself")
        edges[tid] = upstream

    order = topological_order(edges)

    return {"templates": templates, "instances": instances, "edges": edges, "order": order}


# ---------------------------------------------------------------------- state --

def state_path(config: Config, instance: dict[str, Any]) -> Path:
    return config.out_root / instance["out"] / "_src" / "state.json"


def read_state(config: Config, instance: dict[str, Any]) -> dict[str, Any]:
    """Per-task build state, or {} when the task has never been published."""
    path = state_path(config, instance)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScribeError(f"corrupt state.json at {path}: {exc}") from exc


# ---------------------------------------------------------------------- brain --

def read_synced_files(brain_db: Path) -> dict[str, str]:
    """doc_id -> sha from the Brain's `synced_files` table, opened strictly read-only."""
    if not brain_db.is_file():
        raise ScribeError(f"brain_db not found: {brain_db}")
    con = sqlite3.connect(f"file:{brain_db.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute("SELECT doc_id, sha FROM synced_files").fetchall()
    finally:
        con.close()
    return {doc_id: sha for doc_id, sha in rows}


def read_brain_meta(brain_db: Path) -> dict[str, str]:
    """`{"name", "taxonomy_version", ...}` from the Brain's key/value `meta` table."""
    if not brain_db.is_file():
        return {}
    con = sqlite3.connect(f"file:{brain_db.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute("SELECT key, value FROM meta").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return {k: v for k, v in rows}


def read_brain_identity(brain_db: Path) -> dict[str, Any]:
    """`{"db", "name", "taxonomy_version", "chunks"}` for the Brain the scripts
    read, or `{"db", "error"}` when it cannot be opened (read-only)."""
    if not brain_db.is_file():
        return {"db": str(brain_db), "error": "brain_db not found"}
    meta = read_brain_meta(brain_db)
    con = sqlite3.connect(f"file:{brain_db.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        con.execute("PRAGMA query_only=ON")
        chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    except sqlite3.OperationalError as exc:
        return {"db": str(brain_db), "error": f"cannot read chunks: {exc}"}
    finally:
        con.close()
    return {
        "db": str(brain_db),
        "name": meta.get("name", ""),
        "taxonomy_version": meta.get("taxonomy_version", ""),
        "chunks": chunks,
    }


def compute_brain_delta(config: Config, state: dict[str, Any]) -> dict[str, Any]:
    current = read_synced_files(config.brain_db)
    prior: dict[str, str] = state.get("brain_snapshot") or {}
    changed = sorted(doc for doc, sha in current.items() if doc in prior and prior[doc] != sha)
    added = sorted(current.keys() - prior.keys())
    removed = sorted(prior.keys() - current.keys())
    return {"changed": changed, "added": added, "removed": removed, "current": current}


# ------------------------------------------------------------------------ raw --

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _glob_path_match(rel: str, pattern: str) -> bool:
    """Segment-aware glob match where a bare '**' path segment matches zero or
    more path segments.

    `fnmatch.fnmatch("top-dir/f.txt", "**/top-dir/**")` is False, because
    fnmatch translates each '*' independently (to a plain ".*") and the
    pattern's literal '/' before "top-dir" then has nothing to match at the
    very start of the string — "**/DIR/**" only matches DIR when it is
    nested under at least one other directory. That silently breaks the
    exact exclude shape the contract documents
    (`**/internal/**`) whenever the excluded directory
    sits at the top of raw_root. This matcher instead matches path segment by
    segment, letting a '**' segment consume any number (including zero) of
    path segments, so a top-level match works the same as a nested one.
    """
    rel_parts = rel.split("/")
    pat_parts = pattern.split("/")

    def _match(ri: int, pi: int) -> bool:
        if pi == len(pat_parts):
            return ri == len(rel_parts)
        part = pat_parts[pi]
        if part == "**":
            return any(_match(k, pi + 1) for k in range(ri, len(rel_parts) + 1))
        if ri == len(rel_parts):
            return False
        return fnmatch.fnmatch(rel_parts[ri], part) and _match(ri + 1, pi + 1)

    return _match(0, 0)


def raw_root_available(config: Config) -> bool:
    """Whether the synced raw-replay folder (OneDrive/SharePoint) is mounted
    right now. `False` — offline, not "empty" — must never be read as "every
    raw file was deleted": callers use this to distinguish an unreachable
    root from a root that genuinely has nothing matching."""
    return config.raw_root.is_dir()


def select_raw_files(config: Config, raw_inputs: dict[str, Any]) -> list[Path] | None:
    """Files under raw_root matching globs (default "**/*"), minus exclude globs,
    whose path OR PARSED text contains any `match` term, case-insensitively.
    Empty `match` selects every file the globs/exclude allow.

    Returns `None` — "unknown", not "empty" — when `raw_root` is not
    currently a directory (e.g. the synced folder is offline): a caller that
    naively treated `[]` as "nothing selected" would then read a temporary
    outage as every raw file having been deleted (m2). Callers must check for
    `None` before comparing against a prior snapshot.

    Both `globs` and `exclude` are matched with `_glob_path_match`, not
    `Path.glob()`/`fnmatch.fnmatch` directly: stdlib `Path.glob()` treats a
    *trailing bare* `**` segment (e.g. `**/internal/**`,
    the exact shape a task's `inputs.raw.globs` uses to scope a glob to one
    subtree) as matching only that directory itself, not the files beneath
    it — the same class of bug `_glob_path_match` was written to fix for
    `exclude`. Walking every file once and testing it against each pattern
    keeps `globs` and `exclude` on one consistent, dependency-free matcher.

    Fix round 1 (task-2-review.md, item 1): the text half of `match` used to
    fall back to a best-effort UTF-8 decode of a file's raw bytes, which is
    close to useless for binary office formats (an alias genuinely present
    inside an `.xlsx`/`.docx`/`.pdf` rarely survives that decode as a clean
    substring). It now calls `scribe_lib.parsing.extract` — the SAME
    `parse_corpus.parse_one` call `gather-raw` uses for its final output —
    so a file's selection decision can never disagree with what it actually
    parses to. Imported lazily (not at module level) to avoid a config.py
    <-> parsing.py import cycle, since `parsing` imports `Config`/`ScribeError`
    from this module.
    """
    globs = raw_inputs.get("globs") or ["**/*"]
    excludes = raw_inputs.get("exclude") or []
    match_terms = [str(m).lower() for m in (raw_inputs.get("match") or [])]

    root = config.raw_root
    if not raw_root_available(config):
        return None

    candidates: set[Path] = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(_glob_path_match(rel, pattern) for pattern in globs):
            candidates.add(p)

    selected: list[Path] = []
    for p in sorted(candidates):
        rel = p.relative_to(root).as_posix()
        if any(_glob_path_match(rel, ex) for ex in excludes):
            continue
        if not match_terms:
            selected.append(p)
            continue
        if any(term in rel.lower() for term in match_terms):
            selected.append(p)
            continue
        from scribe_lib import parsing as _parsing  # lazy: see docstring

        text, _method = _parsing.extract(config, p)
        if text and any(term in text.lower() for term in match_terms):
            selected.append(p)
    return selected


def raw_snapshot(config: Config, raw_inputs: dict[str, Any]) -> dict[str, str] | None:
    """`None` — never `{}` — when `raw_root_available(config)` is `False`, so a
    caller writing this into `state.json` can tell "raw is offline" apart
    from "raw is online and genuinely empty" and keep the prior snapshot
    instead of overwriting it with an apparent mass deletion (m2)."""
    files = select_raw_files(config, raw_inputs)
    if files is None:
        return None
    return {p.relative_to(config.raw_root).as_posix(): sha256_file(p) for p in files}


def compute_raw_delta(config: Config, raw_inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    prior: dict[str, str] = state.get("raw_snapshot") or {}
    current = raw_snapshot(config, raw_inputs)
    if current is None:
        # Offline: never compare against the prior snapshot (every prior
        # path would look "removed"). Report it as unavailable so `plan`
        # can note it instead of flagging `raw_changed`.
        return {"new": [], "changed": [], "removed": [], "current": prior, "unavailable": True}
    new = sorted(current.keys() - prior.keys())
    changed = sorted(p for p in (current.keys() & prior.keys()) if current[p] != prior[p])
    removed = sorted(prior.keys() - current.keys())
    return {"new": new, "changed": changed, "removed": removed, "current": current, "unavailable": False}
