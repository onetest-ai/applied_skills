"""Smoke tests for the run harness's script additions: plan skips disabled
tasks, plan prints `now`/`run_report`, `report` appends run-report rows,
prepare clears the previous run's per-run files, doctor reports the scripts'
Brain."""
from __future__ import annotations

import json
import sqlite3

from scribe_lib import brain as brain_mod
from scribe_lib import parsing as parsing_mod
from scribe_lib.config import load_config, read_brain_identity
from scribe_lib.pack import prepare_task
from test_config_plan import _run, _three_task_project
from test_prepare import _domain_task, _fixture_brain_db, _make_project


def _disable(proj, task_id):
    path = proj / "tasks" / f"{task_id}.task.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(f"id: {task_id}\n", f"id: {task_id}\nenabled: false\n", 1), encoding="utf-8")


def test_plan_skips_disabled_task_unless_named(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-15")
    proj = _three_task_project(tmp_path)
    _disable(proj, "t1")

    code, payload = _run(proj, "plan")
    assert code == 0, payload
    assert payload["now"] == "2026-01-15"
    assert payload["run_report"].endswith("out/_runs/2026-01-15.json")
    t1 = next(t for t in payload["tasks"] if t["id"] == "t1")
    assert t1["due"] is False and t1["not_due_reason"] == "disabled"

    code, payload = _run(proj, "plan", "--task", "t1")
    assert payload["tasks"][0]["due"] is True  # onboarding's dry run names it


def test_report_appends_rows_and_shows_them(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-15")
    proj = _three_task_project(tmp_path)
    code, payload = _run(proj, "report", "--task", "t1", "--status", "noop", "--stage", "prepare", "--reason", "no stale sections")
    assert code == 0, payload
    code, payload = _run(proj, "report", "--status", "failed", "--stage", "preflight", "--reason", "no Brain")
    assert code == 0, payload

    code, payload = _run(proj, "report")
    assert code == 0
    rows = payload["rows"]
    assert rows == [
        {"task": "t1", "version": None, "status": "noop", "reasons": ["no stale sections"], "stage": "prepare"},
        {"task": "_run", "version": None, "status": "failed", "reasons": ["no Brain"], "stage": "preflight"},
    ]
    on_disk = json.loads((proj / "out" / "_runs" / "2026-01-15.json").read_text(encoding="utf-8"))
    assert on_disk == rows


def test_report_refuses_unknown_task_and_missing_reason(tmp_path):
    proj = _three_task_project(tmp_path)
    code, payload = _run(proj, "report", "--task", "nope", "--status", "noop", "--reason", "x")
    assert code == 1 and "unknown task" in payload["reason"]
    code, payload = _run(proj, "report", "--task", "t1", "--status", "noop")
    assert code == 1 and "--reason" in payload["reason"]


def test_prepare_clears_previous_run_outputs(tmp_path, monkeypatch):
    brain_mod.reset_cache()
    parsing_mod.reset_cache()
    db_path = _fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = _make_project(tmp_path, brain_db=db_path)
    _domain_task(proj, aliases=[], exclude=[])
    monkeypatch.setattr(
        brain_mod,
        "search",
        lambda cfg, q, limit, tag: [{"chunk_id": "111", "source": "d.md", "section": "S", "score": 1.0, "text": "Alpha."}],
    )
    config = load_config(proj)
    work = config.work_dir / "t1"
    (work / "sections").mkdir(parents=True)
    (work / "sections" / "overview.md").write_text("old draft [RAG:1]\n", encoding="utf-8")
    (work / "render").mkdir()
    (work / "render" / "old.docx").write_bytes(b"x")
    for name in ("next.md", "merge.json", "check-file.json", "verifier.json"):
        (work / name).write_text("{}", encoding="utf-8")

    prepare_task(config, "t1")

    assert not (work / "sections").exists()
    assert not (work / "render").exists()
    for name in ("next.md", "merge.json", "check-file.json", "verifier.json"):
        assert not (work / name).exists(), name
    assert (work / "pack" / "plan.json").is_file()


def test_read_brain_identity(tmp_path):
    db = tmp_path / "k.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [("name", "Test Brain"), ("taxonomy_version", "2")])
    con.execute("CREATE TABLE chunks(id INTEGER)")
    con.executemany("INSERT INTO chunks VALUES (?)", [(1,), (2,), (3,)])
    con.commit()
    con.close()
    assert read_brain_identity(db) == {"db": str(db), "name": "Test Brain", "taxonomy_version": "2", "chunks": 3}
    assert "error" in read_brain_identity(tmp_path / "missing.sqlite")


def test_pack_marks_diagram_sections(tmp_path, monkeypatch):
    brain_mod.reset_cache()
    parsing_mod.reset_cache()
    db_path = _fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = _make_project(tmp_path, brain_db=db_path)
    (proj / "tasks" / "s1.task.md").write_text(
        "---\ntemplate: subsystem-profile@1\nid: s1\ntitle: \"S1\"\n"
        "params:\n  name: \"Sys\"\n  tags: []\n  aliases: []\n"
        "audience: \"team\"\ncadence: on-brain-update\npublish: auto\nout: \"s1\"\n---\nNotes.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        brain_mod,
        "search",
        lambda cfg, q, limit, tag: [{"chunk_id": "111", "source": "d.md", "section": "S", "score": 1.0, "text": "Sys."}],
    )
    config = load_config(proj)
    prepare_task(config, "s1")
    pack = config.work_dir / "s1" / "pack"
    assert "Kind: diagram" in (pack / "context.pack.md").read_text(encoding="utf-8")
    assert "Kind:" not in (pack / "overview.pack.md").read_text(encoding="utf-8")


def test_accept_after_render_fails_on_a_failed_diagram(tmp_path):
    """scribe:run runs render before accept, so diagrams_render reads tonight's render.json."""
    from scribe_fixtures import load as _load3, setup_mini_project
    from scribe_lib.accept import accept_task

    proj = setup_mini_project(tmp_path)
    config, data = _load3(proj)
    work_dir = config.work_dir / "m1"
    (work_dir / "render").mkdir(parents=True)
    (work_dir / "next.md").write_text(
        "<!-- scribe: task=m1 version=1 built_at=2026-01-01T00:00:00 template=mini-profile@1 base=none -->\n\n"
        "# Mini m1\n\n## Changes in this version {#changes}\n\n- initial\n\n"
        "## Overview {#overview}\n\nA cited claim. [RAG:1] <!-- c:aaaa0001 -->\n\n"
        "## Details {#details}\n\nNot modeled: nothing found yet.\n\n",
        encoding="utf-8",
    )
    (work_dir / "render" / "render.json").write_text(
        json.dumps({"docx": "a.docx", "pdf": "a.pdf", "ok": True, "errors": [],
                    "diagrams": [{"diagram": "overview-1", "ok": False, "error": "parse error"}]}),
        encoding="utf-8",
    )
    inst, template = data["instances"]["m1"], data["templates"]["mini-profile"]
    result = accept_task(config, "m1", inst, template)
    assert result["passed"] is False
    assert result["checks"]["diagrams_render"].get("skipped") is not True
