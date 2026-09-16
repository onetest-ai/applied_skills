"""_ensure_schema must raise ValueError when dim mismatches an existing chunks_vec.

The conftest autouse fixture replaces _ensure_schema with a stub on platforms
where enable_load_extension is unavailable (all macOS system-Python builds).
To test the *real* guard logic we must:

1. Import the real function before pytest installs the monkeypatch.
2. Drive the guard ourselves against an in-memory SQLite connection that
   contains a chunks_vec table whose DDL contains the float[N] annotation
   (i.e. the real CREATE VIRTUAL TABLE DDL, or a plain-table simulation that
   preserves the annotation).

The guard uses `sqlite_master` to probe the stored dimension — it falls through
(does nothing) when no `float[N]` DDL is found, so the stub's `embedding BLOB`
schema will never trigger a false raise.
"""
import sqlite3
import sys
from pathlib import Path
import pytest

# Insert module directory before collection so knowledge_index can be imported.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import knowledge_index as _ki_module  # noqa: E402

# Capture a reference to the *real* function BEFORE the conftest monkeypatch
# runs. This reference is a plain Python callable, unaffected by later
# monkeypatch.setattr() calls on the module.
_real_ensure_schema = _ki_module._ensure_schema


def _make_db_with_dim(dim: int) -> sqlite3.Connection:
    """Return an in-memory connection where chunks_vec already exists at *dim*.

    We simulate the real DDL by creating a regular table with the same column
    expression as the virtual table: ``embedding float[{dim}]``.  SQLite stores
    the expression verbatim in sqlite_master, so the regex probe works.

    The stub created by the conftest has ``embedding BLOB`` — no ``float[N]`` —
    so that path is never confused with this fixture.
    """
    c = sqlite3.connect(":memory:")
    # Minimal schema expected by _ensure_schema (executescript portion).
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
    # Create chunks_vec with the float[N] annotation in the DDL so the regex
    # probe inside the guard can read it.
    c.execute(
        f"CREATE TABLE IF NOT EXISTS chunks_vec("
        f"rowid INTEGER PRIMARY KEY, embedding float[{dim}])"
    )
    return c


def test_dim_mismatch_raises_value_error():
    """Calling _ensure_schema a second time with a different dim must raise ValueError."""
    db = _make_db_with_dim(4)
    with pytest.raises(ValueError, match="existing index has dim=4"):
        _real_ensure_schema(db, dim=8)


def test_same_dim_does_not_raise():
    """Calling _ensure_schema a second time with the SAME dim is idempotent."""
    db = _make_db_with_dim(4)
    _real_ensure_schema(db, dim=4)  # must not raise


def test_no_ddl_float_annotation_does_not_raise():
    """If chunks_vec exists but has no float[N] DDL (stub env), guard skips the check."""
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
    # Stub-style table: no float[N] annotation.
    c.execute("CREATE TABLE IF NOT EXISTS chunks_vec(rowid INTEGER PRIMARY KEY, embedding BLOB)")
    # Should not raise regardless of requested dim.
    _real_ensure_schema(c, dim=768)
