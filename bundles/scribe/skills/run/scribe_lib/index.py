"""index: `out/_lineage/index.json`, the reverse index over every task's
latest lineage — `doc_id` (RAG citations) or raw-relative path (FILE
citations) -> `[{task, version, section, claim}]`.

Built fresh from every task's `_src/v<state.version>.lineage.json` each call
(a task with no published version, or no lineage written for it yet, is
simply absent — never an error). `[TASK:]` citations are followed
transitively through `derived_from_task`: if task T3's claim C cites
`[TASK:T1#c:c1]` and T1's own claim `c1` cites doc X, X's index entry also
gets an entry for T3's claim C, not just T1's.
"""
from __future__ import annotations

import json
from typing import Any

from scribe_lib.config import Config, read_state, validate_all


def _load_lineages(config: Config) -> dict[str, dict[str, Any]]:
    data = validate_all(config)
    lineages: dict[str, dict[str, Any]] = {}
    for tid, inst in data["instances"].items():
        state = read_state(config, inst)
        version = state.get("version")
        if not version:
            continue
        path = config.out_root / inst["out"] / "_src" / f"v{version:03d}.lineage.json"
        if not path.is_file():
            continue
        lineages[tid] = json.loads(path.read_text(encoding="utf-8"))
    return lineages


def _direct_targets(lineage: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """claim_id -> {doc_ids, paths, task_refs} for the CURRENT version's claims
    only, plus claim_id -> section, both scoped to this one task's lineage."""
    version = lineage["header"]["version"]
    nodes_by_id = {n["id"]: n for n in lineage["nodes"]}
    claim_node_to_cid = {
        n["id"]: n["claim_id"] for n in lineage["nodes"] if n["type"] == "claim" and n.get("version") == version
    }
    claim_section = {
        n["claim_id"]: n["section"] for n in lineage["nodes"] if n["type"] == "claim" and n.get("version") == version
    }
    targets: dict[str, dict[str, Any]] = {
        cid: {"doc_ids": set(), "paths": set(), "task_refs": set()} for cid in claim_node_to_cid.values()
    }
    for e in lineage["edges"]:
        if e["type"] != "cites":
            continue
        cid = claim_node_to_cid.get(e["from"])
        if cid is None:
            continue
        to_node = nodes_by_id.get(e["to"])
        if to_node is None:
            continue
        if to_node["type"] == "chunk" and to_node.get("doc_id"):
            targets[cid]["doc_ids"].add(to_node["doc_id"])
        elif to_node["type"] == "raw_span" and to_node.get("path"):
            targets[cid]["paths"].add(to_node["path"])
        elif to_node["type"] == "task_claim":
            targets[cid]["task_refs"].add((to_node["task"], to_node["claim_id"]))
    return targets, claim_section


def _resolve(
    task_id: str,
    claim_id: str,
    per_task_targets: dict[str, dict[str, dict[str, Any]]],
    memo: dict[tuple[str, str], dict[str, set]],
    visiting: set[tuple[str, str]],
) -> dict[str, set]:
    key = (task_id, claim_id)
    if key in memo:
        return memo[key]
    if key in visiting:  # DAG guard; shouldn't happen given inputs.tasks is acyclic
        return {"doc_ids": set(), "paths": set()}
    targets = per_task_targets.get(task_id, {}).get(claim_id)
    if targets is None:
        memo[key] = {"doc_ids": set(), "paths": set()}
        return memo[key]
    visiting.add(key)
    doc_ids = set(targets["doc_ids"])
    paths = set(targets["paths"])
    for up_task, up_claim in targets["task_refs"]:
        sub = _resolve(up_task, up_claim, per_task_targets, memo, visiting)
        doc_ids |= sub["doc_ids"]
        paths |= sub["paths"]
    visiting.discard(key)
    result = {"doc_ids": doc_ids, "paths": paths}
    memo[key] = result
    return result


def build_index(config: Config) -> dict[str, list[dict[str, Any]]]:
    lineages = _load_lineages(config)
    per_task_targets: dict[str, dict[str, dict[str, Any]]] = {}
    claim_meta: dict[tuple[str, str], dict[str, Any]] = {}
    for tid, lineage in lineages.items():
        targets, claim_section = _direct_targets(lineage)
        per_task_targets[tid] = targets
        version = lineage["header"]["version"]
        for cid, sid in claim_section.items():
            claim_meta[(tid, cid)] = {"task": tid, "version": version, "section": sid, "claim": cid}

    index: dict[str, list[dict[str, Any]]] = {}
    memo: dict[tuple[str, str], dict[str, set]] = {}
    for tid, targets in per_task_targets.items():
        for cid in targets:
            resolved = _resolve(tid, cid, per_task_targets, memo, set())
            meta = claim_meta[(tid, cid)]
            for doc_id in resolved["doc_ids"]:
                index.setdefault(doc_id, []).append(meta)
            for path in resolved["paths"]:
                index.setdefault(path, []).append(meta)

    for key, entries in index.items():
        seen: set[tuple[Any, ...]] = set()
        deduped = []
        for m in sorted(entries, key=lambda m: (m["task"], m["version"], m["section"], m["claim"])):
            k = (m["task"], m["version"], m["section"], m["claim"])
            if k in seen:
                continue
            seen.add(k)
            deduped.append(m)
        index[key] = deduped

    index_dir = config.out_root / "_lineage"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    return index


def query_index(config: Config, key: str) -> list[dict[str, Any]]:
    return build_index(config).get(key, [])
