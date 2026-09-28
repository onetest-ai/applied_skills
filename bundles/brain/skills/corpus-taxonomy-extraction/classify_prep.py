#!/usr/bin/env python3
"""Prepare per-chunk taxonomy classification for SONNET agents (map stage).

Meaning is agentic: rather than force a deterministic classifier, hand each chunk
to a Sonnet model with the taxonomy vocabulary and let it assign the real
L1 categories. This script only prepares batches + the vocabulary + instructions;
Sonnet subagents do the classification; classify_write.py writes results back.

Reads `chunks` from the knowledge SQLite, writes:
  <out>/vocab.md          — L1 categories (each with its L2 children) — the closed list
  <out>/instructions.md   — the classification task
  <out>/batch_<k>.json     — [{id, source, title, preview}] for subagent k

Batches hold at most --max-chunks chunks and --max-bytes bytes, so one agent's reply stays
under the 32K output-token limit and its batch file is read in one Read call; --batches is
only a minimum.

Usage: classify_prep.py --db knowledge.sqlite --taxonomy taxonomy_v0.json --out <dir> [--batches 5] [--preview 400]
                        [--max-chunks 150] [--max-bytes 60000]
"""
import argparse, json, math, os, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxo_io import one_line

BatchItem = dict[str, int | str]


def _serialized_size(batch: list[BatchItem]) -> int:
    return len(json.dumps(batch, indent=1).encode())


def _fit(batch: list[BatchItem], max_bytes: int) -> list[list[BatchItem]]:
    if len(batch) <= 1 or _serialized_size(batch) <= max_bytes:
        return [batch]
    mid = len(batch) // 2
    return _fit(batch[:mid], max_bytes) + _fit(batch[mid:], max_bytes)


def split_batches(items: list[BatchItem], max_chunks: int, max_bytes: int, min_batches: int) -> list[list[BatchItem]]:
    if not items:
        return []
    n = min(len(items), max(1, min_batches, math.ceil(len(items) / max(1, max_chunks))))
    size = math.ceil(len(items) / n)
    groups = [items[k * size:(k + 1) * size] for k in range(n)]
    return [part for g in groups if g for part in _fit(g, max_bytes)]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--batches", type=int, default=5)
    ap.add_argument("--preview", type=int, default=400)
    ap.add_argument("--max-chunks", type=int, default=150, help="most chunks per batch (default 150)")
    ap.add_argument("--max-bytes", type=int, default=60000, help="most bytes per batch file (default 60000)")
    ap.add_argument("--docs", help="comma list of source relpaths — prep ONLY these docs' chunks (incremental reclassify)")
    ap.add_argument("--chunks", help="comma list of chunk ids — prep ONLY these chunks (incremental reclassify)")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    tax = json.load(open(a.taxonomy))
    it = tax.get("intent_taxonomy", {})
    tree = it.get("tree", {})
    l1s = sorted(set(it.get("l1", [])) | set(tree.keys()))
    desc = tax.get("descriptions") or {}
    with open(os.path.join(a.out, "vocab.md"), "w") as f:
        f.write("# Taxonomy — allowed categories (use EXACT names). Assign the MOST SPECIFIC that fits:\n"
                "# an **L2** (indented) when the chunk is specifically about it, else its **L1**.\n\n")
        for l1 in l1s:
            f.write(f"- {l1}" + (f" — {one_line(desc[l1])}" if desc.get(l1) else "") + "\n")
            for l2 in (tree.get(l1) or []):
                f.write(f"    - {l2}" + (f" — {one_line(desc[l2])}" if desc.get(l2) else "") + "\n")
    with open(os.path.join(a.out, "instructions.md"), "w") as f:
        f.write(
            "# Classify each chunk against the taxonomy (L1 + L2)\n\n"
            "For every chunk below, choose the categories from `vocab.md` the chunk is genuinely "
            "ABOUT (0–3). **Prefer the most specific level:** pick an **L2** when the chunk is "
            "specifically about that sub-topic; otherwise pick its **L1**. You may mix L1 and L2. "
            "Use EXACT names. Do NOT force a tag. Two different answers when nothing fits:\n"
            "- `[\"__no_topic__\"]` — the chunk carries no topic at all: filler (\"Okay.\", \"Yep.\"), "
            "greetings and introductions, meeting logistics, agenda/boilerplate, or content unrelated to "
            "the goal (e.g. a case study about another client).\n"
            "- `[]` — the chunk IS substantive and on-goal, but no category in `vocab.md` fits it. "
            "These are the gaps the taxonomy review looks at, so do not use `[]` for filler.\n"
            "Descriptions (after the —) define each category's boundary; "
            "use them to choose between similar labels.\n\n"
            "Output ONE JSON file `result_<k>.json` mapping chunk id -> list of category names "
            "(each an EXACT L1 or L2 label):\n"
            '  {"12": ["Unauthorized items"], "13": [], "14": ["Track Delivery","Billing & Payments"], "15": ["__no_topic__"]}\n'
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
    items: list[BatchItem] = [{"id": r[0], "source": r[1], "title": r[2], "preview": " ".join((r[3] or "").split())} for r in rows]
    batches = split_batches(items, a.max_chunks, a.max_bytes, a.batches)
    for k, batch in enumerate(batches):
        json.dump(batch, open(os.path.join(a.out, f"batch_{k}.json"), "w"), indent=1)
    print(f"prepared {len(rows)} chunks into {len(batches)} batches "
          f"(<= {a.max_chunks} chunks, <= {a.max_bytes} bytes each) -> {a.out}")

if __name__ == "__main__":
    main()
