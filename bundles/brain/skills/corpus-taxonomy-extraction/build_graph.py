#!/usr/bin/env python3
"""Write the taxonomy as a graph (node/edge tables) into the knowledge SQLite —
the deterministic replacement for a knowledge-graph engine's entity graph. Query it
with plain SQL JOINs / recursive CTEs (no separate graph DB, no server).

Tables:
  graph_nodes(id TEXT PK, label TEXT, kind TEXT, parent TEXT)   -- kind: intent_l1|intent_l2|entity_kind|capability_l1|capability_l2
  graph_edges(source TEXT, target TEXT, rel TEXT)               -- rel: subclass_of | addressed_by

Built from taxonomy_v0.json (intent L1/L2 hierarchy + entity kinds). Lives in the
same .sqlite as chunks/fts/vec and facts, so one file is the whole knowledge store.

A SECOND taxonomy (capabilities / vision pillars, induced by a second
corpus-taxonomy-extraction pass) can be layered in with --capabilities, and problems
linked to the capabilities that address them with --links (intent --addressed_by-->
capability). Then a problem→capability traceability question resolves as a graph JOIN
instead of narrative synthesis.

Usage: build_graph.py --taxonomy taxonomy_v0.json --db knowledge.sqlite
       [--capabilities capabilities.json] [--links addressed_by.json]
"""
import argparse, json, re, sqlite3

def nid(s): return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "n"

# Capability ids live in a separate id namespace from intents: both taxonomies are induced
# from the SAME corpus, so labels collide (e.g. "Proactive Communications" is both an intent
# L1 and a vision pillar). Without this prefix the capability node would overwrite the intent
# node (flipping its kind and orphaning its about/chunk_topics edges), and an intent→capability
# link on a shared label would become a self-loop. `nid(intent)==CAP_PREFIX+nid(capability)`
# can never collide, so intent and capability stay distinct nodes.
CAP_PREFIX = "cap__"

def add_tree(it, nodes, edges, kind_l1, kind_l2, id_prefix=""):
    """Ingest an intent-taxonomy-shaped block ({tree, l1, unassigned_l2}) as nodes +
    subclass_of edges, tagged with the given L1/L2 kinds. `id_prefix` namespaces the node
    ids (empty for intents, CAP_PREFIX for capabilities) so the two taxonomies never share
    an id even when their labels collide."""
    def add(label, kind, parent=None):
        i = id_prefix + nid(label)
        nodes[i] = (i, label, kind, (id_prefix + nid(parent)) if parent else None)
        if parent: edges.append((i, id_prefix + nid(parent), "subclass_of"))
        return i
    for l1, kids in it.get("tree", {}).items():
        add(l1, kind_l1)
        for l2 in kids: add(l2, kind_l2, l1)
    for l1 in it.get("l1", []): add(l1, kind_l1)
    for l2 in it.get("unassigned_l2", []): add(l2, kind_l2)

def build_addressed_by(links_doc, nodes, cap_prefix=CAP_PREFIX):
    """Resolve intent→capability link pairs to `addressed_by` edges. Accepts
    {"addressed_by": [{"intent","capability"}, ...]} or a bare list of such pairs.
    The intent is matched by nid, the capability by the namespaced id; a pair whose intent
    OR capability is not a known node — or that would be a self-loop — is skipped (returned
    separately) so the graph never carries a dangling or degenerate edge."""
    pairs = links_doc.get("addressed_by", links_doc) if isinstance(links_doc, dict) else links_doc
    edges, skipped = [], []
    for p in pairs or []:
        si, tc = nid(p["intent"]), cap_prefix + nid(p["capability"])
        if si in nodes and tc in nodes and si != tc:
            edges.append((si, tc, "addressed_by"))
        else:
            skipped.append((p.get("intent"), p.get("capability")))
    return edges, skipped

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--capabilities", help="second taxonomy (capabilities/pillars); reads its "
                    "capability_taxonomy or intent_taxonomy block -> capability_l1/l2 nodes")
    ap.add_argument("--links", help="intent->capability link pairs -> addressed_by edges")
    a = ap.parse_args()
    tax = json.load(open(a.taxonomy)); it = tax.get("intent_taxonomy", {})
    nodes, edges = {}, []
    add_tree(it, nodes, edges, "intent_l1", "intent_l2")
    for kind in tax.get("entities", {}):
        nodes[nid(kind)] = (nid(kind), kind, "entity_kind", None)

    # optional second taxonomy: capabilities / vision pillars
    if a.capabilities:
        ctax = json.load(open(a.capabilities))
        ct = ctax.get("capability_taxonomy") or ctax.get("intent_taxonomy", {})
        add_tree(ct, nodes, edges, "capability_l1", "capability_l2", id_prefix=CAP_PREFIX)

    # optional cross-links: intent --addressed_by--> capability
    addressed, skipped = ([], [])
    if a.links:
        addressed, skipped = build_addressed_by(json.load(open(a.links)), nodes)

    c = sqlite3.connect(a.db)
    # Non-destructive: this script OWNS the taxonomy (nodes) and the subclass_of +
    # addressed_by edges, but must NOT touch the 'about' edges (chunk↔vertex) that
    # classify_write manages.
    c.executescript("""
      CREATE TABLE IF NOT EXISTS graph_nodes(id TEXT PRIMARY KEY, label TEXT, kind TEXT, parent TEXT);
      CREATE TABLE IF NOT EXISTS graph_edges(source TEXT, target TEXT, rel TEXT);
      CREATE INDEX IF NOT EXISTS idx_edges_src ON graph_edges(source);
      CREATE INDEX IF NOT EXISTS idx_edges_tgt ON graph_edges(target);
      CREATE INDEX IF NOT EXISTS idx_nodes_kind ON graph_nodes(kind);
    """)
    c.execute("DELETE FROM graph_edges WHERE rel IN ('subclass_of','addressed_by')")  # rebuild owned edges
    c.executemany("INSERT OR REPLACE INTO graph_nodes VALUES(?,?,?,?)", list(nodes.values()))
    c.executemany("INSERT INTO graph_edges VALUES(?,?,?)", edges + addressed)
    # prune nodes that vanished from the taxonomy, with their dependent rows (no dangling refs)
    keep = set(nodes)
    have_ct = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_topics'").fetchone())
    removed = [r[0] for r in c.execute("SELECT id FROM graph_nodes")] if keep else []
    removed = [nidv for nidv in removed if nidv not in keep]
    for nidv in removed:
        c.execute("DELETE FROM graph_nodes WHERE id=?", (nidv,))
        c.execute("DELETE FROM graph_edges WHERE rel='about' AND target=?", (nidv,))
        if have_ct:
            c.execute("DELETE FROM chunk_topics WHERE category_id=?", (nidv,))
    c.commit()
    from collections import Counter
    kinds = Counter(n[2] for n in nodes.values())
    print(f"graph -> {a.db}: {len(nodes)} nodes {dict(kinds)}, {len(edges)} subclass_of"
          + (f", {len(addressed)} addressed_by" if addressed else "") + " edges"
          + (f"; pruned {len(removed)} vanished node(s) + their tags/about-edges" if removed else "")
          + " (about edges preserved)")
    if skipped:
        print(f"⚠️  skipped {len(skipped)} addressed_by link(s) with an unknown intent/capability: "
              + ", ".join(f"{i!r}->{c_!r}" for i, c_ in skipped[:6]) + ("…" if len(skipped) > 6 else ""))
    c.close()

if __name__ == "__main__":
    main()
