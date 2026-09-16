"""Session-level fixtures for knowledge-index tests.

The ``connect()`` function in knowledge_index calls
``sqlite3.Connection.enable_load_extension`` and uses the ``vec0`` virtual table
module from sqlite_vec.  Both require the sqlite_vec shared library which is
unavailable in many CPython builds (macOS system Python, most CI images).

The vector search functionality is NOT exercised by any test in this package
(tests use ``monkeypatch`` to stub ``embed()`` and do not verify ANN results).
This conftest:

1. Injects a no-op ``sqlite_vec`` stub into ``sys.modules`` before collection.
2. Monkey-patches ``knowledge_index.connect`` to skip ``enable_load_extension``.
3. Monkey-patches ``knowledge_index._ensure_schema`` to replace the
   ``CREATE VIRTUAL TABLE ... USING vec0`` with a plain ``chunks_vec`` table
   so all non-vector SQL paths work without the extension.
4. Monkey-patches ``knowledge_index.build_related`` to skip the vec0 ANN query
   (keeps existing non-SIMILAR edges intact, returns edge count).
"""
import sqlite3
import sys
import types

import pytest


def make_db():
    """Return a fresh in-memory SQLite connection with the minimal schema.

    Used by tests that verify schema structure (e.g., index creation).
    The connection is not patched — it uses real SQLite (no vec0 extension needed).
    """
    c = sqlite3.connect(":memory:")
    c.executescript("""
      CREATE TABLE IF NOT EXISTS chunks(
        id INTEGER PRIMARY KEY, source TEXT, ord INT,
        title TEXT, text TEXT, sha TEXT, image TEXT);
      CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
      CREATE TABLE IF NOT EXISTS documents(
        source TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL,
        indexed_at TEXT NOT NULL);
    """)
    # Plain table substituting vec0 virtual table.
    c.execute("""CREATE TABLE IF NOT EXISTS chunks_vec(
        rowid INTEGER PRIMARY KEY, embedding BLOB)""")
    return c


def _install_sqlite_vec_stub():
    if "sqlite_vec" in sys.modules:
        return
    # Only install the stub when the real sqlite_vec is unavailable.
    try:
        import sqlite_vec as _real  # noqa: F401
        return  # real extension loaded — no stub needed
    except ImportError:
        pass
    stub = types.ModuleType("sqlite_vec")
    stub.load = lambda con: None
    stub.serialize_float32 = lambda v: bytes(4 * len(v))
    sys.modules["sqlite_vec"] = stub


# Install immediately so module-level ``import sqlite_vec`` in knowledge_index
# sees the stub during pytest collection.
_install_sqlite_vec_stub()


