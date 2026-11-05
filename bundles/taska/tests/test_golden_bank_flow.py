"""The golden bank from generation to a graded trial: write with the owner's reviewed checks, the recorded write
inputs that regenerate it, the project builder, a trial copy's queue and promotion. No model is called. Invented data
only (the Harbor Logistics cast)."""
import json
import sys
from pathlib import Path

import pytest

from conftest import run_cli

_TASKA_ROOT = Path(__file__).resolve().parents[1]
EVALS = _TASKA_ROOT / "evals"
for _p in (_TASKA_ROOT / "skills" / "run", EVALS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bank as B  # noqa: E402
import run as RUN  # noqa: E402
from golden import generate as G, record as R  # noqa: E402
from taska_lib import config as C, ledger as L, store as S  # noqa: E402
from taska_lib.coverage import LONG_CHARS  # noqa: E402

RULINGS = {"BRAINSTORM": "work", "PEOPLE_MATTER": "not_work"}


def _every(tasks):
    return [c for t in tasks for typ in ("must_create", "must_not_create", "no_duplicate") for c in t.get(typ, [])]


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    root = tmp_path_factory.mktemp("flow")
    draft, problems = G.bank_tasks([1], root / "p0", RULINGS)
    assert problems == []
    every = _every(draft)
    plain = next(c["id"] for c in every if not c["pending_ruling"] and c["id"].endswith("c01"))
    pending = next(c["id"] for c in every if c["pending_ruling"])
    (root / "rulings.json").write_text(json.dumps(RULINGS))
    (root / "reviewed.json").write_text(json.dumps([plain, pending]))
    rc = G.main(["write", "--seeds", "1", "--project", str(root / "p1"), "--out", str(root / "bank"), "--version", "v1",
                 "--date", "2026-11-03", "--rulings", str(root / "rulings.json"), "--reviewed", str(root / "reviewed.json")])
    assert rc == 0
    return root, root / "bank" / "bank.v1.jsonl", plain, pending


def test_write_reviewed_marks_the_owners_checks_but_never_a_pending_one(written):
    _, bank, plain, pending = written
    by = {c["id"]: c for c in _every(B.load_bank(bank)[1])}
    assert by[plain]["status"] == "reviewed"
    assert by[pending]["status"] == "draft" and by[pending]["pending_ruling"] is True
    assert sum(c["status"] == "reviewed" for c in by.values()) == 1


def test_write_reviewed_refuses_an_unknown_check_id(tmp_path, capsys):
    (tmp_path / "reviewed.json").write_text(json.dumps(["nope-c01"]))
    rc = G.main(["write", "--seeds", "1", "--project", str(tmp_path / "p"), "--out", str(tmp_path / "bank"),
                 "--version", "v1", "--reviewed", str(tmp_path / "reviewed.json")])
    assert rc == 2 and "nope-c01" in capsys.readouterr().err
    assert not list(tmp_path.rglob("bank.v1.jsonl"))


def test_the_review_page_shows_every_check_id(written):
    root, bank, plain, _ = written
    tasks = B.load_bank(bank)[1]
    page = G.review_page(tasks, [])
    assert all(f"<code>{c['id']}</code>" in page for c in _every(tasks))


def test_a_reviewed_check_turns_golden_on_three_agreeing_recordings_and_a_pending_one_never(written):
    _, bank, plain, pending = written
    tasks = B.load_bank(bank)[1]
    tid = next(t["task"] for t in tasks if any(c["id"] == plain for c in _every([t])))
    ptid = next(t["task"] for t in tasks if any(c["id"] == pending for c in _every([t])))
    grades = {t: [{"task": t, "status": "done", "checks": [{"id": c["id"], "result": "pass"} for c in _every(
        [x for x in tasks if x["task"] == t])]} for _ in range(3)] for t in {tid, ptid}}
    out, _ = R.promote(tasks, grades)
    by = {c["id"]: c for c in _every(out)}
    assert by[plain]["status"] == "golden"
    assert by[pending]["status"] != "golden"


def test_write_records_its_inputs_beside_the_bank(written):
    root, bank, plain, pending = written
    meta = json.loads(G.meta_path(bank).read_text())
    assert meta == {"version": "v1", "seeds": [1], "rulings": RULINGS, "reviewed": [plain, pending],
                    "date": "2026-11-03", "note": "golden generator: seeds [1]"}


def test_a_written_bank_regenerates_from_its_recorded_inputs(written, tmp_path):
    root, bank, _, _ = written
    assert G.regeneration_problems(bank, tmp_path / "again") == []
    meta = G.meta_path(bank)
    other = tmp_path / "other"
    other.mkdir()
    (other / bank.name).write_bytes(bank.read_bytes())
    (other / meta.name).write_text(json.dumps(json.loads(meta.read_text()) | {"seeds": [2]}))
    assert G.regeneration_problems(other / bank.name, tmp_path / "again2")


def test_a_promoted_bank_keeps_its_recorded_inputs(written, tmp_path):
    root, bank, plain, _ = written
    work = tmp_path / "bank"
    work.mkdir()
    (work / bank.name).write_bytes(bank.read_bytes())
    (work / G.meta_path(bank).name).write_bytes(G.meta_path(bank).read_bytes())
    tasks = B.load_bank(bank)[1]
    rec_root = tmp_path / "recorded"
    for t in tasks:
        for n in (1, 2, 3):
            d = rec_root / "v1" / f"{t['task']}-{n}"
            d.mkdir(parents=True)
            (d / "grade.json").write_text(json.dumps({"task": t["task"], "status": "done", "checks": [
                {"id": c["id"], "result": "pass"} for c in _every([t])]}))
    new, _ = R.promote_bank(work / bank.name, "v2", "promoted", root=rec_root)
    meta = json.loads(G.meta_path(new).read_text())
    assert meta["promoted_from"] == "v1" and meta["seeds"] == [1] and meta["version"] == "v2"
    assert G.regeneration_problems(new, tmp_path / "again") == []


def test_every_committed_golden_bank_regenerates_from_its_seeds(tmp_path):
    banks = sorted(G._HERE.glob("bank.v*.jsonl"))
    if not banks:
        pytest.skip("no golden bank is committed yet")
    for n, bank in enumerate(banks):
        assert G.regeneration_problems(bank, tmp_path / f"p{n}") == [], bank.name


def test_the_project_builder_syncs_a_project_the_bank_grades(written, tmp_path, capsys):
    _, bank, _, _ = written
    assert G.main(["project", "--bank", str(bank), "--out", str(tmp_path / "built")]) == 0
    B.check_transcripts(tmp_path / "built", B.load_bank(bank)[1])
    assert G.main(["project", "--bank", str(bank), "--out", str(tmp_path / "built")]) == 2
    assert "not empty" in capsys.readouterr().err


def test_long_meeting_checks_are_only_on_meetings_that_reach_long_chars(written):
    root, bank, _, _ = written
    ev = S.evidence(C.load(root / "p1").store)
    for t in B.load_bank(bank)[1]:
        if any("long_meeting" in c.get("caps", []) for c in _every([t])):
            assert len(S.full_text(ev[t["evidence"]])) >= LONG_CHARS, t["archetype"]


def _trial(project, task, tmp_path, monkeypatch, name, from_copy=None):
    seen: dict[str, Path] = {}

    def no_model(ai, copy, tier, timeout_s):
        seen["copy"] = Path(copy)
        return "error", "no model in a test"
    monkeypatch.setattr(B, "_run_model", no_model)
    monkeypatch.setattr(RUN, "SESSIONS", tmp_path / "sessions")
    B.trial(project, task, 1, "low", ["stand-in"], tmp_path / name, 60, copies_root=tmp_path / f"copies-{name}",
            from_copy=from_copy)
    return seen["copy"]


def _pending(copy):
    return [i["id"] for i in run_cli(copy, "pending")["items"]]


def test_a_trial_copy_of_a_later_meeting_queues_only_that_meeting_and_a_chain_still_requeues(written, tmp_path,
                                                                                              monkeypatch):
    root, bank, _, _ = written
    tasks = B.load_bank(bank)[1]
    first = next(t for t in tasks if t["archetype"] == "continuity_1")
    second = next(t for t in tasks if t.get("after") == [first["task"]])
    copy = _trial(root / "p1", first, tmp_path, monkeypatch, "a")
    assert _pending(copy) == [first["evidence"]]
    L.mark_processed(C.load(copy).store, first["evidence"], "read")   # what the model's `done` leaves
    later = _trial(root / "p1", second, tmp_path, monkeypatch, "b", from_copy=copy)
    assert _pending(later) == [second["evidence"]]


def test_write_refuses_before_writing_when_the_versions_inputs_file_exists(tmp_path, capsys):
    (tmp_path / "bank").mkdir()
    (tmp_path / "bank" / "bank.v1.meta.json").write_text("{}")
    rc = G.main(["write", "--seeds", "1", "--project", str(tmp_path / "p"), "--out", str(tmp_path / "bank"),
                 "--version", "v1"])
    assert rc == 2 and "never overwritten" in capsys.readouterr().err
    assert not (tmp_path / "bank" / "bank.v1.jsonl").exists()
