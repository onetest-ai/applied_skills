"""Tests for generate_evals.py — corpus-agnostic eval CSV generator."""
import csv
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_evals as G

TAXONOMY = {
    "intent_taxonomy": {
        "l1": ["ActionItem", "KnowledgeGap", "QualityRisk", "TestStrategy",
               "ProcessObservation", "IntegrationPoint", "TransitionDependency"],
        "eval_config": {
            "ActionItem":           {"eval_type": "recall",       "question": "What action items were identified?",      "query_suffix": "owners decisions"},
            "KnowledgeGap":         {"eval_type": "recall",       "question": "What knowledge gaps were identified?",    "query_suffix": "unknown unresolved"},
            "QualityRisk":          {"eval_type": "recall",       "question": "What quality risks were identified?",     "query_suffix": "risk failure"},
            "TestStrategy":         {"eval_type": "faithfulness", "question": "What testing strategies were discussed?", "query_suffix": "approach plan"},
            "ProcessObservation":   {"eval_type": "faithfulness", "question": "What process observations were made?",    "query_suffix": "workflow steps"},
            "IntegrationPoint":     {"eval_type": "completeness", "question": "What integration points were identified?","query_suffix": "dependency connection"},
            "TransitionDependency": {"eval_type": "completeness", "question": "What transition dependencies were identified?", "query_suffix": "handover prerequisite"},
        }
    }
}


def _make_extraction(tmp_path, slug, extractions):
    d = {
        "file_slug": slug,
        "session_date": "2026-09-08",
        "title": f"Meeting {slug}",
        "participants": ["Alice", "Bob"],
        "session_axes": {"product": "TestProduct"},
        "extractions": extractions,
        "ambiguities": [],
    }
    (tmp_path / f"{slug}_extraction.json").write_text(json.dumps(d))


def _make_taxonomy(tmp_path):
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps(TAXONOMY))
    return str(p)


def test_generates_recall_eval_from_single_extraction(tmp_path):
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001",
        "category": "ActionItem",
        "verbatim_quote": "We need to set up access logging by Friday.",
        "context": "Owner discussed setting up access logging.",
        "owner": "Alice",
        "product": "TestProduct",
        "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    assert len(rows) >= 1
    assert any(r["category"] == "ActionItem" for r in rows)


def test_scope_single_session_when_one_source(tmp_path):
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001",
        "category": "QualityRisk",
        "verbatim_quote": "There is a defect in the login flow.",
        "context": "Login flow defect reported.",
        "owner": "",
        "product": "TestProduct",
        "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    single = [r for r in rows if r["scope"] == "single-session"]
    assert len(single) >= 1


def test_pipe_separator_in_must_contain(tmp_path):
    _make_extraction(tmp_path, "abc123", [
        {"id": "ext-001", "category": "ActionItem", "verbatim_quote": "Set up logging.",
         "context": "Logging setup needed.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement"},
        {"id": "ext-002", "category": "ActionItem", "verbatim_quote": "Set up monitoring.",
         "context": "Monitoring setup needed.", "owner": "Bob", "product": "TP", "confidence": "DirectStatement"},
    ])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    multi = [r for r in rows if "|" in r["expected_answer_must_contain"]]
    assert len(multi) >= 1


def test_no_unresolved_product_in_output(tmp_path):
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001", "category": "KnowledgeGap",
        "verbatim_quote": "We don't know the architecture.",
        "context": "Architecture unknown.", "owner": "", "product": "UNRESOLVED",
        "confidence": "Inference",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    for r in rows:
        assert "UNRESOLVED" not in r.get("notes", "")


def test_query_suffix_comes_from_taxonomy(tmp_path):
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001", "category": "ActionItem",
        "context": "Deploy by Friday.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    action_rows = [r for r in rows if r["category"] == "ActionItem"]
    assert all(r["query_suffix"] == "owners decisions" for r in action_rows)


