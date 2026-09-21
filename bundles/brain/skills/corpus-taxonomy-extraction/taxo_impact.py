#!/usr/bin/env python3
"""Impact preview for a taxonomy changeset.

Computed by running the REAL migrations (graph_migrate.run) against an in-memory copy of the
tag tables, so what the reviewer is shown and what build_graph later does cannot disagree.
The source connection is only read.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_migrate as GM  # noqa: E402
from taxo_ops import apply_ops  # noqa: E402

ZERO = {"tags_repointed": 0, "tags_deleted": 0, "tags_deduplicated": 0,
        "chunks_reclassified": 0, "chunks_left_untagged_until_reclassify": 0}


def _scratch(src):
    m = sqlite3.connect(":memory:")
    m.execute("CREATE TABLE chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT)")
    m.execute("CREATE TABLE graph_edges(source TEXT, target TEXT, rel TEXT)")
    if GM.has_table(src, "chunk_topics"):
        m.executemany("INSERT INTO chunk_topics VALUES(?,?,?,?)",
                      src.execute("SELECT chunk_id, category_id, category_label, kind FROM chunk_topics"))
    if GM.has_table(src, "graph_edges"):
        m.executemany("INSERT INTO graph_edges VALUES(?,?,?)",
                      src.execute("SELECT source, target, rel FROM graph_edges WHERE rel='about'"))
    return m


def _tagged(c):
    return {r[0] for r in c.execute("SELECT DISTINCT chunk_id FROM chunk_topics")}


def impact(src, tax, ops):
    """Raises taxo_ops.ChangesetError when the changeset is invalid."""
    _, migs, _ = apply_ops(tax, ops)
    if src is None:
        return dict(ZERO)
    m = _scratch(src)
    before = _tagged(m)
    reclass, _, stats = GM.run(m, migs)
    after = _tagged(m)
    m.close()
    return {**ZERO, **stats, "chunks_reclassified": len(reclass),
            "chunks_left_untagged_until_reclassify": len(before - after)}


def delta(src, tax, prior_ops, op):
    with_op = impact(src, tax, list(prior_ops) + [op])
    without = impact(src, tax, list(prior_ops))
    return {k: with_op[k] - without[k] for k in ZERO}
