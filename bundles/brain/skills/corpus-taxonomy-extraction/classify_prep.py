#!/usr/bin/env python3
"""Prepare per-chunk taxonomy classification for LOW-TIER agents (map stage).

Meaning is agentic: rather than force a deterministic classifier, hand each chunk
to a cheap model (Haiku) with the taxonomy vocabulary and let it assign the real
L1 categories. This script only prepares batches + the vocabulary + instructions;
low-tier subagents do the classification; classify_write.py writes results back.

Reads `chunks` from the knowledge SQLite, writes:
  <out>/vocab.md          — L1 categories (each with its L2 children) — the closed list
  <out>/instructions.md   — the classification task
  <out>/batch_<k>.json     — [{id, source, title, preview}] for subagent k

Usage: classify_prep.py --db knowledge.sqlite --taxonomy taxonomy_v0.json --out <dir> [--batches 5] [--preview 400]
"""
import argparse, json, os, sqlite3

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--batches", type=int, default=5)
    ap.add_argument("--preview", type=int, default=400)
    ap.add_argument("--docs", help="comma list of source relpaths — prep ONLY these docs' chunks (incremental reclassify)")
    ap.add_argument("--chunks", help="comma list of chunk ids — prep ONLY these chunks (incremental reclassify)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    it = json.load(open(a.taxonomy)).get("intent_taxonomy", {})
    tree = it.get("tree", {})
    l1s = sorted(set(it.get("l1", [])) | set(tree.keys()))
    with open(os.path.join(a.out, "vocab.md"), "w") as f:
        f.write("# Taxonomy — allowed categories (use EXACT names). Assign the MOST SPECIFIC that fits:\n"
                "# an **L2** (indented) when the chunk is specifically about it, else its **L1**.\n\n")
        for l1 in l1s:
            f.write(f"- {l1}\n")
            for l2 in (tree.get(l1) or []):
                f.write(f"    - {l2}\n")
    with open(os.path.join(a.out, "instructions.md"), "w") as f:
        f.write(
            "# Classify each chunk against the taxonomy (L1 + L2)\n\n"
            "For every chunk below, choose the categories from `vocab.md` the chunk is genuinely "
            "ABOUT (0–3). **Prefer the most specific level:** pick an **L2** when the chunk is "
            "specifically about that sub-topic; otherwise pick its **L1**. You may mix L1 and L2. "
            "Use EXACT names. If a chunk is generic/administrative and fits none, return an empty "
            "list — do NOT force a tag.\n\n"
            "Output ONE JSON file `result_<k>.json` mapping chunk id -> list of category names "
            "(each an EXACT L1 or L2 label):\n"
            '  {"12": ["Unauthorized items"], "13": [], "14": ["Track Delivery","Billing & Payments"]}\n'
            "Judge by the title + preview. Be precise, not generous. (An L2 auto-includes its L1.)\n")
    con = sqlite3.connect(a.db)
    where, params = "", [a.preview]
    if a.docs:
        docs = [s.strip() for s in a.docs.split(",") if s.strip()]
        where = " WHERE source IN (" + ",".join("?" * len(docs)) + ")"; params += docs
    elif a.chunks:
        ids = [int(x) for x in a.chunks.split(",") if x.strip()]
        where = " WHERE id IN (" + ",".join("?" * len(ids)) + ")"; params += ids
    rows = con.execute(f"SELECT id, source, title, substr(text,1,?) FROM chunks{where} ORDER BY id", params).fetchall()
    if not rows:
        print(f"no chunks match — nothing to classify -> {a.out}"); return
    n = max(1, a.batches)
    size = (len(rows) + n - 1) // n
    for k in range(n):
        batch = rows[k*size:(k+1)*size]
        if not batch: break
        items = [{"id": r[0], "source": r[1], "title": r[2], "preview": " ".join((r[3] or "").split())} for r in batch]
        json.dump(items, open(os.path.join(a.out, f"batch_{k}.json"), "w"), indent=1)
    print(f"prepared {len(rows)} chunks into {min(n, (len(rows)+size-1)//size)} batches -> {a.out}")

if __name__ == "__main__":
    main()
