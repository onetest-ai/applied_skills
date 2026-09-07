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
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    it = json.load(open(a.taxonomy)).get("intent_taxonomy", {})
    tree = it.get("tree", {})
    l1s = sorted(set(it.get("l1", [])) | set(tree.keys()))
    with open(os.path.join(a.out, "vocab.md"), "w") as f:
        f.write("# Taxonomy — allowed L1 categories (use EXACT names)\n\n")
        for l1 in l1s:
            kids = tree.get(l1) or []
            f.write(f"- {l1}" + (f"  (subtopics: {', '.join(kids)})" if kids else "") + "\n")
    with open(os.path.join(a.out, "instructions.md"), "w") as f:
        f.write(
            "# Classify each chunk against the taxonomy\n\n"
            "For every chunk below, choose the L1 categories from `vocab.md` that the chunk is "
            "genuinely ABOUT (0–3). Use EXACT L1 names. If a chunk is generic/administrative and "
            "fits none, return an empty list — do NOT force a tag.\n\n"
            "Output ONE JSON file `result_<k>.json` mapping chunk id -> list of L1 names:\n"
            '  {"12": ["Billing Disputes"], "13": [], "14": ["Delivery & Pickup Management","Billing & Payments"]}\n'
            "Judge by the title + preview. Be precise, not generous.\n")
    con = sqlite3.connect(a.db)
    rows = con.execute("SELECT id, source, title, substr(text,1,?) FROM chunks ORDER BY id", (a.preview,)).fetchall()
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
