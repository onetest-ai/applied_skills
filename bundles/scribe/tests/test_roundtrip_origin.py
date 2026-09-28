"""Smoke tests for the docx round-trip and human-origin persistence.

Defect A: a docx round trip (pandoc docx -> gfm) backslash-escapes Markdown
punctuation inside footnote text, so tags came back as
`[FILE:a \\_ b.docx#Page 1 \\> Page 23]`, were published like that, and then
matched neither the raw manifest nor `state.cited_raw`. `base` now unescapes
recovered tag bodies, the shared claim parser reads any tag unescaped (so an
already-published file with escaped tags is read correctly) and merge writes
tags unescaped.

Defect B: a human-authored claim lost its status one version after the edit,
because the exemption lived only in the CURRENT run's base.json. Origin is now
persisted in the claim comment (`<!-- c:xxxx origin=human -->`), read back by
every command, and skipped by check-file and the verifier.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from scribe_lib import brain as brain_mod
from scribe_lib import claims
from scribe_lib.accept import accept_task
from scribe_lib.basedoc import base_task, split_by_section_id
from scribe_lib.checkfile import check_file_task
from scribe_lib.config import sha256_file
from scribe_lib.lineage import lineage_task
from scribe_lib.merge import merge_task
from scribe_lib.publish import publish_task
from scribe_fixtures import (
    doc,
    draft,
    fake_evidence,
    fixture_brain_db,
    load,
    plan_stale,
    publish_seed,
    run_version,
    setup_mini_project,
    claim_blocks,
    write_json,
)

pandoc_available = shutil.which("pandoc") is not None

RUN_SKILL = REPO_ROOT / "bundles" / "scribe" / "skills" / "run" / "SKILL.md"

ORIG_PATH = "Calls/Session w_X _ Acct.docx"
ORIG_TAG = f"[FILE:{ORIG_PATH}#Page 1 > Page 23]"
ESCAPED_TAG = "[FILE:Calls/Session w_X \\_ Acct.docx#Page 1 \\> Page 23]"


# ============================================================ Defect A ==

def test_parsing_an_escaped_tag_yields_the_unescaped_path():
    block = claims.parse_blocks(f"- A claim. {ESCAPED_TAG} <!-- c:aaaa0001 -->")[0]
    assert block["tags"] == [ORIG_TAG]
    kind, value = claims.parse_tag(block["tags"][0])
    assert (kind, value.split("#", 1)[0]) == ("FILE", ORIG_PATH)
    # parse_tag on the escaped form directly, too (sources/lineage/publish path).
    assert claims.parse_tag(ESCAPED_TAG) == ("FILE", f"{ORIG_PATH}#Page 1 > Page 23")
    # re-emitted text (what merge writes) carries the unescaped tag
    assert ORIG_TAG in block["text"] and "\\_" not in block["text"]
    # the rest of the escape set from the brief
    assert claims.unescape_tag_body(r"a\*b\#c\[d\(e\)f\\g\`h") == "a*b#c[d(e)f\\g`h"


@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
def test_render_docx_base_round_trip_yields_the_exact_original_tag(tmp_path, monkeypatch):
    """render -> docx -> base: a FILE path with `_` and a locator with `>`
    come back as the exact original tag (pandoc's gfm writer escapes both)."""
    import scribe_lib.render as render_mod

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    monkeypatch.setattr(render_mod, "_run_mermaid", lambda mmd, png: (False, "skip"))
    proj = setup_mini_project(tmp_path, title="Mini Round")
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]

    v1 = doc("m1", 1, "Mini Round", {
        "overview": f"- A file claim. {ORIG_TAG} <!-- c:aaaa0001 -->",
        "details": "A cited detail. [RAG:7] <!-- c:bbbb0001 -->",
    })
    render_md = tmp_path / "v1.md"
    render_md.write_text(v1, encoding="utf-8")
    render_mod.render_task(config, "m1", inst, data["instances"], md_path=render_md)
    docx = config.work_dir / "m1" / "render" / "Mini Round.docx"
    assert docx.is_file()

    # Confirm the defect's trigger is really present in pandoc's output.
    gfm = subprocess.run(["pandoc", "-f", "docx", "-t", "gfm", str(docx)], capture_output=True, text=True).stdout
    assert "\\_" in gfm and "\\>" in gfm

    out_dir = config.out_root / "m1"
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(docx, out_dir / "Mini Round.docx")
    # The "previous" published version genuinely lacks the file claim (unlike
    # `v1`, which is only what got rendered into the docx just now) — a real
    # content difference, not just a resaved-docx round trip (A9), so this
    # exercises the docx-recovery path this test targets.
    prev = doc("m1", 1, "Mini Round", {
        "overview": "- Nothing here yet. <!-- c:zzzz0001 -->",
        "details": "A cited detail. [RAG:7] <!-- c:bbbb0001 -->",
    })
    publish_seed(config, inst, prev, 1, docx_sha="sha-of-a-different-docx")  # => treated as human-edited

    result = base_task(config, "m1", inst, template)
    assert result["base_edited"] is True
    base_md = (config.work_dir / "m1" / "base.md").read_text(encoding="utf-8")
    assert f"- A file claim. {ORIG_TAG} " in base_md
    assert "\\_" not in base_md and "\\>" not in base_md


