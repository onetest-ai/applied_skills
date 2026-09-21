#!/usr/bin/env python3
"""Emit stage: render consolidated clusters into a reviewable taxonomy_v0.

Reads consolidate.py output (+ the raw map dir for demoted terms) and writes:
  - taxonomy_v0.json  (structured)
  - taxonomy_v0.md    (human review artifact)

Usage: emit_taxonomy.py --consolidated <file> --map-dir <dir> --out-json <f> --out-md <f> --goal "<text>"
"""
import argparse, glob, json, os, re, sys
from collections import Counter

# Skills are copied as flat, non-package dirs into each host's skills/ folder, so a
# sibling module can't be imported as a package. Prepend this file's dir so `flags`
# (the same directory) resolves regardless of the caller's cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flags import near_duplicate_labels, off_axis_l1

def norm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def majority(members, field):
    vals = [m.get(field) for m in members if m.get(field)]
    return Counter(vals).most_common(1)[0][0] if vals else None

def best_description(members):
    vals = [(m.get("description") or "").strip() for m in members]
    vals = [v for v in vals if v]
    if not vals:
        return None
    cnt = Counter(vals)
    top = max(cnt.values())
    return max((v for v in cnt if cnt[v] == top), key=len)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--consolidated", required=True)
    ap.add_argument("--map-dir", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--goal", default="")
    a = ap.parse_args()

    con = json.load(open(a.consolidated))
    clusters = con["clusters"]

    # ---- intent hierarchy ----
    intents = clusters.get("intent_classes", [])
    l1 = [c for c in intents if majority(c["members"], "level") == "L1"]
    l2 = [c for c in intents if majority(c["members"], "level") != "L1"]
    l1_by_norm = {c["normalized"]: c for c in l1}
    tree = {c["canonical_guess"]: [] for c in l1}
    unassigned = []
    for c in l2:
        parent = majority(c["members"], "parent")
        pc = l1_by_norm.get(norm(parent)) if parent else None
        if pc:
            tree[pc["canonical_guess"]].append(c)
        else:
            unassigned.append(c)

    # ---- metrics inventory ----
    metrics = []
    for c in clusters.get("metrics", []):
        m = c["members"]
        st = majority(m, "source_type") or "stated"
        vals = sorted({f'{x.get("value")} ({x.get("period")})' for x in m
                       if x.get("value") not in (None, "", "null")})
        metrics.append({
            "metric": c["canonical_guess"], "source_type": st,
            "grain": majority(m, "grain"), "n_sources": c["n_sources"],
            "sources": c["sources"], "stated_values": vals,
            "definition": majority(m, "definition"), "variants": c["variants"],
            "avg_confidence": c["avg_confidence"],
        })
    order = {"computable": 0, "both": 1, "stated": 2}
    metrics.sort(key=lambda x: (order.get(x["source_type"], 3), -x["n_sources"]))

    # ---- entities by kind ----
    ents = {}
    for c in clusters.get("entities", []):
        kind = majority(c["members"], "kind") or "other"
        ents.setdefault(kind, []).append(c)

    # ---- demoted (from raw map) ----
    demoted = Counter()
    for jf in glob.glob(os.path.join(a.map_dir, "*.json")):
        try:
            for d in json.load(open(jf)).get("demoted", []) or []:
                demoted[d] += 1
        except Exception:
            pass

    l1_labels = [c["canonical_guess"] for c in l1]
    descriptions = {c["canonical_guess"]: d for c in intents if (d := best_description(c["members"]))}

    # ---- review flags (advisory only; taxonomy above is unchanged) ----
    dup_groups = near_duplicate_labels(l1_labels)
    off_axis = [{"label": lbl, "reason": r}
                for lbl in l1_labels
                for r in [off_axis_l1(lbl)] if r]
    review_flags = {
        "near_duplicate_l1_groups": dup_groups,
        "off_axis_l1_candidates": off_axis,
        "note": "Advisory only, tuned for recall (false positives expected). "
                "A human prompt, not a gate: nothing here auto-merges or "
                "auto-removes taxonomy entries.",
    }

    out = {"goal": a.goal, "summary": con["summary"],
           "intent_taxonomy": {"l1": l1_labels,
                               "tree": {k: [x["canonical_guess"] for x in v] for k, v in tree.items()},
                               "unassigned_l2": [c["canonical_guess"] for c in unassigned]},
           "metrics": metrics,
           "entities": {k: [c["canonical_guess"] for c in v] for k, v in ents.items()},
           "demoted": demoted.most_common(),
           "review_flags": review_flags}
    if descriptions:
        out["descriptions"] = descriptions
    json.dump(out, open(a.out_json, "w"), indent=2)

    if dup_groups or off_axis:
        print(f"review flags: {len(dup_groups)} near-duplicate L1 group(s), "
              f"{len(off_axis)} off-axis L1 candidate(s) — see taxonomy_v0.md §5",
              file=sys.stderr)

    # ---- markdown ----
    L = []
    L.append("# Taxonomy v0 — corpus-taxonomy-extraction\n")
    if a.goal: L.append(f"**Goal lens:** {a.goal}\n")
    s = con["summary"]
    L.append("**Consolidation summary:**\n")
    for k, v in s.items():
        L.append(f"- `{k}`: {v['raw_mentions']} mentions → {v['clusters']} clusters "
                 f"({v['ambiguous_clusters']} need merge review)")
    L.append("\n> Draft for review. Provenance + confidence kept. Numbers here are *stated values with citation*, "
             "never computed answers. Ambiguous merges flagged for ratification.\n")

    L.append("\n## 1. Intent taxonomy (L1 → L2)\n")
    for c in sorted(l1, key=lambda x: -x["n_sources"]):
        kids = tree[c["canonical_guess"]]
        conf = c.get("avg_confidence")
        L.append(f"\n### {c['canonical_guess']}  _(L1, {c['n_sources']} src"
                 + (f", conf {conf}" if conf else "") + ")_")
        d = descriptions.get(c["canonical_guess"])
        if d:
            L.append(f"  _{d}_")
        if len(c["variants"]) > 1:
            L.append(f"  - ⚠️ variants to merge: {c['variants']}")
        for k in sorted(kids, key=lambda x: -x["n_mentions"]):
            kd = descriptions.get(k["canonical_guess"])
            L.append(f"  - {k['canonical_guess']}"
                     + (f"  ⚠️{k['variants']}" if len(k['variants']) > 1 else "")
                     + (f" — {kd}" if kd else ""))
    if unassigned:
        L.append("\n### (L2 without a matched L1 parent)")
        for k in unassigned:
            L.append(f"  - {k['canonical_guess']}")

    L.append("\n## 2. Metric inventory (by how it must be answered)\n")
    L.append("| Metric | source_type | grain | stated values | # src | conf |")
    L.append("|---|---|---|---|---|---|")
    for m in metrics:
        sv = "; ".join(m["stated_values"][:4]) if m["stated_values"] else ""
        L.append(f"| {m['metric']} | **{m['source_type']}** | {m['grain'] or ''} | {sv} | "
                 f"{m['n_sources']} | {m['avg_confidence'] or ''} |")
    L.append("\n_`computable` → deterministic SQL over marts · `stated` → quote w/ citation · "
             "`both` → compute + reconcile._\n")

    L.append("\n## 3. Entity / dimension candidates\n")
    for kind in sorted(ents):
        names = sorted({c["canonical_guess"] for c in ents[kind]})
        L.append(f"- **{kind}** ({len(names)}): {', '.join(names[:40])}")

    if demoted:
        L.append("\n## 4. Demoted (seen but off-goal — not deleted)\n")
        L.append(", ".join(f"{d} ({n})" for d, n in demoted.most_common(40)))

    if dup_groups or off_axis:
        L.append("\n## 5. Review flags (advisory — human gate, not applied)\n")
        L.append("> Heuristic, tuned for recall: expect false positives. Nothing here "
                  "auto-merges or auto-removes taxonomy entries; use it as a checklist.\n")
        if dup_groups:
            L.append("\n**Near-duplicate L1 labels — consider merging:**\n")
            for g in dup_groups:
                L.append(f"  - {g}")
        if off_axis:
            L.append("\n**Off-axis L1 candidates — consider demoting/removing:**\n")
            for f in off_axis:
                L.append(f"  - **{f['label']}** — {f['reason']}")

    open(a.out_md, "w").write("\n".join(L) + "\n")
    print(f"wrote {a.out_json} and {a.out_md}")
    print(json.dumps(con["summary"], indent=2))

if __name__ == "__main__":
    main()
