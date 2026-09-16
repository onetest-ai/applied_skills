"""tag_boost: tagged chunks get RRF bonus but untagged chunks are NOT excluded."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import knowledge_index as K


def _fake_embed(_model, texts):
    # identical embeddings so vec ranking is a tie — only FTS / tag_boost matters
    return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _make_db(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # Chunk A: tagged StatusUpdate — about project blockers
    (corpus / "a.md").write_text(
        "# 01:00 — Alice (cue 1)\n\n<!-- speaker: Alice -->\n\nThe Salesforce integration is a blocker for Q3 milestone.\n",
        encoding="utf-8",
    )
    # Chunk B: untagged — also relevant but no tag
    (corpus / "b.md").write_text(
        "# 02:00 — Bob (cue 2)\n\n<!-- speaker: Bob -->\n\nThe milestone timeline was discussed at length in today's meeting.\n",
        encoding="utf-8",
    )
    db = K.connect(str(tmp_path / "k.sqlite"))
    with patch("knowledge_index.embed", _fake_embed):
        K.index_docs(db, "test-model", str(corpus), ["a.md", "b.md"], dim=4, max_chars=8000)

    # Manually insert chunk_topics for chunk A only
    chunk_a = db.execute("SELECT id FROM chunks WHERE source='a.md'").fetchone()[0]
    db.execute("CREATE TABLE IF NOT EXISTS chunk_topics (chunk_id INTEGER, category_id TEXT, category_label TEXT, kind TEXT)")
    db.execute("INSERT INTO chunk_topics VALUES (?, 'StatusUpdate', 'StatusUpdate', 'intent_l1')", (chunk_a,))
    db.commit()
    return db


def test_tag_boost_ranks_tagged_chunk_higher(tmp_path):
    """tag_boost should rank the tagged chunk above untagged without excluding untagged."""
    db = _make_db(tmp_path)
    with patch("knowledge_index.embed", _fake_embed):
        result = K.search(db, "test-model", "milestone blocker", 5, tag_boost="StatusUpdate")

    ids = [r["source"] for r in result["results"]]
    assert "a.md" in ids, "tagged chunk must appear in results"
    assert "b.md" in ids, "untagged chunk must NOT be excluded"
    assert ids.index("a.md") < ids.index("b.md"), "tagged chunk must rank above untagged"


def test_tag_hard_filter_still_excludes_untagged(tmp_path):
    """tag= (hard filter) still excludes untagged chunks — existing behaviour preserved."""
    db = _make_db(tmp_path)
    with patch("knowledge_index.embed", _fake_embed):
        result = K.search(db, "test-model", "milestone blocker", 5, tag="StatusUpdate")

    sources = [r["source"] for r in result["results"]]
    assert "a.md" in sources, "tagged chunk must appear"
    assert "b.md" not in sources, "untagged chunk must be excluded by hard tag filter"


def test_tag_boost_with_no_tagged_chunks_still_returns_results(tmp_path):
    """tag_boost with a tag that matches nothing must still return untagged results."""
    db = _make_db(tmp_path)
    with patch("knowledge_index.embed", _fake_embed):
        result = K.search(db, "test-model", "milestone blocker", 5, tag_boost="TeamRoster")

    assert len(result["results"]) > 0, "must return results even when no chunk matches tag_boost"
