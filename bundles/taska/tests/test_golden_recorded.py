"""evals/golden/record.py: recorded golden-bank trials and the free replay gate that re-grades them on every test run.
No test calls a model: the paid path's trial runner is injected. Invented data only."""
import json
import sys
from pathlib import Path

import pytest

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402
from golden import record as R  # noqa: E402
from taska_lib import config as C, store as S  # noqa: E402
from test_bank_grade import MEET, S1, S2, node, project, task  # noqa: E402


def test_every_checked_in_recording_replays_to_its_recorded_verdicts_and_keeps_no_session_file():
    if not R.recorded_versions(R.RECORDED):
        pytest.skip("no golden recordings yet: record.py --bank FILE --tier low --k 3 --yes makes them")
    assert R.check_recorded(R.RECORDED, R.GOLDEN) == []


def test_the_gate_fails_clearly_when_a_recorded_version_has_no_bank_file(tmp_path):
    folder, _ = _trial_dir(tmp_path, f"{task()['task']}-1", [node("T001", "task", S1)], read={"1": "recorded"})
    R.save(folder, tmp_path / "recorded" / "v9" / folder.name, MEET)
    problems = R.check_recorded(tmp_path / "recorded", tmp_path / "golden")
    assert problems == [f"recordings of v9 have no bank file {tmp_path / 'golden' / 'bank.v9.jsonl'}"]


def test_the_gate_reports_drift_and_strays(tmp_path, monkeypatch):
    bank = _bank(tmp_path)
    folder, _ = _trial_dir(tmp_path, f"{task()['task']}-1", [node("T001", "task", S1), node("T002", "task", S2)],
                           read={"1": "recorded"})
    rec = R.save(folder, tmp_path / "recorded" / "v1" / folder.name, MEET)
    assert R.check_recorded(tmp_path / "recorded", bank.parent) == []
    (rec / "session.jsonl").write_text("{}\n")
    monkeypatch.setattr(B, "_violates_not", lambda check, sp: False)
    problems = R.check_recorded(tmp_path / "recorded", bank.parent)
    assert any("n-001" in p and rec.name in p for p in problems)
    assert any("session.jsonl" in p for p in problems)


def _trial_dir(root, name, nodes, read=None, processed=True):
    """A finished trial as run_bank leaves it: its copy, meta.json naming the copy, trace.json, grade.json and the
    session transcript the recording must leave behind."""
    copy = project(root / "copies" / name, nodes, read=read, processed=processed)
    (copy / "stray-session.jsonl").write_text('{"type": "assistant"}\n')
    t = task(copy=copy)
    folder = root / "trials" / name
    (folder / "transcript" / "sub").mkdir(parents=True)
    (folder / "transcript" / f"{name}.jsonl").write_text('{"type": "assistant", "cwd": "/x"}\n')
    (folder / "transcript" / "sub" / "agent-1.jsonl").write_text('{"type": "assistant"}\n')
    (folder / "meta.json").write_text(json.dumps({"tier": "low", "copy": str(copy), "session": "/s.jsonl", "status": "done"}))
    (folder / "trace.json").write_text(json.dumps({"schema": "eval-trace/1", "agents": []}))
    graded = B.grade(t, copy)
    trial = {"schema": "eval-trial/1", "task": t["task"], "trial": int(name.rsplit("-", 1)[1]), "status": "done",
             "pass": graded["pass"], "checks": graded["checks"], "unscored": graded["unscored"],
             "unlocated": graded["unlocated"], "reason": ""}
    (folder / "grade.json").write_text(json.dumps(trial))
    return folder, t


def _bank(tmp_path):
    ref = project(tmp_path / "ref", [node("T001", "task", S1)], read={"1": "recorded"})
    return B.write_bank(tmp_path / "bank", [task(copy=ref)], "v1", "2026-11-03", "invented")


