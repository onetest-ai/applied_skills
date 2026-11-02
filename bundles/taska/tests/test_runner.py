"""`taska run-schedule <name>` (taska_lib/runner.py): one scheduled run, from the project's own steps ([[run.steps]])
to the report, the gate and the delivery, every step recorded. A send is HELD when a required step or the gate
failed. Nothing real runs here: the step runner is injected. Invented project only."""
import datetime as dt
import json
import textwrap
from pathlib import Path

import pytest

from taska_lib import config as C, runner as R, store as S

TOML = '''[project]
name = "Harbor Logistics"
timezone = "Europe/Kyiv"

[[run.steps]]
name = "sync"
command = "sync-all {project}"

[[run.steps]]
name = "extract"
command = "extract --week {week} --dir {dir}"
kinds = ["update", "preview", "send"]

[[run.steps]]
name = "prose"
command = "write-prose {dir}/prose.json"
kinds = ["preview", "send"]
required = false

[[schedules]]
name = "Daily update"
kind = "update"
days = [1, 2, 3, 4, 5]
time = "06:00"

[[schedules]]
name = "Friday preview"
kind = "preview"
days = [5]
time = "20:00"
to = ["owner@example.com"]

[[schedules]]
name = "Monday send"
kind = "send"
days = [1]
time = "14:00"
to = ["reader@example.com"]
cc = ["lead@example.com"]
'''
FRI = dt.date(2026, 3, 6)


def _proj(tmp_path, toml=TOML):
    (tmp_path / "taska.toml").write_text(toml)
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    return cfg


def _runner(rcs=None, log=None):
    log = log if log is not None else []
    def run(cmd, cwd):
        log.append(cmd)
        return (rcs or {}).get(cmd.split()[0], 0), f"ran {cmd}"
    return run, log


def _sched(cfg, name):
    return next(s for s in cfg.schedules if s.name == name)


def test_steps_are_filtered_by_kind_and_expanded():
    cfg = type("C", (), {"run": {"steps": [{"name": "a", "command": "x {kind} {name}"}, {"name": "b", "command": "y", "kinds": ["send"]}]}})()
    assert [s["name"] for s in R.steps(cfg, "preview")] == ["a"]
    assert [s["name"] for s in R.steps(cfg, "send")] == ["a", "b"]
    with pytest.raises(ValueError, match="kinds"):
        R.steps(type("C", (), {"run": {"steps": [{"name": "a", "command": "x", "kinds": ["nightly"]}]}})(), "send")


def test_week_for_each_kind():
    assert R.week_for("update", FRI) == "2026-03-02" and R.week_for("preview", FRI) == "2026-03-02"
    assert R.week_for("send", dt.date(2026, 3, 9)) == "2026-03-02"   # Monday: the previous week


def test_an_update_runs_its_steps_in_order_and_records_them(tmp_path):
    cfg = _proj(tmp_path)
    run, log = _runner()
    r = R.run(cfg, _sched(cfg, "Daily update"), today=FRI, run=run)
    assert log == [f"sync-all {tmp_path}", f"extract --week 2026-03-02 --dir {tmp_path / 'reports' / '2026-03-02'}"]
    assert r["kind"] == "update" and r["verdict"] == "done" and r["deliver"] is None
    rec = json.loads((tmp_path / "reports/2026-03-02/run-status.json").read_text())
    assert [(s["id"], s["ok"]) for s in rec["steps"]] == [("sync", True), ("extract", True)] and rec["finished"]


def test_a_preview_delivers_even_without_a_docx(tmp_path):
    cfg = _proj(tmp_path)
    run, log = _runner({"write-prose": 1})
    r = R.run(cfg, _sched(cfg, "Friday preview"), today=FRI, run=run)
    assert [c.split()[0] for c in log] == ["sync-all", "extract", "write-prose"]
    assert r["verdict"] == "done" and r["deliver"].endswith("deliver.json")
    d = json.loads(Path(r["deliver"]).read_text())
    assert d["kind"] == "preview" and d["to"] == ["owner@example.com"] and d["needs_attention"] is True   # the failed prose step
    assert (tmp_path / "reports/2026-03-02/mail.md").exists()
    wb = list((tmp_path / "reports/2026-03-02").glob("workbench*.html"))
    assert wb, "the read-only workbench copy is part of every delivery"


