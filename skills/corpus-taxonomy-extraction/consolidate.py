#!/usr/bin/env python3
"""Deterministic consolidation for corpus-taxonomy-extraction (reduce stage).

Reads per-document map outputs (JSON produced by low-tier map subagents),
pools candidate terms by kind (intent_class | metric | entity), normalizes
names, and clusters near-duplicates so a low-tier model only has to adjudicate
AMBIGUOUS merges rather than eyeball the whole corpus.

Torch-free: uses difflib (stdlib) for fuzzy grouping. No embedding model needed
for v0; swap in embeddings later if fragmentation warrants.

Input : a directory of *.json map files, each shaped like
  {"intent_classes":[{"name","parent","evidence","source","confidence"},...],
   "metrics":[{"name","value","grain","definition","source","type","confidence"},...],
   "entities":[{"name","kind","source","confidence"},...]}
Output: consolidated.json with clusters + provenance, and an auto/ambiguous split.

Usage: consolidate.py --map-dir <dir> --out <file> [--threshold 0.86]
"""
import argparse, glob, json, os, re
from difflib import SequenceMatcher

KINDS = {"intent_classes": "name", "metrics": "name", "entities": "name"}

def norm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    # light singularization / synonym flattening
    s = re.sub(r"\b(managements?|mgmt)\b", "management", s)
    return s.strip()

def cluster(names, threshold):
    """Greedy fuzzy clustering on normalized names. Returns list of member-index lists."""
    reps = []  # (normalized_rep, [indices])
    for idx, nm in enumerate(names):
        n = norm(nm)
        placed = False
        for rep in reps:
            if n == rep[0] or SequenceMatcher(None, n, rep[0]).ratio() >= threshold:
                rep[1].append(idx)
                placed = True
                break
        if not placed:
            reps.append([n, [idx]])
    return reps

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.86)
    a = ap.parse_args()

    pooled = {k: [] for k in KINDS}
    for jf in sorted(glob.glob(os.path.join(a.map_dir, "*.json"))):
        try:
            data = json.load(open(jf))
        except Exception as e:
            print(f"skip {jf}: {e}"); continue
        file_source = data.get("source", os.path.basename(jf))
        for k in KINDS:
            for item in data.get(k, []) or []:
                if isinstance(item, dict) and item.get("name"):
                    item.setdefault("source", file_source)
                    pooled[k].append(item)

    result = {"summary": {}, "clusters": {}}
    for k in KINDS:
        items = pooled[k]
        names = [it["name"] for it in items]
        groups = cluster(names, a.threshold)
        out_clusters = []
        for rep_norm, idxs in groups:
            members = [items[i] for i in idxs]
            variants = sorted({m["name"] for m in members})
            sources = sorted({m.get("source", "?") for m in members})
            confs = [m.get("confidence") for m in members if isinstance(m.get("confidence"), (int, float))]
            out_clusters.append({
                "canonical_guess": max(variants, key=len),
                "normalized": rep_norm,
                "variants": variants,
                "n_mentions": len(members),
                "n_sources": len(sources),
                "sources": sources,
                "avg_confidence": round(sum(confs)/len(confs), 3) if confs else None,
                "ambiguous": len(variants) > 1,   # >1 surface form -> needs adjudication
                "members": members,
            })
        out_clusters.sort(key=lambda c: (-c["n_sources"], -c["n_mentions"]))
        result["clusters"][k] = out_clusters
        result["summary"][k] = {
            "raw_mentions": len(items),
            "clusters": len(out_clusters),
            "ambiguous_clusters": sum(1 for c in out_clusters if c["ambiguous"]),
        }

    json.dump(result, open(a.out, "w"), indent=2)
    print(json.dumps(result["summary"], indent=2))

if __name__ == "__main__":
    main()
