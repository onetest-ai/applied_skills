"""FTS must stay in sync when speaker or breadcrumb changes without body change."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import knowledge_index as K


def _fake_embed(_model, texts):
    return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _fts_text(c, chunk_id):
    row = c.execute("SELECT text FROM chunks_fts WHERE rowid=?", (chunk_id,)).fetchone()
    return row[0] if row else ""


def _write_md(path, speaker, body="Stable body text."):
    path.write_text(
        "# Section\n\n<!-- speaker: {s} -->\n\n{b}\n".format(s=speaker, b=body),
        encoding="utf-8",
    )


def test_fts_updated_when_only_speaker_changes(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    doc = corpus / "doc.md"
    _write_md(doc, "Alice")

    db = K.connect(str(tmp_path / "knowledge.sqlite"))

    with patch("knowledge_index.embed", _fake_embed):
        K.index_docs(db, "test-model", str(corpus), ["doc.md"], dim=4, max_chars=8000)

    row = db.execute("SELECT id FROM chunks WHERE speaker='Alice'").fetchone()
    assert row, "chunk with Alice not indexed"
    cid = row[0]
    assert "Alice" in _fts_text(db, cid), "FTS must contain Alice after first index"

    # Now update only the speaker — body is unchanged
    _write_md(doc, "Bob")

    with patch("knowledge_index.embed", _fake_embed):
        K.index_docs(db, "test-model", str(corpus), ["doc.md"], dim=4, max_chars=8000)

    row2 = db.execute("SELECT id FROM chunks WHERE speaker='Bob'").fetchone()
    assert row2, "chunk with Bob not found after re-index"
    cid2 = row2[0]
    assert "Bob" in _fts_text(db, cid2), "FTS must contain Bob after speaker-only re-index"
    assert "Alice" not in _fts_text(db, cid2), "FTS must not still contain Alice"
