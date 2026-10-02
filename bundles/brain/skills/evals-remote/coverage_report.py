"""Map what data the eval suite tests, and where the gaps are — brain-agnostic.

Three lenses, none brain-specific (all policy comes from the config):
  1. Narrative-corpus coverage (AUTHORITATIVE) — evals vs the in-scope narrative files in the
     corpus inventory. This is the honest denominator; it does not depend on retrieval.
  2. Brain-source coverage — the brain's sources (the list_sources catalog plus superseded
     sources a latest_only=false sweep surfaces; on a server without list_sources, the sweep
     alone, which is a floor).
  3. Governed-metric coverage — which numbers (marts) an eval pins. Tabular files are covered here,
     not as narrative sources.
"""
import argparse
import csv
import json
import re
import sys

from evals_config import load_config
from scope_rules import classify
from sharepoint_inventory import normalize, read_inventory
from source_key import match_key

# eval ground_truth_source values that name no real corpus file
_NON_FILE = {"none", "list_metrics", ""}


def read_eval_sources(csv_path):
    """Return list of {eval_id, category, scope, sources:[normalized keys], question, notes, no_halluc}."""
    out = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = (row.get("ground_truth_source") or "").strip()
            keys = []
            if raw.lower() not in _NON_FILE:
                for part in raw.split(","):
                    part = part.strip()
                    if part and part.lower() not in _NON_FILE:
                        keys.append(normalize(part))
            out.append({
                "eval_id": row["eval_id"],
                "category": row.get("category", ""),
                "scope": row.get("scope", ""),
                "sources": keys,
                "raw_source": raw,
                "question": row.get("question", ""),
                "notes": row.get("notes", ""),
                "no_halluc": str(row.get("min_items", "1")).strip() == "0",
            })
    return out


def _covers(eval_key, file_key):
    """An eval names a file exactly, or a family by its stem ('Survey Summary' covers
    'Survey Summary_MAY_2026.pdf'). Directional prefix only: 'plan.pdf' does not cover
    'Business plan.pdf'."""
    return bool(eval_key) and bool(file_key) and file_key.startswith(eval_key)


def _mentions(blob, name):
    """Whole-word mention of a metric name or its spaced form ('sla' is not in 'translation')."""
    return any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", blob)
               for t in {name.lower(), name.lower().replace("_", " ")})


def map_coverage(eval_rows, brain_sources, config, sp_rows=None, metrics=None):
    narrative_exts = {e.lower() for e in config.get("corpus", {}).get("narrative_exts", [])}
    eval_keys = [k for ev in eval_rows for k in ev["sources"]]

    # --- lens 2: brain-source coverage (classify with the project policy) ---
    brain = {}  # keyed by source: the same filename in two folders is two documents
    for source, meta in brain_sources.items():
        scope = classify(source, meta.get("folder", ""), meta.get("filename", ""),
                         meta.get("modified", ""), config)["scope"]
        brain[source] = {"source": source, "key": match_key(meta.get("filename", "")),
                         "filename": meta.get("filename", ""), "folder": meta.get("folder", ""),
                         "scope": scope, "tested_by": []}
    orphan_eval = []
    for ev in eval_rows:
        for ekey in ev["sources"]:
            hit = [src for src, b in brain.items() if _covers(ekey, b["key"])]
            if hit:
                for src in hit:
                    brain[src]["tested_by"].append(ev["eval_id"])
            else:
                orphan_eval.append({"eval_id": ev["eval_id"], "raw_source": ev["raw_source"]})
    scope_fail = {k: v for k, v in brain.items() if v["scope"] == "fail"}
    in_scope = {k: v for k, v in brain.items() if v["scope"] != "fail"}
    tested = {k: v for k, v in in_scope.items() if v["tested_by"]}
    untested = {k: v for k, v in in_scope.items() if not v["tested_by"]}
    brain_keys = {v["key"] for v in in_scope.values()}

    # --- lens 1: narrative-corpus coverage (authoritative, from the inventory) ---
    corpus_cov = None
    if sp_rows:
        corpus = {}  # dedup in-scope narrative files by match_key
        for r in sp_rows:
            if r["ext"].lower() not in narrative_exts:
                continue
            c = classify(r["filename"], r.get("folder", ""), r["filename"], r.get("modified", ""), config)
            if c["scope"] != "fail":
                corpus.setdefault(r["match_key"], r)
        c_tested, c_untested_in_brain, c_not_in_brain = [], [], []
        for key, r in corpus.items():
            has_eval = any(_covers(ek, key) for ek in eval_keys)
            in_brain = key in brain_keys
            if has_eval:
                c_tested.append(r["filename"])
            elif in_brain:
                c_untested_in_brain.append(r["filename"])
            else:
                c_not_in_brain.append(r["filename"])
        total = len(corpus)
        warning = None
        if not any(r["ext"].lower() in narrative_exts for r in sp_rows):
            warning = (f"inventory has {len(sp_rows)} rows but none match corpus.narrative_exts "
                       f"({sorted(narrative_exts)}); the narrative-corpus coverage denominator is empty")
            print(f"WARNING: {warning}", file=sys.stderr)
        corpus_cov = {
            "corpus_total": total, "tested": len(c_tested),
            "present_untested": len(c_untested_in_brain), "not_retrievable": len(c_not_in_brain),
            "pct": round(100 * len(c_tested) / max(1, total), 1),
            "untested_present_sources": sorted(c_untested_in_brain),
            "not_retrievable_sources": sorted(c_not_in_brain),
        }
        if warning:
            corpus_cov["warning"] = warning

    # --- lens 3: governed-metric coverage ---
    metric_cov = None
    if metrics is not None:
        blob = " ".join(
            f"{ev['eval_id']} {ev['category']} {ev['raw_source']} {ev.get('question', '')} {ev.get('notes', '')}"
            for ev in eval_rows).lower()
        tested_m, untested_m = [], []
        for m in metrics:
            name = m["name"] if isinstance(m, dict) else str(m)
            (tested_m if _mentions(blob, name) else untested_m).append(name)
        metric_cov = {"tested": tested_m, "untested": untested_m,
                      "pct": round(100 * len(tested_m) / max(1, len(metrics)), 1)}

    by_cat, by_scope = {}, {}
    for ev in eval_rows:
        by_cat[ev["category"]] = by_cat.get(ev["category"], 0) + 1
        by_scope[ev["scope"]] = by_scope.get(ev["scope"], 0) + 1

    return {
        "eval_totals": {"cases": len(eval_rows), "by_category": by_cat, "by_scope": by_scope,
                        "no_hallucination": sum(1 for e in eval_rows if e["no_halluc"])},
        "narrative_corpus_coverage": corpus_cov,
        "retrievable_source_floor": {
            "discovered_sources": len(brain), "in_scope_sources": len(in_scope),
            "tested": len(tested), "untested": len(untested),
            "pct": round(100 * len(tested) / max(1, len(in_scope)), 1),
            "untested_sources": sorted(v["source"] for v in untested.values()),
            "scope_fail_excluded": sorted(v["source"] for v in scope_fail.values()),
        },
        "metric_coverage": metric_cov,
        "orphan_eval_sources": orphan_eval,
    }


