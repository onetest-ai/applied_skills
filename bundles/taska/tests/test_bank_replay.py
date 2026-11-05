"""bank.replay: free grading of stored run copies, kind "replay", never comparable with trials. Invented data only."""
import json
import subprocess
import sys

import pytest

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402
from taska_lib import config as C, ledger as L, store as S  # noqa: E402
from test_bank_grade import MEET, S1, S2, node, project, task  # noqa: E402


def record(root, name, nodes, processed=True, read=None, reads=None, copy_exists=True, summary=True, tier="sonnet"):
    """<root>/reports/evals/<name>/ with meta.json -> an invented copy and a trajectory.json."""
    rec = root / "reports" / "evals" / name
    rec.mkdir(parents=True)
    copy = root / "copies" / name
    if copy_exists:
        project(copy, nodes, processed=processed, read=read)
        if reads:
            L.mark_processed(C.load(copy).store, MEET, "again", reads=reads)
    (rec / "meta.json").write_text(json.dumps({"project": str(copy), "session": "/gone.jsonl", "model_tier": tier,
                                               "git_commit": "abc" + name, "key_version": "v3", "key_sha": "f" * 8}))
    if summary:
        (rec / "trajectory.json").write_text(json.dumps({"summary": {"cost_usd": 12.5, "duration_s": 3000}}))
    return rec


def bank_file(tmp_path):
    ref = project(tmp_path / "ref", [node("T001", "task", S1)], read={"1": "recorded"})
    t = task(copy=ref)
    return B.write_bank(tmp_path / "bank", [t], "v1", "2026-11-03", "invented"), t


def test_two_records_grade_per_task_and_the_result_is_a_replay(tmp_path):
    path, t = bank_file(tmp_path)
    good = record(tmp_path, "r1", [node("T001", "task", S1)], read={"1": "recorded"})
    bad = record(tmp_path, "r2", [node("T001", "task", S1), node("T002", "task", S2)], read={"1": "recorded"})
    res = B.replay(path, [good, bad])
    assert res["kind"] == "replay" and res["records"] == ["r1", "r2"] and res["bank"]["version"] == "v1"
    by = {x["trial"]: x for x in res["trials"]}
    assert by["r1"]["status"] == "done" and by["r1"]["pass"] is True and by["r1"]["task"] == t["task"]
    assert by["r2"]["pass"] is False and by["r2"]["schema"] == "eval-trial/1"
    assert by["r1"]["latency_s"] is None and by["r1"]["cost_usd"] is None and by["r1"]["tokens"] is None
    assert res["per_record"]["r1"]["cost_usd"] == 12.5 and res["per_record"]["r1"]["latency_s"] == 3000
    assert res["per_record"]["r1"]["tier"] == "sonnet" and res["per_record"]["r1"]["key_version"] == "v3"
    assert res["per_record"]["r1"]["code"] == "abcr1"


def test_missing_copy_or_unprocessed_meeting_is_an_error_not_a_miss(tmp_path):
    path, _ = bank_file(tmp_path)
    gone = record(tmp_path, "r1", [], copy_exists=False, summary=False)
    unread = record(tmp_path, "r2", [node("T001", "task", S1)], processed=False)
    res = B.replay(path, [gone, unread])
    by = {x["trial"]: x for x in res["trials"]}
    assert by["r1"]["status"] == "error" and "copy" in by["r1"]["reason"]
    assert by["r2"]["status"] == "error" and "processed" in by["r2"]["reason"]
    assert res["per_record"]["r1"]["cost_usd"] is None
    agg = B.aggregate(res["trials"])
    assert agg["overall"]["valid"] == 0 and agg["overall"]["error"] == 2


def test_decisions_read_is_na_on_old_code_and_checked_on_new(tmp_path):
    path, _ = bank_file(tmp_path)
    old = record(tmp_path, "old", [node("T001", "task", S1)])
    new = record(tmp_path, "new", [node("T001", "task", S1)], reads={"decisions": 1})
    res = B.replay(path, [old, new])
    assert res["per_record"]["old"]["code_has_second_read"] is False
    assert res["per_record"]["new"]["code_has_second_read"] is True
    by = {x["trial"]: x for x in res["trials"]}
    dr = lambda x: next(c for c in x["checks"] if c["id"] == "decisions_read")["result"]
    assert dr(by["old"]) == "n/a" and dr(by["new"]) == "pass"


