"""evals/run.py: the one eval command measures the code beside it, on a copy that never syncs and never touches the
project's store; a model call is a command a test replaces. Invented data only."""
import hashlib
import json
import os
import stat
import sys
import time
import tomllib
from pathlib import Path

import pytest

from conftest import TASKA_ROOT, run_cli

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import run as RUN  # noqa: E402
from key_tools import write_version  # noqa: E402


def _synced(project):
    run_cli(project, "sync")
    return [i["id"] for i in run_cli(project, "pending")["items"]]


def _key(tmp_path, evid):
    item = {"cid": "k-001", "evidence": evid, "quote": "the warehouse walkthrough session happened yesterday and the team took a lot of photos",
            "kind": "task", "commitment": "committed", "owner": "Nora Quill", "due": None, "status": "done",
            "client_owed": False, "covered_by": "T999", "title": "Walkthrough photos"}
    (tmp_path / "key").mkdir()
    return write_version(tmp_path / "key", [item], "v1", "2026-03-04", "unit")


def _md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else ""


def test_prepare_copy_never_syncs_and_leaves_only_key_meetings_pending(project, tmp_path):
    day1, day2 = _synced(project)
    before = _md5(project / ".taska" / "events.jsonl")
    copy = RUN.prepare_copy(project, day1, {day2}, tmp_path / "copy")
    t = tomllib.loads((copy / "taska.toml").read_text())
    assert "sources" not in t and t["run"] == {"max_items": 50, "backfill_max": 0}
    assert os.stat(copy / "taska").st_mode & stat.S_IXUSR
    assert [i["id"] for i in run_cli(copy, "pending")["items"]] == [day2]
    assert _md5(project / ".taska" / "events.jsonl") == before


def test_the_session_is_the_one_that_names_the_copy(tmp_path):
    root, copy = tmp_path / "projects", tmp_path / "evals" / "runs" / "x-tier2"
    (root / "a").mkdir(parents=True)
    (root / "a" / "other.jsonl").write_text('{"cwd": "/elsewhere"}\n')
    (root / "a" / "ours.jsonl").write_text(json.dumps({"cwd": str(copy)}) + "\n")
    os.utime(root / "a" / "ours.jsonl", (1, 1))
    assert RUN.find_session(copy, since=0, root=root) == root / "a" / "ours.jsonl"
    assert RUN.find_session(copy, since=10, root=root) is None


def test_tier2_with_a_stand_in_model_leaves_a_neutral_record(project, tmp_path, monkeypatch):
    monkeypatch.setattr(RUN, "SESSIONS", tmp_path / "no-sessions")
    day1, day2 = _synced(project)
    key = _key(tmp_path, day2)
    fake = tmp_path / "fake-ai"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    out = RUN.run(project, key, day1, tier=2, ai=[str(fake)], test_dirs=[], skip_lower=True,
                  runs_root=tmp_path / "runs", out_root=tmp_path / "records")
    meta = json.loads((out["record"] / "meta.json").read_text())
    assert meta["tier"] == "2" and meta["covered_by"] is False and meta["snapshot_of"] == f"before-{day1}"
    assert json.loads((out["record"] / "score.json").read_text())["incomplete"] is True   # the stand-in read nothing
    assert out["compare"] is None and out["ok"] is False


