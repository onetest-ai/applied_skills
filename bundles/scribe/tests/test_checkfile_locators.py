"""F3 — precise transcript locators.

Evidence: 6 audited claims cited `#00:17` (meeting start) while the
supporting cue was elsewhere; 4 repeated the same tag twice. `check-file`
now, for every `[FILE:]` claim with a fresh evidence quote, rewrites the
tag's locator to the section of the parsed raw file that actually contains
the quote (when the one drafted doesn't), and removes an exact-duplicate
`[FILE:]` tag on the same claim. Both are recorded in `check-file.json`
(`relocated`/`deduped`) and folded into `merge.json`/`report --summary` as
`locators_fixed`, the same way F2's `modality` flags feed `modality_flagged`.
"""
from __future__ import annotations

import json

from scribe_lib.checkfile import check_file_task
from scribe_lib.merge import merge_task
from scribe_lib import report

from scribe_fixtures import (
    fixture_brain_db, load, setup_mini_project, write_json, write_text, draft, plan_stale,
)

RAW_TWO_SECTIONS = (
    "## Cue 1\n\n"
    "Unrelated small talk at the top of the call.\n\n"
    "## Cue 2\n\n"
    "The actual quoted passage lives here.\n"
)


def _seed_raw(config, task_id: str, *, path: str = "call.vtt", text: str = RAW_TWO_SECTIONS) -> None:
    work_dir = config.work_dir / task_id
    write_json(
        work_dir / "raw" / "manifest.json",
        [{"path": path, "sha256": "deadbeef", "md": f"{path}.md", "status": "ok", "reason": "test"}],
    )
    write_text(work_dir / "raw" / f"{path}.md", text)