def test_a_send_is_held_when_a_required_step_failed(tmp_path):
    cfg = _proj(tmp_path, REVIEW)
    run, _ = _runner({"extract": 2})
    r = R.run(cfg, _sched(cfg, "Monday review"), today=dt.date(2026, 3, 9), run=run)
    assert r["verdict"] == "held" and "extract" in r["held_why"]
    d = json.loads(Path(r["deliver"]).read_text())
    assert d["kind"] == "send" and d["subject"].startswith("HELD") and d["to"] == ["owner@example.com"]
    assert d["cc"] == []   # a held notice goes to the owner only
    assert "HELD" in (tmp_path / "reports/2026-03-02/mail.md").read_text()


def test_a_send_is_ready_when_everything_passed(tmp_path):
    cfg = _proj(tmp_path, REVIEW)
    run, _ = _runner()
    r = R.run(cfg, _sched(cfg, "Monday review"), today=dt.date(2026, 3, 9), run=run)
    assert r["verdict"] == "ready"
    d = json.loads(Path(r["deliver"]).read_text())
    assert d["to"] == ["owner@example.com"] and d["cc"] == []   # a schedule to the owner is the review copy


def test_a_project_without_steps_still_previews(tmp_path):
    cfg = _proj(tmp_path, TOML.split("[[run.steps]]")[0] + "[[schedules]]" + TOML.split("[[schedules]]", 1)[1])
    run, log = _runner()
    r = R.run(cfg, _sched(cfg, "Friday preview"), today=FRI, run=run)
    assert log == [] and r["verdict"] == "done" and r["deliver"]


def test_dry_run_lists_the_commands_and_runs_nothing(tmp_path):
    cfg = _proj(tmp_path)
    run, log = _runner()
    r = R.run(cfg, _sched(cfg, "Friday preview"), today=FRI, run=run, dry_run=True)
    assert log == [] and [c.split()[0] for c in r["commands"]] == ["sync-all", "extract", "write-prose"]
    assert not (tmp_path / "reports/2026-03-02/run-status.json").exists()


def test_a_second_run_while_one_is_live_is_refused(tmp_path):
    import os
    cfg = _proj(tmp_path)
    lock = cfg.store / "run.lock"; lock.mkdir(); (lock / "pid").write_text(str(os.getpid()))
    run, log = _runner()
    r = R.run(cfg, _sched(cfg, "Daily update"), today=FRI, run=run)
    assert r["verdict"] == "busy" and log == []


REVIEW = TOML + '''
[[schedules]]
name = "Monday review"
kind = "send"
days = [1]
time = "13:00"
to = ["owner@example.com"]
'''
MON = dt.date(2026, 3, 9)


def _deliver(r):
    return json.loads(Path(r["deliver"]).read_text())


def test_a_send_to_the_owner_is_a_review_copy_and_is_recorded(tmp_path):
    cfg = _proj(tmp_path, REVIEW)
    run, log = _runner()
    r = R.run(cfg, _sched(cfg, "Monday review"), today=MON, run=run)
    d = _deliver(r)
    assert r["verdict"] == "ready" and r["audience"] == "owner" and log
    assert d["to"] == ["owner@example.com"] and d["cc"] == [] and d["subject"].startswith("Review copy: ")
    rec = json.loads((tmp_path / "reports/2026-03-02/reviewed.json").read_text())
    assert "ledger_mtime_ns" in rec and "at" in rec and rec["to"] == "owner@example.com"


def test_a_send_to_the_reader_sends_what_the_owner_was_mailed_and_runs_no_step(tmp_path):
    cfg = _proj(tmp_path, REVIEW)
    run, _ = _runner()
    R.run(cfg, _sched(cfg, "Monday review"), today=MON, run=run)
    run2, log2 = _runner()
    r = R.run(cfg, _sched(cfg, "Monday send"), today=MON, run=run2)
    d = _deliver(r)
    assert log2 == [] and r["verdict"] == "ready" and r["audience"] == "reader"   # nothing rebuilt: the reviewed state
    assert d["to"] == ["reader@example.com"] and set(d["cc"]) == {"lead@example.com", "owner@example.com"}
    assert not d["subject"].startswith(("Review", "HELD"))


