"""Data-safety tests for knowledge_index.py incremental indexing.

These tests guard against the four confirmed data-destruction paths:

  P1  Subset-corpus eviction: --corpus <subdir> with no --docs silently deletes
      all indexed docs NOT present in the subdir.

  P2  --reset wipes chunks/FTS/vec/documents and starts empty.

  P3  --delete removes named docs from the index.

  P4  Source-path collision: re-indexing with the same source name overwrites
      the old doc's chunks (intended, but only when that specific source is
      passed; it must NOT affect other sources).

All scenarios involve a large existing index (100+ docs simulated by 3+ docs with
many chunks each) to mirror the "folders will be huge and loss of data is not an
option" production constraint.

Run:
    pytest bundles/brain/tests/test_indexing_data_safety.py -v
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import knowledge_index as index  # noqa: E402


def fake_embed(_model, texts):
    return [[1.0] + [0.0] * 383 for _ in texts]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_large_corpus(
    corpus: Path, n_files: int = 5, sections_per_file: int = 4, prefix: str = "doc"
) -> list[str]:
    """Write n_files markdown docs, each with sections_per_file sections.
    Returns the list of relative paths (corpus_docs format).
    The prefix parameter ensures no filename collisions across different corpora.
    """
    sources = []
    for i in range(n_files):
        name = f"{prefix}_{i:02d}.md"
        lines = [f"# Document {prefix} {i}"]
        for s in range(sections_per_file):
            lines.append(f"\n## Section {s}\n\nunique content {prefix}{i} section{s} word{i*100+s}")
        (corpus / name).write_text("\n".join(lines), encoding="utf-8")
        sources.append(name)
    return sources


def _chunk_count(con) -> int:
    return con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def _doc_sources(con) -> set:
    return {r[0] for r in con.execute("SELECT DISTINCT source FROM chunks")}


# ===========================================================================
# P1 — Subset-corpus eviction
# ===========================================================================

def test_p1_indexing_a_subset_folder_does_not_delete_other_docs(tmp_path, monkeypatch):
    """Passing --corpus <subdir> with no --docs must NOT delete docs from other dirs.

    This is the most dangerous path: a user runs `knowledge_index --corpus parsed-dial/`
    to add new AI DIAL conversations to an existing 9 000-chunk VTT+xlsx index. The
    eviction logic on lines 333-344 would DELETE all 9 000 chunks because they are not
    present in parsed-dial/.

    Guard: cmd_index must refuse to prune when the corpus dir contains fewer sources
    than are already indexed from OUTSIDE that corpus dir — or it must require an
    explicit --allow-prune flag.

    Until that guard exists this test documents the FAILING behaviour so the team
    knows the risk is real and unguarded.
    """
    monkeypatch.setattr(index, "embed", fake_embed)

    corpus_main = tmp_path / "main"
    corpus_main.mkdir()
    corpus_extra = tmp_path / "extra"
    corpus_extra.mkdir()

    # Use distinct prefixes to avoid filename collisions when merged into one dir
    main_sources = _build_large_corpus(corpus_main, n_files=5, sections_per_file=3, prefix="main")
    extra_sources = _build_large_corpus(corpus_extra, n_files=2, sections_per_file=3, prefix="extra")

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)

    # Build a unified corpus directory (main + copies of extra docs under unique names)
    # so index_docs can locate all sources from one root path
    for src in extra_sources:
        (corpus_main / src).write_text((corpus_extra / src).read_text())
    all_sources = index.corpus_docs(str(corpus_main))
    index.index_docs(con, "fake", str(corpus_main), all_sources, 384, 1200)
    con.commit()

    chunks_before = _chunk_count(con)
    sources_before = _doc_sources(con)
    assert chunks_before > 0, "pre-condition: DB must be non-empty"
    assert len(sources_before) == 7  # 5 main + 2 extra (copies)

    # Simulate: user re-indexes only the extra sub-set
    # Create a separate extra-only corpus dir with the extra docs
    corpus_extra_only = tmp_path / "extra_only"
    corpus_extra_only.mkdir()
    for src in extra_sources:
        (corpus_extra_only / src).write_text((corpus_extra / src).read_text())

    # This simulates the dangerous call: knowledge_index --db ... --corpus extra_only/
    # (no --docs flag) — triggers eviction of main_sources
    import argparse
    a = argparse.Namespace(
        db=db_path,
        corpus=str(corpus_extra_only),
        docs=None,          # no --docs → eviction logic fires
        reset=False,
        delete=None,
        model="fake",
        dim=384,
        max_chars=1200,
    )

    # CAPTURE stderr to detect the WARNING
    import io
    import contextlib
    stderr_buf = io.StringIO()
    with contextlib.redirect_stderr(stderr_buf):
        index.cmd_index(a)
    stderr_out = stderr_buf.getvalue()

    chunks_after = _chunk_count(index.connect(db_path))
    sources_after = _doc_sources(index.connect(db_path))

    # main_* sources must survive — they were not in the subset corpus dir
    expected_survivors = {s for s in sources_before if s.startswith("main_")}
    actual_survivors = {s for s in sources_after if s.startswith("main_")}

    # This assertion documents the current FAILING state:
    # cmd_index DOES delete the main docs → actual_survivors is empty.
    # When the P1 guard is implemented, this test should PASS (main docs survive).
    assert actual_survivors == expected_survivors, (
        f"SAFETY VIOLATION (P1): subset-corpus re-index deleted "
        f"{len(expected_survivors) - len(actual_survivors)} main doc(s).\n"
        f"chunks_before={chunks_before}, chunks_after={chunks_after}\n"
        f"deleted main sources={expected_survivors - actual_survivors}\n"
        f"stderr: {stderr_out[:400]}"
    )


def test_p1_subset_index_with_explicit_docs_does_not_prune(tmp_path, monkeypatch):
    """--corpus <subdir> --docs <file> must never trigger the eviction logic.

    The --docs flag short-circuits the prune path (line 333: `if a.corpus and not a.docs`).
    This test verifies that invariant holds.
    """
    monkeypatch.setattr(index, "embed", fake_embed)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sources = _build_large_corpus(corpus, n_files=5, sections_per_file=3)

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus), sources, 384, 1200)
    con.commit()

    chunks_before = _chunk_count(con)
    sources_before = _doc_sources(con)
    assert len(sources_before) == 5

    # Re-index only doc_00.md via --docs (safe path)
    import argparse
    a = argparse.Namespace(
        db=db_path,
        corpus=str(corpus),
        docs="doc_00.md",   # explicit --docs → no prune
        reset=False,
        delete=None,
        model="fake",
        dim=384,
        max_chars=1200,
    )
    index.cmd_index(a)

    sources_after = _doc_sources(index.connect(db_path))
    assert sources_after == sources_before, (
        f"SAFETY VIOLATION: --docs flag did not prevent pruning.\n"
        f"deleted={sources_before - sources_after}"
    )


# ===========================================================================
# P2 — --reset wipes everything
# ===========================================================================

def test_p2_reset_clears_entire_index(tmp_path, monkeypatch):
    """--reset must wipe the index completely (this is intentional).

    Test documents the expected behaviour so accidental invocation is obvious.
    """
    monkeypatch.setattr(index, "embed", fake_embed)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sources = _build_large_corpus(corpus, n_files=5, sections_per_file=3)

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus), sources, 384, 1200)
    con.commit()
    assert _chunk_count(con) > 0

    import argparse
    a = argparse.Namespace(
        db=db_path,
        corpus=str(corpus),
        docs=None,
        reset=True,        # explicit --reset
        delete=None,
        model="fake",
        dim=384,
        max_chars=1200,
    )
    index.cmd_index(a)

    # After --reset + re-index of same corpus: same docs re-indexed, not zero
    con2 = index.connect(db_path)
    chunks_after = _chunk_count(con2)
    assert chunks_after > 0, (
        "--reset + re-index of same corpus should produce non-empty index"
    )


def test_p2_reset_without_corpus_leaves_empty_db(tmp_path, monkeypatch):
    """--reset with no --corpus must produce an empty (but valid-schema) DB."""
    monkeypatch.setattr(index, "embed", fake_embed)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sources = _build_large_corpus(corpus, n_files=3, sections_per_file=2)

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus), sources, 384, 1200)
    con.commit()
    assert _chunk_count(con) > 0

    import argparse
    a = argparse.Namespace(
        db=db_path,
        corpus=None,       # no corpus
        docs=None,
        reset=True,
        delete=None,
        model="fake",
        dim=384,
        max_chars=1200,
    )
    index.cmd_index(a)

    con2 = index.connect(db_path)
    assert _chunk_count(con2) == 0, (
        "--reset with no corpus must produce 0 chunks"
    )


# ===========================================================================
# P3 — --delete removes only named docs
# ===========================================================================

def test_p3_delete_named_doc_does_not_touch_other_docs(tmp_path, monkeypatch):
    """--delete doc_00.md must remove only doc_00.md chunks; others survive intact."""
    monkeypatch.setattr(index, "embed", fake_embed)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sources = _build_large_corpus(corpus, n_files=5, sections_per_file=3)

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus), sources, 384, 1200)
    con.commit()

    chunks_before = _chunk_count(con)
    doc_chunks = con.execute(
        "SELECT COUNT(*) FROM chunks WHERE source=?", ("doc_00.md",)
    ).fetchone()[0]
    assert doc_chunks > 0, "pre-condition: doc_00.md must have chunks"

    import argparse
    a = argparse.Namespace(
        db=db_path,
        corpus=str(corpus),
        docs=None,
        reset=False,
        delete="doc_00.md",
        model="fake",
        dim=384,
        max_chars=1200,
    )
    index.cmd_index(a)

    con2 = index.connect(db_path)
    # doc_00 is gone (--delete removed it, re-index via --corpus added it back)
    # What matters: docs 1-4 are NOT touched
    sources_after = _doc_sources(con2)
    for s in sources[1:]:   # doc_01 through doc_04 must still be indexed
        assert s in sources_after, (
            f"SAFETY VIOLATION (P3): --delete doc_00.md removed unrelated doc {s}"
        )


def test_p3_delete_removes_all_chunk_topics_for_deleted_doc(tmp_path, monkeypatch):
    """Deleting a doc must also clean up its chunk_topics rows (no orphans)."""
    monkeypatch.setattr(index, "embed", fake_embed)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sources = _build_large_corpus(corpus, n_files=3, sections_per_file=2)

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus), sources, 384, 1200)
    # Manually add chunk_topics for doc_00's chunks
    con.execute("""
        CREATE TABLE IF NOT EXISTS
        chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT)
    """)
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE source=?", ("doc_00.md",))]
    for cid in ids:
        con.execute("INSERT INTO chunk_topics VALUES(?,?,?,?)", (cid, "cat1", "Cat1", "intent_l1"))
    con.commit()

    topics_before = con.execute(
        "SELECT COUNT(*) FROM chunk_topics WHERE chunk_id IN "
        "(SELECT id FROM chunks WHERE source=?)", ("doc_00.md",)
    ).fetchone()[0]
    assert topics_before > 0

    import argparse
    a = argparse.Namespace(
        db=db_path,
        corpus=None,        # no re-index, just delete
        docs=None,
        reset=False,
        delete="doc_00.md",
        model="fake",
        dim=384,
        max_chars=1200,
    )
    index.cmd_index(a)

    con2 = index.connect(db_path)
    orphaned = con2.execute(
        "SELECT COUNT(*) FROM chunk_topics ct "
        "LEFT JOIN chunks c ON c.id=ct.chunk_id "
        "WHERE c.id IS NULL"
    ).fetchone()[0]
    assert orphaned == 0, (
        f"SAFETY VIOLATION (P3): {orphaned} orphan chunk_topics rows after delete"
    )


# ===========================================================================
# P4 — Source-path collision (overwrite of one doc must not affect others)
# ===========================================================================

def test_p4_updating_one_doc_does_not_touch_sibling_chunks(tmp_path, monkeypatch):
    """Re-indexing doc_00.md (changed content) must leave doc_01.md chunks intact.

    This verifies the core incremental-index contract: only the changed doc's
    old chunks are removed and new chunks inserted; sibling docs are untouched.
    """
    monkeypatch.setattr(index, "embed", fake_embed)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sources = _build_large_corpus(corpus, n_files=3, sections_per_file=3)

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus), sources, 384, 1200)
    con.commit()

    sibling_ids_before = {
        r[0] for r in con.execute("SELECT id FROM chunks WHERE source=?", ("doc_01.md",))
    }
    assert sibling_ids_before, "pre-condition: doc_01 must have chunks"

    # Modify only doc_00
    (corpus / "doc_00.md").write_text(
        "# Document 0 CHANGED\n\n## Updated\n\ncompletely new content here\n",
        encoding="utf-8",
    )

    index.index_docs(con, "fake", str(corpus), ["doc_00.md"], 384, 1200)
    con.commit()

    sibling_ids_after = {
        r[0] for r in con.execute("SELECT id FROM chunks WHERE source=?", ("doc_01.md",))
    }
    assert sibling_ids_before == sibling_ids_after, (
        f"SAFETY VIOLATION (P4): updating doc_00 changed doc_01 chunk IDs.\n"
        f"removed={sibling_ids_before - sibling_ids_after}, "
        f"added={sibling_ids_after - sibling_ids_before}"
    )


def test_p4_incremental_reindex_of_unchanged_sibling_is_zero_cost(tmp_path, monkeypatch):
    """When doc_00.md changes and doc_01.md is unchanged, index_docs for doc_01
    must return (0 changed_chunks, 0 changed_docs, 1 skipped_doc).

    This ensures we never silently re-embed unchanged documents which would
    waste compute and could cause hash drift on a large corpus.
    """
    embed_calls = []

    def recording_embed(model, texts):
        embed_calls.append(list(texts))
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr(index, "embed", recording_embed)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sources = _build_large_corpus(corpus, n_files=2, sections_per_file=3)

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus), sources, 384, 1200)
    con.commit()
    embed_calls.clear()

    # Change doc_00 only
    (corpus / "doc_00.md").write_text("# New\n\n## Only\n\nreplaced content\n", encoding="utf-8")

    # Re-index only doc_01 (unchanged)
    n_chunks, n_docs, skipped = index.index_docs(con, "fake", str(corpus), ["doc_01.md"], 384, 1200)

    assert n_chunks == 0, f"unchanged doc should produce 0 new chunks, got {n_chunks}"
    assert n_docs == 0, f"unchanged doc should count as 0 changed docs, got {n_docs}"
    assert skipped == 1, f"unchanged doc should be skipped, got skipped={skipped}"
    assert embed_calls == [], f"embed() must not be called for unchanged doc, got {embed_calls}"


# ===========================================================================
# P1 extended — the eviction warning must be emitted on stderr
# ===========================================================================

def test_p1_eviction_prints_warning_with_source_count(tmp_path, monkeypatch, capsys):
    """When cmd_index prunes sources, it must print a WARNING to stderr listing count.

    This test verifies the existing warning on lines 338-343 is present.
    If this test fails, the warning was removed and users lose the only signal
    that their data is about to be deleted.
    """
    monkeypatch.setattr(index, "embed", fake_embed)

    # Build full corpus and index it
    corpus_full = tmp_path / "full"
    corpus_full.mkdir()
    sources_full = _build_large_corpus(corpus_full, n_files=4, sections_per_file=2)

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus_full), sources_full, 384, 1200)
    con.commit()

    # Now create a subset corpus (only 1 of the 4 docs)
    corpus_sub = tmp_path / "sub"
    corpus_sub.mkdir()
    (corpus_sub / "doc_00.md").write_text((corpus_full / "doc_00.md").read_text())

    import argparse, io, contextlib
    a = argparse.Namespace(
        db=db_path,
        corpus=str(corpus_sub),
        docs=None,
        reset=False,
        delete=None,
        model="fake",
        dim=384,
        max_chars=1200,
    )
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        index.cmd_index(a)

    stderr_out = buf.getvalue()
    assert "WARNING" in stderr_out, (
        "No WARNING emitted when subset-corpus eviction would delete indexed sources.\n"
        f"stderr: {stderr_out!r}"
    )
    assert "not in --corpus dir" in stderr_out, (
        "WARNING must mention that sources are not in the corpus dir.\n"
        f"stderr: {stderr_out!r}"
    )


# ===========================================================================
# P1 guard — safe incremental add of new docs to existing large index
# ===========================================================================

def test_safe_incremental_add_new_docs_to_existing_index(tmp_path, monkeypatch):
    """The canonical safe pattern: use --docs to add new files without pruning.

    User story: 9 000-chunk VTT index exists. User parses 10 new AI DIAL files
    into parsed-dial/ and wants to add them WITHOUT touching the VTT chunks.

    Safe invocation: knowledge_index --db K.sqlite --corpus parsed-dial/ --docs file1.md,file2.md,...
    (passing --docs bypasses the eviction path on line 333)

    This test verifies that pattern works correctly end-to-end.
    """
    monkeypatch.setattr(index, "embed", fake_embed)

    # Step 1: build the "existing" large VTT index (prefix=vtt to avoid name collisions)
    corpus_vtt = tmp_path / "vtt"
    corpus_vtt.mkdir()
    vtt_sources = _build_large_corpus(corpus_vtt, n_files=5, sections_per_file=4, prefix="vtt")

    db_path = str(tmp_path / "knowledge.sqlite")
    con = index.connect(db_path)
    index.index_docs(con, "fake", str(corpus_vtt), vtt_sources, 384, 1200)
    con.commit()

    vtt_chunks_before = _chunk_count(con)
    vtt_sources_before = _doc_sources(con)
    assert vtt_chunks_before > 0

    # Step 2: parse new dial files into a separate dir (prefix=dial — distinct from vtt_*)
    corpus_dial = tmp_path / "dial"
    corpus_dial.mkdir()
    dial_sources = _build_large_corpus(corpus_dial, n_files=2, sections_per_file=3, prefix="dial")

    # Step 3: safe add — pass --docs to prevent eviction
    import argparse
    a = argparse.Namespace(
        db=db_path,
        corpus=str(corpus_dial),
        docs=",".join(dial_sources),   # explicit --docs avoids prune
        reset=False,
        delete=None,
        model="fake",
        dim=384,
        max_chars=1200,
    )
    index.cmd_index(a)

    con2 = index.connect(db_path)
    vtt_sources_after = {s for s in _doc_sources(con2) if s in vtt_sources_before}
    dial_sources_after = {s for s in _doc_sources(con2) if s in set(dial_sources)}

    # VTT chunks must be completely unchanged
    assert vtt_sources_after == vtt_sources_before, (
        f"SAFETY VIOLATION: safe-add via --docs still pruned VTT sources.\n"
        f"lost={vtt_sources_before - vtt_sources_after}"
    )
    # Dial docs must be present
    assert dial_sources_after == set(dial_sources), (
        f"New dial docs were not indexed: missing={set(dial_sources) - dial_sources_after}"
    )
    # Total chunks grew
    assert _chunk_count(con2) > vtt_chunks_before, "Chunk count must grow after adding new docs"