def test_backfill_events_without_evidence_do_not_break_the_scan(tmp_path):
    path, _ = bank_file(tmp_path)
    r = record(tmp_path, "r1", [node("T001", "task", S1)], read={"1": "recorded"})
    from taska_lib import store as S
    S.append(C.load(json.loads((r / "meta.json").read_text())["project"]).store,
             [{"type": "backfill", "mid": "b1", "evidence_ids": [MEET], "by": "agent"}])
    assert B.replay(path, [r])["trials"][0]["status"] == "done"


def test_aggregate_tolerates_absent_latency_and_cost(tmp_path):
    path, _ = bank_file(tmp_path)
    r = record(tmp_path, "r1", [node("T001", "task", S1)], read={"1": "recorded"})
    agg = B.aggregate(B.replay(path, [r])["trials"], {t["task"]: t for t in B.load_bank(path)[1]})
    row = next(iter(agg["tasks"].values()))
    assert row["latency_s"]["n"] == 0 and row["cost_usd"]["n"] == 0 and row["tokens"]["n"] == 0
    assert row["pass_at_k"] == 1 and agg["breaches"] == []


def test_cli_writes_a_replay_run_that_compare_refuses_against_trials(tmp_path):
    path, t = bank_file(tmp_path)
    r = record(tmp_path, "r1", [node("T001", "task", S1)], read={"1": "recorded"})
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(EVALS / "bank.py"), "replay", "--bank", str(path), "--records", str(r),
                        "--out", str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    head = json.loads((out / "run.json").read_text())
    assert head["kind"] == "replay" and head["bank_version"] == "v1" and head["records"] == ["r1"]
    assert head["per_record"]["r1"]["tier"] == "sonnet"
    assert (out / "trials" / f"{t['task']}-r1" / "grade.json").exists() and (out / "bank.json").exists()
    trials = tmp_path / "trials"
    (trials / "trials").mkdir(parents=True)
    (trials / "run.json").write_text(json.dumps({"kind": "trials", "bank_version": "v1", "tier": "sonnet", "k": 1}))
    with pytest.raises(ValueError, match="kind"):
        B.compare(out, trials)


