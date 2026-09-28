"""Brain access for Scribe — thin wrapper around `semantic_core` (mcp/brain).

Sets ``BRAIN_DB`` / ``BRAIN_CATALOG`` / ``BRAIN_SKILLS`` from the loaded
``Config`` (``config.brain_db`` already honours the ``SCRIBE_BRAIN_DB``
override — see `scribe_lib.config.load_config`) and imports `semantic_core`
from `config.brain_mcp_dir`, once per process.

Every other module in this package calls `search` / `evidence` here — never
`semantic_core` directly — so `fingerprint`/`prepare`/`base` tests can
monkeypatch these two functions with a small in-memory fake store instead of
opening a real (heavy: sqlite-vec + fastembed) knowledge.sqlite.

`evidence` caches per process by chunk_id: within one `scribe.py` invocation
a chunk_id's text is fixed (Brain is read-only for the lifetime of the run),
so repeated lookups for the same chunk across sections/queries are free after
the first.
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Any

from scribe_lib.config import Config

_semantic_core = None  # cached imported module, per process
_evidence_cache: dict[str, dict[str, Any]] = {}


def _module(config: Config):
    global _semantic_core
    if _semantic_core is not None:
        return _semantic_core
    os.environ["BRAIN_DB"] = str(config.brain_db)
    os.environ["BRAIN_CATALOG"] = str(config.brain_catalog)
    os.environ["BRAIN_SKILLS"] = str(config.brain_skills)
    mcp_dir = str(config.brain_mcp_dir)
    if mcp_dir not in sys.path:
        sys.path.insert(0, mcp_dir)
    import semantic_core  # type: ignore  # noqa: PLC0415

    _semantic_core = semantic_core
    return _semantic_core


def search(config: Config, query: str, limit: int, tag_label: str | None) -> list[dict[str, Any]]:
    """`search_knowledge(query, limit, tag=tag_label)` hits (chunk_id stays a str).

    `tag_label` is the taxonomy node LABEL (e.g. "Domain Tag A"), never an id
    — an id returns 0 hits (see poc-design.md). Pass None for no tag filter.
    """
    sc = _module(config)
    if tag_label:
        result = sc.search_knowledge(query, limit=limit, tag=tag_label)
    else:
        result = sc.search_knowledge(query, limit=limit)
    return result.get("hits", [])


def evidence(config: Config, chunk_id: str) -> dict[str, Any]:
    """{chunk_id, source, section, ord, text, status} for one chunk id, cached."""
    chunk_id = str(chunk_id)
    if chunk_id in _evidence_cache:
        return _evidence_cache[chunk_id]
    sc = _module(config)
    result = sc.get_evidence(chunk_id=chunk_id)
    out = {
        "chunk_id": str(result.get("chunk_id", chunk_id)),
        "source": result.get("source"),
        "section": result.get("section"),
        "ord": result.get("ord"),
        "text": result.get("text", ""),
        "status": result.get("status", "not_modeled"),
    }
    _evidence_cache[chunk_id] = out
    return out


def list_metrics(config: Config) -> list[dict[str, Any]]:
    """`list_metrics()["metrics"]` — the Brain's governed metric catalog,
    read-only. Used only to answer "does this Brain define ANY metric at
    all" (`onboard.coverage`'s `numbers_available`); never to assert a
    figure — a number is only ever asserted from `get_metric`, per the Brain
    contract."""
    sc = _module(config)
    result = sc.list_metrics()
    return result.get("metrics") or []


def doc_id_for_raw(rel_path: str) -> str:
    """The Brain's `doc_id` for a raw file, once it is synced — the
    `parse_corpus` slug rule (verified on a real Brain): `/` -> `__`, `.md`
    appended. `rel_path` is the same path a `[FILE:<path>#...]` tag carries."""
    return rel_path.replace("/", "__") + ".md"


def chunks_for_doc(config: Config, doc_id: str, query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Chunks of one already-synced doc, ranked by relevance to `query` —
    `search_knowledge(query, limit=limit, source_contains=doc_id)["hits"]`.
    Used to offer `[RAG:]` candidates for a `[FILE:]` claim whose raw file
    just entered the Brain (spec A7)."""
    sc = _module(config)
    result = sc.search_knowledge(query, limit=limit, source_contains=doc_id)
    return result.get("hits", [])


def text_hash(text: str) -> str:
    """sha256(text, utf-8) hex[:16] — the fingerprint of a chunk/section's text."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def reset_cache() -> None:
    """Test helper: clear the module + evidence caches between fixtures."""
    global _semantic_core
    _semantic_core = None
    _evidence_cache.clear()
