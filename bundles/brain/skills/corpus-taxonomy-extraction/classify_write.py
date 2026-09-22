#!/usr/bin/env python3
"""Write agent chunk-classification results into the knowledge SQLite.

Consumes result_<k>.json ({chunk_id: [category names]}) produced by the low-tier
classification agents. Names may be **L1 or L2** — this resolves each against the
graph, writes it with its real `kind` (intent_l1 / intent_l2), and — for an L2 —
also rolls up its parent L1 (so L1 filters still catch it). A result of exactly
`["__no_topic__"]` is a NO-TOPIC verdict (filler/off-goal, not a category): no tags
are written, and the chunk is recorded in chunk_verdicts instead. Writes:
  chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT, source TEXT)
  graph_edges rows: (source='chunk:<id>', target=<category_id>, rel='about')   -- note↔vertex
  chunk_verdicts(chunk_id INTEGER PRIMARY KEY, verdict TEXT, taxonomy_version INT)

So the vault tags, the retrieval chunks, and the taxonomy graph all reference the
same category ids (kebab of the L1 label == graph_nodes.id from build_graph.py).

By default this is INCREMENTAL: it replaces the tags only for the chunk ids present
in the result files (delete-then-insert per chunk), so re-classifying a few changed
docs leaves every other chunk's tags intact. Pass --reset to rebuild the whole table.
Pass --merge to only ADD labels: no per-chunk delete, and a (chunk, category) pair
that already exists is left alone — this is what a health-review tags file wants,
since a `tag` op never deletes a tag. --merge and --reset are mutually exclusive.

graph_aliases table (written by build_graph for reviewed renames/merges) lets old labels
resolve to their current node ids.

Usage: classify_write.py --db knowledge.sqlite --results <dir> [--reset | --merge] [--reclassify-done PATH]
"""
import argparse, glob, json, os, re, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taxo_io  # noqa: E402

def nid(s): return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "n"

NO_TOPIC = "__no_topic__"   # classifier sentinel: filler / boilerplate / off-goal — not a category