def test_a_saved_recording_keeps_the_ledger_toml_trace_and_grade_and_no_session_file(tmp_path):
    folder, t = _trial_dir(tmp_path, f"{task()['task']}-1", [node("T001", "task", S1)], read={"1": "recorded"})
    rec = R.save(folder, tmp_path / "recorded" / "v1" / folder.name, MEET)
    assert (rec / ".taska" / "events.jsonl").is_file() and (rec / "taska.toml").is_file()
    assert (rec / "trace.json").is_file() and (rec / "grade.json").is_file()
    assert not (rec / "transcript").exists() and not (rec / "meta.json").exists()
    assert [p for p in rec.rglob("*.jsonl") if R.STORE not in p.relative_to(rec).parts] == []
    assert R.strays(rec) == []
    (rec / "agent-1.jsonl").write_text("{}\n")
    (rec / "transcript").mkdir()
    (rec / ".taska" / "x.jsonl").write_text("{}\n")
    assert R.strays(rec) == [".taska/x.jsonl", "agent-1.jsonl", "transcript"]


def test_the_replay_grades_every_recording_and_asserts_each_recorded_verdict(tmp_path):
    bank = _bank(tmp_path)
    tid = task()["task"]
    good, _ = _trial_dir(tmp_path, f"{tid}-1", [node("T001", "task", S1)], read={"1": "recorded"})
    bad, _ = _trial_dir(tmp_path, f"{tid}-2", [node("T001", "task", S1), node("T002", "task", S2)], read={"1": "recorded"})
    recs = [R.save(f, tmp_path / "recorded" / "v1" / f.name, MEET) for f in (good, bad)]
    rows = {r["recording"]: r for r in R.replay_recorded(bank, recs)}
    assert set(rows) == {f"{tid}-1", f"{tid}-2"} and all(r["drift"] == [] for r in rows.values())
    assert rows[f"{tid}-1"]["replayed"]["n-001"] == ("pass", None)
    assert rows[f"{tid}-2"]["recorded"]["n-001"] == ("fail", None) and rows[f"{tid}-2"]["status"] == "done"


def test_a_grader_change_that_flips_a_verdict_fails_the_replay(tmp_path, monkeypatch):
    bank = _bank(tmp_path)
    folder, _ = _trial_dir(tmp_path, f"{task()['task']}-1", [node("T001", "task", S1), node("T002", "task", S2)],
                           read={"1": "recorded"})
    rec = R.save(folder, tmp_path / "recorded" / "v1" / folder.name, MEET)
    monkeypatch.setattr(B, "_violates_not", lambda check, sp: False)
    row = R.replay_recorded(bank, [rec])[0]
    assert "n-001" in row["drift"] and "pass" in row["drift"]


def test_a_recording_whose_copy_no_longer_grades_is_drift_not_a_silent_skip(tmp_path):
    bank = _bank(tmp_path)
    folder, _ = _trial_dir(tmp_path, f"{task()['task']}-1", [node("T001", "task", S1)], read={"1": "recorded"})
    rec = R.save(folder, tmp_path / "recorded" / "v1" / folder.name, MEET)
    (rec / "taska.toml").unlink()
    row = R.replay_recorded(bank, [rec])[0]
    assert row["status"] == "error" and "status" in row["drift"]


def _fake_run(seen: dict, statuses: dict):
    """Stands in for bank.run_bank: k finished trials per task, laid out as run_bank lays them out. No model."""
    def run(project, bank_path, tier, k, tasks=None, parallel=4, ai=None, out_root=None, invalid=""):
        seen.update(project=project, tier=tier, k=k, tasks=tasks, out_root=out_root)
        _, bank = B.load_bank(bank_path)
        for t in bank:
            for n in range(1, k + 1):
                folder, _ = _trial_dir(out_root / f"n{n}", f"{t['task']}-{n}", [node("T001", "task", S1)],
                                       read={"1": "recorded"})
                dest = out_root / "trials" / folder.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                folder.rename(dest)
                st = statuses.get((t["task"], n), statuses.get(n, "done"))
                g = json.loads((dest / "grade.json").read_text())
                (dest / "grade.json").write_text(json.dumps(g | {"task": t["task"]} | (
                    {} if st == "done" else {"status": st, "pass": False})))
        return out_root
    return run


