#!/usr/bin/env python3
"""Write the taxonomy as a graph (node/edge tables) into the knowledge SQLite —
the deterministic replacement for a knowledge-graph engine's entity graph. Query it
with plain SQL JOINs / recursive CTEs (no separate graph DB, no server).

Tables:
  graph_nodes(id TEXT PK, label TEXT, kind TEXT, parent TEXT)   -- kind: intent_l1|intent_l2|entity_kind|capability_l1|capability_l2
  graph_edges(source TEXT, target TEXT, rel TEXT)               -- rel: subclass_of | addressed_by
  graph_aliases(alias_id TEXT PK, node_id TEXT)                  -- old labels of renamed/merged nodes
  meta: taxonomy_version, taxonomy_sha256                        -- which taxonomy the tags are migrated to

Built from taxonomy_v0.json (intent L1/L2 hierarchy + entity kinds). Lives in the
same .sqlite as chunks/fts/vec and facts, so one file is the whole knowledge store.

A SECOND taxonomy (capabilities / vision pillars, induced by a second
corpus-taxonomy-extraction pass) can be layered in with --capabilities, and problems
linked to the capabilities that address them with --links (intent --addressed_by-->
capability). Then a problem→capability traceability question resolves as a graph JOIN
instead of narrative synthesis.

Reviewed renames/merges/removals (taxonomy_merge.py --review) are recorded as migrations in
the taxonomy history; this script runs the ones newer than meta.taxonomy_version before it
prunes, so tags move instead of vanishing. Building from a version older than the sibling
current.json, or older than the store's meta.taxonomy_version, that would prune nodes is
refused unless --yes-prune.

Usage: build_graph.py --taxonomy taxonomy_v0.json --db knowledge.sqlite
       [--capabilities capabilities.json] [--links addressed_by.json]
"""
import argparse, hashlib, json, os, re, sqlite3, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_migrate as GM  # noqa: E402

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


GUARDED_KINDS = ("intent_l1", "intent_l2", "entity_kind")


