"""A run's step record (reports/WEEK/run-status.json): written as run-status.partial.json while the run works and
swapped in only when it finishes, so a run stopped part-way can never erase the record of the last finished run. A
report-only rebuild keeps the steps named in `keep` (the ingest steps, whose failures must stay visible) and replaces
every earlier rebuild's steps. Invented data only."""
import json

from taska_lib import runlog as R

KEEP = ("sync", "model", "taska_run")
NIGHTLY = [{"id": i, "ok": True, "detail": ""} for i in KEEP + ("report", "gate", "deliver")]


def _finished(wk, steps=NIGHTLY):
    (wk / "run-status.json").write_text(json.dumps({"steps": steps}))


def _ids(wk):
    return [s["id"] for s in json.loads((wk / "run-status.json").read_text())["steps"]]


def test_an_interrupted_run_keeps_the_last_finished_record(tmp_path):
    _finished(tmp_path)
    R.begin(tmp_path, report_only=True, keep=KEEP)
    R.step(tmp_path, "report", 0, "rebuilt")
    # killed here: no finish
    assert _ids(tmp_path) == [s["id"] for s in NIGHTLY]
    assert R.unfinished(tmp_path)["last_step"] == "report"


def test_a_finished_report_only_run_keeps_the_ingest_steps(tmp_path):
    _finished(tmp_path)
    R.begin(tmp_path, report_only=True, keep=KEEP)
    R.step(tmp_path, "report", 0, "rebuilt")
    R.step(tmp_path, "gate", 1, "G4 failed")
    R.finish(tmp_path)
    steps = json.loads((tmp_path / "run-status.json").read_text())["steps"]
    assert [(s["id"], s["ok"]) for s in steps] == [("sync", True), ("model", True), ("taska_run", True), ("report", True), ("gate", False)]
    assert R.unfinished(tmp_path) is None and R.failed(tmp_path) == ["gate"]


def test_a_full_run_starts_a_fresh_record(tmp_path):
    _finished(tmp_path)
    R.begin(tmp_path, keep=KEEP)
    R.step(tmp_path, "sync", 4, "stale")
    R.finish(tmp_path)
    assert _ids(tmp_path) == ["sync"] and R.failed(tmp_path) == ["sync"]


def test_a_rebuild_is_judged_by_its_own_steps_not_the_kept_ones(tmp_path):
    _finished(tmp_path, [dict(NIGHTLY[0], ok=False)] + NIGHTLY[1:])
    R.begin(tmp_path, report_only=True, keep=KEEP)
    R.step(tmp_path, "report", 0, "")
    R.finish(tmp_path)
    assert R.failed(tmp_path) == ["sync"] and R.failed(tmp_path, this_run=True) == []


def test_the_cli_records_and_reports(project):
    from conftest import run_cli
    wk = project / "reports" / "2026-03-02"
    wk.mkdir(parents=True)
    run_cli(project, "run-log", "begin", str(wk))
    run_cli(project, "run-log", "step", str(wk), "gate", "1", "G4 failed")
    run_cli(project, "run-log", "finish", str(wk))
    r = run_cli(project, "run-log", "failed", str(wk), check=False)
    assert r["failed"] == ["gate"] and _ids(wk) == ["gate"]
