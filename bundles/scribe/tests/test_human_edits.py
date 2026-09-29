# bundles/scribe/tests/test_human_edits.py
import json, shutil, subprocess
import pytest
from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed, plan_stale, draft,
                             claim_blocks)
from scribe_lib.basedoc import base_task
from scribe_lib.merge import merge_task

pytestmark = pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc missing")

V1_OVERVIEW = ("Delivery takes 3 days. [RAG:1] <!-- c:aaaa0001 -->\n\n"
               "Routes are planned weekly. [RAG:3] <!-- c:aaaa0002 -->")
V1_DETAILS = "Detail one. [RAG:2] <!-- c:bbbb0001 -->\n\n```mermaid\nflowchart LR\n  A-->B\n```"


def _edited_docx(path, overview_md, details_md, overview_title="Overview", preamble=""):
    src = path.parent / "edit.md"
    src.write_text(f"# Mini m1\n\n{preamble}## {overview_title}\n\n{overview_md}\n\n## Details\n\n{details_md}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pandoc", str(src), "-o", str(path)], check=True)


def _setup(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": V1_OVERVIEW, "details": V1_DETAILS}), 1,
                 docx_sha="sha-at-publish")
    return config, data, inst, tpl


def test_small_numeric_correction_is_human_modified(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx",
                 "Delivery takes 10 days.^[RAG:1 — doc, Intro]\n\nRoutes are planned weekly.^[RAG:3 — doc, Intro]",
                 "Detail one.^[RAG:2 — doc, Intro]")
    base_task(config, "m1", inst, tpl)
    b = json.loads((config.work_dir / "m1" / "base.json").read_text())
    assert [h["claim_id"] for h in b["human_modified"]] == ["aaaa0001"]


def test_deleted_claim_becomes_tombstone_and_is_not_re_added(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx",
                 "Delivery takes 3 days.^[RAG:1 — doc, Intro]", "Detail one.^[RAG:2 — doc, Intro]")
    base_task(config, "m1", inst, tpl)
    b = json.loads((config.work_dir / "m1" / "base.json").read_text())
    assert [t["claim_id"] for t in b["human_deleted"]] == ["aaaa0002"]
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", "Delivery takes 3 days. [RAG:1] <!-- c:aaaa0001 -->\n\nRoutes are planned weekly. [RAG:3]\n")
    merge_task(config, "m1", data["instances"], data["templates"])
    ids = [x["claim_id"] for x in claim_blocks(config.work_dir / "m1" / "next.md")["overview"]]
    assert ids == ["aaaa0001"]
    m = json.loads((config.work_dir / "m1" / "merge.json").read_text())["sections"]["overview"]
    assert m["suppressed_tombstone"] == 1


def test_renamed_heading_is_refused(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx", "Delivery takes 3 days.^[RAG:1 — doc, Intro]",
                 "Detail one.^[RAG:2 — doc, Intro]", overview_title="Summary")
    r = base_task(config, "m1", inst, tpl)
    assert r["status"] == "failed" and r["reason"] == "section_heading_changed" and r["headings"] == ["Summary"]
    assert not (config.work_dir / "m1" / "base.md").exists()