def ensure_verdicts(c):
    c.execute("CREATE TABLE IF NOT EXISTS chunk_verdicts("
              "chunk_id INTEGER PRIMARY KEY, verdict TEXT, taxonomy_version INT)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--results", required=True)
    ap.add_argument("--reset", action="store_true", help="rebuild the whole chunk_topics table (default: only the chunks in the results)")
    ap.add_argument("--merge", action="store_true", help="only add labels — skip the per-chunk delete of existing "
                    "tags/edges, and never re-insert a (chunk, category) pair that already exists")
    ap.add_argument("--reclassify-done", help="taxonomy/work/reclassify.json — remove the chunk ids just written "
                    "(the file is deleted once empty)")
    a = ap.parse_args()
    if a.merge and a.reset:
        print("ERROR: --merge and --reset are mutually exclusive", file=sys.stderr)
        sys.exit(2)
    if a.merge and a.reclassify_done:
        # a reclassification REPLACES a chunk's tags; --merge only adds, so the old (migrated-away)
        # tags would stay while the queue entries were marked done
        print("ERROR: --merge and --reclassify-done are mutually exclusive: reclassified chunks are written "
              "without --merge", file=sys.stderr)
        sys.exit(2)
    queue = None
    if a.reclassify_done and os.path.exists(a.reclassify_done):
        # read and validate the queue BEFORE any write, so a bad queue never leaves tags committed
        # with the queue untouched
        try:
            with open(a.reclassify_done, encoding="utf-8") as f:
                queue = json.load(f)
            if not isinstance(queue, dict) or not isinstance(queue.get("reasons"), dict):
                raise ValueError('expected {"version", "chunk_ids", "reasons": {...}}')
        except (OSError, ValueError) as e:
            print(f"ERROR: cannot read the reclassify queue {a.reclassify_done}: {e}", file=sys.stderr)
            sys.exit(2)
    c = sqlite3.connect(a.db)
    if a.reset:
        tables_now = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ge_clause = "DELETE FROM graph_edges WHERE rel='about';" if "graph_edges" in tables_now else ""
        c.executescript(f"DROP TABLE IF EXISTS chunk_topics; DROP TABLE IF EXISTS chunk_verdicts; {ge_clause}")
    c.executescript("""
      CREATE TABLE IF NOT EXISTS chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT);
      CREATE INDEX IF NOT EXISTS idx_ct_chunk ON chunk_topics(chunk_id);
      CREATE INDEX IF NOT EXISTS idx_ct_cat ON chunk_topics(category_id);
    """)
    ensure_verdicts(c)
    meta = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tax_version = None
    if "meta" in meta:
        row = c.execute("SELECT value FROM meta WHERE key='taxonomy_version'").fetchone()
        tax_version = int(row[0]) if row and str(row[0]).isdigit() else None
    n_no_topic = 0
    # gather results first so we know exactly which chunks are being (re)classified
    results = {}
    for rf in sorted(glob.glob(os.path.join(a.results, "result_*.json"))):
        try: data = json.load(open(rf))
        except Exception as e: print("skip", rf, e); continue
        results.update({int(cid): labels for cid, labels in data.items()})
    # Detect stale batch files BEFORE any deletions: chunk IDs that no longer exist in the DB.
    # Happens when --reset re-indexes after chunking param changes or corpus edits.
    live_ids = {r[0] for r in c.execute("SELECT id FROM chunks")}
    stale = [cid for cid in results if cid not in live_ids]
    if stale:
        print(
            f"WARNING: {len(stale)} chunk ID(s) in result files not found in chunks table "
            f"— batch files are stale (re-run classify_prep after re-indexing). "
            f"Stale IDs will be skipped.",
            file=sys.stderr,
        )
        for cid in stale:
            del results[cid]
    # graph_edges must exist — created by build_graph.py, which must run before classify_write.py
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "graph_edges" not in tables:
        print("ERROR: graph_edges table not found — run build_graph.py before classify_write.py", file=sys.stderr)
        sys.exit(1)
    # incremental: clear only these chunks' existing tags/edges before re-inserting
    # (--merge skips this: it only adds labels, never deletes existing ones)
    if not a.merge:
        for cid in results:
            c.execute("DELETE FROM chunk_topics WHERE chunk_id=?", (cid,))
            c.execute("DELETE FROM graph_edges WHERE rel='about' AND source=?", (f"chunk:{cid}",))
    node = {i: (lbl, kind, par) for i, lbl, kind, par in
            c.execute("SELECT id,label,kind,parent FROM graph_nodes")}
    # old labels of reviewed renames/merges resolve to their node (build_graph writes graph_aliases)
    alias = dict(c.execute("SELECT alias_id, node_id FROM graph_aliases")) if "graph_aliases" in tables else {}

    n_assign, n_l2, n_chunks, skipped = 0, 0, 0, 0
    for cid, labels in results.items():
        n_chunks += 1
        labels = labels or []
        real = [l for l in labels if l != NO_TOPIC]
        if NO_TOPIC in labels and not real:
            if not a.merge:
                c.execute("INSERT INTO chunk_verdicts VALUES(?,?,?) ON CONFLICT(chunk_id) DO UPDATE SET "
                          "verdict=excluded.verdict, taxonomy_version=excluded.taxonomy_version",
                          (cid, "no_topic", tax_version))
                n_no_topic += 1
            continue
        if real:
            c.execute("DELETE FROM chunk_verdicts WHERE chunk_id=?", (cid,))
        added = set()
        def put(catid, label, kind):
            nonlocal n_assign
            if catid in added: return
            added.add(catid)
            if a.merge and c.execute(
                    "SELECT 1 FROM chunk_topics WHERE chunk_id=? AND category_id=?", (cid, catid)).fetchone():
                return
            c.execute("INSERT INTO chunk_topics VALUES(?,?,?,?)", (cid, catid, label, kind))
            c.execute("INSERT INTO graph_edges VALUES(?,?,?)", (f"chunk:{cid}", catid, "about"))
            n_assign += 1
        for lbl in real:
            cat = nid(lbl)
            if cat not in node and cat in alias: cat = alias[cat]
            if node and cat not in node: skipped += 1; continue     # keep to the graph vocabulary
            label, kind, parent = node.get(cat, (lbl, "intent_l1", None))
            put(cat, label, kind)
            if kind == "intent_l2":                                  # roll up the parent L1
                n_l2 += 1
                if parent and parent in node:
                    put(parent, node[parent][0], "intent_l1")
    c.commit()
    print(f"chunk_topics: {n_assign} assignments over {n_chunks} chunks ({n_l2} L2)"
          + (" [reset]" if a.reset else " [merge]" if a.merge else " [incremental]")
          + (f" ({skipped} off-vocabulary dropped)" if skipped else "")
          + (f", {n_no_topic} no-topic" if n_no_topic else ""))
    dist = c.execute("SELECT category_label, count(*) FROM chunk_topics GROUP BY category_id ORDER BY 2 DESC LIMIT 8").fetchall()
    print("top categories:", dist)
    if queue is not None:
        data = queue
        for cid in results:
            data["reasons"].pop(str(cid), None)
        data["chunk_ids"] = sorted(int(k) for k in data["reasons"])
        if data["chunk_ids"]:
            taxo_io.atomic_write_bytes(a.reclassify_done, taxo_io.dump_bytes(data))
        else:
            os.remove(a.reclassify_done)
        print(f"reclassify queue: {len(data['chunk_ids'])} chunk(s) left")
    c.close()

if __name__ == "__main__":
    main()