@pytest.fixture(autouse=True)
def _patch_knowledge_index_for_no_ext(monkeypatch):
    """Patch connect(), _ensure_schema(), and build_related() when vec0 is unavailable."""
    try:
        import knowledge_index as ki
    except ImportError:
        yield
        return

    _probe = sqlite3.connect(":memory:")
    _has_ext = hasattr(_probe, "enable_load_extension")
    _probe.close()
    if _has_ext:
        yield
        return

    # --- patch connect() ---
    def _safe_connect(db):
        return sqlite3.connect(db)

    monkeypatch.setattr(ki, "connect", _safe_connect)

    # --- patch _ensure_schema() ---
    def _safe_ensure_schema(c, dim):
        c.executescript("""
          CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT,
            sha TEXT, image TEXT);
          CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
          CREATE TABLE IF NOT EXISTS documents(
            source TEXT PRIMARY KEY, content_hash TEXT NOT NULL, indexed_at TEXT NOT NULL);
        """)
        # Plain table substituting vec0 virtual table.
        c.execute("""CREATE TABLE IF NOT EXISTS chunks_vec(
            rowid INTEGER PRIMARY KEY, embedding BLOB)""")
        cols = {r[1] for r in c.execute("PRAGMA table_info(chunks)")}
        additions = {
            "sha": "TEXT", "image": "TEXT", "embedding_content_hash": "TEXT",
            "created_at": "TEXT", "event_date": "TEXT", "valid_from": "TEXT",
            "valid_to": "TEXT", "status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
            "parent_heading": "TEXT", "breadcrumb_path": "TEXT", "speaker": "TEXT",
        }
        for col, decl in additions.items():
            if col not in cols:
                c.execute(f"ALTER TABLE chunks ADD COLUMN {col} {decl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status)")

    monkeypatch.setattr(ki, "_ensure_schema", _safe_ensure_schema)

    # --- patch build_related() ---
    # The vec0 ANN query cannot run without the extension.  The tests that call
    # build_related() are checking that TYPED (non-SIMILAR) edges survive a
    # rebuild — they don't care about the SIMILAR edges themselves.  We skip
    # the similarity search and only delete stale SIMILAR edges, preserving the
    # behaviour under test.
    def _safe_build_related(c, k=6, min_score=0.55, cross_doc=True, commit=True):
        ki._ensure_related_schema(c)
        # _ensure_related_schema now creates both idx_rel_chunk and idx_rel_related
        c.execute("DELETE FROM related WHERE edge_type='SIMILAR'")
        if commit:
            c.commit()
        return c.execute("SELECT COUNT(*) FROM related").fetchone()[0]

    monkeypatch.setattr(ki, "build_related", _safe_build_related)

    # --- patch search() ---
    # The full search() uses vec_distance_cosine (vec0-specific).  We replace
    # it with a FTS-only implementation for the test environment.
    import re as _re

    def _safe_search(c, model, query, k, as_of=None, latest_only=False,
                     source_contains=None, tag=None, tag_boost=None):
        chunk_cols = {row[1] for row in c.execute("PRAGMA table_info(chunks)")}
        clauses, params = ki._search_filter(as_of, latest_only, source_contains, tag)
        where = (" AND " + " AND ".join(clauses)) if clauses else ""
        toks = [t for t in _re.findall(r"[A-Za-z0-9]+", query.lower()) if len(t) > 2]
        fts_q = " OR ".join(toks) if toks else '""'
        fts = [r[0] for r in c.execute(
            "SELECT chunks_fts.rowid FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
            "WHERE chunks_fts MATCH ?" + where + " ORDER BY bm25(chunks_fts) LIMIT ?",
            (fts_q, *params, ki.POOL))]
        score = {rid: 0.6 / (ki.RRF_K + rank) for rank, rid in enumerate(fts, 1)}
        # Apply tag_boost: add RRF bonus for chunks matching the boost tag
        if tag_boost:
            has_ct = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_topics'"
            ).fetchone()
            if has_ct:
                for rid in list(score):
                    if c.execute(
                        "SELECT 1 FROM chunk_topics WHERE chunk_id=? AND category_label=? LIMIT 1",
                        (rid, tag_boost)
                    ).fetchone():
                        score[rid] += ki.W_VEC / (ki.RRF_K + 1)
        top = sorted(score, key=score.get, reverse=True)[:k]
        out = []
        for rid in top:
            optional_cols = ("event_date", "status", "parent_heading", "breadcrumb_path",
                             "speaker", "valid_from", "valid_to")
            projection = ",".join(
                col if col in chunk_cols else f"NULL AS {col}" for col in optional_cols)
            row = c.execute(
                f"SELECT source,ord,title,text,{projection} FROM chunks WHERE id=?",
                (rid,)).fetchone()
            src, ordv, title, txt, event_date, status, parent, breadcrumb, speaker, valid_from, valid_to = row
            out.append({"id": rid, "score": round(score[rid], 5), "source": src,
                        "ord": ordv, "title": title, "text": txt,
                        "event_date": event_date, "status": status or "ACTIVE",
                        "parent_heading": parent or "", "breadcrumb_path": breadcrumb or "",
                        "speaker": speaker or "", "valid_from": valid_from,
                        "valid_to": valid_to})
        return {"query": query, "fts_hits": len(fts), "vec_hits": 0, "results": out}

    monkeypatch.setattr(ki, "search", _safe_search)

    # Update the serialize_float32 stub so inserts don't crash.
    sys.modules["sqlite_vec"].serialize_float32 = lambda v: bytes(4 * len(v))

    yield
