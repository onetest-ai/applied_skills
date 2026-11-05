"""Tests for similar(): token-set Jaccard similarity for taska nodes.

t = Jaccard(title_tokens); score = min(1.0, t + 0.25 * Jaccard(parent_title_tokens))
only when t >= 0.3 and both have parents; else score = t.
Stopwords and single-character tokens are excluded. Invented data only (public repo).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.similar import similar


def _node(nid, kind, title, parent=None, state="open", merged_into=None):
    return {"id": nid, "kind": kind, "title": title, "parent": parent,
            "state": state, "merged_into": merged_into, "depends_on": []}


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------

def test_no_candidates_of_right_kind_returns_empty():
    N = {
        "E01": _node("E01", "epic", "Harbor Fleet Management"),
    }
    assert similar(N, "Configure webhook integration", "task") == []


def test_exact_title_match_scores_one():
    N = {
        "T001": _node("T001", "task", "Configure webhook integration"),
        "T002": _node("T002", "task", "Configure webhook integration"),
    }
    results = similar(N, "Configure webhook integration", "task", node_id="T001")
    assert len(results) == 1
    assert results[0]["id"] == "T002"
    assert results[0]["score"] == pytest.approx(1.0)


def test_stopwords_excluded_from_tokens():
    """'the', 'of', 'the' are stopwords and stripped; score is based on remaining tokens only."""
    N = {
        "T001": _node("T001", "task", "the integration of the webhook"),
        "T002": _node("T002", "task", "integration webhook setup"),
    }
    # T001 after stopwords → {integration, webhook}; T002 → {integration, webhook, setup}
    # Jaccard = 2 / 3
    results = similar(N, "the integration of the webhook", "task", node_id="T001")
    assert len(results) == 1
    assert results[0]["score"] == pytest.approx(2 / 3)


def test_single_char_tokens_excluded():
    """Single-character tokens (e.g. 'a') must not inflate Jaccard scores."""
    N = {
        "T001": _node("T001", "task", "a b c configure webhook"),
        "T002": _node("T002", "task", "configure webhook test"),
    }
    # tokens(T001) after stripping stopword 'a', length-1 'b'/'c' → {configure, webhook}
    # tokens(T002) → {configure, webhook, test}
    # Jaccard = 2/3
    results = similar(N, "a b c configure webhook", "task", node_id="T001")
    assert results[0]["score"] == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Exclusion rules
# ---------------------------------------------------------------------------

def test_merged_node_excluded():
    N = {
        "T001": _node("T001", "task", "Configure shipping webhook"),
        "T002": _node("T002", "task", "Configure shipping webhook", merged_into="T001"),
    }
    results = similar(N, "Configure shipping webhook", "task", node_id="T001")
    assert results == []


def test_deleted_node_excluded():
    N = {
        "T001": _node("T001", "task", "Configure shipping webhook"),
        "T002": _node("T002", "task", "Configure shipping webhook", state="deleted"),
    }
    results = similar(N, "Configure shipping webhook", "task", node_id="T001")
    assert results == []


def test_done_node_is_still_a_candidate():
    """similar() itself does not filter by done/cancelled; that's lint's job."""
    N = {
        "T001": _node("T001", "task", "Configure shipping webhook"),
        "T002": _node("T002", "task", "Configure shipping webhook", state="done"),
    }
    results = similar(N, "Configure shipping webhook", "task", node_id="T001")
    assert len(results) == 1 and results[0]["id"] == "T002"


def test_different_kind_not_returned():
    N = {
        "T001": _node("T001", "task", "Configure webhook integration"),
        "S001": _node("S001", "story", "Configure webhook integration"),
    }
    results = similar(N, "Configure webhook integration", "task", node_id="T001")
    assert results == []


# ---------------------------------------------------------------------------
# Parent bonus
# ---------------------------------------------------------------------------

def test_parent_bonus_applied_when_both_have_parents():
    """Same-parent candidate scores higher than different-parent candidate with identical title similarity.

    Titles are deliberately non-identical so the base Jaccard < 1.0 and the parent bonus
    makes a measurable difference rather than being capped away.
    """
    N = {
        "S001": _node("S001", "story", "Carrier integration"),
        "S002": _node("S002", "story", "Unrelated infrastructure feature"),
        # T001 is the query node; T002 shares parent S001, T003 has different parent S002
        "T001": _node("T001", "task", "Configure carrier notification dispatch", parent="S001"),
        "T002": _node("T002", "task", "Configure carrier event handler",         parent="S001"),
        "T003": _node("T003", "task", "Configure carrier event handler",         parent="S002"),
    }
    # Query against T001's title from T001's point of view (parent S001 known)
    results = similar(N, N["T001"]["title"], "task", node_id="T001", min_score=0.0, k=10)
    s002 = next(r for r in results if r["id"] == "T002")
    s003 = next(r for r in results if r["id"] == "T003")
    # T002 shares S001 with T001 → parent Jaccard = 1.0 → +0.25 bonus (t ≥ 0.3 gate passed)
    # T003 has S002 whose title shares no tokens with S001 → bonus = 0
    assert s002["score"] > s003["score"]


def test_no_parent_bonus_when_query_has_no_parent():
    """No parent bonus when the query node has no parent, even if candidates do."""
    N = {
        "S001": _node("S001", "story", "Carrier integration"),
        "T001": _node("T001", "task", "Configure carrier webhook"),          # no parent
        "T002": _node("T002", "task", "Configure carrier webhook", parent="S001"),
    }
    results = similar(N, N["T001"]["title"], "task", node_id="T001", min_score=0.0)
    assert len(results) == 1
    # No bonus applied, so score = pure Jaccard = 1.0
    assert results[0]["score"] == pytest.approx(1.0)


def test_no_parent_bonus_when_candidate_has_no_parent():
    """Bonus only applies when BOTH have parents."""
    N = {
        "S001": _node("S001", "story", "Carrier integration"),
        "T001": _node("T001", "task", "Configure carrier webhook", parent="S001"),
        "T002": _node("T002", "task", "Configure carrier webhook"),          # no parent
    }
    results = similar(N, N["T001"]["title"], "task", node_id="T001", min_score=0.0)
    assert len(results) == 1
    # No bonus for T002 (no parent); T001 has parent, T002 doesn't → no bonus
    assert results[0]["score"] == pytest.approx(1.0)


def test_siblings_with_no_shared_words_are_not_similar():
    """Two tasks under the same parent with completely disjoint titles must NOT be flagged.

    With the revised formula (parent bonus gated on t >= 0.3), title Jaccard = 0 means
    no bonus is applied and the score stays at 0.0, well below the 0.5 threshold.
    """
    from taska_lib.lint import lint as _lint
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01"),
        "T001": _node("T001", "task",  "Audit cargo manifest records",  parent="S001"),
        "T002": _node("T002", "task",  "Schedule crew safety briefing", parent="S001"),
    }
    # similar() should return nothing at min_score=0.5
    results = similar(N, N["T001"]["title"], "task", node_id="T001", min_score=0.5)
    assert results == [], f"expected no results, got {results}"
    # score must be strictly below the threshold
    results_all = similar(N, N["T001"]["title"], "task", node_id="T001", min_score=0.0)
    assert len(results_all) == 1
    assert results_all[0]["score"] < 0.5, f"expected score < 0.5, got {results_all[0]['score']}"
    # C17 must not flag this pair
    c17_pairs = {(d[0], d[1]) for d in _lint(N)["C17_duplicates"]}
    assert ("T001", "T002") not in c17_pairs


# ---------------------------------------------------------------------------
# Ordering and k-cap
# ---------------------------------------------------------------------------

def test_results_sorted_by_score_desc_then_id():
    """Ties in score are broken by id (ascending)."""
    N = {
        "E01": _node("E01", "epic", "Harbor Fleet"),
        "T001": _node("T001", "task", "Deploy container service"),
        # 3/4 = 0.75 overlap with query "Deploy container service"
        "T002": _node("T002", "task", "Deploy container service update"),
        # 3/4 = 0.75 overlap
        "T004": _node("T004", "task", "Provision container service deploy"),
        # 2/4 = 0.5 overlap
        "T003": _node("T003", "task", "Deploy containerised service"),
    }
    results = similar(N, "Deploy container service", "task", node_id="T001", min_score=0.0, k=10)
    scores = [r["score"] for r in results]
    for i in range(len(scores) - 1):
        if scores[i] == scores[i + 1]:
            assert results[i]["id"] < results[i + 1]["id"]
        else:
            assert scores[i] > scores[i + 1]


def test_k_limits_results():
    N = {f"T{i:03d}": _node(f"T{i:03d}", "task", f"Configure webhook {i}") for i in range(10)}
    results = similar(N, "Configure webhook 0", "task", node_id="T000", min_score=0.0, k=3)
    assert len(results) <= 3


def test_min_score_filters_low_similarity():
    N = {
        "T001": _node("T001", "task", "Configure webhook"),
        "T002": _node("T002", "task", "Write unit tests for payment module"),
    }
    results = similar(N, "Configure webhook", "task", node_id="T001", min_score=0.5)
    assert results == []


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------

def test_result_fields_present():
    N = {
        "S001": _node("S001", "story", "Carrier integration"),
        "T001": _node("T001", "task", "Configure webhook integration", parent="S001"),
        "T002": _node("T002", "task", "Configure webhook integration", parent="S001"),
    }
    results = similar(N, N["T001"]["title"], "task", node_id="T001")
    r = results[0]
    assert set(r.keys()) >= {"id", "title", "state", "parent", "parent_state", "score"}
    assert r["parent"] == "S001"
    assert r["parent_state"] == "open"
