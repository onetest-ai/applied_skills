#!/usr/bin/env python3
"""Tag migrations: turn taxonomy history ops into chunk_topics / about-edge changes.

build_graph.py runs these inside its transaction, BEFORE it upserts nodes and prunes, so a
reviewed rename or merge moves its tags instead of losing them. The store remembers which
taxonomy version it has in meta.taxonomy_version; each version's migrations run once.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxo_io import atomic_write_bytes  # noqa: E402

META_VERSION, META_SHA = "taxonomy_version", "taxonomy_sha256"


class LegacyStoreError(RuntimeError):
    pass


def has_table(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def chunk_coverage(c):
    """Classification coverage of the store, or None without a chunks table.

    `untagged` counts chunks with neither a chunk_topics row nor a `no_topic` verdict — a
    no-topic chunk (filler, boilerplate, off-goal) was classified and is not a coverage gap.
    `no_topic` counts only verdicts whose chunk still exists. Every review-side view of the
    untagged count (health, the review app, plan stats) reads it from here so they agree."""
    if c is None or not has_table(c, "chunks"):
        return None
    has_t, has_v = has_table(c, "chunk_topics"), has_table(c, "chunk_verdicts")
    total = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    tagged = c.execute("SELECT COUNT(DISTINCT chunk_id) FROM chunk_topics").fetchone()[0] if has_t else 0
    untagged = c.execute(
        "SELECT COUNT(*) FROM chunks c WHERE 1=1"
        + (" AND NOT EXISTS (SELECT 1 FROM chunk_topics t WHERE t.chunk_id = c.id)" if has_t else "")
        + (" AND NOT EXISTS (SELECT 1 FROM chunk_verdicts v WHERE v.chunk_id = c.id AND v.verdict = 'no_topic')"
           if has_v else "")).fetchone()[0]
    no_topic = (c.execute("SELECT COUNT(*) FROM chunk_verdicts v WHERE v.verdict = 'no_topic' "
                          "AND EXISTS (SELECT 1 FROM chunks c WHERE c.id = v.chunk_id)").fetchone()[0]
                if has_v else 0)
    return {"total": total, "tagged": tagged, "untagged": untagged, "no_topic": no_topic}


def _ensure_meta(c):
    c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")


def read_version(c):
    if not has_table(c, "meta"):
        return None
    row = c.execute("SELECT value FROM meta WHERE key=?", (META_VERSION,)).fetchone()
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def write_version(c, version, sha):
    _ensure_meta(c)
    for k, v in ((META_VERSION, str(version)), (META_SHA, sha)):
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))


def pending_migrations(tax, start):
    out = []
    for h in tax.get("history") or []:
        v = h.get("version")
        if isinstance(v, int) and (start is None or v > start) and h.get("migrations"):
            out.append((v, h["migrations"]))
    return sorted(out, key=lambda x: x[0])


def _tagged(c):
    return has_table(c, "chunk_topics") and c.execute("SELECT 1 FROM chunk_topics LIMIT 1").fetchone() is not None


def resolve_start(c, tax):
    """Version the store is at. Untagged stores need no migration; a tagged store with no recorded
    version cannot be migrated safely when the history carries migrations."""
    v = read_version(c)
    if v is not None:
        return v
    if _tagged(c) and pending_migrations(tax, None):
        raise LegacyStoreError(
            "the store has tags but no meta.taxonomy_version, and the taxonomy history carries "
            "migrations. Run `taxonomy_review.py adopt --taxonomy <the version this store was built "
            "from> --db <db>` (add --meta-only if taxonomy/current.json already exists) first, then rebuild.")
    return tax.get("version") or 0


def run(c, migrations):
    reclass, explained = {}, set()
    stats = {"tags_repointed": 0, "tags_deleted": 0, "tags_deduplicated": 0}
    has_ct, has_ge = has_table(c, "chunk_topics"), has_table(c, "graph_edges")
    for m in migrations:
        kind = m["kind"]
        if kind == "reclassify_node":
            if has_ct:
                for (cid,) in c.execute("SELECT DISTINCT chunk_id FROM chunk_topics WHERE category_id=?",
                                        (m["node_id"],)).fetchall():
                    reclass.setdefault(cid, m.get("reason") or "taxonomy change")
        elif kind == "repoint":
            f, t = m["from_id"], m["to_id"]
            explained.add(f)
            if f == t:
                if has_ct:
                    c.execute("UPDATE chunk_topics SET category_label=? WHERE category_id=?", (m["label"], t))
                continue
            if has_ct:
                cur = c.execute("DELETE FROM chunk_topics WHERE category_id=? AND chunk_id IN "
                                "(SELECT chunk_id FROM chunk_topics WHERE category_id=?)", (f, t))
                stats["tags_deduplicated"] += cur.rowcount
                cur = c.execute("UPDATE chunk_topics SET category_id=?, category_label=?, kind=? WHERE category_id=?",
                                (t, m["label"], m["to_kind"], f))
                stats["tags_repointed"] += cur.rowcount
            if has_ge:
                c.execute("DELETE FROM graph_edges WHERE rel='about' AND target=? AND source IN "
                          "(SELECT source FROM graph_edges WHERE rel='about' AND target=?)", (f, t))
                c.execute("UPDATE graph_edges SET target=? WHERE rel='about' AND target=?", (t, f))
        elif kind == "delete_node":
            explained.add(m["node_id"])
            if has_ct:
                cur = c.execute("DELETE FROM chunk_topics WHERE category_id=?", (m["node_id"],))
                stats["tags_deleted"] += cur.rowcount
            if has_ge:
                c.execute("DELETE FROM graph_edges WHERE rel='about' AND target=?", (m["node_id"],))
        else:
            raise ValueError(f"unknown migration kind {kind!r}")
    return reclass, explained, stats


def write_reclassify(path, version, reclass):
    """Merge chunk ids into reclassify.json (never drops ids an earlier run left unprocessed).

    Written atomically (temp file + os.replace) in the same directory, so a crash or a full
    disk mid-write never leaves a truncated file and never looks like a successful queue —
    the caller must run this BEFORE committing the tag-deleting transaction, so a failure here
    aborts the migration instead of silently losing chunks that need reclassification."""
    data = {"version": version, "chunk_ids": [], "reasons": {}}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError(f"{path}: cannot read existing reclassify file ({e})") from e
        if not isinstance(existing, dict) or "reasons" not in existing:
            raise ValueError(f"{path}: existing reclassify file is malformed (missing 'reasons')")
        data = existing
    for cid, why in reclass.items():
        data["reasons"].setdefault(str(cid), why)
    data["chunk_ids"] = sorted(int(k) for k in data["reasons"])
    data["version"] = version
    atomic_write_bytes(path, (json.dumps(data, indent=1) + "\n").encode("utf-8"))
