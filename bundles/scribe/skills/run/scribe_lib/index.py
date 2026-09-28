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


ClaimRef = tuple[str, str]  # (task, claim_id)


def _direct_targets(
    task_id: str, lineage: dict[str, Any]
) -> tuple[dict[ClaimRef, dict[str, Any]], dict[ClaimRef, str]]:
    """(task, claim_id) -> {doc_ids, paths, task_refs} for the CURRENT version's
    claims only, plus (task, claim_id) -> section. A claim id is unique within a
    section: `base` (docx recovery) and `merge` both mint with
    `claims.mint_claim_id`, which re-hashes on a clash, and `merge` ignores
    ids not in the base. Across sections the section id is part of the hash,
    so two sections share an id only on a 32-bit prefix collision; the key
    names exactly one claim short of that."""
    version = lineage["header"]["version"]
    nodes_by_id = {n["id"]: n for n in lineage["nodes"]}
    current = [n for n in lineage["nodes"] if n["type"] == "claim" and n.get("version") == version]
    claim_node_to_ref = {n["id"]: (task_id, n["claim_id"]) for n in current}
    claim_section = {(task_id, n["claim_id"]): n["section"] for n in current}
    targets: dict[ClaimRef, dict[str, Any]] = {
        ref: {"doc_ids": set(), "paths": set(), "task_refs": set()} for ref in claim_node_to_ref.values()
    }
    for e in lineage["edges"]:
        if e["type"] != "cites":
            continue
        ref = claim_node_to_ref.get(e["from"])
        if ref is None:
            continue
        to_node = nodes_by_id.get(e["to"])
        if to_node is None:
            continue
        if to_node["type"] == "chunk" and to_node.get("doc_id"):
            targets[ref]["doc_ids"].add(to_node["doc_id"])
        elif to_node["type"] == "raw_span" and to_node.get("path"):
            targets[ref]["paths"].add(to_node["path"])
        elif to_node["type"] == "task_claim":
            targets[ref]["task_refs"].add((to_node["task"], to_node["claim_id"]))
    return targets, claim_section


def _resolve(
    ref: ClaimRef,
    targets_by_ref: dict[ClaimRef, dict[str, Any]],
    memo: dict[ClaimRef, dict[str, set]],
    visiting: set[ClaimRef],
) -> dict[str, set]:
    if ref in memo:
        return memo[ref]
    if ref in visiting:  # DAG guard; shouldn't happen given inputs.tasks is acyclic
        return {"doc_ids": set(), "paths": set()}
    targets = targets_by_ref.get(ref)
    if targets is None:
        memo[ref] = {"doc_ids": set(), "paths": set()}
        return memo[ref]
    visiting.add(ref)
    doc_ids = set(targets["doc_ids"])
    paths = set(targets["paths"])
    for up_ref in targets["task_refs"]:
        sub = _resolve(up_ref, targets_by_ref, memo, visiting)
        doc_ids |= sub["doc_ids"]
        paths |= sub["paths"]
    visiting.discard(ref)
    result = {"doc_ids": doc_ids, "paths": paths}
    memo[ref] = result
    return result


def build_index(config: Config) -> dict[str, list[dict[str, Any]]]:
    lineages = _load_lineages(config)
    targets_by_ref: dict[ClaimRef, dict[str, Any]] = {}
    claim_meta: dict[ClaimRef, dict[str, Any]] = {}
    for tid, lineage in lineages.items():
        targets, claim_section = _direct_targets(tid, lineage)
        targets_by_ref.update(targets)
        version = lineage["header"]["version"]
        for (task, cid), sid in claim_section.items():
            claim_meta[(task, cid)] = {"task": task, "version": version, "section": sid, "claim": cid}

    index: dict[str, list[dict[str, Any]]] = {}
    memo: dict[ClaimRef, dict[str, set]] = {}
    for ref in targets_by_ref:
        resolved = _resolve(ref, targets_by_ref, memo, set())
        meta = claim_meta[ref]
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