def test_carried_check_file_passes_for_an_escaped_published_tag(tmp_path):
    """The Night 5 failure: base (a copy of the published v003.md) holds the
    escaped tag; the agent keeps the claim verbatim; check-file must treat it
    as carried (unescaped path found in state.cited_raw), not demand a quote."""
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    raw = config.raw_root / ORIG_PATH
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("the session transcript\n", encoding="utf-8")

    claim = f"- Widget changes are made in the legacy tool. {ESCAPED_TAG} <!-- c:aaaa0001 -->"
    base = doc("m1", 3, "Mini m1", {"overview": claim, "details": "Detail. [RAG:1] <!-- c:bbbb0001 -->"})
    work_dir = config.work_dir / "m1"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base, encoding="utf-8")
    publish_seed(config, inst, base, 3, docx_sha=None, cited_raw={ORIG_PATH: sha256_file(raw)})

    draft(config, "m1", "overview", claim + "\n", evidence=[])
    result = check_file_task(config, "m1", inst)
    assert result["failed"] == []
    assert (result["checked"], result["passed"]) == (1, 1)


def test_merge_writes_tags_unescaped(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    base = doc("m1", 1, "Mini m1", {
        "overview": f"- Old claim. {ESCAPED_TAG} <!-- c:aaaa0001 -->",
        "details": "Detail. [RAG:1] <!-- c:bbbb0001 -->",
    })
    publish_seed(config, inst, base, 1, docx_sha=None)
    work_dir = config.work_dir / "m1"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base, encoding="utf-8")
    write_json(work_dir / "base.json", {"base_version": 1, "base_edited": False, "human_added": [], "human_modified": []})
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", f"- Old claim. {ESCAPED_TAG} <!-- c:aaaa0001 -->\n- New claim. {ESCAPED_TAG}\n")

    merge_task(config, "m1", data["instances"], data["templates"])
    overview = split_by_section_id((work_dir / "next.md").read_text(encoding="utf-8"))["overview"]
    assert overview.count(ORIG_TAG) == 2
    assert "\\_" not in overview and "\\>" not in overview


# ============================================================ Defect B ==

HUMAN_PARA = "Editor note: a follow-up widget review is being scheduled."
V1_OVERVIEW = "Widget purpose is to move alpha records between systems. [RAG:1] <!-- c:aaaa0001 -->"
HUMAN_MODIFIED = "Widget purpose is to move alpha and beta records between regional systems."


