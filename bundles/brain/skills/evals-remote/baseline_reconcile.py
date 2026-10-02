"""Reconcile the corpus inventory vs brain sources; gate on out-of-scope (policy) leakage.

Brain-agnostic: the scope policy and corpus definition come from the config, not the code.
"""
import argparse
import json

from evals_config import load_config
from scope_rules import classify
from sharepoint_inventory import read_inventory
from source_key import match_key


def reconcile(sp_rows, brain, config):
    narrative_exts = {e.lower() for e in config.get("corpus", {}).get("narrative_exts", [])}
    sp_by_key = {r["match_key"]: r for r in sp_rows}
    covered, orphaned, fail_sources, review = [], [], [], []
    brain_keys = set()
    for source, meta in brain["sources"].items():
        c = classify(source, meta.get("folder", ""), meta.get("filename", ""), meta.get("modified", ""), config)
        key = match_key(meta.get("filename", ""))
        brain_keys.add(key)
        rec = {"source": source, "filename": meta.get("filename", ""), "folder": meta.get("folder", ""), **c}
        if c["scope"] == "fail":
            fail_sources.append(rec)
        elif c["scope"] == "review":
            review.append(rec)
        (covered if key in sp_by_key else orphaned).append(rec)
    # missing = in-scope NARRATIVE inventory files absent from the brain (tabular files -> marts, not narrative gaps)
    missing = []
    for r in sp_rows:
        if r["ext"].lower() not in narrative_exts:
            continue
        if r["match_key"] in brain_keys:
            continue
        c = classify(r["filename"], r.get("folder", ""), r["filename"], r.get("modified", ""), config)
        if c["scope"] != "fail":
            missing.append({"filename": r["filename"], "folder": r.get("folder", ""), **c})
    gate = "FAIL" if fail_sources else "PASS"
    return {
        "gate": gate, "fail_sources": fail_sources, "covered": covered,
        "superseded_sweep": brain.get("superseded_sweep", "ran"),
        "missing": missing, "orphaned": orphaned, "review": review,
        "counts": {
            "brain_sources": len(brain["sources"]), "inventory": len(sp_rows),
            "covered": len(covered), "missing_narrative": len(missing), "orphaned": len(orphaned),
            "fail": len(fail_sources), "review": len(review),
        },
    }


def render_md(rep, brain_version="unknown"):
    L = [f"# Baseline report — {brain_version}", "", f"**Scope gate: {rep['gate']}**", ""]
    if rep.get("superseded_sweep") == "not_run":
        L += ["> ⚠️ Superseded documents were NOT checked: no sweep ran. The gate covers visible sources only.", ""]
    L += ["## Counts", "```", json.dumps(rep["counts"], indent=2), "```"]
    if rep["fail_sources"]:
        L += ["", "## FAIL — out-of-scope / junk in brain (remove — policy violation)"]
        L += [f"- `{s['source']}` — {s['reason']}" for s in rep["fail_sources"]]
    L += ["", "## Missing in-scope narrative files (in inventory, not in brain)"]
    L += ([f"- {m['filename']} ({m['folder']})" for m in rep["missing"][:100]] or ["- none"])
    L += ["", "## Review queue"]
    L += ([f"- `{s['source']}` — {s['reason']}" for s in rep["review"][:100]] or ["- none"])
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--brain-json", required=True)
    p.add_argument("--config", help="evals config JSON (or EVALS_CONFIG env)")
    p.add_argument("--xlsx", help="corpus inventory xlsx (overrides config corpus.inventory_xlsx)")
    p.add_argument("--out")
    p.add_argument("--brain-version", default="unknown")
    a = p.parse_args(argv)
    config = load_config(a.config)
    xlsx = a.xlsx or config.get("corpus", {}).get("inventory_xlsx")
    if not xlsx:
        p.error("no inventory: pass --xlsx or set corpus.inventory_xlsx in the config")
    sp_rows = read_inventory(xlsx, config.get("corpus", {}).get("inventory_sheet", "Query"))
    brain = json.load(open(a.brain_json))
    rep = reconcile(sp_rows, brain, config)
    md = render_md(rep, a.brain_version)
    if a.out:
        open(a.out, "w").write(md)
        open(a.out.replace(".md", ".json"), "w").write(json.dumps(rep, indent=2))
    print(md)
    return 1 if rep["gate"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
