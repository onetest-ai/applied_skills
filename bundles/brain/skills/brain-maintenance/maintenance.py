#!/usr/bin/env python3
"""Read-only planning for an existing Brain maintenance run.

This coordinator intentionally does not mutate sources, parsed artifacts, SQLite, or a
remote deployment. The coding agent consumes its plan and executes gated phases using the
component skills documented in SKILL.md.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore

VERSION = 1
EXPECTED_TOOLS = [
    "find_related_content", "get_evidence", "get_metric", "get_taxonomy",
    "health", "list_metrics", "search_knowledge",
]


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _inside(project: Path, raw: str, field: str, *, must_exist: bool = False) -> Path:
    candidate = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not candidate.is_absolute():
        candidate = project / candidate
    resolved = candidate.resolve(strict=must_exist)
    if resolved != project and project not in resolved.parents:
        raise ValueError(f"{field} must resolve inside project: {raw}")
    return resolved


def _section(raw: dict[str, Any], name: str, allowed: set[str], *, required: set[str] = set()) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a table")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown [{name}] field(s): " + ", ".join(sorted(unknown)))
    missing = required - set(value)
    if missing:
        raise ValueError(f"missing [{name}] field(s): " + ", ".join(sorted(missing)))
    return value


def load_profile(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("maintenance profile must be a TOML table")
    allowed = {"version", "project", "paths", "runtime", "update", "classification", "marts", "verification", "safety", "deployment"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError("unknown profile section(s): " + ", ".join(sorted(unknown)))
    if raw.get("version") != VERSION:
        raise ValueError("maintenance profile must contain version = 1")
    project_cfg = _section(raw, "project", {"root"}, required={"root"})
    project_raw = project_cfg["root"]
    project = Path(os.path.expandvars(os.path.expanduser(project_raw)))
    if not project.is_absolute():
        project = path.parent / project
    project = project.resolve()
    path_fields = {"brain_config", "db", "parsed", "manifest", "assets", "taxonomy", "runs"}
    required = {"brain_config", "db", "parsed", "manifest", "taxonomy", "runs"}
    paths = _section(raw, "paths", path_fields, required=required)
    if not all(isinstance(value, str) and value for value in paths.values()):
        raise ValueError("every [paths] value must be a non-empty string")
    resolved = {name: _inside(project, value, f"paths.{name}") for name, value in paths.items()}
    _section(raw, "runtime", {"python", "skills"})
    update = _section(raw, "update", {"root_key"}, required={"root_key"})
    root_key = update["root_key"]
    if not isinstance(root_key, str) or not root_key:
        raise ValueError("update.root_key is required")
    classification = _section(raw, "classification", {"batches", "max_labels"})
    for key in ("batches", "max_labels"):
        if key in classification and (not isinstance(classification[key], int) or classification[key] < 1):
            raise ValueError(f"classification.{key} must be a positive integer")
    marts = _section(raw, "marts", {"enabled", "root_key", "families", "metrics", "out"})
    if "enabled" in marts and not isinstance(marts["enabled"], bool):
        raise ValueError("marts.enabled must be boolean")
    verification = _section(raw, "verification", {"required_lanes", "smoke_query", "expected_tools"})
    for key in ("required_lanes", "expected_tools"):
        if key in verification and (not isinstance(verification[key], list) or not all(isinstance(x, str) and x for x in verification[key])):
            raise ValueError(f"verification.{key} must be a string array")
    safety_fields = {"require_strict_sources", "require_snapshot", "allow_legacy_unlinked_delete", "max_deleted_docs"}
    safety = _section(raw, "safety", safety_fields, required=safety_fields)
    if safety["require_strict_sources"] is not True:
        raise ValueError("safety.require_strict_sources must be true")
    if safety["require_snapshot"] is not True:
        raise ValueError("safety.require_snapshot must be true")
    if safety["allow_legacy_unlinked_delete"] is not False:
        raise ValueError("safety.allow_legacy_unlinked_delete must be false")
    if not isinstance(safety.get("max_deleted_docs", 0), int) or safety.get("max_deleted_docs", 0) < 0:
        raise ValueError("safety.max_deleted_docs must be a non-negative integer")
    deployment_value = raw.get("deployment", {})
    if isinstance(deployment_value, dict):
        for key in deployment_value:
            if any(token in key.lower() for token in ("password", "token", "api_key", "secret_value")):
                raise ValueError(f"deployment.{key} looks like a credential; store only environment-variable or secret-reference names")
    deployment = _section(raw, "deployment", {"enabled", "adapter", "profile", "secret_env", "require_immutable_version"})
    deployment_paths = {}
    if deployment.get("enabled", False):
        for key in ("adapter", "profile"):
            if not isinstance(deployment.get(key), str) or not deployment[key]:
                raise ValueError(f"deployment.{key} is required when deployment is enabled")
            deployment_paths[key] = _inside(project, deployment[key], f"deployment.{key}")
        if not deployment_paths["adapter"].is_file() or not os.access(deployment_paths["adapter"], os.X_OK):
            raise ValueError("deployment.adapter must be an executable file inside the project")
        if not deployment_paths["profile"].is_file():
            raise ValueError("deployment.profile must be a file inside the project")
        if deployment.get("require_immutable_version", True) is not True:
            raise ValueError("deployment.require_immutable_version must be true")
        secrets = deployment.get("secret_env", [])
        if not isinstance(secrets, list) or not all(isinstance(x, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", x) for x in secrets):
            raise ValueError("deployment.secret_env must contain environment-variable names")
    return {"raw": raw, "profile": path.resolve(), "project": project, "paths": resolved,
            "root_key": root_key, "safety": safety, "deployment": deployment,
            "deployment_paths": deployment_paths, "classification": classification,
            "marts": marts, "verification": verification}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest(path: Path, parsed: Path, sync) -> tuple[list[dict[str, Any]], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("parsed manifest must be a JSON array")
    parsed_root = parsed.resolve()
    seen_sources: set[str] = set(); seen_docs: set[str] = set(); errors: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or item.get("error"):
            continue
        if not isinstance(item.get("source"), str) or not isinstance(item.get("md"), str):
            errors.append(f"manifest[{i}] must contain string source and md")
            continue
        try:
            source, doc = sync._safe_rel(item["source"]), sync._safe_rel(item["md"])
        except ValueError as exc:
            errors.append(f"manifest[{i}] {exc}")
            continue
        if source in seen_sources: errors.append(f"duplicate manifest source: {source}")
        if doc in seen_docs: errors.append(f"duplicate manifest document: {doc}")
        seen_sources.add(source); seen_docs.add(doc)
        target = (parsed_root / doc).resolve(strict=False)
        if target != parsed_root and parsed_root not in target.parents:
            errors.append(f"manifest[{i}] output escapes parsed root: {doc}")
        elif not target.is_file():
            errors.append(f"manifest output missing: {doc}")
    return data, errors


def taxonomy_review_status(taxonomy_path: Path) -> dict[str, Any]:
    """Read-only summary of taxonomy review state beside the configured taxonomy file."""
    tax_dir = taxonomy_path.parent
    reviews = sorted((tax_dir / "reviews").glob("review_*.json"), key=lambda p: p.stat().st_mtime)
    latest = None
    if reviews:
        try:
            latest = json.loads(reviews[-1].read_text(encoding="utf-8")).get("review_id")
        except (OSError, json.JSONDecodeError):
            latest = None
    submitted, applied = [], set()
    dec = tax_dir / "decisions.jsonl"
    if dec.is_file():
        for line in dec.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("action") == "submit":
                submitted.append(rec.get("review_id"))
            elif rec.get("action") == "applied":
                applied.add(rec.get("review_id"))
    pending = 0
    rc = tax_dir / "work" / "reclassify.json"
    if rc.is_file():
        try:
            pending = len(json.loads(rc.read_text(encoding="utf-8")).get("chunk_ids", []))
        except json.JSONDecodeError:
            pending = 0
    return {"current_json": (tax_dir / "current.json").is_file(), "latest_review": latest,
            "submitted_unapplied": [r for r in submitted if r not in applied], "pending_reclassify": pending}


def build_status(profile: dict[str, Any]) -> dict[str, Any]:
    paths = profile["paths"]
    for key in ("brain_config", "db", "manifest", "taxonomy"):
        if not paths[key].is_file():
            raise FileNotFoundError(f"{key} is not a file: {paths[key]}")
    if not paths["parsed"].is_dir():
        raise FileNotFoundError(f"parsed is not a directory: {paths['parsed']}")
    skill_dir = Path(__file__).resolve().parent.parent / "knowledge-pipeline"
    registry = _load_module("brain_maintenance_source_registry", skill_dir / "source_registry.py")
    sync = _load_module("brain_maintenance_sync", skill_dir / "brain_sync.py")
    config = registry.load_config(paths["brain_config"])
    if profile["root_key"] not in config["roots"]:
        raise ValueError(f"unknown update.root_key: {profile['root_key']}")
    with closing(registry.connect_readonly(str(paths["db"]))) as con:
        source_plan = registry.build_plan(con, config, profile["root_key"])
    manifest, manifest_errors = _manifest(paths["manifest"], paths["parsed"], sync)
    parsed_delta = {
        "added": [], "changed": [], "unchanged": [], "deleted": [],
        "blocked_missing_parsed": [], "legacy_unlinked_deleted": [], "superseded_by_video": [],
    }
    unmanaged: list[str] = []
    strict_error = None
    with closing(sqlite3.connect(paths["db"].as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        if not manifest_errors:
            try:
                _, parsed_delta = sync.delta(con, str(paths["parsed"]), mutate_schema=False, manifest=str(paths["manifest"]))
                _, unmanaged = sync.source_ids(con, str(paths["parsed"]), str(paths["manifest"]), profile["root_key"], True)
            except (ValueError, RuntimeError, sqlite3.Error) as exc:
                strict_error = str(exc)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("sources", "documents", "chunks", "chunk_topics", "facts", "related") if table in tables}
        # Classification coverage: chunks carrying no topic. Batch validation only
        # proves dispatched chunks returned; it never checks whole-population
        # coverage, so surface the unclassified share for the human gate.
        unclassified_chunks = None
        if "chunks" in tables and "chunk_topics" in tables and counts.get("chunks"):
            unclassified_chunks = con.execute(
                "SELECT COUNT(*) FROM chunks c "
                "WHERE NOT EXISTS (SELECT 1 FROM chunk_topics t WHERE t.chunk_id = c.id)"
            ).fetchone()[0]
        registered_kinds = ({r["source_id"]: r["source_kind"] for r in con.execute("SELECT source_id,source_kind FROM sources")}
                            if "sources" in tables else {})
    actions = Counter(item["action"] for item in source_plan["actions"])
    blocking_actions = [item for item in source_plan["actions"] if item["action"] in {"corrupt", "remove_candidate"}]
    unavailable = [root for root in source_plan["roots"] if root["status"] == "root_unavailable"]
    # Same-hash add+missing groups are ambiguous renames that source_registry deliberately
    # leaves unresolved; maintenance must stop rather than mint a duplicate source identity.
    by_sha = {}
    for item in source_plan["actions"]:
        if item["action"] in {"add", "missing"}:
            by_sha.setdefault(item.get("sha256"), set()).add(item["action"])
    ambiguous_moves = sorted(sha for sha, kinds in by_sha.items() if sha and kinds == {"add", "missing"})
    blockers = []
    if unavailable: blockers.append("source_root_unavailable")
    if blocking_actions: blockers.append("source_actions_need_human_resolution")
    if ambiguous_moves: blockers.append("ambiguous_source_move")
    if manifest_errors: blockers.append("manifest_invalid")
    if strict_error: blockers.append("strict_source_validation_failed")
    if unmanaged: blockers.append("unmanaged_parsed_documents")
    if parsed_delta["blocked_missing_parsed"]: blockers.append("active_source_missing_parsed_output")
    if parsed_delta["legacy_unlinked_deleted"] and not profile["safety"].get("allow_legacy_unlinked_delete", False):
        blockers.append("legacy_unlinked_delete_forbidden")
    if len(parsed_delta["deleted"]) > profile["safety"].get("max_deleted_docs", 0):
        blockers.append("deletion_limit_exceeded")
    changed = [x for x in source_plan["actions"] if x["action"] in {"add", "content_change"}]
    reporting_root = profile["marts"].get("root_key")
    reporting_changed = any(item["root_key"] == reporting_root or registered_kinds.get(item.get("source_id")) == "reporting" for item in changed)
    narrative_work = [item for item in changed if not (item["root_key"] == reporting_root or registered_kinds.get(item.get("source_id")) == "reporting")]
    lane_tables = {"narrative": "chunks", "taxonomy": "chunk_topics", "numbers": "facts"}
    required_lanes = profile["verification"].get("required_lanes", [])
    empty_required_lanes = [lane for lane in required_lanes if lane not in lane_tables or not counts.get(lane_tables.get(lane, ""), 0)]
    if empty_required_lanes:
        blockers.append("required_lane_empty")
    ready_to_stage = not blockers
    next_gate = ("resolve blockers" if blockers else
                 "process narrative work in staging" if narrative_work else
                 "rebuild reporting marts" if reporting_changed else
                 "verify existing artifact; deploy only after an external adapter plan and human approval")
    return {
        "version": VERSION,
        "mode": "read_only_plan",
        "ready": ready_to_stage,
        "ready_to_stage": ready_to_stage,
        "ready_to_apply": False,
        "ready_to_deploy": False,
        "blockers": blockers,
        "profile_sha256": sha_file(profile["profile"]),
        "brain_config_sha256": sha_file(paths["brain_config"]),
        "database_sha256": sha_file(paths["db"]),
        "manifest_sha256": sha_file(paths["manifest"]),
        "taxonomy_sha256": sha_file(paths["taxonomy"]),
        "taxonomy_review": taxonomy_review_status(paths["taxonomy"]),
        "source_plan": source_plan,
        "source_action_counts": dict(sorted(actions.items())),
        "narrative_work": narrative_work,
        "metadata_moves": [item for item in source_plan["actions"] if item["action"] == "move"],
        "ambiguous_move_hashes": ambiguous_moves,
        "reporting_rebuild_required": reporting_changed,
        "manifest": {"entries": len(manifest), "errors": manifest_errors},
        "parsed_delta": parsed_delta,
        "strict_source_error": strict_error,
        "unmanaged_documents": unmanaged,
        "store_counts": counts,
        "classification_coverage": {
            "chunks": counts.get("chunks"),
            "unclassified_chunks": unclassified_chunks,
            "unclassified_pct": (round(100.0 * unclassified_chunks / counts["chunks"], 1)
                                 if unclassified_chunks is not None and counts.get("chunks") else None),
        },
        "empty_required_lanes": empty_required_lanes,
        "classification": {"required_after_apply": bool(narrative_work or parsed_delta["added"] or parsed_delta["changed"]), "chunk_ids": "from sync_plan.json after apply"},
        "deployment": {"enabled": bool(profile["deployment"].get("enabled", False)), "preview_only": True,
                       "profile": profile["deployment"].get("profile"),
                       "adapter": profile["deployment"].get("adapter"),
                       "readiness": "external_adapter_and_human_gate_required"},
        "expected_tools": profile["verification"].get("expected_tools", EXPECTED_TOOLS),
        "next_gate": next_gate,
    }


def atomic_json(path: Path, value: Any, allowed_root: Path) -> None:
    allowed_root = allowed_root.resolve()
    path = path.resolve(strict=False)
    if path != allowed_root and allowed_root not in path.parents:
        raise ValueError(f"output must stay inside configured runs directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only Brain maintenance planner")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("validate-profile", "status", "plan"):
        p = sub.add_parser(name)
        p.add_argument("--profile", required=True)
        p.add_argument("--out")
    args = ap.parse_args(argv)
    try:
        profile = load_profile(Path(args.profile).expanduser().resolve())
        if args.command == "validate-profile":
            result = {"status": "ok", "version": VERSION, "project": str(profile["project"]), "root_key": profile["root_key"]}
        else:
            result = build_status(profile)
        if args.out:
            atomic_json(Path(args.out), result, profile["paths"]["runs"])
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ready", True) else 2
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, RuntimeError, json.JSONDecodeError) as exc:
        print(f"brain maintenance error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
