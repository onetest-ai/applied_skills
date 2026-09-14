"""_ensure_schema must create indexes for known hot-path columns."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.conftest import make_db
import knowledge_index as K


def _indexes(db):
    return {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }


def test_chunks_source_index_exists_after_ensure_schema():
    db = make_db()
    K._ensure_schema(db, dim=4)
    assert "idx_chunks_source" in _indexes(db), \
        "_ensure_schema must create idx_chunks_source on chunks(source)"


def test_chunks_status_index_exists_after_ensure_schema():
    db = make_db()
    K._ensure_schema(db, dim=4)
    assert "idx_chunks_status" in _indexes(db), \
        "_ensure_schema must create idx_chunks_status on chunks(status)"


def test_related_related_id_index_exists_after_ensure_related_schema():
    db = make_db()
    K._ensure_related_schema(db)
    assert "idx_rel_related" in _indexes(db), \
        "_ensure_related_schema must create idx_rel_related on related(related_id)"
