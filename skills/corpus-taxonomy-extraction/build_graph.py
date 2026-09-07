#!/usr/bin/env python3
"""Write the taxonomy as a graph (node/edge tables) into the knowledge SQLite —
the deterministic replacement for a knowledge-graph engine's entity graph. Query it
with plain SQL JOINs / recursive CTEs (no separate graph DB, no server).

Tables:
  graph_nodes(id TEXT PK, label TEXT, kind TEXT, parent TEXT)   -- kind: intent_l1|intent_l2|entity_kind
  graph_edges(source TEXT, target TEXT, rel TEXT)               -- rel: subclass_of

Built from taxonomy_v0.json (intent L1/L2 hierarchy + entity kinds). Lives in the
same .sqlite as chunks/fts/vec and facts, so one file is the whole knowledge store.

Usage: build_graph.py --taxonomy taxonomy_v0.json --db knowledge.sqlite
"""
import argparse, json, re, sqlite3

def nid(s): return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    tax = json.load(open(a.taxonomy)); it = tax.get("intent_taxonomy", {})
    nodes, edges = {}, []
    def add(label, kind, parent=None):
        i = nid(label); nodes[i] = (i, label, kind, nid(parent) if parent else None)
        if parent: edges.append((i, nid(parent), "subclass_of"))
        return i
    for l1, kids in it.get("tree", {}).items():
        add(l1, "intent_l1")
        for l2 in kids: add(l2, "intent_l2", l1)
    for l1 in it.get("l1", []): add(l1, "intent_l1")
    for l2 in it.get("unassigned_l2", []): add(l2, "intent_l2")
    for kind in tax.get("entities", {}): add(kind, "entity_kind")

    c = sqlite3.connect(a.db)
    c.executescript("""
      DROP TABLE IF EXISTS graph_nodes; DROP TABLE IF EXISTS graph_edges;
      CREATE TABLE graph_nodes(id TEXT PRIMARY KEY, label TEXT, kind TEXT, parent TEXT);
      CREATE TABLE graph_edges(source TEXT, target TEXT, rel TEXT);
      CREATE INDEX idx_edges_src ON graph_edges(source);
      CREATE INDEX idx_edges_tgt ON graph_edges(target);
      CREATE INDEX idx_nodes_kind ON graph_nodes(kind);
    """)
    c.executemany("INSERT OR REPLACE INTO graph_nodes VALUES(?,?,?,?)", list(nodes.values()))
    c.executemany("INSERT INTO graph_edges VALUES(?,?,?)", edges)
    c.commit()
    from collections import Counter
    kinds = Counter(n[2] for n in nodes.values())
    print(f"graph -> {a.db}: {len(nodes)} nodes {dict(kinds)}, {len(edges)} edges (subclass_of)")
    c.close()

if __name__ == "__main__":
    main()