def test_missing_eval_config_falls_back_to_default(tmp_path):
    # Taxonomy with no eval_config — should not crash, falls back to DEFAULT_QUERY_SUFFIX
    taxonomy = {"intent_taxonomy": {"l1": ["ActionItem"]}}
    taxo_path = tmp_path / "taxonomy.json"
    taxo_path.write_text(json.dumps(taxonomy))
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001", "category": "ActionItem",
        "context": "Do the thing.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", str(taxo_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    action_rows = [r for r in rows if r["category"] == "ActionItem"]
    assert len(action_rows) >= 1
    assert all(r["query_suffix"] == G.DEFAULT_QUERY_SUFFIX for r in action_rows)


def test_empty_taxonomy_produces_no_evals(tmp_path):
    # Taxonomy with no l1 categories — extraction data exists but no categories match
    taxonomy = {"intent_taxonomy": {"l1": [], "eval_config": {}}}
    taxo_path = tmp_path / "taxonomy.json"
    taxo_path.write_text(json.dumps(taxonomy))
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001", "category": "ActionItem",
        "context": "Do the thing.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", str(taxo_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    assert rows == []


def test_bad_taxonomy_path_raises(tmp_path):
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "generate_evals",
         "--extractions", str(tmp_path),
         "--taxonomy", str(tmp_path / "nonexistent.json"),
         "--out", str(tmp_path / "out.csv")],
        capture_output=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode != 0


def test_unknown_category_warns(tmp_path):
    import warnings
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001", "category": "UnknownCategory",
        "context": "Some unknown thing.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    msgs = [str(warning.message) for warning in w]
    assert any("UnknownCategory" in m for m in msgs)
    # unknown category produces no eval rows
    rows = list(csv.DictReader(out.open()))
    assert not any(r["category"] == "UnknownCategory" for r in rows)


def test_no_hallucination_uses_corpus_categories_not_taxonomy_order(tmp_path):
    # Only TestStrategy extractions present; no-hallucination evals should pick TestStrategy,
    # not the first category in taxonomy l1 (ActionItem)
    _make_extraction(tmp_path, "abc123", [
        {"id": "ext-001", "category": "TestStrategy",
         "context": "Regression suite coverage.", "owner": "Bob", "product": "TP", "confidence": "DirectStatement"},
        {"id": "ext-002", "category": "TestStrategy",
         "context": "Unit test plan drafted.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement"},
    ])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    no_hal = [r for r in rows if r["category"] == "no-hallucination"]
    assert len(no_hal) >= 1
    assert all("TestStrategy" in r["notes"] for r in no_hal)
    # ActionItem not in corpus — must not appear in no-hallucination
    assert not any("ActionItem" in r["notes"] for r in no_hal)


def test_no_hallucination_query_suffix_from_taxonomy(tmp_path):
    # No-hallucination rows must use per-category query_suffix, not DEFAULT_QUERY_SUFFIX
    _make_extraction(tmp_path, "abc123", [
        {"id": "ext-001", "category": "ActionItem",
         "context": "Set up logging.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement"},
        {"id": "ext-002", "category": "ActionItem",
         "context": "Review auth module.", "owner": "Bob", "product": "TP", "confidence": "DirectStatement"},
    ])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    no_hal = [r for r in rows if r["category"] == "no-hallucination"]
    assert len(no_hal) >= 1
    # ActionItem query_suffix in TAXONOMY fixture is "owners decisions"
    assert all(r["query_suffix"] == "owners decisions" for r in no_hal)
    assert not any(r["query_suffix"] == G.DEFAULT_QUERY_SUFFIX for r in no_hal)


# ---------------------------------------------------------------------------
# --db mode tests
# ---------------------------------------------------------------------------

def _make_sqlite_db(path_str):
    """Create a minimal knowledge.sqlite with chunks + chunk_topics."""
    c = sqlite3.connect(path_str)
    c.executescript("""
      CREATE TABLE chunks(
        id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT,
        status TEXT DEFAULT 'ACTIVE'
      );
      CREATE TABLE chunk_topics(
        chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT
      );
    """)
    # Two sources, two categories
    rows = [
        # source alpha — ActionItem
        (1, "alpha.vtt.md", 0, "Meeting start", "Assign logging setup to Alice by Friday.", "ACTIVE"),
        (2, "alpha.vtt.md", 1, "Follow up", "Alice to review the dashboard metrics.", "ACTIVE"),
        # source beta — ActionItem
        (3, "beta.vtt.md", 0, "Beta action", "Bob must finalize the deployment plan.", "ACTIVE"),
        # source alpha — DecisionPoint
        (4, "alpha.vtt.md", 2, "Decision", "We decided to use Gatling for load testing.", "ACTIVE"),
    ]
    c.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?)", rows)
    topics = [
        (1, "action_item", "ActionItem", "intent_l1"),
        (2, "action_item", "ActionItem", "intent_l1"),
        (3, "action_item", "ActionItem", "intent_l1"),
        (4, "decision_point", "DecisionPoint", "intent_l1"),
    ]
    c.executemany("INSERT INTO chunk_topics VALUES (?,?,?,?)", topics)
    c.commit()
    c.close()