def _is_current(tax_path, raw):
    """(not_current, current_version): not_current when a sibling current.json exists and differs."""
    cur = os.path.join(os.path.dirname(os.path.abspath(tax_path)), "current.json")
    if not os.path.exists(cur) or os.path.abspath(cur) == os.path.abspath(tax_path):
        return False, None
    cur_raw = open(cur, "rb").read()
    if hashlib.sha256(cur_raw).digest() == hashlib.sha256(raw).digest():
        return False, None
    return True, json.loads(cur_raw).get("version")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--capabilities", help="second taxonomy (capabilities/pillars); reads its "
                    "capability_taxonomy or intent_taxonomy block -> capability_l1/l2 nodes")
    ap.add_argument("--links", help="intent->capability link pairs -> addressed_by edges")
    ap.add_argument("--yes-prune", action="store_true",
                    help="build from a taxonomy that is not current.json even though nodes and their tags "
                         "will be pruned (intentional rollback)")
    ap.add_argument("--reclassify-out",
                    help="chunk ids a migration sends back to classification "
                         "(default: <taxonomy dir>/work/reclassify.json)")
    a = ap.parse_args()
    raw = open(a.taxonomy, "rb").read()
    tax = json.loads(raw)
    it = tax.get("intent_taxonomy", {})
    nodes, edges = {}, []
    add_tree(it, nodes, edges, "intent_l1", "intent_l2")
    for kind in tax.get("entities", {}):
        nodes[nid(kind)] = (nid(kind), kind, "entity_kind", None)

    node_desc = {i: (tax.get("descriptions") or {}).get(v[1]) for i, v in nodes.items()}

    if a.capabilities:
        ctax = json.load(open(a.capabilities))
        ct = ctax.get("capability_taxonomy") or ctax.get("intent_taxonomy", {})
        add_tree(ct, nodes, edges, "capability_l1", "capability_l2", id_prefix=CAP_PREFIX)
        cdesc = ctax.get("descriptions") or {}
        node_desc.update({i: cdesc.get(v[1]) for i, v in nodes.items() if i not in node_desc})

    addressed, skipped = ([], [])
    if a.links:
        addressed, skipped = build_addressed_by(json.load(open(a.links)), nodes)

    c = sqlite3.connect(a.db)
    # Non-destructive: this script OWNS the taxonomy (nodes), the subclass_of + addressed_by
    # edges and graph_aliases, but must NOT touch 'about' edges except through a reviewed
    # migration recorded in the taxonomy history.
    c.executescript("""
      CREATE TABLE IF NOT EXISTS graph_nodes(id TEXT PRIMARY KEY, label TEXT, kind TEXT, parent TEXT);
      CREATE TABLE IF NOT EXISTS graph_edges(source TEXT, target TEXT, rel TEXT);
      CREATE TABLE IF NOT EXISTS graph_aliases(alias_id TEXT PRIMARY KEY, node_id TEXT);
      CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
      CREATE INDEX IF NOT EXISTS idx_edges_src ON graph_edges(source);
      CREATE INDEX IF NOT EXISTS idx_edges_tgt ON graph_edges(target);
      CREATE INDEX IF NOT EXISTS idx_nodes_kind ON graph_nodes(kind);
    """)
    if "description" not in {r[1] for r in c.execute("PRAGMA table_info(graph_nodes)")}:
        c.execute("ALTER TABLE graph_nodes ADD COLUMN description TEXT")
    store_version = GM.read_version(c)
    try:
        start = GM.resolve_start(c, tax)
    except GM.LegacyStoreError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        c.close()
        sys.exit(2)
    reclass, explained, mstats = {}, set(), Counter()
    for _, migs in GM.pending_migrations(tax, start):
        r, e, s = GM.run(c, migs)
        for k, why in r.items():
            reclass.setdefault(k, why)
        explained |= e
        mstats.update(s)

    c.execute("DELETE FROM graph_edges WHERE rel IN ('subclass_of','addressed_by')")
    c.executemany("INSERT OR REPLACE INTO graph_nodes(id,label,kind,parent,description) VALUES(?,?,?,?,?)",
                  [(*v, node_desc.get(k)) for k, v in nodes.items()])
    c.executemany("INSERT INTO graph_edges VALUES(?,?,?)", edges + addressed)
    keep = set(nodes)
    have_ct = GM.has_table(c, "chunk_topics")
    existing = c.execute("SELECT id, kind FROM graph_nodes").fetchall()
    removed = [i for i, _ in existing if i not in keep] if keep else []
    unexplained = [i for i, k in existing if i not in keep and k in GUARDED_KINDS and i not in explained]
    if unexplained:
        n_tags = 0
        if have_ct:
            q = ",".join("?" * len(unexplained))
            n_tags = c.execute(f"SELECT COUNT(*) FROM chunk_topics WHERE category_id IN ({q})", unexplained).fetchone()[0]
        sample = ", ".join(unexplained[:6]) + ("…" if len(unexplained) > 6 else "")
        not_current, cur_version = _is_current(a.taxonomy, raw)
        older = store_version is not None and (tax.get("version") or 0) < store_version
        if (not_current or older) and not a.yes_prune:
            c.rollback()
            c.close()
            why = (f"current.json is version {cur_version}" if not_current
                   else f"the store is already at taxonomy version {store_version}")
            print(f"REFUSED: building from {os.path.basename(a.taxonomy)} (version {tax.get('version') or 0}) but "
                  f"{why}; {len(unexplained)} node(s) and {n_tags} tag(s) exist only in the store and would be "
                  f"pruned: {sample}. Build from taxonomy/current.json, or pass --yes-prune for an intentional "
                  f"rollback.", file=sys.stderr)
            sys.exit(3)
        print(f"⚠️  {len(unexplained)} node(s) vanished without a review op (hand edit?) — pruning them and "
              f"{n_tags} tag(s): {sample}", file=sys.stderr)
    for nidv in removed:
        c.execute("DELETE FROM graph_nodes WHERE id=?", (nidv,))
        c.execute("DELETE FROM graph_edges WHERE rel='about' AND target=?", (nidv,))
        if have_ct:
            c.execute("DELETE FROM chunk_topics WHERE category_id=?", (nidv,))
    c.execute("DELETE FROM graph_aliases")
    c.executemany("INSERT OR REPLACE INTO graph_aliases VALUES(?,?)",
                  [(nid(al), nid(canon)) for al, canon in (tax.get("aliases") or {}).items()
                   if nid(al) not in nodes and nid(canon) in nodes])
    version = tax.get("version") or 0
    GM.write_version(c, version, hashlib.sha256(raw).hexdigest())
    if reclass:
        out = a.reclassify_out or os.path.join(os.path.dirname(os.path.abspath(a.taxonomy)), "work", "reclassify.json")
        try:
            GM.write_reclassify(out, version, reclass)
        except (OSError, ValueError) as e:
            c.rollback()
            c.close()
            print(f"ERROR: could not queue {len(reclass)} chunk(s) for reclassification ({e}); "
                  f"the tag migration was NOT committed — fix the problem and rebuild.", file=sys.stderr)
            sys.exit(1)
    c.commit()
    kinds = Counter(n[2] for n in nodes.values())
    print(f"graph -> {a.db}: {len(nodes)} nodes {dict(kinds)}, {len(edges)} subclass_of"
          + (f", {len(addressed)} addressed_by" if addressed else "") + " edges"
          + (f"; pruned {len(removed)} vanished node(s) + their tags/about-edges" if removed else "")
          + " (about edges preserved)")
    if any(mstats.values()) or reclass:
        print(f"migrated tags to taxonomy v{version}: {dict(mstats)}; {len(reclass)} chunk(s) queued for "
              f"reclassification")
    if skipped:
        print(f"⚠️  skipped {len(skipped)} addressed_by link(s) with an unknown intent/capability: "
              + ", ".join(f"{i!r}->{c_!r}" for i, c_ in skipped[:6]) + ("…" if len(skipped) > 6 else ""))
    c.close()


if __name__ == "__main__":
    main()
