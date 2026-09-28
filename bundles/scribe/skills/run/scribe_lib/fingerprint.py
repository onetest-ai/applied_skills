"""fingerprint: per-section staleness detection against the Brain + raw index.

Per section: run each `queries` entry (params substituted) through
`scribe_lib.brain.search` once per tag label in `inputs.brain.tags`, **and**
always once more with no tag filter (controller ruling R10) — a chunk synced
into the Brain is untagged until classification runs, so a tag-only search
would make a newly synced document invisible to a tagged task until its next
classification pass. A task with no tags already searched untagged only, and
still issues exactly one search per query — unchanged. Hits are unioned by
`chunk_id`: when the same chunk comes back from more than one search, the
kept copy is whichever has the higher `score`, and every hit carries `via`
(sorted `["tag:<label>", ...]` plus `"untagged"` when that search also
produced it) recording which search(es) found it. Run the same queries
against `work/<task>/raw/raw.sqlite` (built by `gather-raw`) via FTS5 bm25,
keeping a raw hit only if its text contains >= 2 distinct query tokens
(len > 2, word-bounded) or the whole query phrase — this is the "relevance
floor" the brief calls out to stop every transcript matching every section
under plain OR-matching. `considered` = the sorted union of
`(chunk_id, text_hash)` (brain) and `(path#locator, text_hash)` (raw);
`fingerprint = sha256(json({considered}))` — a section's fingerprint no
longer includes upstream task version numbers (spec A6): an upstream publish,
by itself, is not evidence that THIS section changed.

## State shape this module reads (and that `publish` writes)

This module defines the per-task state shape fingerprint needs, and `publish`
writes compatibly:

    state["sections"][<section id>] = {
        "fingerprint":  "<sha256 hex>",                     # from the last fingerprint run this section was drafted against
        "cited_chunks": {"<chunk_id>": "<text_hash16>"},    # RAG chunks this section's merged claims actually cite
        "cited_raw":    {"<raw-rel-path>": "<sha256>"},     # raw files this section's merged claims actually cite (whole-file hash)
        "cited_task_claims": {"<up>#<claim_id>": "<text_hash16>"},  # `[TASK:]` claims this section's merged claims actually cite
    }
    state["upstream_versions"] = {"<task id>": <int version>}   # diagnostic only (plan/report); not part of a section's fingerprint

`cited_chunks`/`cited_raw`/`cited_task_claims` are populated by `publish`
(and `observe`, for a noop) from the claims that were actually merged into a
section — `fingerprint` itself never writes state. Before a task's first
publish, every section's prior `cited_chunks`/`cited_raw`/`cited_task_claims`
is empty, so only "fingerprint_changed" (via
`state["sections"][sid]["fingerprint"]`) or "first_run" can fire;
"cited_chunk_changed"/"cited_chunk_gone"/"raw_file_changed"/"raw_file_removed"/
"cited_task_claim_changed"/"cited_task_claim_gone"
are exercised in this task's tests against a hand-written state.json.

**A6 — staleness by consumption, not by version.** There is no
"upstream_published" reason: a downstream section is stale because an
upstream claim it actually cites (`[TASK:up#c:id]`, recorded at publish as
`cited_task_claims`) changed text or disappeared — not because task `up`
published a new version that this section never looked at. A section that
cites nothing from `up` never goes stale just because `up` did.

Writes `work/<task>/fingerprint.json`:

    {
      "task": <id>,
      "sections": {
        <section id>: {
          "queries": [...],
          "brain_hits": [{"chunk_id","source","section","score","text","text_hash","via":[...]}, ...],
          "raw_hits":   [{"path","locator","text","text_hash"}, ...],
          "considered": {"brain": [[chunk_id, text_hash], ...], "raw": [["path#locator", text_hash], ...]},
          "fingerprint": "<sha256 hex>",
          "cited_chunk_status": {"<chunk_id>": "same"|"changed"|"gone"},
          "cited_raw_status": {"<path>": "same"|"changed"|"removed"},
          "cited_task_claim_status": {"<up>#<claim_id>": "same"|"changed"|"gone"},
          "stale": bool,
          "stale_reasons": [...]
        }
      }
    }

`brain_hits`/`raw_hits` carry full evidence `text` (not just its hash) so
`prepare`'s pack-writer can cite it without re-fetching from the Brain.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from scribe_lib import brain as brain_mod
from scribe_lib import claims
from scribe_lib.checktask import _upstream_claims
from scribe_lib.config import (
    Config,
    parse_template_ref,
    read_state,
    resolve_instance_inputs,
    sha256_file,
    substitute_params,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokens(query: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(query) if len(t) > 2]


def _fts_query(query: str) -> str:
    toks = _tokens(query)
    return " OR ".join(toks) if toks else '""'


def _raw_hits(raw_db: Path, query: str, top_k: int) -> list[dict[str, Any]]:
    if not raw_db.is_file():
        return []
    toks = _tokens(query)
    if not toks:
        return []
    con = sqlite3.connect(f"file:{raw_db.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT path, locator, text FROM raw_fts WHERE raw_fts MATCH ? "
            "ORDER BY bm25(raw_fts) LIMIT ?",
            (_fts_query(query), top_k),
        ).fetchall()
    finally:
        con.close()
    phrase = query.strip().lower()
    hits = []
    for path, locator, text in rows:
        low = (text or "").lower()
        distinct = {t for t in toks if re.search(r"\b" + re.escape(t) + r"\b", low)}
        if len(distinct) >= 2 or (phrase and phrase in low):
            hits.append({"path": path, "locator": locator, "text": text or ""})
    return hits


def fingerprint_task(
    config: Config,
    task_id: str,
    instances: dict[str, Any],
    templates: dict[str, Any],
    edges: dict[str, list[str]],
) -> dict[str, Any]:
    instance = instances[task_id]
    template_id, _ = parse_template_ref(instance["template"])
    template = templates[template_id]
    resolved = resolve_instance_inputs(instance, template)
    params = instance.get("params") or {}
    sections_spec = (template.get("output") or {}).get("sections") or []

    tag_field = (resolved.get("brain") or {}).get("tags")
    tags: list[str | None]
    if isinstance(tag_field, list) and tag_field:
        tags = list(tag_field)
    elif isinstance(tag_field, str) and tag_field:
        tags = [tag_field]
    else:
        tags = [None]

    raw_db = config.work_dir / task_id / "raw" / "raw.sqlite"

    state = read_state(config, instance)
    prior_sections = state.get("sections") or {}
    upstream_claims_cache: dict[str, dict[str, dict]] = {}

    out_sections: dict[str, Any] = {}
    for sec in sections_spec:
        sid = sec["id"]
        queries = [substitute_params(q, params) for q in (sec.get("queries") or [])]

        # Each tag label gets its own tag-filtered search; a task with tags
        # ALSO always gets one untagged search per query (R10), so a chunk
        # not yet classified is still visible. A task with no tags (`tags ==
        # [None]`) issues only that one untagged search per query — unchanged.
        searches: list[tuple[str | None, str]] = [(tag, f"tag:{tag}") for tag in tags if tag is not None]
        searches.append((None, "untagged"))

        brain_hit_map: dict[str, dict[str, Any]] = {}
        brain_via_map: dict[str, set[str]] = {}
        for q in queries:
            for tag, via in searches:
                for h in brain_mod.search(config, q, config.top_k, tag):
                    cid = str(h["chunk_id"])
                    existing = brain_hit_map.get(cid)
                    if existing is None or (h.get("score") or 0) > (existing.get("score") or 0):
                        brain_hit_map[cid] = h
                    brain_via_map.setdefault(cid, set()).add(via)

        raw_hit_map: dict[tuple[str, str], dict[str, Any]] = {}
        for q in queries:
            for h in _raw_hits(raw_db, q, config.top_k):
                raw_hit_map[(h["path"], h["locator"])] = h

        considered_brain = sorted(
            (cid, brain_mod.text_hash(h.get("text", ""))) for cid, h in brain_hit_map.items()
        )
        considered_raw = sorted(
            (f"{p}#{loc}", brain_mod.text_hash(h.get("text", "")))
            for (p, loc), h in raw_hit_map.items()
        )
        payload = {
            "considered": {
                "brain": [list(x) for x in considered_brain],
                "raw": [list(x) for x in considered_raw],
            },
        }
        fp = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        prior = prior_sections.get(sid) or {}
        stale_reasons: list[str] = []
        cited_chunk_status: dict[str, str] = {}
        cited_raw_status: dict[str, str] = {}
        cited_task_claim_status: dict[str, str] = {}
        if not state:
            stale_reasons.append("first_run")
        else:
            if prior.get("fingerprint") != fp:
                stale_reasons.append("fingerprint_changed")

            for cid, prior_hash in (prior.get("cited_chunks") or {}).items():
                ev = brain_mod.evidence(config, cid)
                if ev.get("status") != "ok":
                    cited_chunk_status[cid] = "gone"
                elif brain_mod.text_hash(ev.get("text", "")) != prior_hash:
                    cited_chunk_status[cid] = "changed"
                else:
                    cited_chunk_status[cid] = "same"
            if "changed" in cited_chunk_status.values():
                stale_reasons.append("cited_chunk_changed")
            if "gone" in cited_chunk_status.values():
                stale_reasons.append("cited_chunk_gone")

            for path, prior_sha in (prior.get("cited_raw") or {}).items():
                full = config.raw_root / path
                if not full.is_file():
                    cited_raw_status[path] = "removed"
                elif sha256_file(full) != prior_sha:
                    cited_raw_status[path] = "changed"
                else:
                    cited_raw_status[path] = "same"
            if "changed" in cited_raw_status.values():
                stale_reasons.append("raw_file_changed")
            if "removed" in cited_raw_status.values():
                stale_reasons.append("raw_file_removed")

            for key, prior_hash in (prior.get("cited_task_claims") or {}).items():
                up, _, cid = key.partition("#")
                if up not in upstream_claims_cache:
                    upstream_claims_cache[up] = _upstream_claims(config, instances[up]) if up in instances else {}
                target = upstream_claims_cache[up].get(cid)
                if target is None:
                    cited_task_claim_status[key] = "gone"
                elif brain_mod.text_hash(claims.normalize_text(target["content"])) != prior_hash:
                    cited_task_claim_status[key] = "changed"
                else:
                    cited_task_claim_status[key] = "same"
            if "changed" in cited_task_claim_status.values():
                stale_reasons.append("cited_task_claim_changed")
            if "gone" in cited_task_claim_status.values():
                stale_reasons.append("cited_task_claim_gone")

        out_sections[sid] = {
            "queries": queries,
            "brain_hits": [
                {
                    "chunk_id": cid,
                    "source": h.get("source"),
                    "section": h.get("section"),
                    "score": h.get("score"),
                    "text": h.get("text", ""),
                    "text_hash": brain_mod.text_hash(h.get("text", "")),
                    "via": sorted(brain_via_map.get(cid, ())),
                }
                for cid, h in sorted(brain_hit_map.items())
            ],
            "raw_hits": [
                {
                    "path": p,
                    "locator": loc,
                    "text": h.get("text", ""),
                    "text_hash": brain_mod.text_hash(h.get("text", "")),
                }
                for (p, loc), h in sorted(raw_hit_map.items())
            ],
            "considered": payload["considered"],
            "fingerprint": fp,
            "cited_chunk_status": cited_chunk_status,
            "cited_raw_status": cited_raw_status,
            "cited_task_claim_status": cited_task_claim_status,
            "stale": bool(stale_reasons),
            "stale_reasons": stale_reasons,
        }

    result = {"task": task_id, "sections": out_sections}
    fp_path = config.work_dir / task_id / "fingerprint.json"
    fp_path.parent.mkdir(parents=True, exist_ok=True)
    fp_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
