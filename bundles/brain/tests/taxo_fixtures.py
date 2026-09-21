"""Synthetic taxonomy + tagged store shared by the taxonomy workbench tests."""
import copy
import json
import os
import sqlite3

_TAX = {
    "goal": "test",
    "intent_taxonomy": {
        "l1": ["Billing & Payments", "Billing & Payments Admin", "Delivery & Pickup", "Transform"],
        "tree": {"Billing & Payments": ["Duplicate Charge", "Refunds"],
                 "Billing & Payments Admin": [],
                 "Delivery & Pickup": ["Track Delivery", "Proof of Delivery"],
                 "Transform": []},
        "unassigned_l2": []},
    "entities": {"branch": ["Dallas"], "system": ["Genesys"]},
    "metrics": [
        {"metric": "Average Handle Time", "source_type": "computable", "grain": "branch", "n_sources": 2,
         "sources": ["a.md", "b.md"], "stated_values": ["6.2 min (June 2026)"], "definition": "talk+hold+wrap",
         "variants": ["AHT", "Average Handle Time"], "avg_confidence": 0.8},
        {"metric": "Avg Handle Time", "source_type": "stated", "grain": None, "n_sources": 1,
         "sources": ["c.md"], "stated_values": ["6 min"], "definition": None,
         "variants": ["Avg Handle Time"], "avg_confidence": 0.6},
        {"metric": "Refund Rate", "source_type": "computable", "grain": "branch", "n_sources": 1,
         "sources": ["d.md"], "stated_values": [], "definition": None,
         "variants": ["Refund Rate"], "avg_confidence": 0.7}],
    "demoted": [["Office relocation", 3]],
    "review_flags": {
        "near_duplicate_l1_groups": [["Billing & Payments", "Billing & Payments Admin"]],
        "off_axis_l1_candidates": [{"label": "Transform", "reason": "looks like a roadmap/phase label"}]},
}

# chunk id -> labels the classifier assigned. L2 tags roll up to their L1, as classify_write does.
TAGS = {1: ["Duplicate Charge"], 2: ["Refunds"], 3: ["Billing & Payments Admin"],
        4: ["Billing & Payments Admin", "Refunds"], 5: ["Track Delivery"],
        6: ["Proof of Delivery"], 7: ["Transform"], 8: []}


def taxonomy(version=None):
    t = copy.deepcopy(_TAX)
    if version is not None:
        t["version"] = version
    return t


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def tagged_store(db_path, tax, tags=None):
    """Graph nodes from `tax` (build_graph's own add_tree), chunks tagged like classify_write."""
    import build_graph as G
    c = sqlite3.connect(db_path)
    c.executescript(
        "CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, title TEXT, text TEXT);"
        "CREATE TABLE chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT);"
        "CREATE TABLE graph_nodes(id TEXT PRIMARY KEY, label TEXT, kind TEXT, parent TEXT);"
        "CREATE TABLE graph_edges(source TEXT, target TEXT, rel TEXT);")
    nodes, edges = {}, []
    G.add_tree(tax["intent_taxonomy"], nodes, edges, "intent_l1", "intent_l2")
    for kind in tax.get("entities", {}):
        nodes[G.nid(kind)] = (G.nid(kind), kind, "entity_kind", None)
    c.executemany("INSERT INTO graph_nodes VALUES(?,?,?,?)", list(nodes.values()))
    c.executemany("INSERT INTO graph_edges VALUES(?,?,?)", edges)
    for cid, labels in (tags or TAGS).items():
        c.execute("INSERT INTO chunks VALUES(?,?,?,?)",
                  (cid, f"doc{cid}.md", f"Section {cid}", f"chunk {cid} about {' and '.join(labels) or 'nothing'}"))
        put = set()
        for lbl in labels:
            i = G.nid(lbl)
            _, label, kind, parent = nodes[i]
            rows = [(i, label, kind)]
            if kind == "intent_l2" and parent:
                rows.append((parent, nodes[parent][1], "intent_l1"))
            for cat, lab, k in rows:
                if cat in put:
                    continue
                put.add(cat)
                c.execute("INSERT INTO chunk_topics VALUES(?,?,?,?)", (cid, cat, lab, k))
                c.execute("INSERT INTO graph_edges VALUES(?,?,?)", (f"chunk:{cid}", cat, "about"))
    c.commit()
    c.close()


def tag_rows(db_path):
    c = sqlite3.connect(db_path)
    rows = sorted(c.execute("SELECT chunk_id, category_id FROM chunk_topics"))
    c.close()
    return rows