def _human_edited_docx(path: Path) -> None:
    """What a human's edit to the published docx looks like after render:
    one claim reworded (keeping its footnote), one uncited paragraph added."""
    md = (
        "# Mini Human\n\n"
        "## Overview\n\n"
        f"{HUMAN_MODIFIED}^[RAG:1 — doc, Intro]\n\n"
        f"{HUMAN_PARA}\n\n"
        "## Details\n\n"
        "Detail one.^[RAG:2 — doc, Intro]\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    src = path.parent.parent / "human-edit.md"
    src.write_text(md, encoding="utf-8")
    subprocess.run(["pandoc", str(src), "-o", str(path)], check=True)


@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
def test_human_claims_keep_origin_across_two_more_versions_and_pass_accept(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05")
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    db_path = fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = setup_mini_project(tmp_path, title="Mini Human", brain_db=db_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]
    src_dir = config.out_root / "m1" / "_src"

    v1 = doc("m1", 1, "Mini Human", {"overview": V1_OVERVIEW, "details": "Detail one. [RAG:2] <!-- c:bbbb0001 -->"})
    _human_edited_docx(config.out_root / "m1" / "Mini Human.docx")
    publish_seed(config, inst, v1, 1, docx_sha="sha-at-publish")  # docx changed since => human edit

    # --- v2: the edit is picked up; Details is re-drafted, Overview carried.
    accepted = run_version(
        config, data, inst, template, ["details"],
        {"details": "Detail one. [RAG:2] <!-- c:bbbb0001 -->\n\nDetail two. [RAG:3]\n"},
    )
    assert accepted["passed"], accepted
    v2 = claim_blocks(src_dir / "v002.md")["overview"]
    by_origin = {b["origin"]: b for b in v2}
    assert set(by_origin) == {"human", "human_modified"}
    human_id = by_origin["human"]["claim_id"]
    assert human_id == claims.assign_claim_id("m1", "overview", claims.normalize_text(HUMAN_PARA))
    assert by_origin["human_modified"]["claim_id"] == "aaaa0001"

    # --- v3: base is now plain v002.md (base.json has no human entries);
    # Overview is re-drafted and the agent copies the human claims verbatim.
    prior = split_by_section_id((src_dir / "v002.md").read_text(encoding="utf-8"))["overview"]
    accepted = run_version(
        config, data, inst, template, ["overview"],
        {"overview": prior + "\n\nA new cited overview claim. [RAG:4]\n"},
    )
    base_json = json.loads((config.work_dir / "m1" / "base.json").read_text(encoding="utf-8"))
    assert base_json["human_added"] == [] and base_json["human_modified"] == []
    assert accepted["passed"], accepted
    v3 = {b["claim_id"]: b for b in claim_blocks(src_dir / "v003.md")["overview"]}
    assert v3[human_id]["origin"] == "human" and v3[human_id]["tags"] == []
    assert v3["aaaa0001"]["origin"] == "human_modified"

    # lineage on v+2 still shows both origins
    lineage = json.loads((src_dir / "v003.lineage.json").read_text(encoding="utf-8"))
    origins = {n["claim_id"]: n.get("origin") for n in lineage["nodes"] if n["type"] == "claim" and n.get("version") == 3}
    assert origins[human_id] == "human"
    assert origins["aaaa0001"] == "human_modified"


def test_legacy_idless_human_paragraph_redrafted_gets_id_origin_and_passes_accept(tmp_path):
    """The exact Night 5 failure: v003.md (published before origin was persisted in the comment) holds a
    human paragraph with no id and no tag; base.json is empty; the section is
    re-drafted with the paragraph kept verbatim."""
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]
    v3 = doc("m1", 3, "Mini m1", {
        "overview": f"{V1_OVERVIEW}\n\n{HUMAN_PARA}",
        "details": "Detail one. [RAG:2] <!-- c:bbbb0001 -->",
    }, base="v002 (human-edited)")
    publish_seed(config, inst, v3, 3, docx_sha=None)
    base_task(config, "m1", inst, template)
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", f"{V1_OVERVIEW}\n\n{HUMAN_PARA}\n\nA new claim. [RAG:9]\n")

    merge_task(config, "m1", data["instances"], data["templates"])
    accepted = accept_task(config, "m1", inst, template)
    assert accepted["passed"], accepted
    overview = claim_blocks(config.work_dir / "m1" / "next.md")["overview"]
    human = [b for b in overview if b["origin"] == "human"]
    assert len(human) == 1 and human[0]["claim_id"] and human[0]["normalized"] == claims.normalize_text(HUMAN_PARA)


def test_agent_cannot_claim_human_origin_and_reworded_human_claim_loses_it(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]
    base = doc("m1", 2, "Mini m1", {
        "overview": f"{HUMAN_PARA} <!-- c:cccc0001 origin=human -->",
        "details": "Detail one. [RAG:2] <!-- c:bbbb0001 -->",
    })
    publish_seed(config, inst, base, 2, docx_sha=None)
    base_task(config, "m1", inst, template)
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", (
        "Editor note: a follow-up widget review has been scheduled. <!-- c:cccc0001 origin=human -->\n\n"
        "An uncited agent sentence. <!-- c:dddd0001 origin=human -->\n"
    ))
    merge_task(config, "m1", data["instances"], data["templates"])
    overview = claim_blocks(config.work_dir / "m1" / "next.md")["overview"]
    assert all(b["origin"] is None for b in overview)
    accepted = accept_task(config, "m1", inst, template)
    assert not accepted["checks"]["zero_unverified"]["passed"]
    assert len(accepted["checks"]["zero_unverified"]["claims"]) == 2


def test_check_file_skips_human_origin_claims(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    claim = f"- A human-corrected statement. {ORIG_TAG} <!-- c:cccc0001 origin=human_modified -->"
    base = doc("m1", 2, "Mini m1", {"overview": claim, "details": "Detail. [RAG:1] <!-- c:bbbb0001 -->"})
    work_dir = config.work_dir / "m1"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base, encoding="utf-8")
    publish_seed(config, inst, base, 2, docx_sha=None)  # no cited_raw: nothing to carry against
    draft(config, "m1", "overview", claim + "\n", evidence=[])

    result = check_file_task(config, "m1", inst)
    assert result["failed"] == []
    assert result["human_origin_skipped"] == 1 and result["checked"] == 0
    assert (work_dir / "sections" / "overview.md").read_text(encoding="utf-8") == claim + "\n"


