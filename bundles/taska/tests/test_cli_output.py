"""The CLI is the agents' only tool: its answers must be readable in slices and answer what agents otherwise improvise
shell for (Nov 3: 26 of 292 tool calls wasted on pipes, wc and greps of saved output). Invented data only."""
import json
import subprocess
import sys

import pytest

import taska
from conftest import RUN_DIR, run_cli
from taska_lib import config as C, ledger as L
from test_leftovers import EV as LONG_EV, _project as long_project

Q_BOOKED = "warehouse walkthrough session with the client is booked for next Tuesday"


def _raw(project, *args):
    r = subprocess.run([sys.executable, str(RUN_DIR / "taska.py"), "--project", str(project), *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture
def tree(project):
    run_cli(project, "setup")
    run_cli(project, "sync")
    cfg = C.load(project)
    d1 = run_cli(project, "pending")["items"][0]["id"]
    r = L.apply(cfg.store, cfg, d1, [
        {"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Warehouse understood",
         "ac": ["Walkthrough held"], "quote": Q_BOOKED},
        {"op": "create", "kind": "task", "type": "session", "parent": "s1", "title": "Warehouse walkthrough",
         "due_text": "next Tuesday", "quote": Q_BOOKED}])
    assert not r["rejected"]
    return project, cfg


def test_context_is_multiline_and_its_lines_fit_a_read(tree):
    lines = _raw(tree[0], "context").splitlines()
    assert len(lines) > 1 and max(map(len, lines)) <= 2000   # Read shows 2,000 characters of a line


def test_context_without_flags_is_unchanged(tree):
    project, cfg = tree
    assert run_cli(project, "context") == json.loads(json.dumps(L.context(cfg.store, cfg), default=str))


def test_large_output_names_the_filter_and_drops_nothing(capsys):
    big = {"status": "ok", "nodes": [{"id": f"T{i:03d}", "title": "x" * 80} for i in range(400)]}
    with pytest.raises(SystemExit):
        taska.out(big, next_hint="taska context --ids T001,T002 | --compact")
    o = json.loads(capsys.readouterr().out)
    assert o["next"] == "narrow with: taska context --ids T001,T002 | --compact" and len(o["nodes"]) == 400


def test_small_output_has_no_next(capsys):
    with pytest.raises(SystemExit):
        taska.out({"status": "ok"}, next_hint="taska context --compact")
    assert "next" not in json.loads(capsys.readouterr().out)


def test_pending_rows_carry_the_meeting_size(project):
    run_cli(project, "setup")
    run_cli(project, "sync")
    row = run_cli(project, "pending")["items"][0]
    assert isinstance(row["words"], int) and row["words"] > 0 and row["chars"] > row["words"]


def test_context_compact_keeps_the_tree_and_drops_the_detail(tree):
    epic = run_cli(tree[0], "context", "--compact")["epics"][0]
    story = epic["stories"][0]
    assert story["id"] == "S001" and "ac" not in story and "since" not in story
    assert story["tasks"][0] == {"id": "T001", "title": "Warehouse walkthrough", "state": "open", "due": "2026-03-10"}


def test_context_ids_returns_those_nodes_with_their_children(tree):
    o = run_cli(tree[0], "context", "--ids", "S001,T001")
    assert [n["id"] for n in o["nodes"]] == ["S001", "T001"] and o["nodes"][0]["tasks"][0]["id"] == "T001"
    assert "epics" not in o and o["people"]


def test_context_ids_says_which_ids_are_unknown(tree):
    o = run_cli(tree[0], "context", "--ids", "S001,T999")
    assert [n["id"] for n in o["nodes"]] == ["S001"] and o["unknown"] == ["T999"]


def test_leftovers_one_part(tmp_path):
    long_project(tmp_path)
    full = run_cli(tmp_path, "leftovers", "--evidence", LONG_EV)
    o = run_cli(tmp_path, "leftovers", "--evidence", LONG_EV, "--part", "2")
    assert [p["part"] for p in o["parts"]] == [2] and o["parts"][0] == full["parts"][1]
    assert o["token"] == full["token"] and o["count"] == full["count"]   # done's token is the meeting's, not the part's


def test_readable_output_costs_almost_nothing_over_one_line(tree):
    project, cfg = tree
    raw = _raw(project, "context")
    one_line = json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":"))
    assert len(raw) <= 1.1 * len(one_line)   # indentation was +33 % characters on a real tree, re-read every turn


def test_context_ids_tolerate_spaces(tree):
    o = run_cli(tree[0], "context", "--ids", "S001, T001")
    assert [n["id"] for n in o["nodes"]] == ["S001", "T001"] and o["unknown"] == []


def test_the_next_hint_has_no_pipe(capsys):
    with pytest.raises(SystemExit):
        taska.out({"nodes": ["x" * 100] * 300}, next_hint=taska.CONTEXT_HINT)
    assert "|" not in json.loads(capsys.readouterr().out)["next"]


def test_leftovers_part_also_narrows_the_decision_cues(tmp_path):
    long_project(tmp_path)
    o = run_cli(tmp_path, "leftovers", "--evidence", LONG_EV, "--part", "2")
    assert {p["part"] for p in o["decisions"]["parts"]} == {2}
