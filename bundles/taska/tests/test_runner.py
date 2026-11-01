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
    cfg = _proj(tmp_path)
    run, _ = _runner({"extract": 2})
    r = R.run(cfg, _sched(cfg, "Monday send"), today=dt.date(2026, 3, 9), run=run)
    assert r["verdict"] == "held" and "extract" in r["held_why"]
    d = json.loads(Path(r["deliver"]).read_text())
    assert d["kind"] == "send" and d["subject"].startswith("HELD") and d["to"] == ["owner@example.com"]
    assert "HELD" in (tmp_path / "reports/2026-03-02/mail.md").read_text()


def test_a_send_is_ready_when_everything_passed(tmp_path):
    cfg = _proj(tmp_path)
    run, _ = _runner()
    r = R.run(cfg, _sched(cfg, "Monday send"), today=dt.date(2026, 3, 9), run=run)
    assert r["verdict"] == "ready"
    d = json.loads(Path(r["deliver"]).read_text())
    assert d["to"] == ["reader@example.com"] and "owner@example.com" in d["cc"] and "lead@example.com" in d["cc"]


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


REVIEWED = TOML.replace('cc = ["lead@example.com"]\n', 'cc = ["lead@example.com"]\nreview = true\n')


def test_a_reviewed_send_goes_to_the_owner_as_a_shadow(tmp_path):
    cfg = _proj(tmp_path, REVIEWED)
    run, _ = _runner()
    r = R.run(cfg, _sched(cfg, "Monday send"), today=dt.date(2026, 3, 9), run=run)
    assert r["verdict"] == "ready" and r["shadow"] is True
    d = json.loads(Path(r["deliver"]).read_text())
    assert d["to"] == ["owner@example.com"] and d["cc"] == [] and d["needs_attention"] is False
    assert d["subject"].startswith("[Review: would go to reader@example.com, cc lead@example.com] ")
    assert "reader@example.com" not in (tmp_path / "reports/2026-03-02/mail.md").read_text().split("\n")[1]


def test_send_now_ignores_review(tmp_path):
    cfg = _proj(tmp_path, REVIEWED)
    run, _ = _runner()
    r = R.run(cfg, _sched(cfg, "Monday send"), today=dt.date(2026, 3, 9), run=run, send=True)
    assert r["verdict"] == "ready" and r["shadow"] is False
    d = json.loads(Path(r["deliver"]).read_text())
    assert d["to"] == ["reader@example.com"] and "owner@example.com" in d["cc"] and not d["subject"].startswith("[Review")


def test_the_cli_takes_send(tmp_path):
    from conftest import run_cli
    _proj(tmp_path, REVIEWED)
    r = run_cli(tmp_path, "run-schedule", "Monday send", "--send", "--dry-run")
    assert r["status"] == "ok" and r["shadow"] is False
    r = run_cli(tmp_path, "run-schedule", "Monday send", "--dry-run")
    assert r["shadow"] is True