def test_a_second_tier2_run_is_compared_and_carries_its_trajectory(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    (sessions / "p").mkdir(parents=True)
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    day1, day2 = _synced(project)
    key = _key(tmp_path, day2)
    fake = tmp_path / "fake-ai"
    fake.write_text("#!/bin/sh\n"
                    f'printf \'{{"cwd": "%s", "type": "assistant", "message": {{"model": "{RUN.TIERS['med']}", "content": []}}}}\\n\' "$PWD" '
                    f'> "{sessions}/p/$(basename "$PWD").jsonl"\n'
                    # the stand-in finishes the key meeting, so the first run is complete and can be the second's baseline
                    f'echo \'{{"type": "processed", "evidence": "{day2}", "by": "agent", "at": "2026-03-04T10:00:00"}}\' >> "$PWD/.taska/events.jsonl"\n')
    fake.chmod(0o755)
    kw = dict(tier=2, ai=[str(fake)], test_dirs=[], skip_lower=True, runs_root=tmp_path / "runs", out_root=tmp_path / "records")
    first = RUN.run(project, key, day1, **kw)
    time.sleep(1.1)   # copies and records are named by the second
    second = RUN.run(project, key, day1, **kw)
    assert json.loads((second["record"] / "meta.json").read_text())["models"] == [RUN.TIERS["med"]]
    assert (second["record"] / "trajectory.html").exists() and (second["record"] / "compare.html").exists()
    assert second["compare"]["a"] == str(first["record"])


def _recording_ai(tmp_path, sessions, model):
    """A stand-in claude: logs its arguments and subagent model, writes a session whose agents ran `model`."""
    fake = tmp_path / "fake-ai"
    fake.write_text("#!/bin/sh\n"
                    f'echo "$@" > "{tmp_path}/args.txt"; echo "$CLAUDE_CODE_SUBAGENT_MODEL" > "{tmp_path}/sub.txt"\n'
                    f'printf \'{{"cwd": "%s", "type": "assistant", "message": {{"model": "{model}", "content": []}}}}\\n\' "$PWD" '
                    f'> "{sessions}/p/$(basename "$PWD").jsonl"\n')
    fake.chmod(0o755)
    return fake


def test_a_tier_pins_the_model_for_the_run_and_its_subagents(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    (sessions / "p").mkdir(parents=True)
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    day1, day2 = _synced(project)
    fake = _recording_ai(tmp_path, sessions, RUN.TIERS["low"])
    out = RUN.run(project, _key(tmp_path, day2), day1, tier=2, ai=[str(fake)], test_dirs=[], skip_lower=True,
                  runs_root=tmp_path / "runs", out_root=tmp_path / "records", model="low")
    assert f"--model {RUN.TIERS['low']}" in (tmp_path / "args.txt").read_text()
    assert (tmp_path / "sub.txt").read_text().strip() == RUN.TIERS["low"]
    assert out["copy"].name.endswith("-tier2-low")
    meta = json.loads((out["record"] / "meta.json").read_text())
    assert meta["model_tier"] == "low" and meta["model_pinned"] == RUN.TIERS["low"] and meta["tiers"] == RUN.TIERS


def test_a_run_whose_agents_used_another_model_is_not_that_tier(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    (sessions / "p").mkdir(parents=True)
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    day1, day2 = _synced(project)
    fake = _recording_ai(tmp_path, sessions, "claude-other-model")
    out = RUN.run(project, _key(tmp_path, day2), day1, tier=2, ai=[str(fake)], test_dirs=[], skip_lower=True,
                  runs_root=tmp_path / "runs", out_root=tmp_path / "records", model="med")
    meta = json.loads((out["record"] / "meta.json").read_text())
    assert meta["model_tier"] is None and meta["model_mismatch"] == ["claude-other-model"] and out["ok"] is False


def test_the_tiers_are_haiku_sonnet_opus_and_never_fable():
    assert list(RUN.TIERS) == ["low", "med", "high"]
    assert [m.split("-")[1] for m in RUN.TIERS.values()] == ["haiku", "sonnet", "opus"]


def test_a_tier2_run_keeps_a_live_page_and_its_last_write_names_the_record(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    (sessions / "p").mkdir(parents=True)
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    day1, day2 = _synced(project)
    fake = _recording_ai(tmp_path, sessions, RUN.TIERS["med"])
    out = RUN.run(project, _key(tmp_path, day2), day1, tier=2, ai=[str(fake)], test_dirs=[], skip_lower=True,
                  runs_root=tmp_path / "runs", out_root=tmp_path / "records")
    live = json.loads((tmp_path / "records" / "live" / "live.json").read_text())
    assert live["status"] == "finished" and live["record"] == out["record"].name and live["model_tier"] == "med"
    assert len(live["units"]) == 1 and (tmp_path / "records" / "live" / "index.html").exists()
    kept = json.loads((out["record"] / "live" / "live.json").read_text())   # the run keeps its own page
    assert kept["record"] == out["record"].name and (out["record"] / "live" / "index.html").exists()


def _rec(root, name, b7, b13=1, b15=(1, 2), key="k", tier="med", project="/c", md5=None, incomplete=False):
    d = root / name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"key_sha": key, "covered_by": False, "model_tier": tier, "project": project,
                                             "store_events_md5": md5 or name}))
    (d / "score.json").write_text(json.dumps({"incomplete": incomplete, "B7": {"ok": b7, "n": 10}, "B13": {"ok": b13, "n": 5},
                                              "B15": {"ok": b15[0], "n": b15[1]}}))
    return d


def test_the_baseline_is_the_best_run_so_far_on_the_same_key_and_tier(tmp_path):
    _rec(tmp_path, "20260301-100000-2", 6)
    best = _rec(tmp_path, "20260302-100000-2", 8)
    _rec(tmp_path, "20260303-100000-2", 7)
    _rec(tmp_path, "20260304-100000-2", 9, tier="high")       # another tier
    _rec(tmp_path, "20260305-100000-2", 9, key="other")       # another key
    _rec(tmp_path, "20260306-100000-2", 10, incomplete=True)  # an incomplete run is never a baseline
    assert RUN.best_baseline(tmp_path, "k", "med") == (best, 3)


def test_ties_go_to_decisions_then_precision_then_the_newest_and_a_run_counts_once(tmp_path):
    _rec(tmp_path, "20260301-100000-2", 8, b13=2)
    _rec(tmp_path, "20260302-100000-2", 8, b13=3, b15=(1, 4))
    winner = _rec(tmp_path, "20260303-100000-2", 8, b13=3, b15=(3, 4))
    _rec(tmp_path, "20260304-100000-2", 8, b13=3, b15=(3, 4), project="/c", md5="20260303-100000-2")   # a rescore of it
    exclude = _rec(tmp_path, "20260305-100000-2", 10)
    found, considered = RUN.best_baseline(tmp_path, "k", "med", exclude=exclude)
    assert found.name == "20260304-100000-2" and considered == 3   # the newest score of the winning run, 3 runs
    assert found != winner


def _finished_copy(project, tmp_path, sessions, name, day1, day2):
    copy = RUN.prepare_copy(project, day1, {day2}, tmp_path / "runs" / name)
    (sessions / name).mkdir(parents=True)
    (sessions / name / "s.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"cwd": str(copy), "type": "assistant", "timestamp": "2026-03-04T10:00:00Z", "message": {"model": RUN.TIERS["med"], "content": []}},
        {"cwd": str(copy), "type": "cost-state", "totalCostUSD": 2.0, "timestamp": "2026-03-04T10:10:00Z"}]) + "\n")
    with open(copy / ".taska" / "events.jsonl", "a") as f:
        f.write(json.dumps({"type": "processed", "evidence": day2, "by": "agent", "at": "2026-03-04T10:05:00"}) + "\n")
    return copy


