#!/usr/bin/env python3
"""Assisted taxonomy DELTA — prepare for agents to PROPOSE additive L1/L2 terms.

When the corpus (or the parse) shifts, some chunks carry real concepts with no home
in the taxonomy — they end up UNTAGGED. This gathers those chunks (the signal) and
asks low-tier agents to propose **additions only**: either map a chunk to an existing
L1/L2, or propose a NEW L1, or a NEW L2 under a named parent L1 — with evidence.
The agent proposes; a human gates the merge (see taxonomy_merge.py). Never rename.

Reads the current taxonomy + the store. Writes:
  <out>/vocab.md          — current L1/L2 (what already exists — do NOT duplicate)
  <out>/instructions.md   — the proposal task
  <out>/batch_<k>.json    — [{id, source, title, preview}] of UNTAGGED chunks

Usage:
  taxonomy_refine_prep.py --db K.sqlite --taxonomy taxonomy_v0.json --out <dir>
                          [--batches 5] [--preview 500] [--docs a,b]  [--limit 400]
"""
import argparse, json, os, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxo_io import one_line

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--batches", type=int, default=5)
    ap.add_argument("--preview", type=int, default=500); ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--docs", help="restrict to these source relpaths (comma list)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tax = json.load(open(a.taxonomy))
    it = tax.get("intent_taxonomy", {})
    tree = it.get("tree", {}); l1s = sorted(set(it.get("l1", [])) | set(tree.keys()))
    desc = tax.get("descriptions") or {}
    with open(os.path.join(a.out, "vocab.md"), "w") as f:
        f.write("# CURRENT taxonomy (already exists — do NOT re-propose these; only propose what's missing)\n\n")
        for l1 in l1s:
            f.write(f"- {l1}" + (f" — {one_line(desc[l1])}" if desc.get(l1) else "") + "\n")
            for l2 in (tree.get(l1) or []):
                f.write(f"    - {l2}" + (f" — {one_line(desc[l2])}" if desc.get(l2) else "") + "\n")
    with open(os.path.join(a.out, "instructions.md"), "w") as f:
        f.write(
            "# Propose ADDITIVE taxonomy terms for the untagged chunks\n\n"
            "These chunks were left untagged — the classifier found no fitting category. For each, "
            "decide ONE of:\n"
            "- it actually fits an EXISTING category in `vocab.md` → record that (the classifier missed it); or\n"
            "- it carries a real, recurring concept with NO home → propose a NEW term:\n"
            "  a new **L2** under a named existing (or newly proposed) **L1** parent (preferred — most new\n"
            "  concepts are sub-topics), or a new **L1** only when it's a genuinely new top-level theme.\n\n"
            "Rules: additive only — never rename/replace existing terms. Be conservative: propose a term "
            "only if several chunks share it. Give a short evidence quote and example chunk ids. Give every "
            "proposed term a one-sentence description.\n\n"
            "Output ONE JSON file `result_<k>.json`:\n"
            '  {\n'
            '    "proposals": [\n'
            '      {"name":"Proof of Delivery","level":"L2","parent":"Delivery & Pickup Management",\n'
            '       "description":"one sentence: what this covers and how it differs from its siblings",\n'
            '       "evidence":"AI-verified proof of delivery + push notification","example_ids":[123,456]},\n'
            '      {"name":"AI & Automation","level":"L1","parent":null,\n'
            '       "description":"one sentence: what this covers and how it differs from its siblings",\n'
            '       "evidence":"…","example_ids":[789]}\n'
            '    ],\n'
            '    "map": {"123":["Track Delivery"]}   // chunks that DO fit an existing category after all\n'
            '  }\n')
    con = sqlite3.connect(a.db)
    where, params = "t.chunk_id IS NULL", [a.preview]
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_verdicts'").fetchone():
        where += " AND NOT EXISTS (SELECT 1 FROM chunk_verdicts v WHERE v.chunk_id = c.id)"
    if a.docs:
        docs = [s.strip() for s in a.docs.split(",") if s.strip()]
        where += " AND c.source IN (" + ",".join("?" * len(docs)) + ")"; params += docs
    rows = con.execute(
        f"SELECT c.id,c.source,c.title,substr(c.text,1,?) FROM chunks c "
        f"LEFT JOIN chunk_topics t ON t.chunk_id=c.id WHERE {where} ORDER BY c.id LIMIT {int(a.limit)}",
        params).fetchall()
    if not rows:
        print(f"no untagged chunks{' for those docs' if a.docs else ''} — taxonomy looks sufficient."); return
    n = max(1, a.batches); size = (len(rows) + n - 1) // n; made = 0
    for k in range(n):
        b = rows[k*size:(k+1)*size]
        if not b: break
        items = [{"id": r[0], "source": r[1], "title": r[2], "preview": " ".join((r[3] or "").split())} for r in b]
        json.dump(items, open(os.path.join(a.out, f"batch_{k}.json"), "w"), indent=1); made += 1
    print(f"prepared {len(rows)} UNTAGGED chunks into {made} batch(es) -> {a.out}\n"
          f"→ dispatch agents (read instructions.md + vocab.md + batch_k.json) → result_k.json, then: taxonomy_review.py plan --mode drift "
          f"--taxonomy <current.json> --proposals {a.out} --db <db> → serve → taxonomy_merge.py --review <review> --apply")
    con.close()

if __name__ == "__main__":
    main()
