import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import chunking  # noqa: E402
import knowledge_index as index  # noqa: E402


def fake_embed(_model, texts):
    return [[1.0] + [0.0] * 383 for _ in texts]


def test_parent_child_chunking_retains_breadcrumb():
    records = chunking.section_records("# Architecture\n\n## FastMCP Proxy\n\n### Transport\n\nSet port to 8002.")
    leaf = records[-1]
    assert leaf["parent_heading"] == "FastMCP Proxy"
    assert leaf["breadcrumb_path"] == "Architecture > FastMCP Proxy > Transport"


def test_incremental_index_skips_document_and_reembeds_only_changed_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "embed", fake_embed)
    corpus = tmp_path / "corpus"; corpus.mkdir()
    doc = corpus / "meeting.md"
    doc.write_text("# Meeting\n\n## One\n\nalpha\n\n## Two\n\nbeta\n", encoding="utf-8")
    con = index.connect(str(tmp_path / "knowledge.sqlite"))
    first = index.index_docs(con, "fake", str(corpus), ["meeting.md"], 384, 1200)
    second = index.index_docs(con, "fake", str(corpus), ["meeting.md"], 384, 1200)
    doc.write_text("# Meeting\n\n## One\n\nalpha changed\n\n## Two\n\nbeta\n", encoding="utf-8")
    third = index.index_docs(con, "fake", str(corpus), ["meeting.md"], 384, 1200)
    assert first == (2, 1, 0)
    assert second == (0, 0, 1)
    assert third == (1, 1, 0)
    assert con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 2


def test_incremental_index_reembeds_when_model_changes_and_preserves_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "embed", fake_embed)
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "meeting.md").write_text("# Meeting\n\ncurrent fact", encoding="utf-8")
    con = index.connect(str(tmp_path / "knowledge.sqlite"))
    index.index_docs(con, "model-a", str(corpus), ["meeting.md"], 384, 1200)
    con.execute("UPDATE chunks SET status='SUPERSEDED',valid_to='2025-09-21'")
    changed = index.index_docs(con, "model-b", str(corpus), ["meeting.md"], 384, 1200)
    assert changed == (1, 1, 0)
    assert con.execute("SELECT status,valid_to FROM chunks").fetchone() == (
        "SUPERSEDED", "2025-09-21"
    )


def test_search_filters_temporal_source_tag_and_returns_context_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "embed", fake_embed)
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "meetings").mkdir()
    (corpus / "meetings" / "2025-09-20.md").write_text(
        "event_date: 2025-09-20\n\n# Project\n\n## Budget\n\n<!-- speaker: Alex -->\n\nalpha old budget", encoding="utf-8")
    (corpus / "meetings" / "2025-09-21.md").write_text(
        "event_date: 2025-09-21\n\n# Project\n\n## Budget\n\n<!-- speaker: Marta -->\n\nalpha current budget", encoding="utf-8")
    con = index.connect(str(tmp_path / "knowledge.sqlite"))
    sources = index.corpus_docs(str(corpus))
    index.index_docs(con, "fake", str(corpus), sources, 384, 1200)
    new_id = con.execute("SELECT id FROM chunks WHERE source=? AND title='Budget'", ("meetings/2025-09-21.md",)).fetchone()[0]
    con.execute("UPDATE chunks SET status='SUPERSEDED',valid_to='2025-09-21' WHERE source=?", ("meetings/2025-09-20.md",))
    con.execute("CREATE TABLE chunk_topics(chunk_id INT, category_label TEXT)")
    con.execute("INSERT INTO chunk_topics VALUES(?,?)", (new_id, "Budget"))
    latest = index.search(con, "fake", "alpha budget", 5, latest_only=True)
    historical = index.search(
        con, "fake", "alpha budget", 5, as_of="2025-09-20", latest_only=True
    )
    faceted = index.search(con, "fake", "alpha budget", 5, source_contains="2025-09-21", tag="Budget")
    assert {r["source"] for r in latest["results"]} == {"meetings/2025-09-21.md"}
    assert {r["source"] for r in historical["results"]} == {"meetings/2025-09-20.md"}
    assert [r["source"] for r in faceted["results"]] == ["meetings/2025-09-21.md"]
    assert faceted["results"][0]["speaker"] == "Marta"
    assert faceted["results"][0]["breadcrumb_path"] == "Project > Budget"


