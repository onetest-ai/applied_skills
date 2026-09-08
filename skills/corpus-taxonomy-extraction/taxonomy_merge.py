#!/usr/bin/env python3
"""Assisted taxonomy MERGE — additive, human-gated.

Takes the agent proposals (from taxonomy_refine_prep.py) and merges the NEW L1/L2
terms into the taxonomy — **additively only**: add under the right parent, never
rename or remove (renaming an L1 changes its slug id and orphans every chunk_topic
and 'about' edge pointing at it). Deduplicates against the existing vocabulary
(exact + fuzzy) and drops near-duplicates.

Default is a DRY RUN that prints the proposed diff for a human to review; pass
--apply to write the new taxonomy (version bumped). This is the human gate.

Usage:
  taxonomy_merge.py --taxonomy taxonomy_v0.json --proposals <dir>            # review the diff
  taxonomy_merge.py --taxonomy taxonomy_v0.json --proposals <dir> --apply \
                    --out taxonomy_v1.json                                    # commit additions
Then (deterministic downstream): build_graph.py (adds vertices) → reclassify the
affected chunks (classify_prep --docs/--chunks → agents → classify_write) → related → vault.
"""
import argparse, difflib, glob, json, os, re

def nid(s): return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "n"

def near(name, pool, thresh=0.88):
    """Return an existing label that's an exact/fuzzy duplicate of `name`, else None."""
    k = nid(name)
    for p in pool:
        if nid(p) == k:
            return p
    m = difflib.get_close_matches(name, list(pool), n=1, cutoff=thresh)
    return m[0] if m else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy", required=True); ap.add_argument("--proposals", required=True)
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--out")
    ap.add_argument("--fuzzy", type=float, default=0.88)
    a = ap.parse_args()
    tax = json.load(open(a.taxonomy))
    it = tax.setdefault("intent_taxonomy", {})
    tree = it.setdefault("tree", {})
    # normalize: ensure every L1 (from tree keys + l1 list) is a tree key
    for l1 in list(it.get("l1", [])):
        tree.setdefault(l1, tree.get(l1, []))
    l1_set = set(tree.keys())
    l2_index = {l2: l1 for l1, kids in tree.items() for l2 in kids}

    proposals = []
    for rf in sorted(glob.glob(os.path.join(a.proposals, "result_*.json"))):
        try: data = json.load(open(rf))
        except Exception as e: print("skip", rf, e); continue
        proposals += data.get("proposals", []) if isinstance(data, dict) else []

    add_l1, add_l2, dropped = [], [], []          # (name) / (name,parent) / (name,reason)
    # 1st pass: new L1s (so L2s can attach to a just-proposed L1)
    proposed_l1 = set()
    for p in proposals:
        if (p.get("level") or "").upper() != "L1": continue
        name = (p.get("name") or "").strip()
        if not name: continue
        dup = near(name, l1_set | proposed_l1, a.fuzzy)
        if dup: dropped.append((name, f"dup of L1 '{dup}'")); continue
        proposed_l1.add(name); add_l1.append(name)
    # 2nd pass: new L2s under a valid parent
    for p in proposals:
        if (p.get("level") or "").upper() != "L2": continue
        name = (p.get("name") or "").strip(); parent = (p.get("parent") or "").strip()
        if not name: continue
        dup = near(name, set(l2_index) | {x[0] for x in add_l2}, a.fuzzy)
        if dup: dropped.append((name, f"dup of L2 '{dup}'")); continue
        pmatch = near(parent, l1_set | proposed_l1, a.fuzzy) if parent else None
        if not pmatch:
            dropped.append((name, f"L2 parent '{parent or '—'}' not an existing/new L1")); continue
        add_l2.append((name, pmatch))

    print(f"=== taxonomy merge (from {a.proposals}) — {'APPLY' if a.apply else 'DRY RUN'} ===")
    print(f"proposed: +{len(add_l1)} L1, +{len(add_l2)} L2 ; dropped {len(dropped)} dup/invalid")
    for n_ in add_l1: print(f"  + L1  {n_}")
    for n_, par in add_l2: print(f"  + L2  {n_}   (under {par})")
    for n_, why in dropped[:20]: print(f"  · skip {n_}  — {why}")
    if not a.apply:
        print("\nreview the above, then re-run with --apply --out taxonomy_v1.json"); return

    for n_ in add_l1: tree.setdefault(n_, [])
    for n_, par in add_l2: tree.setdefault(par, []); (n_ not in tree[par]) and tree[par].append(n_)
    it["l1"] = sorted(tree.keys())
    v = tax.get("version", 0)
    tax["version"] = (v + 1) if isinstance(v, int) else v
    tax.setdefault("history", []).append(
        {"added_l1": add_l1, "added_l2": add_l2, "from": os.path.abspath(a.proposals)})
    out = a.out or a.taxonomy
    json.dump(tax, open(out, "w"), indent=2)
    print(f"\napplied -> {out} (version {tax['version']}). NEXT: build_graph.py --taxonomy {out} --db <db> ; "
          f"then reclassify affected chunks (classify_prep --docs … / --chunks …) → classify_write.")

if __name__ == "__main__":
    main()
