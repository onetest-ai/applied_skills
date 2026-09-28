import json
from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed,
                             plan_stale, draft, write_json)
from scribe_lib.basedoc import base_task
from scribe_lib.merge import merge_task
from scribe_lib import report

BASE = ("Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\nChecked away. [FILE:a.md#p1] <!-- c:aaaa0002 -->\n\n"
        "Model dropped. [RAG:3] <!-- c:aaaa0003 -->")


def _merged(tmp_path, body):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": BASE, "details": "D. [RAG:2] <!-- c:bbbb0001 -->"}), 1, docx_sha=None)
    base_task(config, "m1", inst, tpl)
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", body)
    write_json(config.work_dir / "m1" / "check-file.json",
               {"checked": 1, "passed": 0, "failed": [{"section": "overview", "claim": "aaaa0002", "tag": "[FILE:a.md#p1]", "reason": "quote not found"}], "needs_quote": []})
    merge_task(config, "m1", data["instances"], data["templates"])
    return config, json.loads((config.work_dir / "m1" / "merge.json").read_text())["sections"]


def test_drops_are_attributed_to_checks_or_model(tmp_path):
    _, s = _merged(tmp_path, "Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\nNot modeled: quote not found.\n\nNew. [RAG:4]\n")
    ov = s["overview"]
    assert (ov["kept"], ov["dropped_by_check"], ov["dropped_by_model"], ov["added"]) == (1, 1, 1, 1)
    assert ov["false_stale"] is False and "dropped" not in ov


def test_redraft_with_no_new_content_is_false_stale(tmp_path):
    _, s = _merged(tmp_path, "Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\nChecked away. [FILE:a.md#p1] <!-- c:aaaa0002 -->\n\n"
                              "Model dropped. [RAG:3] <!-- c:aaaa0003 -->\n\nNot modeled: nothing new.\n")
    assert s["overview"]["false_stale"] is True


def test_report_summary_reads_run_rows(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    write_json(config.out_root / "_runs" / "2026-01-05.json", [
        {"task": "m1", "status": "published", "version": 2, "merge": {"overview": {"status": "drafted", "false_stale": True,
         "kept": 3, "reworded": 0, "recited": 1, "added": 0, "superseded": 0, "dropped_by_check": 0, "dropped_by_model": 0}}},
        {"task": "m2", "status": "noop", "version": None, "merge": {}}])
    out = report.summary(config, "2026-01-05")
    assert out["totals"]["false_stale"] == 1 and out["totals"]["recited"] == 1
    assert [t["status"] for t in out["tasks"]] == ["published", "noop"]