def test_record_bank_files_done_trials_under_the_bank_version_with_a_changelog_line(tmp_path):
    bank = _bank(tmp_path)
    proj = project(tmp_path / "proj", [], processed=False)
    seen: dict = {}
    root = tmp_path / "recorded"
    tid = task()["task"]
    res = R.record_bank(bank, "low", 3, project=proj, run=_fake_run(seen, {3: "timeout"}), root=root, note="first")
    assert seen["k"] == 3 and seen["tier"] == "low" and seen["project"] == proj
    assert res["recorded"] == [f"{tid}-1", f"{tid}-2"] and res["skipped"] == [{"trial": f"{tid}-3", "status": "timeout"}]
    assert sorted(p.name for p in (root / "v1").iterdir()) == [f"{tid}-1", f"{tid}-2"]
    assert all(R.strays(r) == [] for r in R.recordings(root / "v1"))
    log = (root / "CHANGELOG.md").read_text().splitlines()
    assert len(log) == 1 and log[0].startswith("- v1 (") and "2 recordings" in log[0] and "tier low" in log[0]
    assert "k=3" in log[0] and log[0].endswith("— first")
    R.record_bank(bank, "low", 1, project=proj, run=_fake_run(seen, {}), root=root, note="again")
    assert sorted(p.name for p in (root / "v1").iterdir()) == [f"{tid}-1"]   # a refresh replaces the task's recordings
    assert len((root / "CHANGELOG.md").read_text().splitlines()) == 2


def test_record_bank_refuses_a_project_whose_transcripts_differ_before_any_trial(tmp_path, monkeypatch):
    bank = _bank(tmp_path)
    other = project(tmp_path / "proj", [], processed=False, text="a different meeting entirely, nothing in common")
    monkeypatch.setattr(B, "trial", lambda *a, **k: pytest.fail("a trial ran on a project the bank cannot grade"))
    with pytest.raises(ValueError, match="transcript"):   # bank.run_bank's own check, the one every run passes
        R.record_bank(bank, "low", 1, project=other, root=tmp_path / "recorded")
    assert not (tmp_path / "recorded").exists()


def test_record_cli_refuses_without_yes_and_runs_nothing(tmp_path, capsys, monkeypatch):
    bank = _bank(tmp_path)
    monkeypatch.setattr(R, "record_bank", lambda *a, **kw: pytest.fail("record_bank ran without --yes"))
    assert R.main(["--bank", str(bank), "--tier", "low", "--k", "1"]) == 2
    assert "--yes" in capsys.readouterr().out


def _g(n: int, verdicts: dict[str, str], status: str = "done") -> dict:
    return {"task": "t1", "trial": n, "status": status,
            "checks": [{"id": cid, "result": r} for cid, r in verdicts.items()]}


def _task_with(status: str, difficulty=None) -> dict:
    return {"task": "t1", "must_create": [{"id": "c1", "status": status, "difficulty": difficulty}],
            "must_not_create": [{"id": "n1", "status": status, "difficulty": difficulty}]}


def test_a_reviewed_check_turns_golden_when_two_of_three_recordings_agree():
    grades = [_g(1, {"c1": "pass", "c1:dup": "pass", "n1": "pass"}),
              _g(2, {"c1": "pass", "c1:dup": "pass", "n1": "fail"}),
              _g(3, {"c1": "fail", "c1:dup": "n/a", "n1": "fail"})]
    (out,), changes = R.promote([_task_with("reviewed")], {"t1": grades})
    assert out["must_create"][0]["status"] == "golden" and out["must_not_create"][0]["status"] == "golden"
    assert out["must_create"][0]["difficulty"] == "medium" and out["must_not_create"][0]["difficulty"] == "medium"
    assert {c["id"] for c in changes} == {"c1", "n1"}