def test_default_out_is_under_the_project_of_the_first_record(tmp_path):
    path, _ = bank_file(tmp_path)
    r = record(tmp_path, "r1", [node("T001", "task", S1)], read={"1": "recorded"})
    p = subprocess.run([sys.executable, str(EVALS / "bank.py"), "replay", "--bank", str(path), "--records", str(r)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    made = list((tmp_path / "reports" / "evals" / "bank").glob("replay-v1-*/run.json"))
    assert len(made) == 1


def decisions_result(tmp_path, extra):
    p = project(tmp_path, [node("T001", "task", S1)], processed=False)
    store = C.load(p).store
    for kw in extra:
        if kw == "reprocess":
            S.append(store, [{"type": "reprocess", "mid": "rp", "evidence": MEET, "by": "human"}])
        else:
            L.mark_processed(store, MEET, "x", **kw)
    g = B.grade(task(copy=p), p, code_has_second_read=True)
    return next(c for c in g["checks"] if c["id"] == "decisions_read")["result"]


def test_decisions_read_follows_the_ledgers_own_answer(tmp_path):
    assert decisions_result(tmp_path / "a", [{"reads": {"decisions": 1}}, {}]) == "pass"       # later re-extraction, no reprocess
    assert decisions_result(tmp_path / "b", [{"reads": {"decisions": 1}}, "reprocess", {}]) == "fail"
    assert decisions_result(tmp_path / "c", [{"decisions_read": {}}]) == "pass"                 # legacy {} is v1
    assert decisions_result(tmp_path / "d", [{"reads": {"decisions": 0}}]) == "fail"


def test_default_out_uses_the_reports_ancestor_and_otherwise_needs_out(tmp_path, capsys):
    path, _ = bank_file(tmp_path)
    r = record(tmp_path, "r1", [node("T001", "task", S1)], read={"1": "recorded"})
    other = tmp_path / "elsewhere" / "stray"
    other.mkdir(parents=True)
    assert B.main(["replay", "--bank", str(path), "--records", str(other)]) == 2
    assert "--out" in capsys.readouterr().err
    r2 = record(tmp_path / "p2", "r2", [node("T001", "task", S1)], read={"1": "recorded"})
    assert B.main(["replay", "--bank", str(path), "--records", str(r), str(r2)]) == 2
    assert "--out" in capsys.readouterr().err
    nested = B.default_out([tmp_path / "reports" / "evals" / "x" / "deeper"], "v1")
    assert nested.parent == tmp_path / "reports" / "evals" / "bank" and nested.name.startswith("replay-v1-")


def test_a_refused_task_is_invalid_in_a_replay_and_never_a_scored_miss(tmp_path):
    ref = project(tmp_path / "ref", [node("T001", "task", S1)], read={"1": "recorded"})
    path = B.write_bank(tmp_path / "bank", [task(copy=ref, transcript_sha="0" * 64)], "v1", "2026-11-03", "invented")
    r = record(tmp_path, "r1", [node("T001", "task", S1)], read={"1": "recorded"})
    t = B.replay(path, [r])["trials"][0]
    assert t["status"] == "invalid" and t["pass"] is False and "transcript" in t["reason"]
    agg = B.aggregate([t])
    assert agg["overall"]["valid"] == 0 and agg["overall"]["invalid"] == 1 and agg["overall"]["pass_hat_k"]["n"] == 0


def _meta(rec, **extra):
    m = json.loads((rec / "meta.json").read_text())
    (rec / "meta.json").write_text(json.dumps(m | extra))


def test_a_record_naming_code_has_second_read_overrides_the_ledger(tmp_path):
    path, _ = bank_file(tmp_path)
    r = record(tmp_path, "r1", [node("T001", "task", S1)])
    dr = lambda res: next(c for c in res["trials"][0]["checks"] if c["id"] == "decisions_read")["result"]
    assert dr(B.replay(path, [r])) == "n/a" and B.replay(path, [r])["per_record"]["r1"]["code_has_second_read"] is False
    _meta(r, code_has_second_read=True)
    res = B.replay(path, [r])
    assert dr(res) == "fail" and res["per_record"]["r1"]["code_has_second_read"] is True
    _meta(r, code_has_second_read="yes")   # not a bool: the ledger decides
    assert dr(B.replay(path, [r])) == "n/a"


def test_an_as_trial_record_is_graded_even_when_its_meeting_was_never_processed(tmp_path):
    path, _ = bank_file(tmp_path)
    r = record(tmp_path, "r1", [node("T001", "task", S1)], processed=False)
    assert B.replay(path, [r])["trials"][0]["status"] == "error"
    _meta(r, as_trial=True)
    t = B.replay(path, [r])["trials"][0]
    assert t["status"] == "done" and t["pass"] is False
    assert next(c for c in t["checks"] if c["id"] == "processed")["result"] == "fail"
    _meta(r, as_trial="true")   # only the boolean true
    assert B.replay(path, [r])["trials"][0]["status"] == "error"


def test_a_task_list_limits_the_replay_to_those_tasks(tmp_path):
    ref = project(tmp_path / "ref", [node("T001", "task", S1)], read={"1": "recorded"})
    one = task(copy=ref)
    path = B.write_bank(tmp_path / "bank", [one, one | {"task": "ff00ee11"}], "v1", "2026-11-03", "invented")
    r = record(tmp_path, "r1", [node("T001", "task", S1)], read={"1": "recorded"})
    assert [t["task"] for t in B.replay(path, [r])["trials"]] == [one["task"], "ff00ee11"]
    assert [t["task"] for t in B.replay(path, [r], ["ff00ee11"])["trials"]] == ["ff00ee11"]
    assert B.replay(path, [r], [])["trials"] == []