def test_diagram_source_comes_from_previous_src_not_docx(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    png = tmp_path / "d.png"; png.write_bytes(b"\x89PNG\r\n\x1a\n")
    _edited_docx(config.out_root / "m1" / "Mini m1.docx",
                 "Delivery takes 3 days.^[RAG:1 — doc, Intro]\n\nRoutes are planned weekly.^[RAG:3 — doc, Intro]",
                 f"Detail one, edited.^[RAG:2 — doc, Intro]\n\n![details diagram]({png})")
    base_task(config, "m1", inst, tpl)
    details = (config.work_dir / "m1" / "base.md").read_text().split("{#details}")[1]
    assert "```mermaid\nflowchart LR\n  A-->B\n```" in details
    assert "<img" not in details and "<figure" not in details
    b = json.loads((config.work_dir / "m1" / "base.json").read_text())
    assert all(h["section"] != "details" for h in b["human_added"])


def test_docx_with_comment_and_tracked_change_recovers_accepted_text(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx",
                 "Delivery takes [3]{.deletion author=\"r\"}[10]{.insertion author=\"r\"} days.^[RAG:1 — doc, Intro] "
                 "[note]{.comment-start id=\"0\" author=\"r\"}[]{.comment-end id=\"0\"}\n\n"
                 "Routes are planned weekly.^[RAG:3 — doc, Intro]", "Detail one.^[RAG:2 — doc, Intro]")
    base_task(config, "m1", inst, tpl)
    base_md = (config.work_dir / "m1" / "base.md").read_text()
    assert "Delivery takes 10 days." in base_md and "note" not in base_md


def test_identical_new_paragraphs_in_one_section_get_distinct_ids(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx",
                 "Delivery takes 3 days.^[RAG:1 — doc, Intro]\n\nRoutes are planned weekly.^[RAG:3 — doc, Intro]\n\n"
                 "Brand new human claim.^[RAG:9 — doc, Intro]\n\nBrand new human claim.^[RAG:9 — doc, Intro]",
                 "Detail one.^[RAG:2 — doc, Intro]")
    base_task(config, "m1", inst, tpl)
    b = json.loads((config.work_dir / "m1" / "base.json").read_text())
    ids = [h["claim_id"] for h in b["human_added"]]
    assert len(ids) == 2 and len(set(ids)) == 2
    overview = claim_blocks(config.work_dir / "m1" / "base.md")["overview"]
    assert len({c["claim_id"] for c in overview}) == len(overview) == 4


def test_tombstone_survives_publish_and_suppresses_later_redrafts(tmp_path, monkeypatch):
    from scribe_fixtures import fake_evidence
    from scribe_lib import brain as brain_mod
    from scribe_lib.publish import publish_task
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx",
                 "Delivery takes 3 days.^[RAG:1 — doc, Intro]", "Detail one.^[RAG:2 — doc, Intro]")
    base_task(config, "m1", inst, tpl)
    plan_stale(config, "m1", [], ["overview", "details"])
    merge_task(config, "m1", data["instances"], data["templates"])
    assert publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)["status"] == "ok"
    state = json.loads((config.out_root / "m1" / "_src" / "state.json").read_text())
    assert [(t["section"], t["claim_id"]) for t in state["human_deleted"]] == [("overview", "aaaa0002")]

    # Next run: the docx is no longer edited, so base.json has no tombstone; state.json still does.
    base_task(config, "m1", inst, tpl)
    assert json.loads((config.work_dir / "m1" / "base.json").read_text())["human_deleted"] == []
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", "Delivery takes 3 days. [RAG:1] <!-- c:aaaa0001 -->\n\nRoutes are planned weekly. [RAG:4]\n")
    merge_task(config, "m1", data["instances"], data["templates"])
    m = json.loads((config.work_dir / "m1" / "merge.json").read_text())
    assert m["sections"]["overview"]["suppressed_tombstone"] == 1 and m["noop"] is True


def test_pack_lists_claims_removed_by_a_person():
    from scribe_lib.pack import _render_pack_section
    text = _render_pack_section(None, {"id": "overview", "title": "Overview"}, {}, "", {}, {},
                                ["routes are planned weekly."])
    assert "Removed by a person — do not re-add:\n\n- routes are planned weekly." in text


def test_prepare_fails_and_writes_no_pack_on_a_renamed_heading(tmp_path):
    from scribe_lib.pack import prepare_task
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx", "Delivery takes 3 days.^[RAG:1 — doc, Intro]",
                 "Detail one.^[RAG:2 — doc, Intro]", overview_title="Summary")
    r = prepare_task(config, "m1")
    assert r["status"] == "failed" and r["reason"] == "section_heading_changed"
    assert r["headings"] == ["Summary"] and r["missing"] == ["Overview"]
    assert not (config.work_dir / "m1" / "pack").exists()


