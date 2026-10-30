"""Tests for lint(): C17 duplicate pairs, C18 open-under-closed, C19 dependency cycles,
C20 story open but all task-children done. Invented data only (public repo).

All nodes come from a plain dict N (id → node) matching what ledger.fold() returns;
no real store is needed for these deterministic checks.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.lint import lint


def _node(nid, kind, title, parent=None, state="open", merged_into=None, depends_on=None):
    return {"id": nid, "kind": kind, "title": title, "parent": parent,
            "state": state, "merged_into": merged_into,
            "depends_on": list(depends_on or [])}


# ---------------------------------------------------------------------------
# C17: duplicate pairs (same-kind, open, Jaccard ≥ 0.5)
# ---------------------------------------------------------------------------

def test_three_nodes_for_one_dependency_are_flagged():
    """Three near-identical open tasks under the same story: all three pairs are C17 duplicates.
    Invented Harbor Logistics scenario: three tasks all titled 'Configure shipping webhook <variant>'.
    """
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01"),
        "T001": _node("T001", "task",  "Configure shipping webhook integration", parent="S001"),
        "T002": _node("T002", "task",  "Configure shipping webhook connection",  parent="S001"),
        "T003": _node("T003", "task",  "Configure shipping webhook setup",       parent="S001"),
    }
    result = lint(N)
    dups = result["C17_duplicates"]
    pairs = {(d[0], d[1]) for d in dups}
    assert ("T001", "T002") in pairs
    assert ("T001", "T003") in pairs
    assert ("T002", "T003") in pairs
    for a, b, _ in dups:
        assert a < b, f"expected id_a < id_b, got ({a}, {b})"
    # scores should be floats ≥ 0.5
    for _, _, score in dups:
        assert isinstance(score, float)
        assert score >= 0.5


def test_done_nodes_not_flagged_as_c17_duplicates():
    """A done task is not 'open' for C17 purposes."""
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01"),
        "T001": _node("T001", "task",  "Configure shipping webhook integration", parent="S001"),
        "T002": _node("T002", "task",  "Configure shipping webhook connection",  parent="S001", state="done"),
    }
    result = lint(N)
    pairs = {(d[0], d[1]) for d in result["C17_duplicates"]}
    assert ("T001", "T002") not in pairs


def test_c17_sorted_by_ids():
    """C17 list is sorted (id_a, id_b) ascending."""
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01"),
        "T001": _node("T001", "task",  "Configure shipping webhook integration", parent="S001"),
        "T002": _node("T002", "task",  "Configure shipping webhook connection",  parent="S001"),
        "T003": _node("T003", "task",  "Configure shipping webhook setup",       parent="S001"),
    }
    dups = lint(N)["C17_duplicates"]
    pairs = [(d[0], d[1]) for d in dups]
    assert pairs == sorted(pairs)


# ---------------------------------------------------------------------------
# C18: open task/story under a done or cancelled parent
# ---------------------------------------------------------------------------

def test_open_task_under_done_story_is_flagged():
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Onboarding materials", parent="E01", state="done"),
        "T001": _node("T001", "task",  "Upload crew handbook to portal", parent="S001"),   # open under done
        "T002": _node("T002", "task",  "Send welcome email to new joiners", parent="S001", state="done"),
    }
    result = lint(N)
    assert "T001" in result["C18_open_under_closed"]
    assert "T002" not in result["C18_open_under_closed"]


def test_open_story_under_cancelled_epic_is_flagged():
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management", state="cancelled"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01"),   # open under cancelled
        "S002": _node("S002", "story", "Done integration",    parent="E01", state="done"),
    }
    result = lint(N)
    assert "S001" in result["C18_open_under_closed"]
    assert "S002" not in result["C18_open_under_closed"]


def test_c18_merged_node_not_flagged():
    """Merged/deleted nodes are ignored everywhere."""
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01", state="done"),
        "T001": _node("T001", "task",  "Upload crew handbook", parent="S001", merged_into="T002"),
        "T002": _node("T002", "task",  "Upload crew handbook updated", parent="S001"),
    }
    result = lint(N)
    assert "T001" not in result["C18_open_under_closed"]


# ---------------------------------------------------------------------------
# C19: dependency cycles among tasks/stories
# ---------------------------------------------------------------------------

def test_dependency_cycle_is_flagged():
    """T001 → T002 → T001 is a cycle."""
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01"),
        "T001": _node("T001", "task",  "Configure webhook",      parent="S001", depends_on=["T002"]),
        "T002": _node("T002", "task",  "Test webhook response",  parent="S001", depends_on=["T001"]),
        "T003": _node("T003", "task",  "Write documentation",    parent="S001"),   # no cycle
    }
    result = lint(N)
    assert result["C18_open_under_closed"] == []
    assert result["C20_open_with_done_children_all"] == []
    cycles = result["C19_cycles"]
    assert len(cycles) == 1
    assert sorted(cycles[0]) == ["T001", "T002"]


def test_three_node_cycle_is_flagged():
    """T010 → T011 → T012 → T010."""
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Dock assignment", parent="E01"),
        "T010": _node("T010", "task",  "Design grid",        parent="S001", depends_on=["T012"]),
        "T011": _node("T011", "task",  "Implement algorithm",parent="S001", depends_on=["T010"]),
        "T012": _node("T012", "task",  "Write UI",           parent="S001", depends_on=["T011"]),
    }
    result = lint(N)
    cycles = result["C19_cycles"]
    assert len(cycles) == 1
    assert sorted(cycles[0]) == ["T010", "T011", "T012"]


def test_dag_has_no_cycle():
    """T001 → T002 is a valid dependency (no cycle)."""
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01"),
        "T001": _node("T001", "task",  "Deploy webhook",    parent="S001", depends_on=["T002"]),
        "T002": _node("T002", "task",  "Configure webhook", parent="S001"),
    }
    result = lint(N)
    assert result["C19_cycles"] == []


# ---------------------------------------------------------------------------
# C20: open story with ≥ 1 task child, all task children done
# ---------------------------------------------------------------------------

def test_story_with_all_tasks_done_but_open_is_flagged():
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Dock assignment system", parent="E01"),     # open, all tasks done → C20
        "S002": _node("S002", "story", "Crew scheduling",        parent="E01"),     # open, one task still open
        "T001": _node("T001", "task",  "Design dock grid",                parent="S001", state="done"),
        "T002": _node("T002", "task",  "Implement assignment algorithm",   parent="S001", state="done"),
        "T003": _node("T003", "task",  "Write crew roster module",         parent="S002", state="done"),
        "T004": _node("T004", "task",  "Add scheduling UI",                parent="S002"),      # still open
    }
    result = lint(N)
    assert "S001" in result["C20_open_with_done_children_all"]
    assert "S002" not in result["C20_open_with_done_children_all"]


def test_story_with_no_task_children_not_flagged():
    """C20 requires ≥ 1 task child; a story with only story children is not flagged."""
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Carrier integration", parent="E01"),  # no task children
    }
    result = lint(N)
    assert "S001" not in result["C20_open_with_done_children_all"]


def test_done_story_not_flagged_c20():
    """A done story is not 'open' for C20."""
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Dock assignment", parent="E01", state="done"),
        "T001": _node("T001", "task",  "Design grid", parent="S001", state="done"),
    }
    result = lint(N)
    assert "S001" not in result["C20_open_with_done_children_all"]


def test_c20_cancelled_story_not_flagged():
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Management"),
        "S001": _node("S001", "story", "Dock assignment", parent="E01", state="cancelled"),
        "T001": _node("T001", "task",  "Design grid", parent="S001", state="done"),
    }
    result = lint(N)
    assert "S001" not in result["C20_open_with_done_children_all"]


# ---------------------------------------------------------------------------
# Clean tree
# ---------------------------------------------------------------------------

def test_lint_clean_tree_is_empty():
    """A well-formed tree with no issues returns all empty lists.

    Tasks are under different stories (whose titles share no tokens), so the parent-title
    Jaccard bonus is 0 and title Jaccard is also 0 — no pair reaches the 0.5 threshold.
    """
    N = {
        "E01":  _node("E01",  "epic",  "Harbor Fleet Operations"),
        "E02":  _node("E02",  "epic",  "Port Infrastructure Projects"),
        "S001": _node("S001", "story", "Cargo tracking integration",  parent="E01"),
        "S002": _node("S002", "story", "Berth allocation system",     parent="E02"),
        # Tasks under different stories → parent tokens don't overlap → bonus = 0
        "T001": _node("T001", "task",  "Configure transponder data feeds", parent="S001",
                      depends_on=["T002"]),
        "T002": _node("T002", "task",  "Build berth management module",    parent="S002"),
    }
    result = lint(N)
    assert result["C17_duplicates"] == []
    assert result["C18_open_under_closed"] == []
    assert result["C19_cycles"] == []
    assert result["C20_open_with_done_children_all"] == []


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------

def test_lint_returns_expected_keys():
    result = lint({})
    assert set(result.keys()) == {"C17_duplicates", "C18_open_under_closed",
                                  "C19_cycles", "C20_open_with_done_children_all"}