def test_from_db_generates_single_session_eval(tmp_path):
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    G.main(["--db", db, "--out", out])
    rows = list(csv.DictReader(open(out)))
    single = [r for r in rows if r["scope"] == "single-session"]
    assert len(single) >= 1


def test_from_db_generates_cross_session_eval_when_two_sources(tmp_path):
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    G.main(["--db", db, "--out", out])
    rows = list(csv.DictReader(open(out)))
    cross = [r for r in rows if r["scope"] == "cross-session" and r["category"] == "ActionItem"]
    assert len(cross) >= 1, f"Expected cross-session for ActionItem (2 sources). Got:\n{rows}"


def test_from_db_question_uses_category_label(tmp_path):
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    # Pass a nonexistent taxonomy so the generic fallback (containing the label) is used
    G.main(["--db", db, "--out", out, "--taxonomy", str(tmp_path / "no_taxonomy.json")])
    rows = list(csv.DictReader(open(out)))
    for r in rows:
        if r["category"] == "ActionItem":
            assert "ActionItem" in r["question"], "Question should reference category: {}".format(r["question"])
            break


def test_from_db_expected_contains_chunk_text_snippet(tmp_path):
    # After the source-slug fix: must_contain contains source slugs, not raw chunk text.
    # The fixture has sources alpha.vtt.md and beta.vtt.md → slugs "alpha" and "beta".
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    G.main(["--db", db, "--out", out])
    rows = list(csv.DictReader(open(out)))
    all_must_contain = " ".join(r["expected_answer_must_contain"] for r in rows)
    # slugs from fixture: alpha.vtt.md → "alpha", beta.vtt.md → "beta"
    assert "alpha" in all_must_contain, (
        "Source slug 'alpha' must appear in must_contain. Got: {}".format(all_must_contain[:200])
    )


def test_from_db_single_session_expected_contains_source_slug(tmp_path):
    # expected_answer_must_contain for a single-session DB eval must be the source slug,
    # not a raw chunk text prefix.
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    G.main(["--db", db, "--out", out])
    rows = list(csv.DictReader(open(out)))
    single = [r for r in rows if r["scope"] == "single-session"]
    assert len(single) >= 1
    for r in single:
        slug = r["ground_truth_source"]
        assert slug in r["expected_answer_must_contain"], (
            "Single-session must_contain must be the source slug {!r}, got: {!r}".format(
                slug, r["expected_answer_must_contain"]
            )
        )


def test_from_db_cross_session_expected_contains_both_slugs(tmp_path):
    # cross-session eval must_contain must pipe-join both source slugs
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    G.main(["--db", db, "--out", out])
    rows = list(csv.DictReader(open(out)))
    cross = [r for r in rows if r["scope"] == "cross-session" and r["category"] == "ActionItem"]
    assert len(cross) >= 1
    for r in cross:
        must = r["expected_answer_must_contain"]
        slugs = [s.strip() for s in must.split("|") if s.strip()]
        assert len(slugs) >= 2, (
            "Cross-session must_contain must pipe-join >=2 source slugs, got: {!r}".format(must)
        )
        for slug in slugs:
            assert slug in r["ground_truth_source"], (
                "Each slug in must_contain must appear in ground_truth_source. "
                "slug={!r} ground_truth={!r}".format(slug, r["ground_truth_source"])
            )


def test_from_db_expected_does_not_contain_raw_icebreaker_text(tmp_path):
    # Raw chunk text (first 60 chars of ASR) must NOT appear in expected_answer_must_contain.
    # The fixture chunk text is "Assign logging setup to Alice by Friday." —
    # this must not be in must_contain after the fix.
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    G.main(["--db", db, "--out", out])
    rows = list(csv.DictReader(open(out)))
    db_rows = [r for r in rows if "db-mode" in r.get("notes", "")]
    for r in db_rows:
        assert "Assign logging" not in r["expected_answer_must_contain"], (
            "Raw chunk text must not be used as expected fact in DB mode, "
            "got: {!r}".format(r["expected_answer_must_contain"])
        )


def test_from_db_and_extractions_together_raises(tmp_path):
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    import subprocess
    result = subprocess.run(
        [sys.executable, "generate_evals.py",
         "--db", db,
         "--extractions", str(tmp_path),
         "--out", out],
        capture_output=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode != 0, "Should fail when both --db and --extractions are given"


def test_extractions_mode_still_works_unchanged(tmp_path):
    """Regression: --extractions mode must still produce output after --db is added."""
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001", "category": "ActionItem",
        "context": "Deploy by Friday.", "owner": "Alice", "product": "TP",
        "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    assert len(rows) >= 1