def test_typed_edges_are_directional_and_survive_similarity_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "embed", fake_embed)
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nquestion alpha", encoding="utf-8")
    (corpus / "b.md").write_text("# B\n\nanswer alpha", encoding="utf-8")
    con = index.connect(str(tmp_path / "knowledge.sqlite"))
    index.index_docs(con, "fake", str(corpus), index.corpus_docs(str(corpus)), 384, 1200)
    index._ensure_related_schema(con)
    a, b = index.chunk_id("a.md", 0), index.chunk_id("b.md", 0)
    con.execute("INSERT INTO related VALUES(?,?,1.0,'ANSWERS',1)", (b, a))
    index.build_related(con, k=1, min_score=0.0)
    row = con.execute("SELECT edge_type,directed FROM related WHERE chunk_id=? AND related_id=?", (b, a)).fetchone()
    assert row == ("ANSWERS", 1)
    # The explicit edge is also preserved when its direction matches the
    # normalized pair used by the similarity builder.
    con.execute("DELETE FROM related")
    first, second = sorted((a, b))
    con.execute("INSERT INTO related VALUES(?,?,1.0,'REFERENCES',1)", (first, second))
    index.build_related(con, k=1, min_score=0.0)
    assert con.execute(
        "SELECT edge_type,directed FROM related WHERE chunk_id=? AND related_id=?",
        (first, second),
    ).fetchone() == ("REFERENCES", 1)


def test_index_docs_skips_embed_call_when_all_sections_unchanged(tmp_path, monkeypatch):
    """Bug 9: embed() must not be called with an empty list.

    When a doc's cache-key (model/dim/max_chars/content hash) changes but every
    section's embedding_content_hash is already stored with the same value,
    ``changed`` is empty.  The original code called ``embed(model, [])``
    unconditionally, which has undefined behaviour for fastembed.

    After the fix, embed() is guarded: it is only called when ``changed`` is
    non-empty.

    Scenario: index with max_chars=1200, then re-index with max_chars=800.
    Both calls produce the same section bodies (content fits in 800 chars), so
    emb_hash is identical but doc_hash differs → all sections are in ``clean``
    but none are in ``changed``.
    """
    embed_calls = []

    def recording_embed(model, texts):
        embed_calls.append(texts)
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr(index, "embed", recording_embed)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "note.md").write_text("# Note\n\nShort body.", encoding="utf-8")

    con = index.connect(str(tmp_path / "knowledge.sqlite"))

    # First index with max_chars=1200 — sections are embedded.
    index.index_docs(con, "fake", str(corpus), ["note.md"], 384, 1200)
    assert any(len(c) > 0 for c in embed_calls), "first index should embed"
    embed_calls.clear()

    # Re-index with max_chars=800 — doc_hash changes (max_chars in hash),
    # but emb_hash is the same (title+body identical) → changed == [].
    # Bug 9: embed(model, []) called → potential crash or incorrect behaviour.
    # After fix: embed is not called at all.
    index.index_docs(con, "fake", str(corpus), ["note.md"], 384, 800)
    for call_texts in embed_calls:
        assert len(call_texts) > 0, (
            "Bug 9: embed() was called with an empty list; "
            "guard 'if changed:' must wrap the embed() call"
        )


def test_benchmark_calculates_hit_rate_and_mrr():
    cases = [
        {"eval_id": "R1", "expected_sources": "a.md"},
        {"eval_id": "R2", "expected_sources": "b.md|c.md"},
        {"eval_id": "R3", "expected_sources": "missing.md"},
    ]
    result = index.benchmark_metrics(cases, [["a.md"], ["x.md", "b.md"], ["z.md"]])
    assert result["hit_rate_at_k"] == 2 / 3
    assert result["mrr"] == 0.5
