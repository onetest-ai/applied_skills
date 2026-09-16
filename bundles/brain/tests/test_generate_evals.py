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


def test_query_suffix_comes_from_fact_keywords(tmp_path):
    # query_suffix must be derived from the verbatim quote, not the taxonomy label.
    # The quote "Deploy the auth module by Friday" contains searchable keywords.
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001", "category": "ActionItem",
        "verbatim_quote": "Deploy the auth module by Friday.",
        "context": "Deploy by Friday.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    action_rows = [r for r in rows if r["category"] == "ActionItem" and r["scope"] == "single-session"]
    assert len(action_rows) >= 1
    for r in action_rows:
        suffix = r["query_suffix"]
        # Must contain keywords from the verbatim quote — NOT the taxonomy label string
        assert "Deploy" in suffix or "auth" in suffix or "module" in suffix or "Friday" in suffix, (
            f"query_suffix should contain verbatim-fact keywords, got: {suffix!r}"
        )
        assert suffix != "owners decisions", (
            "query_suffix must not be the taxonomy eval_config value — must come from the fact"
        )


def test_missing_eval_config_falls_back_to_default(tmp_path):
    # Taxonomy with no eval_config — should not crash; query_suffix comes from fact keywords
    taxonomy = {"intent_taxonomy": {"l1": ["ActionItem"]}}
    taxo_path = tmp_path / "taxonomy.json"
    taxo_path.write_text(json.dumps(taxonomy))
    _make_extraction(tmp_path, "abc123", [{
        "id": "ext-001", "category": "ActionItem",
        "verbatim_quote": "Deploy the auth module by Friday.",
        "context": "Do the thing.", "owner": "Alice", "product": "TP", "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", str(taxo_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    action_rows = [r for r in rows if r["category"] == "ActionItem"]
    assert len(action_rows) >= 1
    # query_suffix comes from verbatim_quote keywords — not from taxonomy (which has none)
    for r in action_rows:
        suffix = r["query_suffix"]
        assert len(suffix) > 0, "query_suffix must be non-empty"
        # When no eval_config exists, fact keywords are still used
        assert "Deploy" in suffix or "auth" in suffix or "module" in suffix or "Friday" in suffix, (
            f"query_suffix should contain fact keywords even without eval_config, got: {suffix!r}"
        )


def test_empty_taxonomy_produces_only_no_halluc_evals(tmp_path):
    # Taxonomy with no l1 categories — extraction data exists but no categories match.
    # No-hallucination evals are fixed (not per-category) so they still appear.
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
    # No category rows (no matching categories), but fixed no-hallucination rows ARE produced
    category_rows = [r for r in rows if r["category"] != "no-hallucination"]
    assert category_rows == []
    no_hal = [r for r in rows if r["category"] == "no-hallucination"]
    assert len(no_hal) == 3


def test_bad_taxonomy_path_raises(tmp_path):
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "generate_evals",
         "--extractions", str(tmp_path),
         "--taxonomy", str(tmp_path / "nonexistent.json"),
         "--out", str(tmp_path / "out.csv")],
        capture_output=True,
        cwd=str(Path(__file__).resolve().parent.parent / "skills" / "evals"),
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


def test_no_hallucination_produces_fixed_absent_data_questions(tmp_path):
    # No-hallucination evals are now a fixed set of 3 domain-absent questions.
    # They do NOT vary by corpus category — the old per-category approach caused false
    # failures when categories like MetricOrKPI had real numeric data in the corpus.
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
    assert len(no_hal) == 3
    # All three must target absent-data topics, not corpus category names
    notes = [r["notes"] for r in no_hal]
    assert any("absent-pii" in n for n in notes)
    assert any("absent-hr-records" in n for n in notes)
    assert any("absent-health-data" in n for n in notes)


def test_no_hallucination_question_does_not_contain_answer_name(tmp_path):
    # No-hallucination questions must never embed an expected answer in the question text.
    # Fixed absent-data questions have no person names or specific figures in the question.
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
    assert len(no_hal) == 3
    # None of the questions should mention "not established" (that's the expected answer)
    for r in no_hal:
        assert "not established" not in r["question"]
        assert "not found" not in r["question"]


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
            assert slug in r["ground_truth_source"].split(","), (
                "Each slug in must_contain must appear as a discrete entry in ground_truth_source. "
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
        cwd=str(Path(__file__).resolve().parent.parent / "skills" / "evals"),
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


# ---------------------------------------------------------------------------
# NEW: fact-derived question + query_suffix (judge quality fix)
# ---------------------------------------------------------------------------

def test_question_from_fact_contains_keywords():
    """_question_from_fact returns a question containing keywords from the verbatim quote."""
    q = G._question_from_fact(
        "availability calendar which allows us to see who and when will be here",
        "aug31_sync"
    )
    lower = q.lower()
    # Must contain searchable keywords from the fact — not the taxonomy label
    assert any(w in lower for w in ["availability", "calendar", "scheduling", "when"]), (
        f"question should contain fact keywords, got: {q!r}"
    )
    # Must reference the source slug
    assert "aug31_sync" in q, f"question should include source slug, got: {q!r}"
    # Must NOT be just the taxonomy label pattern
    assert "IntegrationPoint items" not in q, f"must not use taxonomy label, got: {q!r}"


def test_question_from_fact_ends_with_question_mark():
    """_question_from_fact result must end with a question mark."""
    q = G._question_from_fact("deploy the auth module by Friday", "sep10_sync")
    assert q.strip().endswith("?"), f"question must end with ?, got: {q!r}"


def test_query_suffix_from_fact_extracts_keywords():
    """_query_suffix_from_fact extracts meaningful keywords from the verbatim quote."""
    suffix = G._query_suffix_from_fact(
        "availability calendar which allows us to see who and when will be here"
    )
    # Must contain real keywords (>3 chars, not stop words)
    assert "availability" in suffix or "calendar" in suffix, (
        f"suffix should contain fact keywords, got: {suffix!r}"
    )
    # Must NOT contain stop words alone
    assert suffix.strip() != "", "suffix must not be empty"


def test_query_suffix_from_fact_excludes_stop_words():
    """_query_suffix_from_fact drops common stop words (the, and, which, etc.)."""
    suffix = G._query_suffix_from_fact("the team will coordinate with the vendor")
    words = suffix.lower().split()
    stop = {"the", "and", "with", "will", "which", "that", "this", "for"}
    assert not all(w in stop for w in words), (
        f"suffix should not be only stop words, got: {suffix!r}"
    )


def test_from_db_single_session_must_contain_uses_chunk_keyword_not_not_established(tmp_path):
    # RCA: "slug | not established in slug" as must_contain gives the grader a false prior.
    # When a source HAS chunk data, the second option should be a keyword from the chunk,
    # not "not established" — which causes LLM judges to fail correct answers that cite
    # specific facts by deciding they must be "hallucinated" since "not established" implies
    # the source might be empty.
    # Contract: single-session must_contain for a source WITH chunks must NOT contain
    # the phrase "not established in".
    db = str(tmp_path / "k.sqlite")
    _make_sqlite_db(db)
    out = str(tmp_path / "evals.csv")
    G.main(["--db", db, "--out", out])
    rows = list(csv.DictReader(open(out)))
    single = [r for r in rows if r["scope"] == "single-session"]
    assert len(single) >= 1, "Expected at least one single-session eval"
    for r in single:
        must = r["expected_answer_must_contain"]
        assert "not established in" not in must, (
            "Single-session eval from a source WITH chunks must NOT use 'not established in' "
            "as must_contain option — grader will falsely fail correct answers. got: {!r}".format(must)
        )


def test_extraction_mode_question_uses_fact_not_label(tmp_path):
    """In extraction mode, question is derived from verbatim_quote, not taxonomy label."""
    _make_extraction(tmp_path, "aug31_sync", [{
        "id": "ext-001",
        "category": "IntegrationPoint",
        "verbatim_quote": "availability calendar which allows us to see who and when will be here",
        "context": "Team discussed using a shared calendar for visibility.",
        "owner": "", "product": "TP", "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    ip_rows = [r for r in rows if r["category"] == "IntegrationPoint" and r["scope"] == "single-session"]
    assert len(ip_rows) >= 1, "Expected at least one IntegrationPoint single-session eval"
    for r in ip_rows:
        q = r["question"]
        # Must NOT be the generic taxonomy label question
        assert "What integration points were identified?" not in q, (
            f"question must not use taxonomy label: {q!r}"
        )
        # Must contain keywords from the verbatim quote
        lower = q.lower()
        assert any(w in lower for w in ["availability", "calendar", "when", "scheduling"]), (
            f"question must contain fact keywords, got: {q!r}"
        )


def test_extraction_mode_query_suffix_uses_fact_keywords(tmp_path):
    """In extraction mode, query_suffix contains keywords from verbatim_quote."""
    _make_extraction(tmp_path, "aug31_sync", [{
        "id": "ext-001",
        "category": "IntegrationPoint",
        "verbatim_quote": "availability calendar which allows us to see who and when will be here",
        "context": "Shared calendar discussed.",
        "owner": "", "product": "TP", "confidence": "DirectStatement",
    }])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    ip_rows = [r for r in rows if r["category"] == "IntegrationPoint" and r["scope"] == "single-session"]
    assert len(ip_rows) >= 1
    for r in ip_rows:
        suffix = r["query_suffix"]
        assert "availability" in suffix or "calendar" in suffix, (
            f"query_suffix must contain fact keywords for retrieval, got: {suffix!r}"
        )
        # Must NOT be the taxonomy eval_config value
        assert suffix != "dependency connection", (
            "query_suffix must come from the fact, not the taxonomy eval_config"
        )


def test_cross_session_question_uses_fact_keywords_not_label(tmp_path):
    """Cross-session question must be derived from combined fact keywords, not taxonomy label."""
    _make_extraction(tmp_path, "aug31_sync", [
        {"id": "ext-001", "category": "IntegrationPoint",
         "verbatim_quote": "availability calendar allows team to see scheduling",
         "context": "Calendar discussed.", "owner": "", "product": "TP", "confidence": "DirectStatement"},
    ])
    _make_extraction(tmp_path, "sep10_sync", [
        {"id": "ext-002", "category": "IntegrationPoint",
         "verbatim_quote": "Salesforce CRM connected to the billing pipeline",
         "context": "CRM integration.", "owner": "", "product": "TP", "confidence": "DirectStatement"},
    ])
    out = tmp_path / "evals.csv"
    G.main(["--extractions", str(tmp_path), "--taxonomy", _make_taxonomy(tmp_path), "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    cross = [r for r in rows if r["category"] == "IntegrationPoint" and r["scope"] == "cross-session"]
    assert len(cross) >= 1, "Expected cross-session eval with 2 sources"
    for r in cross:
        q = r["question"].lower()
        # Must NOT be the generic taxonomy label question
        assert "what integration points were identified" not in q, (
            f"cross-session question must not be taxonomy label, got: {r['question']!r}"
        )
        # Must contain keywords from the verbatim facts
        assert any(w in q for w in ["availability", "calendar", "salesforce", "crm", "billing", "scheduling"]), (
            f"cross-session question must contain fact keywords, got: {r['question']!r}"
        )