def test_status_stays_when_draft_or_when_recordings_disagree_or_are_too_few():
    split = [_g(1, {"c1": "pass", "c1:dup": "pass", "n1": "pass"}),
             _g(2, {"c1": "pass", "c1:dup": "fail", "n1": "pass"}),
             _g(3, {"c1": "fail", "c1:dup": "n/a", "n1": "pass"})]
    (out,), _ = R.promote([_task_with("reviewed")], {"t1": split})
    assert out["must_create"][0]["status"] == "reviewed"   # (pass,pass) (pass,fail) (fail,n/a): no two agree
    assert out["must_not_create"][0]["status"] == "golden" and out["must_not_create"][0]["difficulty"] == "easy"
    (out,), _ = R.promote([_task_with("draft")], {"t1": split})
    assert out["must_not_create"][0]["status"] == "draft" and out["must_not_create"][0]["difficulty"] == "easy"
    (out,), changes = R.promote([_task_with("reviewed", "hard")], {"t1": split[:2]})
    assert out["must_not_create"][0] == {"id": "n1", "status": "reviewed", "difficulty": "hard"} and changes == []
    timeouts = split[:2] + [_g(3, {}, status="timeout")]
    (out,), _ = R.promote([_task_with("reviewed")], {"t1": timeouts})
    assert out["must_not_create"][0]["status"] == "reviewed"   # a trial that did not finish is not a recording


def test_difficulty_is_hard_when_no_recording_found_it_and_pending_checks_use_their_ruled_verdict():
    grades = [{"task": "t1", "trial": n, "status": "done",
               "checks": [{"id": "c1", "result": "n/a", "ruled_result": "fail", "pending_ruling": True},
                          {"id": "c1:dup", "result": "n/a"}, {"id": "n1", "result": "fail"}]} for n in (1, 2, 3)]
    pending = _task_with("reviewed")
    pending["must_create"][0]["pending_ruling"] = True
    (out,), _ = R.promote([pending], {"t1": grades})
    assert out["must_create"][0]["difficulty"] == "hard" and out["must_not_create"][0]["difficulty"] == "hard"
    assert out["must_create"][0]["status"] == "reviewed"   # a check pending the owner's ruling is never golden
    assert out["must_not_create"][0]["status"] == "golden"


def test_promote_reads_the_recordings_on_disk_and_writes_a_new_bank_version(tmp_path):
    ref = project(tmp_path / "ref", [node("T001", "task", S1)], read={"1": "recorded"})
    t = task(copy=ref)
    t["must_create"][0]["status"] = "reviewed"
    bank = B.write_bank(tmp_path / "bank", [t], "v1", "2026-11-03", "invented")
    root = tmp_path / "recorded"
    for n in (1, 2, 3):
        folder, _ = _trial_dir(tmp_path / f"r{n}", f"{t['task']}-{n}", [node("T001", "task", S1)], read={"1": "recorded"})
        R.save(folder, root / "v1" / folder.name, MEET)
    new, changes = R.promote_bank(bank, "v2", "promoted from recordings", root=root)
    head, tasks = B.load_bank(new)
    assert head["version"] == "v2" and tasks[0]["must_create"][0]["status"] == "golden"
    assert tasks[0]["must_create"][0]["difficulty"] == "easy" and tasks[0]["must_not_create"][0]["status"] == "draft"
    assert [c["id"] for c in changes if c["status"] == "golden"] == ["k-001"]
    assert S.events(C.load(R.recordings(root / "v1")[0]).store)


