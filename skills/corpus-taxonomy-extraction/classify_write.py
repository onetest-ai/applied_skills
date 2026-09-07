#!/usr/bin/env python3
"""Write agent chunk-classification results into the knowledge SQLite.

Consumes result_<k>.json ({chunk_id: [L1 names]}) produced by the low-tier
classification agents, and writes:
  chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT, source TEXT)
  graph_edges rows: (source='chunk:<id>', target=<category_id>, rel='about')   -- note↔vertex

So the vault tags, the retrieval chunks, and the taxonomy graph all reference the
same category ids (kebab of the L1 label == graph_nodes.id from build_graph.py).

Usage: classify_write.py --db knowledge.sqlite --results <dir>
"""
import argparse, glob, json, os, re, sqlite3

def nid(s): return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--results", required=True)
    a = ap.parse_args()
    c = sqlite3.connect(a.db)
    c.executescript("""
      DROP TABLE IF EXISTS chunk_topics;
      CREATE TABLE chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT);
      CREATE INDEX idx_ct_chunk ON chunk_topics(chunk_id);
      CREATE INDEX idx_ct_cat ON chunk_topics(category_id);
    """)
    c.execute("DELETE FROM graph_edges WHERE rel='about'")
    valid = {r[0] for r in c.execute("SELECT id FROM graph_nodes")}
    n_assign, n_chunks, skipped = 0, 0, 0
    for rf in sorted(glob.glob(os.path.join(a.results, "result_*.json"))):
        try: data = json.load(open(rf))
        except Exception as e: print("skip", rf, e); continue
        for cid, labels in data.items():
            n_chunks += 1
            for lbl in (labels or []):
                cat = nid(lbl)
                if valid and cat not in valid: skipped += 1; continue   # keep to the graph vocabulary
                c.execute("INSERT INTO chunk_topics VALUES(?,?,?,?)", (int(cid), cat, lbl, "intent_l1"))
                c.execute("INSERT INTO graph_edges VALUES(?,?,?)", (f"chunk:{cid}", cat, "about"))
                n_assign += 1
    c.commit()
    print(f"chunk_topics: {n_assign} assignments over {n_chunks} chunks"
          + (f" ({skipped} off-vocabulary dropped)" if skipped else ""))
    dist = c.execute("SELECT category_label, count(*) FROM chunk_topics GROUP BY category_id ORDER BY 2 DESC LIMIT 8").fetchall()
    print("top categories:", dist)
    c.close()

if __name__ == "__main__":
    main()
