#!/usr/bin/env python3
"""Write agent chunk-classification results into the knowledge SQLite.

Consumes result_<k>.json ({chunk_id: [category names]}) produced by the low-tier
classification agents. Names may be **L1 or L2** — this resolves each against the
graph, writes it with its real `kind` (intent_l1 / intent_l2), and — for an L2 —
also rolls up its parent L1 (so L1 filters still catch it). Writes:
  chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT, source TEXT)
  graph_edges rows: (source='chunk:<id>', target=<category_id>, rel='about')   -- note↔vertex

So the vault tags, the retrieval chunks, and the taxonomy graph all reference the
same category ids (kebab of the L1 label == graph_nodes.id from build_graph.py).

By default this is INCREMENTAL: it replaces the tags only for the chunk ids present
in the result files (delete-then-insert per chunk), so re-classifying a few changed
docs leaves every other chunk's tags intact. Pass --reset to rebuild the whole table.

Usage: classify_write.py --db knowledge.sqlite --results <dir> [--reset]
"""
import argparse, glob, json, os, re, sqlite3

def nid(s): return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--results", required=True)
    ap.add_argument("--reset", action="store_true", help="rebuild the whole chunk_topics table (default: only the chunks in the results)")
    a = ap.parse_args()
    c = sqlite3.connect(a.db)
    if a.reset:
        c.executescript("DROP TABLE IF EXISTS chunk_topics; DELETE FROM graph_edges WHERE rel='about';")
    c.executescript("""
      CREATE TABLE IF NOT EXISTS chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT);
      CREATE INDEX IF NOT EXISTS idx_ct_chunk ON chunk_topics(chunk_id);
      CREATE INDEX IF NOT EXISTS idx_ct_cat ON chunk_topics(category_id);
    """)
    # gather results first so we know exactly which chunks are being (re)classified
    results = {}
    for rf in sorted(glob.glob(os.path.join(a.results, "result_*.json"))):
        try: data = json.load(open(rf))
        except Exception as e: print("skip", rf, e); continue
        results.update({int(cid): labels for cid, labels in data.items()})
    # incremental: clear only these chunks' existing tags/edges before re-inserting
    for cid in results:
        c.execute("DELETE FROM chunk_topics WHERE chunk_id=?", (cid,))
        c.execute("DELETE FROM graph_edges WHERE rel='about' AND source=?", (f"chunk:{cid}",))
    # resolve each label against the graph: id -> (label, kind, parent_id)
    node = {i: (lbl, kind, par) for i, lbl, kind, par in
            c.execute("SELECT id,label,kind,parent FROM graph_nodes")}
    # Detect stale batch files: chunk IDs that no longer exist in the DB.
    # Happens when --reset re-indexes after chunking param changes or corpus edits.
    live_ids = {r[0] for r in c.execute("SELECT id FROM chunks")}
    stale = [cid for cid in results if cid not in live_ids]
    if stale:
        import sys
        print(
            f"WARNING: {len(stale)} chunk ID(s) in result files not found in chunks table "
            f"— batch files are stale (re-run classify_prep after re-indexing). "
            f"Stale IDs will be skipped.",
            file=sys.stderr,
        )
        for cid in stale:
            del results[cid]

    n_assign, n_l2, n_chunks, skipped = 0, 0, 0, 0
    for cid, labels in results.items():
        n_chunks += 1
        added = set()
        def put(catid, label, kind):
            nonlocal n_assign
            if catid in added: return
            added.add(catid)
            c.execute("INSERT INTO chunk_topics VALUES(?,?,?,?)", (cid, catid, label, kind))
            c.execute("INSERT INTO graph_edges VALUES(?,?,?)", (f"chunk:{cid}", catid, "about"))
            n_assign += 1
        for lbl in (labels or []):
            cat = nid(lbl)
            if node and cat not in node: skipped += 1; continue     # keep to the graph vocabulary
            label, kind, parent = node.get(cat, (lbl, "intent_l1", None))
            put(cat, label, kind)
            if kind == "intent_l2":                                  # roll up the parent L1
                n_l2 += 1
                if parent and parent in node:
                    put(parent, node[parent][0], "intent_l1")
    c.commit()
    print(f"chunk_topics: {n_assign} assignments over {n_chunks} chunks ({n_l2} L2)"
          + (" [reset]" if a.reset else " [incremental]")
          + (f" ({skipped} off-vocabulary dropped)" if skipped else ""))
    dist = c.execute("SELECT category_label, count(*) FROM chunk_topics GROUP BY category_id ORDER BY 2 DESC LIMIT 8").fetchall()
    print("top categories:", dist)
    c.close()

if __name__ == "__main__":
    main()