def test_a_recording_without_a_second_read_or_never_processed_replays_with_zero_drift(tmp_path):
    bank = _bank(tmp_path)
    tid = task()["task"]
    no_read, _ = _trial_dir(tmp_path, f"{tid}-1", [node("T001", "task", S1)])
    unread, _ = _trial_dir(tmp_path, f"{tid}-2", [node("T001", "task", S1)], processed=False)
    recs = [R.save(f, tmp_path / "recorded" / "v1" / f.name, MEET) for f in (no_read, unread)]
    assert json.loads((recs[0] / "recording.json").read_text()) == {"task": tid, "code_has_second_read": True,
                                                                     "processed": True}
    assert json.loads((recs[1] / "recording.json").read_text())["processed"] is False
    rows = {r["recording"]: r for r in R.replay_recorded(bank, recs)}
    assert rows[f"{tid}-1"]["recorded"]["decisions_read"] == ("fail", None)
    assert rows[f"{tid}-2"]["recorded"]["processed"] == ("fail", None)
    assert all(r["drift"] == [] and r["status"] == "done" for r in rows.values())


HOME = "/home/nora.quill/secret-place/notes.txt"
SID = "6f1c2a9e-0b7d-4c3e-9a51-2d8e7f3b4c10"


def test_recorded_files_carry_no_session_id_and_no_absolute_local_path(tmp_path):
    folder, _ = _trial_dir(tmp_path, f"{task()['task']}-1", [node("T001", "task", S1)], read={"1": "recorded"})
    copy = Path(json.loads((folder / "meta.json").read_text())["copy"])
    trace = {"schema": "eval-trace/1", "session": SID, "agents": [{"id": SID, "turns": 2}],
             "spans": [{"step": f"Read {copy}/.taska/evidence/x.md", "note": f"saw {HOME} and {SID}.jsonl"}]}
    (folder / "trace.json").write_text(json.dumps(trace))
    g = json.loads((folder / "grade.json").read_text())
    (folder / "grade.json").write_text(json.dumps(g | {"reason": f"left {HOME}", "session": SID}))
    store = C.load(copy).store
    S.append(store, [{"type": "processed", "evidence": "elsewhere", "by": "agent", "note": f"from {HOME}"}])
    (store / "x.jsonl").write_text(json.dumps({"sessionId": SID}) + "\n")
    (store / "out" / "export.json").write_text(json.dumps({"path": HOME}))
    rec = R.save(folder, tmp_path / "recorded" / "v1" / folder.name, MEET)
    assert not (rec / ".taska" / "x.jsonl").exists() and R.strays(rec) == []
    for p in rec.rglob("*"):
        if p.is_file():
            body = p.read_text(encoding="utf-8")
            assert HOME not in body and SID not in body and str(copy) not in body and str(tmp_path) not in body, p
    kept = json.loads((rec / "trace.json").read_text())
    assert "session" not in kept and kept["spans"][0]["step"] == "Read .taska/evidence/x.md"
    assert "<abs>" in kept["spans"][0]["note"]


EARLY = "2026-03-01_meetings_ee11ff22"
COPY = "2026-03-05_meetings_cc33dd44"


