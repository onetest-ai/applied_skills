import json
from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed,
                             plan_stale, draft, write_json, write_text, fake_evidence)
from scribe_lib import brain as brain_mod
from scribe_lib.basedoc import base_task
from scribe_lib.checkfile import check_file_task
from scribe_lib.merge import merge_task
from scribe_lib.publish import publish_task
from scribe_lib import report

BASE = ("Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\nChecked away. [FILE:a.md#p1] <!-- c:aaaa0002 -->\n\n"
        "Model dropped. [RAG:3] <!-- c:aaaa0003 -->")


def _setup(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": BASE, "details": "D. [RAG:2] <!-- c:bbbb0001 -->"}), 1, docx_sha=None)
    base_task(config, "m1", inst, tpl)
    plan_stale(config, "m1", ["overview"], ["details"])
    return config, data


def test_drops_are_attributed_via_real_check_file_output(tmp_path):
    """Real pipeline, not a hand-written check-file.json: `check_file_task`
    fails "Checked away" (its evidence quote doesn't occur in the parsed raw
    text) and rewrites it to `Not modeled:` before `merge_task` ever sees
    the section, exactly as the run harness would. "Model dropped" is never
    mentioned in the draft at all — the model's own call, not a check's."""
    config, data = _setup(tmp_path)
    work_dir = config.work_dir / "m1"
    raw_dir = work_dir / "raw"
    write_text(raw_dir / "a.md.md", "Some unrelated raw content.\n")
    write_json(
        raw_dir / "manifest.json",
        [{"path": "a.md", "sha256": "deadbeef", "md": "a.md.md", "status": "ok", "reason": "test"}],
    )
    draft(
        config, "m1", "overview",
        "Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\n"
        "Checked away. [FILE:a.md#p1] <!-- c:aaaa0002 -->\n\n"
        "New. [RAG:4]\n",
    )
    write_json(
        work_dir / "sections" / "overview.evidence.json",
        [{"claim_ref": 1, "tag": "[FILE:a.md#p1]", "quote": "Checked away."}],
    )

    check_result = check_file_task(config, "m1", data["instances"]["m1"])
    assert len(check_result["failed"]) == 1
    assert check_result["failed"][0]["claim"] == "aaaa0002"
    assert check_result["failed"][0]["normalized"] == "checked away."

    merge_task(config, "m1", data["instances"], data["templates"])
    s = json.loads((work_dir / "merge.json").read_text())["sections"]
    ov = s["overview"]
    assert (ov["kept"], ov["dropped_by_check"], ov["dropped_by_model"], ov["added"]) == (1, 1, 1, 1)
    assert ov["false_stale"] is False and "dropped" not in ov


def test_check_task_and_verifier_failures_also_attribute_dropped_by_check(tmp_path):
    """`check-task.json.failed` (`{"claim": claim_id, ...}`, checktask.py)
    and `verifier.json.rejected` (`{"claim": "<c:id or first 8 words>", ...}`,
    per skills/run/SKILL.md step 6) already carry a `claim` field merge's
    matching reads directly — confirmed here without needing a real
    check-task/verifier run, since both producers' shapes are exercised
    elsewhere (test_task_citations.py) and this only needs to prove merge's
    attribution honors those shapes."""
    config, data = _setup(tmp_path)
    work_dir = config.work_dir / "m1"
    write_json(work_dir / "check-task.json", {"checked": 1, "passed": 0, "failed": [
        {"section": "overview", "claim": "aaaa0002", "tag": "[TASK:up#c:x]", "reason": "upstream_claim_gone"},
    ]})
    write_json(work_dir / "verifier.json", {"checked": 1, "human_origin_claims": 0, "rejected": [
        {"section": "overview", "claim": "aaaa0003", "verdict": "unsupported", "reason": "no matching evidence"},
    ], "unmatched": []})
    draft(config, "m1", "overview", "Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\nNew. [RAG:4]\n")

    merge_task(config, "m1", data["instances"], data["templates"])
    ov = json.loads((work_dir / "merge.json").read_text())["sections"]["overview"]
    assert (ov["kept"], ov["dropped_by_check"], ov["dropped_by_model"], ov["added"]) == (1, 2, 0, 1)


def test_redraft_with_no_new_content_is_false_stale(tmp_path):
    config, data = _setup(tmp_path)
    draft(
        config, "m1", "overview",
        "Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\nChecked away. [FILE:a.md#p1] <!-- c:aaaa0002 -->\n\n"
        "Model dropped. [RAG:3] <!-- c:aaaa0003 -->\n\nNot modeled: nothing new.\n",
    )
    merge_task(config, "m1", data["instances"], data["templates"])
    s = json.loads((config.work_dir / "m1" / "merge.json").read_text())["sections"]
    assert s["overview"]["false_stale"] is True


def test_merge_counts_modality_flagged_per_section_and_report_summary_totals_it(tmp_path):
    """F2: `check-file.json.modality` entries (left by the deterministic
    hedge pre-check, still present after the SKILL's revise-once retry)
    are counted per-section as `modality_flagged` in `merge.json`, and
    `report.summary` totals them across the run — without failing
    anything (accept/merge never reject on this)."""
    config, data = _setup(tmp_path)
    work_dir = config.work_dir / "m1"
    write_json(work_dir / "check-file.json", {"checked": 1, "passed": 1, "human_origin_skipped": 0,
        "failed": [], "needs_quote": [], "raw_offline_notes": [], "modality": [
            {"section": "overview", "claim": "aaaa0001", "tag": "[FILE:a.md#p1]", "quote_marker": "maybe"},
            {"section": "overview", "claim": None, "tag": "[FILE:b.md#p1]", "quote_marker": "?"},
        ]})
    draft(config, "m1", "overview", "Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\nNew. [RAG:4]\n")

    merge_task(config, "m1", data["instances"], data["templates"])
    s = json.loads((work_dir / "merge.json").read_text())["sections"]
    assert s["overview"]["modality_flagged"] == 2
    assert s["details"]["modality_flagged"] == 0  # carried section: nothing flagged in it

    write_json(config.out_root / "_runs" / "2026-03-01.json", [
        {"task": "m1", "status": "published", "version": 2, "merge": s},
    ])
    out = report.summary(config, "2026-03-01")
    assert out["totals"]["modality_flagged"] == 2


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


def test_publish_row_embeds_merge_counts_and_summary_reads_them(tmp_path, monkeypatch):
    """Minor #1 (fix round 1): `merge_task` then `publish_task` (no_render)
    -> the run-report row `append_run` writes for that publish embeds the
    SAME `merge.json` sections dict, and `report.summary` over that run
    report returns them — end to end, not asserted only via a hand-written
    row (as `test_report_summary_reads_run_rows` above does) or via
    `merge_counts_for_row` in isolation."""
    monkeypatch.setenv("SCRIBE_NOW", "2026-02-01")
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    config, data = _setup(tmp_path)
    draft(config, "m1", "overview", "Keep me. [RAG:1] <!-- c:aaaa0001 -->\n\nNew fact. [RAG:9]\n")
    merged = merge_task(config, "m1", data["instances"], data["templates"])
    assert merged["noop"] is False

    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    published = publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)
    assert published["status"] == "ok", published

    merge_sections = json.loads((config.work_dir / "m1" / "merge.json").read_text())["sections"]
    run_rows = json.loads((config.out_root / "_runs" / "2026-02-01.json").read_text())
    row = next(r for r in run_rows if r["task"] == "m1" and r["status"] == "published")
    assert row["merge"] == merge_sections

    out = report.summary(config, "2026-02-01")
    task_entry = next(t for t in out["tasks"] if t["task"] == "m1")
    assert task_entry["added"] == merge_sections["overview"]["added"]
    assert task_entry["kept"] == merge_sections["overview"]["kept"] + merge_sections["details"]["kept"]