def test_a_send_to_the_reader_without_a_review_copy_is_held_to_the_owner(tmp_path):
    cfg = _proj(tmp_path, REVIEW)
    run, log = _runner()
    r = R.run(cfg, _sched(cfg, "Monday send"), today=MON, run=run)
    d = _deliver(r)
    assert r["verdict"] == "held" and "no review copy" in r["held_why"] and log == []
    assert d["to"] == ["owner@example.com"] and d["cc"] == [] and d["subject"].startswith("HELD")


def test_a_send_to_the_reader_after_the_tree_changed_is_held(tmp_path):
    import os
    cfg = _proj(tmp_path, REVIEW)
    run, _ = _runner()
    R.run(cfg, _sched(cfg, "Monday review"), today=MON, run=run)
    ev = cfg.store / "events.jsonl"
    ev.touch()   # the ledger is written after the review copy (its mtime moves on)
    st = ev.stat(); os.utime(ev, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    r = R.run(cfg, _sched(cfg, "Monday send"), today=MON, run=_runner()[0])
    assert r["verdict"] == "held" and "changed since your review copy" in r["held_why"]


def test_the_cli_has_no_send_flag(tmp_path):
    from conftest import run_cli
    _proj(tmp_path, REVIEW)
    r = run_cli(tmp_path, "run-schedule", "Monday send", "--send", check=False)
    assert "error" in (r.get("err") or "") or r.get("status") == "error"


def _preview_matches_deliver(tmp_path, toml, names, today):
    from taska_lib import workbench as WB
    tmp_path.mkdir(parents=True)
    cfg = _proj(tmp_path, toml)
    for name in names[:-1]:
        R.run(cfg, _sched(cfg, name), today=today, run=_runner()[0])
    name = names[-1]
    p = WB.run_preview(cfg, name, today=today)
    r = R.run(cfg, _sched(cfg, name), today=today, run=_runner()[0])
    d = _deliver(r)
    assert p["week"] == r["week"] and p["mail"]["to"] == d["to"] and sorted(p["mail"]["cc"]) == sorted(d["cc"])
    assert set(p["mail"]["include"]) == {k for k, v in _sched(cfg, name).include.items() if v}
    return p


def test_run_preview_matches_what_the_runner_delivers(tmp_path):
    p = _preview_matches_deliver(tmp_path / "a", TOML, ["Friday preview"], FRI)
    assert p["mail"]["to"] == ["owner@example.com"] and p["steps_known"]
    p = _preview_matches_deliver(tmp_path / "b", REVIEW, ["Monday review"], MON)
    assert p["mail"]["to"] == ["owner@example.com"] and p["mail"]["cc"] == [] and p["audience"] == "owner"
    p = _preview_matches_deliver(tmp_path / "c", REVIEW, ["Monday review", "Monday send"], MON)
    assert p["mail"]["to"] == ["reader@example.com"] and "owner@example.com" in p["mail"]["cc"] and p["audience"] == "reader"
    assert p["steps"] == [] and "review copy" in p["mail"]["condition"]


def test_run_preview_uses_describe_or_says_it_cannot(tmp_path):
    from taska_lib import workbench as WB
    script = TOML.replace("[[run.steps]]", '[run]\nscheduled = "bin/weekly.py {kind}"\ndescribe = { update = ["pull", "extract"] }\n\n[[run.steps]]', 1)
    cfg = _proj(tmp_path, script)
    up = WB.run_preview(cfg, "Daily update", today=FRI)
    assert up["steps"] == ["pull", "extract"] and up["steps_known"] and up["mail"] is None and up["command"] == "bin/weekly.py update"
    pv = WB.run_preview(cfg, "Friday preview", today=FRI)
    assert pv["steps"] == [] and pv["steps_known"] is False


def test_run_preview_says_a_project_script_decides_the_mail_and_shows_the_held_path(tmp_path):
    from taska_lib import workbench as WB
    script = REVIEW.replace("[[run.steps]]", '[run]\nscheduled = "bin/weekly.py {kind}"\n\n[[run.steps]]', 1)
    cfg = _proj(tmp_path, script)
    p = WB.run_preview(cfg, "Monday send", today=MON)
    assert p["decided_by"] == "bin/weekly.py send" and p["mail"]["held"] == {"to": ["owner@example.com"], "cc": []}
    (tmp_path / "plain").mkdir()
    assert WB.run_preview(_proj(tmp_path / "plain", REVIEW), "Monday send", today=MON)["decided_by"] is None
