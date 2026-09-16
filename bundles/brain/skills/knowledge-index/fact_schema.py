#!/usr/bin/env python3
"""Pure, deterministic helpers for temporal fact intake (no I/O, no LLM)."""
import hashlib, json, re

SENTIMENTS = frozenset({"positive", "neutral", "negative", "mixed"})
_STOP = {"the", "a", "an", "of", "for", "to", "is", "are"}


def normalize(text: str) -> str:
    toks = re.findall(r"[a-z0-9][a-z0-9'-]*", (text or "").lower())
    return " ".join(t for t in toks if t not in _STOP)


def canon_key(entity: str, predicate: str, aliases: dict) -> tuple[str, str]:
    e, p = normalize(entity), normalize(predicate)
    mapped = aliases.get(f"{e}|{p}")
    if mapped and "|" in mapped:
        e, p = mapped.split("|", 1)
    return e, p


def assertion_id(entity: str, predicate: str, value, source: str, segment_id: str) -> str:
    raw = "|".join([entity, predicate, json.dumps(value, sort_keys=True), source, segment_id])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_ledger(assertions: list, links: list) -> dict:
    by_id = {a["assertion_id"]: a for a in assertions}
    for src_id, relation, tgt_id in links:
        by_id[src_id].setdefault(relation, []).append(tgt_id)
    return {"schema_version": "1.0", "assertions": assertions}
