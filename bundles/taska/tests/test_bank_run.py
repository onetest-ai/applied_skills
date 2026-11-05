"""bank.trial / run_bank / the run and grade commands, with a stand-in for the model: no test calls Claude.
Invented data only."""
import json
import os
import stat
import sys
import textwrap
import time
from pathlib import Path

import pytest

from conftest import TASKA_ROOT, run_cli

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402
import run as RUN  # noqa: E402
from taska_lib import config as C, store as S  # noqa: E402
from taska_lib.text import norm  # noqa: E402

QUOTE = "the warehouse walkthrough session happened yesterday and the team took a lot of photos of the picking process"

STAND_IN = textwrap.dedent('''\
    #!%(py)s
    import json, os, sys, time
    from pathlib import Path
    if "--probe" in sys.argv:
        sys.exit(int(os.environ.get("PROBE_RC", "0")))
    copy = Path.cwd()
    t0 = time.time()
    if os.environ.get("GRAND"):
        import subprocess
        Path(os.environ["GRAND"]).write_text(str(subprocess.Popen(["sleep", "30"]).pid))
    if os.environ.get("SLEEP"):
        time.sleep(float(os.environ["SLEEP"]))
    with open(str(copy) + ".times", "w") as tf:
        tf.write(json.dumps([t0, time.time()]))
    if os.environ.get("RC"):
        sys.exit(int(os.environ["RC"]))
    n = copy.name.rsplit("-", 1)[-1]
    cost = {"1": 0.5}.get(n, 0.9)
    model = sys.argv[sys.argv.index("--model") + 1]
    store = copy / ".taska"
    epic = "E01"
    evid = "%(evid)s"
    with open(store / "events.jsonl", "a") as f:
        f.write(json.dumps({"type": "create", "mid": "e1", "id": epic, "kind": "epic", "by": "plan", "evidence": None,
                            "date": "2026-03-01", "fields": {"title": "Onboarding"}}) + "\\n")
        f.write(json.dumps({"type": "create", "mid": "m-" + copy.name, "id": "T1" + n, "kind": "task", "by": "agent",
                            "evidence": evid, "date": "2026-03-03", "quote": "%(quote)s",
                            "fields": {"title": "Photos", "parent": epic, "internal_owner": "Nora Quill", "client_owner": None,
                                       "client_owed": False, "ac": [], "due": None, "plan": None, "commitment": "committed"}}) + "\\n")
        f.write(json.dumps({"type": "processed", "evidence": evid, "by": "agent", "at": "2026-03-03T12:05:00"}) + "\\n")
    sessions = Path(os.environ["SESSIONS_DIR"]) / "p"
    sessions.mkdir(parents=True, exist_ok=True)
    recs = [{"cwd": str(copy), "type": "assistant", "timestamp": "2026-03-03T12:00:00Z",
             "message": {"model": model, "content": [], "usage": {"input_tokens": 10, "output_tokens": 5}}},
            {"cwd": str(copy), "type": "cost-state", "totalCostUSD": cost, "timestamp": "2026-03-03T12:07:00Z"}]
    (sessions / (copy.name + ".jsonl")).write_text("\\n".join(json.dumps(r) for r in recs) + "\\n")
''')


def _setup(project, tmp_path, monkeypatch, name="stand-in"):
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("SESSIONS_DIR", str(sessions))
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    run_cli(project, "sync")
    day2 = [i["id"] for i in run_cli(project, "pending")["items"]][1]
    stand = tmp_path / name
    stand.write_text(STAND_IN % {"py": sys.executable, "evid": day2, "quote": QUOTE, "run_dir": str(RUN.RUN_DIR)})
    stand.chmod(stat.S_IRWXU)
    item = S.evidence(C.load(project).store)[day2]
    text = norm(S.full_text(item))
    a = text.find(norm(QUOTE))
    t = {"task": "d2task", "evidence": day2, "transcript_sha": B.transcript_sha(item),
         "must_create": [{"id": "k-1", "span": [a, a + len(norm(QUOTE))], "quote": QUOTE, "kind": "task",
                          "commitment": "committed", "owner": "Nora Quill", "caps": ["committed"], "source": "invented"}],
         "must_not_create": [], "outcome": ["processed"], "budget": {"cost_usd": 1, "minutes": None}}
    bank = B.write_bank(tmp_path / "bank", [t], "v1", "2026-11-03", "unit")
    return day2, t, bank, stand