def render_md(rep, brain_version="unknown", catalog="sweep"):
    et = rep["eval_totals"]
    L = [f"# Eval coverage report — {brain_version}", "",
         f"**Eval cases:** {et['cases']} ({et['no_hallucination']} adversarial no-hallucination)", ""]
    cc = rep["narrative_corpus_coverage"]
    if cc:
        L += [f"**Narrative-corpus coverage (authoritative):** {cc['tested']}/{cc['corpus_total']} in-scope "
              f"narrative files tested (**{cc['pct']}%**); {cc['present_untested']} present-but-untested, "
              f"{cc['not_retrievable']} in the corpus but not surfaced by retrieval."]
    m = rep["metric_coverage"]
    if m:
        L += [f"**Governed-metric coverage:** {len(m['tested'])}/{len(m['tested']) + len(m['untested'])} "
              f"metrics referenced (**{m['pct']}%**)"]
    fl = rep["retrievable_source_floor"]
    exact = catalog == "list_sources"
    L += [f"**Brain-source coverage{'' if exact else ' (floor)'}:** {fl['tested']}/{fl['in_scope_sources']} "
          f"in-scope brain sources tested; {len(fl['scope_fail_excluded'])} scope-FAIL excluded.",
          "", ("> Brain sources come from the list_sources catalog, plus superseded sources a "
               "latest_only=false sweep surfaced (that part is a floor)." if exact else
               "> This server has no list_sources, so brain sources come from a retrieval sweep alone: a "
               "lower bound."),
          "> The narrative-corpus lens (from the inventory) is the honest denominator. Tabular files are "
          "covered by the governed-metric lens, not as narrative sources.", ""]
    if cc:
        L += ["## Present but untested (work queue)"]
        L += [f"- {s}" for s in cc["untested_present_sources"][:80]] or ["- none"]
        if cc["not_retrievable_sources"]:
            L += ["", "## In corpus but not surfaced by retrieval (ingestion/retrieval gaps to verify)"]
            L += [f"- {s}" for s in cc["not_retrievable_sources"][:80]]
    if m and m["untested"]:
        L += ["", "## Untested metrics (candidate numeric evals)"]
        L += [f"- {mm}" for mm in m["untested"]]
    if fl["scope_fail_excluded"]:
        L += ["", "## Excluded — scope-FAIL (remove from brain, do NOT write evals)"]
        L += [f"- {s}" for s in fl["scope_fail_excluded"]]
    if rep["orphan_eval_sources"]:
        L += ["", "## ⚠️ Orphan eval sources (ground_truth not found in brain — weak grounding)"]
        L += [f"- {o['eval_id']}: {o['raw_source']}" for o in rep["orphan_eval_sources"]]
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--evals", required=True)
    p.add_argument("--brain-json", required=True)
    p.add_argument("--config", help="evals config JSON (or EVALS_CONFIG env)")
    p.add_argument("--xlsx", help="corpus inventory xlsx (overrides config corpus.inventory_xlsx)")
    p.add_argument("--metrics-json")
    p.add_argument("--brain-version", default="unknown")
    p.add_argument("--out")
    a = p.parse_args(argv)

    config = load_config(a.config)
    eval_rows = read_eval_sources(a.evals)
    brain_doc = json.load(open(a.brain_json))
    brain = brain_doc["sources"]
    xlsx = a.xlsx or config.get("corpus", {}).get("inventory_xlsx")
    sheet = config.get("corpus", {}).get("inventory_sheet", "Query")
    sp_rows = read_inventory(xlsx, sheet) if xlsx else None
    metrics = None
    if a.metrics_json:
        md = json.load(open(a.metrics_json))
        metrics = md.get("metrics", md) if isinstance(md, dict) else md

    rep = map_coverage(eval_rows, brain, config, sp_rows=sp_rows, metrics=metrics)
    md = render_md(rep, a.brain_version, brain_doc.get("catalog", "sweep"))
    if a.out:
        open(a.out, "w").write(md)
        open(a.out.replace(".md", ".json"), "w").write(json.dumps(rep, indent=2))
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