def test_a_finished_copy_whose_harness_died_can_be_recorded_later(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    day1, day2 = _synced(project)
    key = _key(tmp_path, day2)
    first = RUN.record_copy(_finished_copy(project, tmp_path, sessions, "a-tier2-med", day1, day2), key, day1, "med", tmp_path / "records")
    time.sleep(1.1)
    second = RUN.record_copy(_finished_copy(project, tmp_path, sessions, "b-tier2-med", day1, day2), key, day1, "med", tmp_path / "records")
    meta = json.loads((second["record"] / "meta.json").read_text())
    assert meta["model_tier"] == "med" and (second["record"] / "trajectory.json").exists()
    trace = json.loads((second["record"] / "trace.json").read_text())   # the call timeline, for Perfetto
    assert any(e["ph"] == "M" for e in trace["traceEvents"])
    assert second["compare"]["a"] == str(first["record"])                    # compared with the best run so far
    page = json.loads((second["record"] / "live" / "live.json").read_text())
    assert page["status"] == "finished" and page["record"] == second["record"].name and page["baseline"]["of"] == 1


def test_the_model_runs_without_the_starting_sessions_variables_and_with_room_for_long_output(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    (sessions / "p").mkdir(parents=True)
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    day1, day2 = _synced(project)
    fake = tmp_path / "fake-ai"
    fake.write_text(f'#!/bin/sh\nenv > "{tmp_path}/env.txt"\n')
    fake.chmod(0o755)
    RUN.run(project, _key(tmp_path, day2), day1, tier=2, ai=[str(fake)], test_dirs=[], skip_lower=True,
            runs_root=tmp_path / "runs", out_root=tmp_path / "records")
    env = dict(l.split("=", 1) for l in (tmp_path / "env.txt").read_text().splitlines() if "=" in l)
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == RUN.TIERS["med"] and int(env["BASH_MAX_OUTPUT_LENGTH"]) >= 100000


def test_a_failure_after_the_run_leaves_a_reason_and_the_recovery_command(project, tmp_path, monkeypatch):
    monkeypatch.setattr(RUN, "SESSIONS", tmp_path / "none")
    day1, day2 = _synced(project)
    fake = tmp_path / "fake-ai"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)

    def broken(*a, **k):
        raise RuntimeError("disk full while scoring")
    monkeypatch.setattr(RUN, "after_run", broken)
    try:
        RUN.run(project, _key(tmp_path, day2), day1, tier=2, ai=[str(fake)], test_dirs=[], skip_lower=True,
                runs_root=tmp_path / "runs", out_root=tmp_path / "records")
    except RuntimeError:
        pass
    else:
        raise AssertionError("the failure was swallowed")
    note = next((tmp_path / "runs").glob("*.after_run_error.txt")).read_text()
    assert "disk full while scoring" in note and "--record-copy" in note


def test_every_recorded_run_gets_its_quality_page_and_the_live_page_links_it(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    day1, day2 = _synced(project)
    r = RUN.record_copy(_finished_copy(project, tmp_path, sessions, "a-tier2-med", day1, day2), _key(tmp_path, day2), day1, "med",
                        tmp_path / "records")
    rec = r["record"]
    assert json.loads((rec / "quality" / "quality.json").read_text())["schema"] == "eval-quality/1"
    assert (rec / "quality" / "index.html").exists() and not (rec / "quality" / "judge").exists()   # no judge run, ever
    own = json.loads((rec / "live" / "live.json").read_text())["caveats"]
    newest = json.loads((tmp_path / "records" / "live" / "live.json").read_text())["caveats"]
    assert own and {c["href"] for c in own} == {"../quality/index.html"}
    assert {c["href"] for c in newest} == {f"../{rec.name}/quality/index.html"}


def test_a_quality_failure_is_written_down_and_never_costs_the_record(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(RUN, "SESSIONS", sessions)

    def broken(*a, **k):
        raise RuntimeError("echo folder unreadable")
    monkeypatch.setattr(RUN.quality, "document", broken)
    day1, day2 = _synced(project)
    r = RUN.record_copy(_finished_copy(project, tmp_path, sessions, "a-tier2-med", day1, day2), _key(tmp_path, day2), day1, "med",
                        tmp_path / "records")
    assert "echo folder unreadable" in (r["record"] / "quality_error.txt").read_text()
    assert json.loads((r["record"] / "live" / "live.json").read_text())["status"] == "finished" and r["ok"]


def test_a_full_tier2_needs_full_and_says_the_time(project, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(RUN, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran without --full")))
    day1, day2 = _synced(project)
    rc = RUN.main(["--project", str(project), "--key", str(_key(tmp_path, day2)), "--before", day1, "--tier", "2", "--yes",
                   "--ai", str(tmp_path / "no-ai")])
    assert rc == 2 and "a full tier 2 takes 13–35 min: add --full, or run --smart" in capsys.readouterr().out


def test_with_full_a_tier2_runs(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    (sessions / "p").mkdir(parents=True)
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    day1, day2 = _synced(project)
    fake = _recording_ai(tmp_path, sessions, RUN.TIERS["med"])
    rc = RUN.main(["--project", str(project), "--key", str(_key(tmp_path, day2)), "--before", day1, "--tier", "2", "--yes",
                   "--full", "--skip-lower", "--ai", str(fake)])
    assert rc != 2 and "-p " in (tmp_path / "args.txt").read_text()               # the model ran (a stand-in)
    assert next((project / "reports" / "evals").glob("*-2")).is_dir()


def _hooked_run(project, tmp_path, monkeypatch, guard):
    sessions = tmp_path / "sessions"
    (sessions / "p").mkdir(parents=True)
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    monkeypatch.setattr(RUN, "GUARD", guard)
    day1, day2 = _synced(project)
    fake = _recording_ai(tmp_path, sessions, RUN.TIERS["med"])
    out = RUN.run(project, _key(tmp_path, day2), day1, tier=2, ai=[str(fake)], test_dirs=[], skip_lower=True,
                  runs_root=tmp_path / "runs", out_root=tmp_path / "records")
    return out["copy"], (tmp_path / "args.txt").read_text()


def test_the_tool_guard_hooks_every_bash_call_when_it_exists(project, tmp_path, monkeypatch):
    guard = tmp_path / "tool_guard.py"
    guard.write_text("import sys\n")
    copy, args = _hooked_run(project, tmp_path, monkeypatch, guard)
    assert f"--allowedTools Bash({copy}/taska:*) Read Write Edit Glob Grep Agent" in args
    settings = json.loads(args.split("--settings ", 1)[1].split(" --", 1)[0].strip())
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Bash" and hook["hooks"][0]["type"] == "command"
    assert hook["hooks"][0]["command"] == f'"{sys.executable}" "{guard}" "{copy}/taska"'


def test_without_the_tool_guard_there_is_no_hook(project, tmp_path, monkeypatch):
    copy, args = _hooked_run(project, tmp_path, monkeypatch, tmp_path / "missing" / "tool_guard.py")
    assert "--settings" not in args and f"--allowedTools Bash({copy}/taska:*) Read Write Edit Glob Grep Agent" in args


def test_every_recorded_run_gets_its_tiers_verdict_page(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(RUN, "SESSIONS", sessions)
    day1, day2 = _synced(project)
    r = RUN.record_copy(_finished_copy(project, tmp_path, sessions, "a-tier2-med", day1, day2), _key(tmp_path, day2), day1, "med",
                        tmp_path / "records", project=project)
    assert (tmp_path / "records" / "verdict" / "med" / "index.html").exists()
    assert not (r["record"] / "verdict_error.txt").exists() and r["ok"]


def test_a_verdict_failure_is_written_down_and_never_costs_the_run(project, tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(RUN, "SESSIONS", sessions)

    def broken(*a, **k):
        raise RuntimeError("bars unreadable")
    monkeypatch.setattr(RUN.verdict, "build", broken)
    day1, day2 = _synced(project)
    r = RUN.record_copy(_finished_copy(project, tmp_path, sessions, "a-tier2-med", day1, day2), _key(tmp_path, day2), day1, "med",
                        tmp_path / "records", project=project)
    assert "bars unreadable" in (r["record"] / "verdict_error.txt").read_text()
    assert json.loads((r["record"] / "live" / "live.json").read_text())["status"] == "finished" and r["ok"]


def test_one_wrapper_writer_serves_the_snapshot_and_the_chained_copy(project, tmp_path, monkeypatch):
    import bank as B
    day1, day2 = _synced(project)
    seen = []
    write = RUN.write_wrapper
    monkeypatch.setattr(RUN, "write_wrapper", lambda dest: seen.append(Path(dest)) or write(dest))
    copy = RUN.prepare_copy(project, day1, {day2}, tmp_path / "copy")
    chained = B._chain_copy(copy, day2, tmp_path / "chained")
    assert seen == [copy, chained]
    assert f'--project "{chained}"' in (chained / "taska").read_text()
    assert os.stat(chained / "taska").st_mode & stat.S_IXUSR


def test_the_verdict_page_refuses_misspelt_bars_and_says_so_beside_the_record(project, tmp_path, monkeypatch):
    (project / "evals").mkdir(exist_ok=True)
    (project / "evals" / "bars.json").write_text(json.dumps({"committed_lb_mn": 0.8}))
    rec = tmp_path / "rec"
    rec.mkdir()
    monkeypatch.setattr(RUN.verdict, "build", lambda *a, **k: pytest.fail("a verdict was built on unchecked bars"))
    assert RUN.verdict_page(tmp_path / "records", "med", project, rec) is None
    assert "committed_lb_mn" in (rec / "verdict_error.txt").read_text()


@pytest.mark.parametrize("r, code", [
    ({"ok": False, "verdict": "ship"}, 1),
    ({"ok": True, "evaluation_errors": ["verdict_error.txt"], "verdict": None}, 4),
    ({"ok": True, "evaluation_errors": [], "verdict": "ship"}, 0),
    ({"ok": True, "evaluation_errors": [], "verdict": "inconclusive"}, 3),
    ({"ok": True, "evaluation_errors": [], "verdict": "hold"}, 5),
    ({"ok": True}, 0),
])
def test_the_exit_code_tells_a_failed_run_from_an_unbuilt_evaluation_and_from_the_verdict(r, code):
    assert RUN.exit_code(r) == code
