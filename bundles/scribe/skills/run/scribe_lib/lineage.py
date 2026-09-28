"""lineage: `_src/vNNN.lineage.json` + `_src/vNNN.sources.json` for the
currently-published version of a task (i.e. run this after `publish`).

Node types: `doc`, `section`, `claim`, `chunk`, `metric`, `graph_node`,
`raw_span`, `task_claim`, `source`. A `task_claim` node carries
`upstream_version: int | None` — the upstream task's CURRENT published
version at lineage time (read via `instances[up_task]`'s state, not the
version that was current when the citing claim was drafted) — so the reverse
index and any impact query can see, without a second lookup, whether the
citing task is still current with what it cites.
Edge types: `has_section`, `has_claim`, `cites {role}`, `from`,
`carried_from` (claim -> the same claim id in the previous version, only if
it existed there), `derived_from_task` (a `[TASK:]`-citing claim -> the
upstream task's claim node it names).

**Human-edited claims (controller ruling, fix round 1).** A claim node
carries `origin: "human_modified"` when its claim id is one `base.json`'s
`human_modified` recorded (it already has a real claim id — carried forward
by `base_task`'s docx-recovery matching — so it gets an ordinary claim node
like any other, just tagged). A `human_added` claim has no claim id at all
(there was never a base claim to carry one from) and so is normally invisible
to the per-claim loop below, which only walks claims that HAVE an id; it gets
a synthetic node instead — id `claim:<task>:vNNN:<section>:human:<hash>`,
`hash = assign_claim_id(task, section, normalized)` (section + normalized
text, deterministic) — with `origin: "human"`, found by matching the current
section's uncited claims' normalized text (truncated to 200 chars, matching
`base.json`'s own truncation) against `human_added`'s recorded text. This can
only find a human addition that survived byte-for-byte into the version being
lineaged (via a non-stale, byte-for-byte carry); one absorbed/reworded by a
later redraft is not — and cannot be — traced back to "human" by this method.
H5 ("human edits survive") is judged from lineage, so these must not be
silently skipped even though they carry no ordinary claim id.

**Origin from the document.** base/merge persist a
human-authored claim's origin in its comment (`<!-- c:xxxx origin=human -->`),
so a claim node's `origin` is read from there first and holds in every later
version, not only the one whose base.json recorded the edit. base.json's
`human_modified` is kept as a fallback. An id-less claim (a legacy human
addition) gets the synthetic `human` node when this run's base.json lists it
OR the previous published version holds the same id-less claim in the same
section — so one carried byte-for-byte into later versions stays visible after
base.json stops listing it.

`retrieval` is copied straight from `work/<task>/fingerprint.json` (whatever
this run last computed — the per-section `queries`/`considered`/
`fingerprint`).

`sources.json` is the kb-shaped projection (`bundles/kb/skills/_shared/
authoring.md`'s sidecar contract): one row per unique tag cited in this
version, `{tag, kind, ...type-specific fields..., source_file}`.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from scribe_lib import brain as brain_mod
from scribe_lib import claims
from scribe_lib.basedoc import split_by_section_id
from scribe_lib.config import Config, ScribeError, read_brain_meta, sha256_file
from scribe_lib.merge import parse_header


def _find_section_for_claim(sections_map: dict[str, str], claim_id: str) -> str | None:
    for sid, body in sections_map.items():
        for b in claims.parse_blocks(body):
            if claims.is_claim(b) and b["claim_id"] == claim_id:
                return sid
    return None


class _NodeSet:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.nodes: list[dict[str, Any]] = []

    def add(self, node: dict[str, Any]) -> None:
        if node["id"] not in self._seen:
            self._seen.add(node["id"])
            self.nodes.append(node)


def lineage_task(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
    instances: dict[str, Any] | None = None,
    *,
    fingerprint_path: Path | None = None,
    base_json_path: Path | None = None,
) -> dict[str, Any]:
    """`fingerprint_path`/`base_json_path` default to `work/<task>/`; `review.approve`
    passes the copies a proposal froze at propose time (final-review I5), because
    `work/<task>/` may belong to a later prepare by the time a proposal is approved."""
    from scribe_lib.config import read_state

    instances_by_id = instances or {}

    state = read_state(config, instance)
    version = state.get("version")
    if not version:
        raise ScribeError(f"'{task_id}' has never published a version — nothing to build lineage for")

    out_dir = config.out_root / instance["out"]
    src_dir = out_dir / "_src"
    md_path = src_dir / f"v{version:03d}.md"
    if not md_path.is_file():
        raise ScribeError(f"published version file missing: {md_path}")
    text = md_path.read_text(encoding="utf-8")
    header = parse_header(text)
    sections = split_by_section_id(text)

    work_dir = config.work_dir / task_id
    fp_path = fingerprint_path or work_dir / "fingerprint.json"
    retrieval = json.loads(fp_path.read_text(encoding="utf-8")).get("sections", {}) if fp_path.is_file() else {}

    base_json_path = base_json_path or work_dir / "base.json"
    base_json = json.loads(base_json_path.read_text(encoding="utf-8")) if base_json_path.is_file() else {}
    human_modified_ids = {h["claim_id"] for h in (base_json.get("human_modified") or []) if h.get("claim_id")}

    human_added_norms_by_section: dict[str, set[str]] = {}
    for h in base_json.get("human_added") or []:
        h_sid = h.get("section")
        if not h_sid:
            continue
        human_added_norms_by_section.setdefault(h_sid, set()).add(claims.normalize_text(h.get("text", ""))[:200])

    meta = read_brain_meta(config.brain_db)

    prev_version = version - 1
    prev_sections: dict[str, str] = {}
    prev_claim_ids: set[str] = set()
    prev_idless_by_section: dict[str, set[str]] = {}
    if prev_version > 0:
        prev_path = src_dir / f"v{prev_version:03d}.md"
        if prev_path.is_file():
            prev_sections = split_by_section_id(prev_path.read_text(encoding="utf-8"))
            for p_sid, body in prev_sections.items():
                for b in claims.parse_blocks(body):
                    if claims.is_claim(b) and b["claim_id"]:
                        prev_claim_ids.add(b["claim_id"])
                    elif claims.is_claim(b) and p_sid != "changes":
                        prev_idless_by_section.setdefault(p_sid, set()).add(b["normalized"][:200])

    raw_db_path = work_dir / "raw" / "raw.sqlite"
    raw_con = sqlite3.connect(f"file:{raw_db_path.as_posix()}?mode=ro", uri=True) if raw_db_path.is_file() else None

    def raw_span_text(path: str, locator: str) -> str | None:
        if raw_con is None:
            return None
        row = raw_con.execute(
            "SELECT text FROM raw_fts WHERE path=? AND locator=?", (path, locator)
        ).fetchone()
        return row[0] if row else None

    nodeset = _NodeSet()
    edges: list[dict[str, Any]] = []

    doc_node_id = f"doc:{task_id}"
    nodeset.add({"id": doc_node_id, "type": "doc", "task": task_id, "title": instance["title"], "version": version})

    try:
        for sid, body in sections.items():
            sec_node_id = f"section:{task_id}:v{version:03d}:{sid}"
            nodeset.add({"id": sec_node_id, "type": "section", "task": task_id, "version": version, "section": sid})
            edges.append({"type": "has_section", "from": doc_node_id, "to": sec_node_id})

            for b in claims.parse_blocks(body):
                if not claims.is_claim(b):
                    continue
                if not b["claim_id"]:
                    # No claim id at all -- the only case is a pre-Task-8
                    # human addition (see module docstring); give it a
                    # synthetic node so it is not silently invisible to
                    # lineage/index (H5 is judged from lineage).
                    key = b["normalized"][:200]
                    if key in human_added_norms_by_section.get(sid, set()) or key in prev_idless_by_section.get(sid, set()):
                        human_hash = claims.assign_claim_id(task_id, sid, b["normalized"])
                        human_node_id = f"claim:{task_id}:v{version:03d}:{sid}:human:{human_hash}"
                        nodeset.add(
                            {
                                "id": human_node_id,
                                "type": "claim",
                                "task": task_id,
                                "version": version,
                                "section": sid,
                                "claim_id": None,
                                "superseded": False,
                                "origin": "human",
                            }
                        )
                        edges.append({"type": "has_claim", "from": sec_node_id, "to": human_node_id})
                    continue
                cid = b["claim_id"]
                claim_node_id = f"claim:{task_id}:v{version:03d}:{sid}:{cid}"
                nodeset.add(
                    {
                        "id": claim_node_id,
                        "type": "claim",
                        "task": task_id,
                        "version": version,
                        "section": sid,
                        "claim_id": cid,
                        "superseded": b["superseded"],
                        "origin": b["origin"] or ("human_modified" if cid in human_modified_ids else None),
                    }
                )
                edges.append({"type": "has_claim", "from": sec_node_id, "to": claim_node_id})

                for tag in b["tags"]:
                    kind, value = claims.parse_tag(tag)
                    if kind == "RAG":
                        ev = brain_mod.evidence(config, value)
                        text_hash = brain_mod.text_hash(ev.get("text", "")) if ev.get("status") == "ok" else None
                        chunk_node_id = f"chunk:{value}"
                        nodeset.add(
                            {
                                "id": chunk_node_id,
                                "type": "chunk",
                                "chunk_id": str(value),
                                "doc_id": ev.get("source"),
                                "section": ev.get("section"),
                                "ord": ev.get("ord"),
                                "text_hash": text_hash,
                            }
                        )
                        edges.append({"type": "cites", "role": "supports", "from": claim_node_id, "to": chunk_node_id})
                        if ev.get("source"):
                            source_node_id = f"source:{ev['source']}"
                            nodeset.add({"id": source_node_id, "type": "source", "doc_id": ev["source"]})
                            edges.append({"type": "from", "from": chunk_node_id, "to": source_node_id})
                    elif kind == "MART":
                        metric, _, grain = value.partition("@")
                        metric_node_id = f"metric:{value}"
                        nodeset.add({"id": metric_node_id, "type": "metric", "metric": metric, "grain": grain or None})
                        edges.append(
                            {"type": "cites", "role": "quantifies", "from": claim_node_id, "to": metric_node_id}
                        )
                    elif kind == "GRAPH":
                        node_id = f"graph_node:{value}"
                        nodeset.add({"id": node_id, "type": "graph_node", "label": value})
                        edges.append({"type": "cites", "role": "supports", "from": claim_node_id, "to": node_id})
                    elif kind == "FILE":
                        path, _, locator = value.partition("#")
                        full = config.raw_root / path
                        sha = sha256_file(full) if full.is_file() else None
                        span_text = raw_span_text(path, locator)
                        text_hash = brain_mod.text_hash(span_text) if span_text is not None else None
                        span_id = f"raw_span:{value}"
                        nodeset.add(
                            {
                                "id": span_id,
                                "type": "raw_span",
                                "path": path,
                                "locator": locator or None,
                                "text_hash": text_hash,
                                "sha256": sha,
                            }
                        )
                        edges.append({"type": "cites", "role": "supports", "from": claim_node_id, "to": span_id})
                        source_node_id = f"source:{path}"
                        nodeset.add({"id": source_node_id, "type": "source", "path": path})
                        edges.append({"type": "from", "from": span_id, "to": source_node_id})
                    elif kind == "TASK":
                        up_task, _, up_claim = value.partition("#c:")
                        up_inst = instances_by_id.get(up_task)
                        up_version = (read_state(config, up_inst).get("version") if up_inst else None)
                        tc_id = f"task_claim:{up_task}:{up_claim}"
                        nodeset.add(
                            {
                                "id": tc_id,
                                "type": "task_claim",
                                "task": up_task,
                                "claim_id": up_claim,
                                "upstream_version": up_version,
                            }
                        )
                        edges.append({"type": "cites", "role": "supports", "from": claim_node_id, "to": tc_id})
                        edges.append({"type": "derived_from_task", "from": claim_node_id, "to": tc_id})

                if cid in prev_claim_ids:
                    prev_sid = _find_section_for_claim(prev_sections, cid)
                    prev_claim_node_id = f"claim:{task_id}:v{prev_version:03d}:{prev_sid}:{cid}"
                    nodeset.add(
                        {
                            "id": prev_claim_node_id,
                            "type": "claim",
                            "task": task_id,
                            "version": prev_version,
                            "section": prev_sid,
                            "claim_id": cid,
                        }
                    )
                    edges.append({"type": "carried_from", "from": claim_node_id, "to": prev_claim_node_id})
    finally:
        if raw_con is not None:
            raw_con.close()

    lineage = {
        "header": {
            "task": task_id,
            "version": version,
            "built_at": header["built_at"],
            "brain": {
                "name": meta.get("name"),
                "taxonomy_version": meta.get("taxonomy_version"),
                "db_path": str(config.brain_db),
            },
            "template": header["template"],
            "base": header["base"],
        },
        "nodes": nodeset.nodes,
        "edges": edges,
        "retrieval": retrieval,
    }
    payload = json.dumps(lineage, indent=2, sort_keys=False)
    lineage_path = src_dir / f"v{version:03d}.lineage.json"
    lineage_path.write_text(payload, encoding="utf-8")

    sources: list[dict[str, Any]] = []
    seen_tags: set[str] = set()
    for body in sections.values():
        for b in claims.parse_blocks(body):
            if not claims.is_claim(b):
                continue
            for tag in b["tags"]:
                if tag in seen_tags:
                    continue
                seen_tags.add(tag)
                kind, value = claims.parse_tag(tag)
                tag_str = f"{kind}:{value}"
                if kind == "RAG":
                    ev = brain_mod.evidence(config, value)
                    sources.append(
                        {"tag": tag_str, "kind": "narrative", "chunk_id": str(value), "source_file": ev.get("source")}
                    )
                elif kind == "MART":
                    metric, _, grain = value.partition("@")
                    sources.append(
                        {"tag": tag_str, "kind": "metric", "metric": metric, "grain": grain or None, "source_file": None}
                    )
                elif kind == "GRAPH":
                    sources.append({"tag": tag_str, "kind": "taxonomy", "node": value, "source_file": None})
                elif kind == "FILE":
                    path, _, locator = value.partition("#")
                    sources.append(
                        {"tag": tag_str, "kind": "file", "source_file": path, "locator": locator or None}
                    )
                elif kind == "TASK":
                    up_task, _, up_claim = value.partition("#c:")
                    sources.append(
                        {"tag": tag_str, "kind": "task", "task": up_task, "claim": up_claim, "source_file": None}
                    )
    sources_path = src_dir / f"v{version:03d}.sources.json"
    sources_path.write_text(json.dumps(sources, indent=2), encoding="utf-8")

    return {
        "status": "ok",
        "task": task_id,
        "version": version,
        "lineage_path": str(lineage_path),
        "sources_path": str(sources_path),
        "bytes": len(payload.encode("utf-8")),
    }
