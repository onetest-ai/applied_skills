"""Chained tasks (`after`): a trial starts from the copy the earlier trial left; a failed predecessor makes the chained
trial invalid without starting the model. Stand-in model, invented data."""
import json
import stat
import sys

from conftest import TASKA_ROOT
from test_bank_run import _setup, QUOTE

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402
from taska_lib import config as C, store as S  # noqa: E402


def _chain(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    second = dict(t, task="d2later", after=["d2task"], must_create=[], outcome=["processed"])
    first = dict(t, task="d2task")
    bank = B.write_bank(tmp_path / "bank2", [first, second], "v1", "2026-11-03", "chain")
    return day2, bank, stand


def test_a_chained_task_starts_from_its_predecessors_copy(project, tmp_path, monkeypatch):
    day2, bank, stand = _chain(project, tmp_path, monkeypatch)
    out = B.run_bank(project, bank, "low", 1, None, 2, [str(stand)], tmp_path / "out")
    grades = {p.parent.name: json.loads(p.read_text()) for p in (out / "trials").glob("*/grade.json")}
    assert grades["d2task-1"]["status"] == "done" and grades["d2later-1"]["status"] == "done", grades
    metas = {n: json.loads((out / "trials" / n / "meta.json").read_text())["copy"] for n in ("d2task-1", "d2later-1")}
    assert metas["d2task-1"] != metas["d2later-1"]
    later = [e for e in S.events(C.load(metas["d2later-1"]).store) if e.get("type") == "create" and e.get("by") == "agent"]
    ids = {e["id"] for e in later}
    assert "T11" in ids   # the earlier trial's node is in the later copy
    assert "d2later-1" in open(metas["d2later-1"] + "/taska").read()   # its wrapper points at its own folder


def test_a_chain_after_a_failed_trial_is_invalid_and_never_runs_the_model(project, tmp_path, monkeypatch):
    day2, bank, stand = _chain(project, tmp_path, monkeypatch)
    monkeypatch.setenv("RC", "3")
    out = B.run_bank(project, bank, "low", 1, None, 2, [str(stand)], tmp_path / "out")
    grades = {p.parent.name: json.loads(p.read_text()) for p in (out / "trials").glob("*/grade.json")}
    assert grades["d2task-1"]["status"] == "error"
    assert grades["d2later-1"]["status"] == "invalid" and "d2task" in grades["d2later-1"]["reason"]
    assert not list((project / "evals" / "runs").glob("bank-*/d2later-1.run.txt"))
    assert B.aggregate(list(grades.values()))["tasks"]["d2later"]["pass_hat_k"] is None


def test_a_chain_names_a_predecessor_that_is_not_selected(project, tmp_path, monkeypatch):
    day2, bank, stand = _chain(project, tmp_path, monkeypatch)
    import pytest
    with pytest.raises(ValueError, match="d2task"):
        B.run_bank(project, bank, "low", 1, ["d2later"], 1, [str(stand)], tmp_path / "out")


def test_a_successor_listed_first_still_runs_after_its_predecessor(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    second = dict(t, task="d2later", after=["d2task"], must_create=[], outcome=["processed"])
    bank = B.write_bank(tmp_path / "bank3", [second, dict(t, task="d2task")], "v1", "2026-11-03", "reversed")
    out = B.run_bank(project, bank, "low", 1, None, 2, [str(stand)], tmp_path / "out")
    grades = {p.parent.name: json.loads(p.read_text()) for p in (out / "trials").glob("*/grade.json")}
    assert grades["d2later-1"]["status"] == "done", grades["d2later-1"]
    copy = json.loads((out / "trials" / "d2later-1" / "meta.json").read_text())["copy"]
    assert "T11" in {e["id"] for e in S.events(C.load(copy).store) if e.get("type") == "create" and e.get("by") == "agent"}