def test_a_slim_recording_replays_to_the_same_verdicts_as_a_full_one(tmp_path):
    from test_bank_grade import TEXT
    folder, t = _trial_dir(tmp_path, f"{task()['task']}-1", [node("T001", "task", S1)], read={"1": "recorded"})
    copy = Path(json.loads((folder / "meta.json").read_text())["copy"])
    store = C.load(copy).store
    early_text = "Opal will bring the gate passes for the night crew to the depot on Monday morning " * 40
    S.write_evidence(store, {"id": EARLY, "kind": "meeting", "at": "2026-03-01T09:00:00", "title": "Kickoff",
                             "source": "meetings", "text": early_text})
    (store / "evidence" / f"{COPY}.json").write_text(json.dumps(
        {"id": COPY, "kind": "meeting", "at": "2026-03-05T12:00:00", "date": "2026-03-05", "title": "Harbor daily sync",
         "source": "meetings", "text": TEXT, "duplicate_of": MEET}))
    early = dict(node("T050", "task", "Opal will bring the gate passes for the night crew"), evidence=EARLY,
                 date="2026-03-01", mid="mT050")
    on_copy = dict(node("T060", "task", S2), evidence=COPY, date="2026-03-05", mid="mT060")
    S.append(store, [early, on_copy])
    graded = B.grade(t, copy)
    g = json.loads((folder / "grade.json").read_text())
    (folder / "grade.json").write_text(json.dumps(g | {"pass": graded["pass"], "checks": graded["checks"],
                                                       "unscored": graded["unscored"]}))
    slim = R.save(folder, tmp_path / "slim" / folder.name, MEET)
    full = R.save(folder, tmp_path / "full" / folder.name, MEET, slim=False)
    assert json.loads((slim / ".taska" / "evidence" / f"{EARLY}.json").read_text())["text"] == ""
    assert json.loads((slim / ".taska" / "evidence" / f"{MEET}.json").read_text())["text"] == TEXT
    assert not list((slim / ".taska").rglob("*.md")) and list((full / ".taska").rglob("*.md"))
    assert (slim / ".taska" / "events.jsonl").read_text().count("\n") == (copy / ".taska" / "events.jsonl").read_text().count("\n")
    bank = _bank(tmp_path)
    a, b = R.replay_recorded(bank, [slim])[0], R.replay_recorded(bank, [full])[0]
    assert a["replayed"] == b["replayed"] and a["drift"] == [] and b["drift"] == []
    assert a["replayed"]["n-001"] == ("fail", None)   # the node made on the re-synced copy counts at the original


def test_a_replay_row_follows_the_recorded_task_and_grades_only_that_task(tmp_path, monkeypatch):
    bank = _bank(tmp_path)
    folder, _ = _trial_dir(tmp_path, f"{task()['task']}-1", [node("T001", "task", S1)], read={"1": "recorded"})
    rec = R.save(folder, tmp_path / "recorded" / "v1" / "renamed-7", MEET)
    seen = []
    real = B.replay
    monkeypatch.setattr(B, "replay", lambda path, records, tasks=None: seen.append(tasks) or real(path, records, tasks))
    row = R.replay_recorded(bank, [rec])[0]
    assert row["task"] == task()["task"] and row["drift"] == [] and seen == [[task()["task"]]]


def test_a_failed_refresh_keeps_the_old_recordings(tmp_path):
    bank = _bank(tmp_path)
    proj = project(tmp_path / "proj", [], processed=False)
    root = tmp_path / "recorded"
    tid = task()["task"]
    R.record_bank(bank, "low", 1, project=proj, run=_fake_run({}, {}), root=root, note="first")
    before = (root / "v1" / f"{tid}-1" / "grade.json").read_bytes()

    def broken(*a, out_root=None, **kw):
        _fake_run({}, {})(*a, out_root=out_root, **kw)
        meta = out_root / "trials" / f"{tid}-1" / "meta.json"
        meta.write_text(json.dumps({"copy": str(tmp_path / "gone")}))
        return out_root
    with pytest.raises(Exception):
        R.record_bank(bank, "low", 1, project=proj, run=broken, root=root, note="second")
    assert (root / "v1" / f"{tid}-1" / "grade.json").read_bytes() == before
    assert sorted(p.name for p in root.iterdir()) == ["CHANGELOG.md", "v1"]
    assert len((root / "CHANGELOG.md").read_text().splitlines()) == 1


def test_a_scratch_project_is_removed_after_recording(tmp_path, monkeypatch):
    bank = _bank(tmp_path)
    made = []

    def fake_tasks(seeds, project, rulings):
        made.append(project)
        globals()["project"](project, [], processed=False)
        return [], []
    import golden.generate as G
    monkeypatch.setattr(G, "bank_tasks", fake_tasks)
    seeded = tmp_path / "seeded"
    head, tasks = B.load_bank(bank)
    seeded_bank = B.write_bank(seeded, [t | {"seed": 1} for t in tasks], "v1", "2026-11-03", "invented")
    R.record_bank(seeded_bank, "low", 1, run=_fake_run({}, {}), root=tmp_path / "recorded")
    assert made and not made[0].exists()
    assert R.recordings(tmp_path / "recorded" / "v1")


