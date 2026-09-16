#!/usr/bin/env python3
"""Resolve entities, link by recency, judge disagreements, emit + load a ledger."""
import argparse, json, math, os, sqlite3, sys
import fact_schema as FS


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def resolve_key(entity, predicate, existing_keys, aliases, embed_fn,
                high, low, model="BAAI/bge-small-en-v1.5"):
    cand = FS.canon_key(entity, predicate, aliases)
    if cand in existing_keys:
        return cand, "exact"
    if not existing_keys:
        return cand, "distinct"
    texts = [f"{cand[0]} {cand[1]}"] + [f"{e} {p}" for (e, p) in existing_keys]
    vecs = embed_fn(model, texts)
    q, rest = vecs[0], vecs[1:]
    sims = [(_cos(q, v), existing_keys[i]) for i, v in enumerate(rest)]
    best_sim, best_key = max(sims, key=lambda t: t[0])
    if best_sim >= high:
        return best_key, "auto"
    if best_sim >= low:
        return best_key, "review"
    return cand, "distinct"


def find_priors(con, entity, predicate):
    rows = con.execute(
        "SELECT assertion_id, value_json, asserted_at FROM memory_assertions "
        "WHERE entity=? AND predicate=? ORDER BY asserted_at", (entity, predicate)).fetchall()
    return [{"assertion_id": r[0], "value": json.loads(r[1]), "asserted_at": r[2]} for r in rows]


def plan_links(new_id, new_value, new_at, priors):
    auto, disagreements = [], []
    for p in priors:
        if p["value"] == new_value:
            continue
        if new_at > p["asserted_at"]:
            auto.append((new_id, "supersedes", p["assertion_id"]))
        elif new_at == p["asserted_at"]:
            disagreements.append({"new_id": new_id, "prior_id": p["assertion_id"],
                                  "new_value": new_value, "prior_value": p["value"]})
        # new_at < prior: no link; recency resolution in current_fact handles it
    return auto, disagreements