OV = "Delivery takes 3 days.^[RAG:1 — doc, Intro]\n\nRoutes are planned weekly.^[RAG:3 — doc, Intro]"
DT = "Detail one.^[RAG:2 — doc, Intro]"


def _assert_structure_refused(config, r, detail):
    assert r == {"status": "failed", "reason": "unsupported_structure", "detail": detail}
    assert not (config.work_dir / "m1" / "base.md").exists()
    assert not (config.work_dir / "m1" / "base.json").exists()  # so no human_deleted tombstone anywhere
    assert "human_deleted" not in json.loads((config.out_root / "m1" / "_src" / "state.json").read_text())


def test_h3_inserted_mid_section_is_refused_not_tombstoned(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx",
                 "Delivery takes 3 days.^[RAG:1 — doc, Intro]\n\n### Human note\n\nA paragraph a person wrote.\n\n"
                 "Routes are planned weekly.^[RAG:3 — doc, Intro]", DT)
    _assert_structure_refused(config, base_task(config, "m1", inst, tpl), ["H3: Human note"])


def test_h3_at_section_end_is_refused_not_read_as_resave(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx", OV + "\n\n### Follow-up\n\nCheck with ops.", DT)
    _assert_structure_refused(config, base_task(config, "m1", inst, tpl), ["H3: Follow-up"])


def test_text_under_the_title_before_the_first_section_is_refused(tmp_path):
    config, data, inst, tpl = _setup(tmp_path)
    _edited_docx(config.out_root / "m1" / "Mini m1.docx", OV, DT, preamble="Draft - do not circulate.\n\n")
    _assert_structure_refused(config, base_task(config, "m1", inst, tpl), ["text before the first section"])


def test_vtt_locator_with_em_dash_survives_a_docx_edit_and_two_cues_stay_distinct(tmp_path):
    """C1 (F3 review, Critical 1): a `[FILE:]` locator that itself contains
    ` — ` (a VTT breadcrumb, `00:17 — Speaker (cue 1) > 03:26 — Speaker (cue
    2)`) is recovered intact from an edited docx — not truncated to its
    first timestamp — because `_recover_tag_body`'s primary path matches
    the footnote text against the PREVIOUS published version's own tag
    bodies (`_known_tag_bodies`), regardless of which separator the
    footnote used. Two distinct cues of the same file therefore stay two
    distinct tags, not one duplicated tag."""
    loc1 = "00:17 — Tatiana Milova (cue 1) > 03:26 — Viachaslau Hurski (cue 31)"
    loc2 = "03:40 — Tatiana Milova (cue 32) > 05:12 — Viachaslau Hurski (cue 40)"
    tag1 = f"[FILE:transcript.vtt#{loc1}]"
    tag2 = f"[FILE:transcript.vtt#{loc2}]"

    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    v1_overview = f"First cue. {tag1} <!-- c:aaaa0001 -->\n\nSecond cue, same file. {tag2} <!-- c:aaaa0002 -->"
    publish_seed(
        config, inst, doc("m1", 1, "Mini m1", {"overview": v1_overview, "details": V1_DETAILS}), 1,
        docx_sha="sha-at-publish",
    )

    # A docx round trip escapes the tag body's own punctuation (pandoc's gfm
    # writer would too) — spelled out here with the LEGACY " — " separator
    # (the worst case: this is exactly what a docx rendered before this fix
    # would still contain), so the fix is proven against the format that
    # actually caused the defect, not just the new one.
    _edited_docx(
        config.out_root / "m1" / "Mini m1.docx",
        f"First cue.^[FILE:transcript.vtt#{loc1} — transcript.vtt, {loc1}]\n\n"
        f"Second cue, same file.^[FILE:transcript.vtt#{loc2} — transcript.vtt, {loc2}]",
        DT,
    )
    base_task(config, "m1", inst, tpl)
    base_md = (config.work_dir / "m1" / "base.md").read_text(encoding="utf-8")
    assert tag1 in base_md
    assert tag2 in base_md