T2 = "ff00ee11"


def _two_task_bank(tmp_path):
    ref = project(tmp_path / "ref", [node("T001", "task", S1)], read={"1": "recorded"})
    one = task(copy=ref)
    return B.write_bank(tmp_path / "bank", [one, one | {"task": T2}], "v1", "2026-11-03", "invented")


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_a_task_whose_trials_all_failed_keeps_its_old_recordings(tmp_path):
    bank = _two_task_bank(tmp_path)
    proj = project(tmp_path / "proj", [], processed=False)
    root = tmp_path / "recorded"
    tid = task()["task"]
    R.record_bank(bank, "low", 2, project=proj, run=_fake_run({}, {}), root=root, note="first")
    old_t2 = {k: v for k, v in _snapshot(root).items() if k.startswith(f"v1/{T2}-")}
    assert set(k.split("/")[1] for k in old_t2) == {f"{T2}-1", f"{T2}-2"}
    res = R.record_bank(bank, "low", 2, project=proj, run=_fake_run({}, {(T2, 1): "timeout", (T2, 2): "error"}),
                        root=root, note="second")
    assert res["recorded"] == [f"{tid}-1", f"{tid}-2"]
    assert {k: v for k, v in _snapshot(root).items() if k.startswith(f"v1/{T2}-")} == old_t2


def test_a_run_that_records_nothing_changes_nothing(tmp_path):
    bank = _bank(tmp_path)
    proj = project(tmp_path / "proj", [], processed=False)
    root = tmp_path / "recorded"
    R.record_bank(bank, "low", 1, project=proj, run=_fake_run({}, {}), root=root, note="first")
    before = _snapshot(root)
    res = R.record_bank(bank, "low", 1, project=proj, run=_fake_run({}, {1: "timeout"}), root=root, note="second")
    assert res["recorded"] == [] and _snapshot(root) == before
    assert sorted(p.name for p in root.iterdir()) == ["CHANGELOG.md", "v1"]


def test_a_rename_failing_halfway_restores_every_old_recording_and_keeps_the_staging(tmp_path, monkeypatch):
    bank = _bank(tmp_path)
    proj = project(tmp_path / "proj", [], processed=False)
    root = tmp_path / "recorded"
    tid = task()["task"]
    R.record_bank(bank, "low", 2, project=proj, run=_fake_run({}, {}), root=root, note="first")
    before = _snapshot(root)
    real = Path.rename
    failed = []

    def flaky(self, target):
        if self.parent.name.startswith(".staging-") and self.name == f"{tid}-2" and not failed:
            failed.append(self)
            raise OSError("disk full")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError, match="disk full"):
        R.record_bank(bank, "low", 2, project=proj, run=_fake_run({}, {}), root=root, note="second")
    assert failed
    after = _snapshot(root)
    assert {k: v for k, v in after.items() if k.startswith("v1/")} == {k: v for k, v in before.items() if k.startswith("v1/")}
    staging = [p for p in root.iterdir() if p.name.startswith(".staging-")]
    assert len(staging) == 1 and sorted(p.name for p in staging[0].iterdir()) == [f"{tid}-1", f"{tid}-2"]
    assert not [p for p in root.iterdir() if p.name.startswith(".old-")]
    assert after["CHANGELOG.md"] == before["CHANGELOG.md"]


def test_the_scrub_keeps_every_field_the_fold_matches_verbatim():
    for key in ("ac_text", "text", "title", "quote", "ac_add", "waiting_for"):
        assert key in R.VERBATIM
    e = {"type": "agent", "op": "ac_met", "ac_text": "Ship it to /srv/harbor/build/out by Friday", "note": "/srv/harbor/x/y"}
    out = R.scrub(e, [])
    assert out["ac_text"] == e["ac_text"] and out["note"] == "<abs>"
