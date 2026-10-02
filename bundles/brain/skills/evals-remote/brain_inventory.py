"""Inventory the brain's distinct sources: the exact catalog plus what it hides.

``list_sources`` (MCP >= 1.3.0) is the exact catalog of *visible* documents. It hides
superseded ones, but an out-of-scope document is a defect even when a date cutoff hides it,
so a retrieval sweep with ``latest_only=false`` adds superseded sources (that part is a floor:
retrieval surfaces a query-dependent subset). A server without ``list_sources`` falls back to
the sweep alone. Seed terms come from the config, then the brain's own taxonomy and metrics —
no domain vocabulary is hardcoded here.
"""
import argparse
import functools
import json
import re
import sys

from brain_mcp_client import BrainClientError, call_tool
from evals_config import load_config, resolve_brain
from source_key import brain_source_folder, brain_source_to_filename


def _meta(source, status, via):
    return {"folder": brain_source_folder(source), "filename": brain_source_to_filename(source),
            "status": status, "via": via}


def default_terms(call=call_tool, seeds=()):
    terms = list(seeds)
    try:
        tax = call("get_taxonomy", {"limit": 100})
        terms += [n["label"] for n in tax.get("nodes", []) if n.get("kind") == "intent_l1"]
    except BrainClientError:
        pass
    try:
        mets = call("list_metrics", {})
        terms += [m["name"] for m in mets.get("metrics", [])]
    except BrainClientError:
        pass
    seen, uniq = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def catalog_sources(call=call_tool):
    """Every visible source via list_sources, paged. Raises BrainClientError if unsupported."""
    sources, offset = {}, 0
    while offset is not None:
        page = call("list_sources", {"offset": offset})
        for row in page.get("sources", []):
            if row.get("source"):
                sources[row["source"]] = _meta(row["source"], "ACTIVE", "list_sources")
        offset = page.get("next_offset")
    return sources


def harvest_sources(terms, *, call=call_tool, per_query_limit=50, patience=8):
    sources, no_new, first = {}, 0, True
    queries_run = 0
    for term in terms:
        try:
            # latest_only stated explicitly: the inventory must see superseded documents,
            # and must not depend on a server default that can change.
            res = call("search_knowledge", {"query": term, "limit": per_query_limit, "latest_only": False})
        except BrainClientError:
            if first:
                raise
            queries_run += 1
            no_new += 1
            if no_new >= patience:
                break
            continue
        first = False
        queries_run += 1
        added = 0
        for hit in res.get("hits", []):
            s = hit.get("source")
            if s and s not in sources:
                sources[s] = _meta(s, hit.get("status") or "unknown", "sweep")
                added += 1
        no_new = 0 if added else no_new + 1
        if no_new >= patience:
            break
    return {"sources": sources, "queries_run": queries_run, "saturated": no_new >= patience}


def _catalog_terms(sources, cap=100):
    """Document names as sweep queries ('Docs__Release_Plan-2026.pdf.md' → 'Release Plan 2026'):
    brain-derived, so a brain without taxonomy, metrics or configured seeds is still swept."""
    terms = []
    for s in sources:
        stem = brain_source_to_filename(s).rsplit(".", 1)[0]
        words = " ".join(re.sub(r"[_\-.]+", " ", stem).split())
        if words and words not in terms:
            terms.append(words)
    return terms[:cap]


def build_inventory(terms, *, call=call_tool, patience=8):
    try:
        sources, catalog = catalog_sources(call), "list_sources"
    except BrainClientError:
        sources, catalog = {}, "sweep"
    terms = list(terms) + [t for t in _catalog_terms(sources) if t not in terms]
    sweep = harvest_sources(terms, call=call, patience=patience) if terms else \
        {"sources": {}, "queries_run": 0, "saturated": False}
    for s, meta in sweep["sources"].items():
        if s not in sources:
            sources[s] = meta
        elif meta["status"] == "SUPERSEDED":
            sources[s]["status"] = "SUPERSEDED"
    return {"sources": sources, "catalog": catalog,
            "superseded_found": sum(1 for m in sources.values() if m["status"] == "SUPERSEDED"),
            "superseded_sweep": "ran" if sweep["queries_run"] else "not_run",
            "queries_run": sweep["queries_run"], "saturated": sweep["saturated"]}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out")
    p.add_argument("--patience", type=int, default=8)
    p.add_argument("--config", help="evals config JSON (or EVALS_CONFIG env)")
    a = p.parse_args(argv)
    config = load_config(a.config)
    url, key, header = resolve_brain(config)
    call = functools.partial(call_tool, url=url, key=key, key_header=header)
    seeds = config.get("corpus", {}).get("seed_terms", [])
    res = build_inventory(default_terms(call=call, seeds=seeds), call=call, patience=a.patience)
    text = json.dumps(res, indent=2)
    if a.out:
        open(a.out, "w").write(text)
    print(f"{len(res['sources'])} sources (catalog: {res['catalog']}, {res['superseded_found']} superseded), "
          f"{res['queries_run']} sweep queries, saturated={res['saturated']}")
    if res["superseded_sweep"] == "not_run":
        print("WARNING: no sweep ran (no seed terms, taxonomy, metrics or catalog names) — superseded "
              "documents were not checked; the scope gate cannot vouch for them", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