def test_run_skill_keeps_human_origin_claims_out_of_the_verifier():
    text = RUN_SKILL.read_text(encoding="utf-8")
    step6 = text.split("### 6.", 1)[1].split("### 7.", 1)[0]
    assert "origin=human" in step6 and "origin=human_modified" in step6
    assert "human_origin_claims" in step6
    step4 = text.split("### 4.", 1)[1].split("### 5.", 1)[0]
    assert "Human-authored claims" in step4


def test_legacy_human_modified_origin_is_restored_from_the_published_lineage(tmp_path):
    """A version published before origin was persisted in the comment kept a human_modified claim's origin
    only in its lineage; base puts it back into the comment, so a re-drafted
    section keeps it (and check-file/accept/verifier treat it as human)."""
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]
    corrected = "Senior staff prefer the legacy tool for widget lookups. [RAG:5] <!-- c:eeee0001 -->"
    v3 = doc("m1", 3, "Mini m1", {"overview": f"{V1_OVERVIEW}\n\n{corrected}", "details": "Detail. [RAG:2] <!-- c:bbbb0001 -->"})
    publish_seed(config, inst, v3, 3, docx_sha=None)
    src_dir = config.out_root / "m1" / "_src"
    write_json(src_dir / "v003.lineage.json", {"nodes": [
        {"id": "claim:m1:v003:overview:eeee0001", "type": "claim", "claim_id": "eeee0001", "origin": "human_modified"},
        {"id": "claim:m1:v003:overview:aaaa0001", "type": "claim", "claim_id": "aaaa0001", "origin": None},
    ]})

    base_task(config, "m1", inst, template)
    base_overview = {b["claim_id"]: b for b in claim_blocks(config.work_dir / "m1" / "base.md")["overview"]}
    assert base_overview["eeee0001"]["origin"] == "human_modified"
    assert base_overview["aaaa0001"]["origin"] is None
    # details has no restored origin -> byte-identical to the published body
    assert split_by_section_id((config.work_dir / "m1" / "base.md").read_text(encoding="utf-8"))["details"] == \
        split_by_section_id(v3)["details"]

    plan_stale(config, "m1", ["overview"], ["details"])
    prior = split_by_section_id((config.work_dir / "m1" / "base.md").read_text(encoding="utf-8"))["overview"]
    # A9: restoring a legacy origin comment alone is not a VISIBLE change
    # (comments are invisible to a reader), so a redraft that only carries
    # `prior` forward unchanged is correctly a noop under the new policy —
    # add a genuinely new claim too, so this run actually publishes and the
    # carried-through origin can be checked in the published next.md.
    new_claim = "A brand new claim about widget lookups. [RAG:9] "
    draft(config, "m1", "overview", prior + "\n\n" + new_claim + "\n")
    merge_result = merge_task(config, "m1", data["instances"], data["templates"])
    assert merge_result["noop"] is False
    merged = {b["claim_id"]: b for b in claim_blocks(config.work_dir / "m1" / "next.md")["overview"]}
    assert merged["eeee0001"]["origin"] == "human_modified"


def test_check_file_quote_path_accepts_an_escaped_tag_in_draft_and_evidence(tmp_path):
    """Review fix 1: no state.cited_raw entry for the path (the live Night 5
    case), so the escaped tag copied from prior text needs a quote; the
    evidence entry written with the same escaped tag must be found."""
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    raw_dir = config.work_dir / "m1" / "raw"
    (raw_dir / "Calls").mkdir(parents=True, exist_ok=True)
    (raw_dir / (ORIG_PATH + ".md")).write_text("Widget changes are made in the legacy tool only.\n", encoding="utf-8")
    write_json(raw_dir / "manifest.json", [{"path": ORIG_PATH, "status": "ok", "md": ORIG_PATH + ".md"}])

    claim = f"- Widget changes are made in the legacy tool. {ESCAPED_TAG} <!-- c:aaaa0001 -->"
    base = doc("m1", 3, "Mini m1", {"overview": claim, "details": "Detail. [RAG:1] <!-- c:bbbb0001 -->"})
    (config.work_dir / "m1" / "base.md").write_text(base, encoding="utf-8")
    publish_seed(config, inst, base, 3, docx_sha=None)  # no cited_raw -> not carried

    quote = "Widget changes are made in the legacy tool only."
    for tag in (ESCAPED_TAG, ORIG_TAG):  # the agent may write either form in evidence.json
        draft(config, "m1", "overview", claim + "\n", evidence=[{"claim_ref": 0, "tag": tag, "quote": quote}])
        result = check_file_task(config, "m1", inst)
        assert result["failed"] == [], (tag, result)
        assert (result["checked"], result["passed"]) == (1, 1)