def test_a_trial_reads_one_meeting_on_its_own_copy_and_records_grade_metrics_and_transcript(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    res = B.trial(project, t, 1, "low", [str(stand)], tmp_path / "out", 60)
    assert res["status"] == "done", res["reason"]
    assert res["pass"] is True and res["trial"] == 1
    assert res["cost_usd"] == 0.5 and res["tokens"]["input"] == 10 and res["latency_s"] == 420
    assert res["checks"][0]["events"] is not None
    folder = tmp_path / "out" / "trials" / "d2task-1"
    for name in ("grade.json", "trace.json", "perfetto.json", "meta.json"):
        assert (folder / name).exists()
    assert json.loads((folder / "trace.json").read_text())["schema"] == "eval-trace/1"
    assert json.loads((folder / "grade.json").read_text())["status"] == "done"
    assert (folder / "transcript" / "d2task-1.jsonl").exists()
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["tier"] == "low" and meta["model"] == RUN.TIERS["low"] and meta["copy"].endswith("d2task-1")
    events = project / ".taska" / "events.jsonl"
    assert not events.exists() or '"by": "agent"' not in events.read_text()   # the project's store is untouched


def test_parallel_trials_each_read_their_own_session(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    monkeypatch.setenv("SLEEP", "1.5")
    out = B.run_bank(project, bank, "low", 2, None, 2, [str(stand)], tmp_path / "out")
    by = {p.parent.name: json.loads(p.read_text()) for p in (out / "trials").glob("*/grade.json")}
    assert by["d2task-1"]["cost_usd"] == 0.5 and by["d2task-2"]["cost_usd"] == 0.9
    assert all(x["pass"] for x in by.values())
    (a0, a1), (b0, b1) = (json.loads(p.read_text()) for p in sorted((project / "evals" / "runs").glob("bank-*/*.times")))
    assert a0 < b1 and b0 < a1   # the two stand-ins really overlapped
    run = json.loads((out / "run.json").read_text())
    assert run["kind"] == "trials" and run["k"] == 2 and run["tier"] == "low" and run["model"] == RUN.TIERS["low"]
    assert run["bank_version"] == "v1" and len(run["bank_sha256"]) == 64 and run["tasks"] == ["d2task"]
    assert run["code_version"] and run["prompts_sha256"] and run["finished"]


def test_run_refuses_without_yes_and_marks_trials_invalid_when_no_route_answers(project, tmp_path, monkeypatch, capsys):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    args = ["run", "--project", str(project), "--bank", str(bank), "--tier", "low", "--k", "2", "--ai", str(stand)]
    assert B.main(args) == 2
    assert not (project / "reports" / "evals" / "bank").exists()
    monkeypatch.setenv("PROBE_RC", "1")
    assert B.main(args + ["--yes"]) == 1
    runs = list((project / "reports" / "evals" / "bank").iterdir())
    grades = [json.loads(p.read_text()) for p in runs[0].glob("trials/*/grade.json")]
    assert len(grades) == 2 and {g["status"] for g in grades} == {"invalid"} and not any(g["pass"] for g in grades)
    assert not (tmp_path / "sessions").exists()   # the stand-in never ran as a model


def test_a_trial_past_its_timeout_is_timeout_not_a_miss(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    monkeypatch.setenv("SLEEP", "5")
    res = B.trial(project, t, 1, "low", [str(stand)], tmp_path / "out", 1)
    assert res["status"] == "timeout" and res["pass"] is False
    assert json.loads((tmp_path / "out" / "trials" / "d2task-1" / "grade.json").read_text())["status"] == "timeout"


def test_no_session_is_an_error_and_a_task_budget_sets_the_timeout(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    silent = tmp_path / "silent"
    silent.write_text("#!/bin/sh\nexit 0\n")
    silent.chmod(stat.S_IRWXU)
    assert B.trial(project, t, 1, "low", [str(silent)], tmp_path / "out", 60)["status"] == "error"
    assert B.timeout_for({"budget": {"minutes": 5}}) == 600 and B.timeout_for({"budget": {"minutes": None}}) == 1800


def test_grade_command_grades_an_existing_copy_for_free(project, tmp_path, monkeypatch, capsys):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    res = B.trial(project, t, 1, "low", [str(stand)], tmp_path / "out", 60)
    copy = json.loads((tmp_path / "out" / "trials" / "d2task-1" / "meta.json").read_text())["copy"]
    capsys.readouterr()
    assert B.main(["grade", "--bank", str(bank), "--copy", copy, "--task", "d2task"]) == 0
    assert json.loads(capsys.readouterr().out)["pass"] is True


def _gone(pid):
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_a_timeout_kills_the_models_grandchildren_too(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    pidfile = tmp_path / "grand.pid"
    monkeypatch.setenv("GRAND", str(pidfile))
    monkeypatch.setenv("SLEEP", "20")
    res = B.trial(project, t, 1, "low", [str(stand)], tmp_path / "out", 2)
    pid = int(pidfile.read_text())
    try:
        assert res["status"] == "timeout" and res["reason"] == "timed out after 2 s"
        assert _gone(pid)
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def test_a_failure_after_the_model_is_an_error_trial_and_the_run_finishes(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)

    def broken(session):
        raise ValueError("truncated session")
    monkeypatch.setattr(B.TJ, "load_run", broken)
    out = B.run_bank(project, bank, "low", 2, None, 2, [str(stand)], tmp_path / "out")
    grades = [json.loads(p.read_text()) for p in (out / "trials").glob("*/grade.json")]
    assert len(grades) == 2 and {g["status"] for g in grades} == {"error"}
    assert all("truncated session" in g["reason"] and not g["pass"] for g in grades)
    assert all((p.parent / "meta.json").exists() for p in (out / "trials").glob("*/grade.json"))
    assert json.loads((out / "run.json").read_text())["finished"]


def test_a_non_zero_exit_is_an_error_never_graded(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    monkeypatch.setenv("RC", "3")
    res = B.trial(project, t, 1, "low", [str(stand)], tmp_path / "out", 60)
    assert res["status"] == "error" and "exit code 3" in res["reason"] and res["pass"] is False and res["checks"] == []


def test_a_probe_whose_binary_is_missing_makes_every_trial_invalid(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    rc = B.main(["run", "--project", str(project), "--bank", str(bank), "--tier", "low", "--k", "1",
                 "--ai", str(tmp_path / "no-such-binary"), "--yes"])
    assert rc == 1
    run = next((project / "reports" / "evals" / "bank").iterdir())
    assert {json.loads(p.read_text())["status"] for p in run.glob("trials/*/grade.json")} == {"invalid"}


def test_a_refused_task_is_invalid_and_never_paid_for(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    res = B.trial(project, dict(t, transcript_sha="0" * 64), 1, "low", [str(stand)], tmp_path / "out", 60)
    assert res["status"] == "invalid" and res["pass"] is False and "transcript" in res["reason"]
    assert not list((project / "evals" / "runs").glob("bank-*/*.times"))   # the stand-in model never ran
    assert not (tmp_path / "sessions").exists()
    saved = json.loads((tmp_path / "out" / "trials" / "d2task-1" / "grade.json").read_text())
    assert saved["status"] == "invalid" and B.aggregate([saved])["overall"]["valid"] == 0


def test_run_defaults_to_three_trials_per_task(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    seen = {}
    stub = tmp_path / "stub-run"
    (stub / "trials").mkdir(parents=True)
    (stub / "run.json").write_text(json.dumps({"kind": "trials", "tasks": [], "k": 3}))
    monkeypatch.setattr(B, "run_bank", lambda project, bank, tier, k, *a, **kw: seen.update(k=k) or stub)
    assert B.main(["run", "--project", str(project), "--bank", str(bank), "--tier", "low", "--ai", str(stand), "--yes"]) == 0
    assert seen["k"] == 3


def test_budget_refuses_a_plan_over_it_before_any_model_call_and_runs_one_under(project, tmp_path, monkeypatch, capsys):
    day2, t, _, stand = _setup(project, tmp_path, monkeypatch)
    bank = B.write_bank(tmp_path / "bank-sized", [t | {"class": {"size": "S"}}], "v1", "2026-11-03", "unit")
    measured = tmp_path / "measured"
    (measured / "trials" / "d2task-1").mkdir(parents=True)
    (measured / "run.json").write_text(json.dumps({"kind": "trials", "tasks": ["d2task"], "tier": "low"}))
    (measured / "trials" / "d2task-1" / "grade.json").write_text(json.dumps(
        {"task": "d2task", "trial": 1, "status": "done", "latency_s": 240}))
    args = ["run", "--project", str(project), "--bank", str(bank), "--tier", "low", "--ai", str(stand),
            "--parallel", "1", "--budget", "10", "--minutes-from", str(measured), "--yes"]
    capsys.readouterr()
    assert B.main(args + ["--k", "3"]) == 2
    out = capsys.readouterr().out.splitlines()
    plan = json.loads(out[0])
    assert plan["expected_minutes"] == 12.0 and plan["over"] and plan["drop"] == ["d2task"]
    assert plan["overhead"] == "overhead not measured"
    assert "drop d2task" in out[1]
    assert not (project / "reports" / "evals" / "bank").exists() and not (tmp_path / "sessions").exists()
    assert B.main(args + ["--k", "2"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["expected_minutes"] == 8.0
    run = next((project / "reports" / "evals" / "bank").iterdir())
    assert len(list(run.glob("trials/*/grade.json"))) == 2


def test_budget_refuses_when_a_class_has_no_measured_minutes(project, tmp_path, monkeypatch, capsys):
    day2, t, _, stand = _setup(project, tmp_path, monkeypatch)
    bank = B.write_bank(tmp_path / "bank-sized", [t | {"class": {"size": "L"}}], "v1", "2026-11-03", "unit")
    empty = tmp_path / "measured"
    empty.mkdir()
    rc = B.main(["run", "--project", str(project), "--bank", str(bank), "--tier", "low", "--ai", str(stand),
                 "--budget", "10", "--minutes-from", str(empty), "--yes"])
    assert rc == 2 and "L" in capsys.readouterr().out
    assert not (tmp_path / "sessions").exists()


def test_run_bank_submits_jobs_in_the_order_the_estimate_uses(tmp_path, monkeypatch):
    size = lambda s: {"size": s, "structure": "conversation", "owners": "0–1", "audience": "internal"}
    tasks = [{"task": "a", "class": size("S")}, {"task": "b", "class": size("M")}, {"task": "c", "class": size("L")},
             {"task": "d", "class": size("S"), "after": ["c"]}]
    bank = B.write_bank(tmp_path / "bank", tasks, "v1", "2026-11-03", "unit")
    m = {"S": 0.5, "M": 1.0, "L": 2.0}
    seen = []

    def fake(project, task, n, *a, **kw):
        seen.append((task["task"], n))
        folder = tmp_path / "out" / "trials" / f"{task['task']}-{n}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps({"copy": str(folder)}))
        return {"task": task["task"], "trial": n, "status": "done"}
    monkeypatch.setattr(B, "trial", fake)
    monkeypatch.setattr(B, "check_transcripts", lambda project, tasks: None)   # invented tasks name no meeting
    B.run_bank(tmp_path, bank, "low", 2, None, 1, ["stand-in"], tmp_path / "out", minutes_by_class=m)
    starts = [x for x in seen if x[0] != "d"]
    assert starts == [(g[0]["task"], n) for g, n in B.job_order(B.load_bank(bank)[1], 2, m)]
    head = json.loads((tmp_path / "out" / "run.json").read_text())
    assert head["parallel"] == 1 and head["order"] == [[g[0]["task"], n] for g, n in B.job_order(B.load_bank(bank)[1], 2, m)]


def test_measured_minutes_from_another_tier_are_ignored(project, tmp_path, monkeypatch, capsys):
    day2, t, _, stand = _setup(project, tmp_path, monkeypatch)
    bank = B.write_bank(tmp_path / "bank-sized", [t | {"class": {"size": "S"}}], "v1", "2026-11-03", "unit")
    other = tmp_path / "other-tier"
    (other / "trials" / "d2task-1").mkdir(parents=True)
    (other / "run.json").write_text(json.dumps({"kind": "trials", "tasks": ["d2task"], "tier": "high"}))
    (other / "trials" / "d2task-1" / "grade.json").write_text(json.dumps(
        {"task": "d2task", "trial": 1, "status": "done", "latency_s": 60}))
    rc = B.main(["run", "--project", str(project), "--bank", str(bank), "--tier", "low", "--ai", str(stand),
                 "--budget", "10", "--minutes-from", str(other), "--yes"])
    assert rc == 2 and "no measured minutes for size S" in capsys.readouterr().out


def test_run_exits_3_when_the_projects_hard_bars_fail_and_0_with_a_note_when_none_are_set(project, tmp_path, monkeypatch, capsys):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    args = ["run", "--project", str(project), "--bank", str(bank), "--tier", "low", "--k", "1", "--ai", str(stand), "--yes"]
    assert B.main(args) == 0
    assert "no hard bar is set" in capsys.readouterr().out
    (project / "evals").mkdir(exist_ok=True)
    (project / "evals" / "bars.json").write_text(json.dumps({"turn_cap": 0, "complete_required": True}))
    assert B.main(args) == 3
    assert "hard bars" in capsys.readouterr().out


def test_run_refuses_invalid_bars_before_any_model_call(project, tmp_path, monkeypatch, capsys):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    (project / "evals").mkdir(exist_ok=True)
    (project / "evals" / "bars.json").write_text(json.dumps({"turn_cap": "80"}))
    rc = B.main(["run", "--project", str(project), "--bank", str(bank), "--tier", "low", "--k", "1", "--ai", str(stand), "--yes"])
    assert rc == 2 and "turn_cap" in capsys.readouterr().err
    assert not (tmp_path / "sessions").exists() and not (project / "reports" / "evals" / "bank").exists()


def _args_stand_in(tmp_path):
    """A stand-in claude that only logs its arguments (one per line) and exits: no session, so the trial is an error."""
    fake = tmp_path / "args-ai"
    fake.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a"; done > "{tmp_path}/args.txt"\nexit 0\n')
    fake.chmod(stat.S_IRWXU)
    return fake


def test_a_bank_trial_hooks_every_bash_call_when_the_guard_exists(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    guard = tmp_path / "tool_guard.py"
    guard.write_text("import sys\n")
    monkeypatch.setattr(RUN, "GUARD", guard)
    B.trial(project, t, 1, "low", [str(_args_stand_in(tmp_path))], tmp_path / "out", 60)
    args = (tmp_path / "args.txt").read_text().splitlines()
    copy = json.loads((tmp_path / "out" / "trials" / "d2task-1" / "meta.json").read_text())["copy"]
    settings = json.loads(args[args.index("--settings") + 1])
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Bash" and hook["hooks"][0]["command"] == f'"{sys.executable}" "{guard}" "{copy}/taska"'
    assert args == RUN.model_cmd([str(tmp_path / "args-ai")], Path(copy), "low")[1:]
    monkeypatch.setattr(RUN, "GUARD", tmp_path / "missing.py")
    B.trial(project, t, 2, "low", [str(_args_stand_in(tmp_path))], tmp_path / "out", 60)
    assert "--settings" not in (tmp_path / "args.txt").read_text().splitlines()


def test_run_bank_refuses_a_project_whose_transcripts_differ_before_any_trial(project, tmp_path, monkeypatch):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    other = B.write_bank(tmp_path / "bank-other", [t | {"transcript_sha": "0" * 64}], "v1", "2026-11-03", "unit")
    monkeypatch.setattr(B, "trial", lambda *a, **k: pytest.fail("a trial ran on a project the bank cannot grade"))
    with pytest.raises(ValueError, match="transcript"):
        B.run_bank(project, other, "low", 1, None, 1, [str(stand)], tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_run_command_names_a_transcript_refusal_and_exits_2(project, tmp_path, monkeypatch, capsys):
    day2, t, bank, stand = _setup(project, tmp_path, monkeypatch)
    other = B.write_bank(tmp_path / "bank-other", [t | {"transcript_sha": "0" * 64}], "v1", "2026-11-03", "unit")
    rc = B.main(["run", "--project", str(project), "--bank", str(other), "--tier", "low", "--k", "1", "--ai", str(stand),
                 "--yes"])
    assert rc == 2 and "transcript" in capsys.readouterr().err


def test_chain_closure_brings_every_earlier_task_and_run_bank_names_them(tmp_path, monkeypatch):
    tasks = [{"task": "a"}, {"task": "b", "after": ["a"]}, {"task": "c", "after": ["b"]}, {"task": "d"}]
    assert B.chain_closure(["c", "d"], tasks) == ["a", "b", "c", "d"]
    bank = B.write_bank(tmp_path / "bank", tasks, "v1", "2026-11-03", "unit")
    monkeypatch.setattr(B, "check_transcripts", lambda project, tasks: None)
    with pytest.raises(ValueError, match="a, b"):
        B.run_bank(tmp_path, bank, "low", 1, ["c"], 1, ["stand-in"], tmp_path / "out")
