"""The planted suite's recorded runs, replayed: each invented meeting was extracted by the real nightly pass
(evals/planted/run.sh --keep evals/planted/recorded SEED...), and its ledger must meet the planted bars: every form
found, declined work never created as a task or story, nothing invented."""
import json
import shutil
import sys
from pathlib import Path

import pytest

PLANTED = Path(__file__).resolve().parents[1] / "evals" / "planted"
sys.path.insert(0, str(PLANTED))

from generate import per_form  # noqa: E402
from taska_lib.evalscore import score  # noqa: E402

RUNS = sorted((PLANTED / "recorded").glob("seed-*"))
pytestmark = pytest.mark.skipif(not RUNS, reason="no recorded planted runs: evals/planted/run.sh --keep evals/planted/recorded 1 2 3 4 5")


@pytest.fixture(params=RUNS, ids=lambda p: p.name)
def run(request, tmp_path):
    copy = tmp_path / request.param.name
    shutil.copytree(request.param, copy)
    return copy, json.loads((copy / "gold.json").read_text())


def test_every_planted_form_is_found(run):
    project, gold = run
    r = per_form(project / ".taska", gold)
    assert not r["incomplete"]
    missed = {f: f"{c['ok']}/{c['n']}" for f, c in r["forms"].items() if c["ok"] < c["n"]}
    assert not missed, missed


def test_nothing_is_invented(run):
    project, gold = run
    b15 = score(project / ".taska", gold)["B15"]
    assert b15["ok"] == b15["n"], b15["fails"]