def _setup(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst = data["instances"]["m1"]
    return config, data, inst


def test_relocates_tag_to_section_that_actually_holds_the_quote(tmp_path):
    config, data, inst = _setup(tmp_path)
    _seed_raw(config, "m1")
    draft(
        config, "m1", "overview",
        "The actual quoted passage lives here. [FILE:call.vtt#Cue 1]\n",
        evidence=[{"claim_ref": 0, "tag": "[FILE:call.vtt#Cue 1]", "quote": "The actual quoted passage lives here."}],
    )

    result = check_file_task(config, "m1", inst)

    assert result["relocated"] == [{"section": "overview", "claim": None, "from": "Cue 1", "to": "Cue 2"}]
    assert result["deduped"] == []
    assert result["failed"] == []
    assert result["checked"] == 1 and result["passed"] == 1

    rewritten = (config.work_dir / "m1" / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "[FILE:call.vtt#Cue 2]" in rewritten
    assert "[FILE:call.vtt#Cue 1]" not in rewritten


def test_leaves_locator_unchanged_when_quote_is_in_the_named_section(tmp_path):
    config, data, inst = _setup(tmp_path)
    _seed_raw(config, "m1")
    draft(
        config, "m1", "overview",
        "The actual quoted passage lives here. [FILE:call.vtt#Cue 2]\n",
        evidence=[{"claim_ref": 0, "tag": "[FILE:call.vtt#Cue 2]", "quote": "The actual quoted passage lives here."}],
    )

    result = check_file_task(config, "m1", inst)

    assert result["relocated"] == []
    assert result["deduped"] == []
    assert result["passed"] == 1

    rewritten = (config.work_dir / "m1" / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "[FILE:call.vtt#Cue 2]" in rewritten


def test_dedupes_exact_duplicate_file_tags_within_one_claim(tmp_path):
    config, data, inst = _setup(tmp_path)
    _seed_raw(config, "m1")
    draft(
        config, "m1", "overview",
        "The actual quoted passage lives here. [FILE:call.vtt#Cue 2] [FILE:call.vtt#Cue 2]\n",
        evidence=[{"claim_ref": 0, "tag": "[FILE:call.vtt#Cue 2]", "quote": "The actual quoted passage lives here."}],
    )

    result = check_file_task(config, "m1", inst)

    assert result["deduped"] == [{"section": "overview", "claim": None, "tag": "[FILE:call.vtt#Cue 2]"}]
    assert result["relocated"] == []
    assert result["passed"] == 1

    rewritten = (config.work_dir / "m1" / "sections" / "overview.md").read_text(encoding="utf-8")
    assert rewritten.count("[FILE:call.vtt#Cue 2]") == 1


def test_quote_found_nowhere_still_fails_as_before(tmp_path):
    config, data, inst = _setup(tmp_path)
    _seed_raw(config, "m1")
    draft(
        config, "m1", "overview",
        "This text never appears in the transcript at all. [FILE:call.vtt#Cue 1]\n",
        evidence=[
            {"claim_ref": 0, "tag": "[FILE:call.vtt#Cue 1]", "quote": "This text never appears in the transcript at all."}
        ],
    )

    result = check_file_task(config, "m1", inst)

    assert result["relocated"] == []
    assert result["deduped"] == []
    assert result["checked"] == 1 and result["passed"] == 0
    assert result["failed"][0]["reason"] == "quote not found in call.vtt"

    rewritten = (config.work_dir / "m1" / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "Not modeled: quote not found in call.vtt." in rewritten


def test_relocation_and_dedupe_are_idempotent_on_a_second_run(tmp_path):
    config, data, inst = _setup(tmp_path)
    _seed_raw(config, "m1")
    draft(
        config, "m1", "overview",
        "The actual quoted passage lives here. [FILE:call.vtt#Cue 1] [FILE:call.vtt#Cue 1]\n",
        evidence=[{"claim_ref": 0, "tag": "[FILE:call.vtt#Cue 1]", "quote": "The actual quoted passage lives here."}],
    )

    first = check_file_task(config, "m1", inst)
    assert first["relocated"] == [{"section": "overview", "claim": None, "from": "Cue 1", "to": "Cue 2"}]
    assert first["deduped"] == [{"section": "overview", "claim": None, "tag": "[FILE:call.vtt#Cue 1]"}]
    after_first = (config.work_dir / "m1" / "sections" / "overview.md").read_text(encoding="utf-8")

    second = check_file_task(config, "m1", inst)
    assert second["relocated"] == []
    assert second["deduped"] == []
    assert second["checked"] == 1 and second["passed"] == 1
    after_second = (config.work_dir / "m1" / "sections" / "overview.md").read_text(encoding="utf-8")
    assert after_second == after_first


def test_ambiguous_relocation_picks_the_first_equally_close_section_and_flags_it(tmp_path):
    """Two sections equidistant from the named (nonexistent) locator both
    hold the quote — the earlier one in document order wins, and the entry
    carries `ambiguous: true` for audit."""
    config, data, inst = _setup(tmp_path)
    raw = (
        "## Cue A\n\nThe shared quoted line appears here too.\n\n"
        "## Cue B\n\nUnrelated middle content.\n\n"
        "## Cue C\n\nThe shared quoted line appears here too.\n"
    )
    _seed_raw(config, "m1", text=raw)
    draft(
        config, "m1", "overview",
        "The shared quoted line appears here too. [FILE:call.vtt#Cue B]\n",
        evidence=[{"claim_ref": 0, "tag": "[FILE:call.vtt#Cue B]", "quote": "The shared quoted line appears here too."}],
    )

    result = check_file_task(config, "m1", inst)

    assert len(result["relocated"]) == 1
    entry = result["relocated"][0]
    assert entry["from"] == "Cue B"
    assert entry["to"] == "Cue A"
    assert entry["ambiguous"] is True


def test_merge_counts_locators_fixed_per_section_and_report_summary_totals_it(tmp_path):
    config, data, inst = _setup(tmp_path)
    work_dir = config.work_dir / "m1"
    write_json(work_dir / "check-file.json", {
        "checked": 1, "passed": 1, "human_origin_skipped": 0,
        "failed": [], "needs_quote": [], "raw_offline_notes": [], "modality": [],
        "relocated": [{"section": "overview", "claim": "aaaa0001", "from": "Cue 1", "to": "Cue 2"}],
        "deduped": [{"section": "overview", "claim": "aaaa0001", "tag": "[FILE:call.vtt#Cue 2]"}],
    })
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", "Keep me. [RAG:1] <!-- c:aaaa0001 -->\n")

    merge_task(config, "m1", data["instances"], data["templates"])
    sections = json.loads((work_dir / "merge.json").read_text())["sections"]
    assert sections["overview"]["locators_fixed"] == 2
    assert sections["details"]["locators_fixed"] == 0

    write_json(config.out_root / "_runs" / "2026-03-02.json", [
        {"task": "m1", "status": "published", "version": 2, "merge": sections},
    ])
    out = report.summary(config, "2026-03-02")
    assert out["totals"]["locators_fixed"] == 2
