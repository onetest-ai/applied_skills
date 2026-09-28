"""Smoke tests for check-file, merge, accept, publish, lineage, index.

Most tests write `work/<task>/*` fixture files directly (bypassing `prepare`,
which needs a real Brain) so the merge/publish/lineage accounting can be
pinned exactly. `scribe_lib.brain.search`/`evidence` are monkeypatched with a
tiny in-memory fake, as in `test_prepare.py`.
"""
from __future__ import annotations

import json
import textwrap

import pytest

from scribe_lib import brain as brain_mod
from scribe_lib import claims
from scribe_lib.accept import accept_task
from scribe_lib.checkfile import check_file_task
from scribe_lib.index import build_index, query_index
from scribe_lib.lineage import lineage_task
from scribe_lib.merge import merge_task
from scribe_lib.publish import publish_task
from scribe_fixtures import (
    MINI_TEMPLATE,
    fixture_brain_db,
    load,
    make_project,
    mini_task,
    setup_mini_project,
    write_json,
    write_text,
)



# ------------------------------------------------------------------- claims --

def test_claim_parser_paragraphs_bullets_mermaid_notmodeled_superseded():
    body = textwrap.dedent(
        """\
        A plain paragraph claim. [RAG:111] <!-- c:aaaa1111 -->

        - A bullet claim. [FILE:a.md#L1] <!-- c:bbbb2222 -->
        - **Superseded (2026-09-21):** old statement here. [FILE:a.md#L1] <!-- c:cccc3333 sup=c:bbbb2222 -->

        ```mermaid
        graph TD
          A --> B
        ```

        Not modeled: no evidence for this yet.
        """
    )
    blocks = claims.parse_blocks(body)
    kinds = [b["kind"] for b in blocks]
    assert kinds == ["para", "bullet", "bullet", "code", "not_modeled"]

    para = blocks[0]
    assert para["claim_id"] == "aaaa1111"
    assert para["tags"] == ["[RAG:111]"]
    assert para["normalized"] == "a plain paragraph claim."

    bullet = blocks[1]
    assert bullet["claim_id"] == "bbbb2222"
    assert bullet["tags"] == ["[FILE:a.md#L1]"]

    superseded = blocks[2]
    assert superseded["superseded"] is True
    assert superseded["superseded_date"] == "2026-09-21"
    assert superseded["claim_id"] == "cccc3333"
    assert superseded["sup_ref"] == "bbbb2222"
    assert superseded["normalized"] == "old statement here."

    code = blocks[3]
    assert "graph TD" in code["raw"]
    assert claims.is_claim(code) is False

    not_modeled = blocks[4]
    assert claims.is_claim(not_modeled) is False
    assert not_modeled["text"].startswith("Not modeled:")


def test_assign_claim_id_is_deterministic_and_scoped():
    a = claims.assign_claim_id("t1", "overview", "widget is great")
    b = claims.assign_claim_id("t1", "overview", "widget is great")
    c = claims.assign_claim_id("t1", "details", "widget is great")
    assert a == b
    assert a != c
    assert len(a) == 8


# -------------------------------------------------------------------- merge --

def _seed_published_v1(config, out: str, sections: dict[str, str], *, title: str) -> str:
    """Write a fake already-published v001.md + state.json (version 1) with
    the given {section_id: body} — no header/Changes-section fuss needed by
    callers, this builds a canonical file directly."""
    lines = [
        "<!-- scribe: task=x version=1 built_at=2026-01-01T00:00:00 template=mini-profile@1 base=none -->",
        "",
        f"# {title}",
        "",
        "## Changes in this version {#changes}",
        "",
        "- initial version",
        "",
    ]
    for sid, body in sections.items():
        lines += [f"## {sid.title()} {{#{sid}}}", "", body.strip(), ""]
    text = "\n".join(lines).rstrip() + "\n"
    src_dir = config.out_root / out / "_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "v001.md").write_text(text, encoding="utf-8")
    write_json(
        src_dir / "state.json",
        {"version": 1, "built_at": "2026-01-01T00:00:00", "published": {"docx_sha256": None, "pdf_sha256": None, "md_sha256": None}},
    )
    return text


def test_merge_carries_non_stale_section_byte_for_byte(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    details_body = "Widget details, unchanged. [RAG:555] <!-- c:dddd4444 -->"
    _seed_published_v1(config, "m1", {"overview": "placeholder", "details": details_body}, title="Mini m1")

    work_dir = config.work_dir / "m1"
    base_text = (config.out_root / "m1" / "_src" / "v001.md").read_text(encoding="utf-8")
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base_text, encoding="utf-8")
    write_json(work_dir / "base.json", {"base_version": 1, "base_edited": False, "human_added": [], "human_modified": []})
    write_json(
        work_dir / "pack" / "plan.json",
        {"task": "m1", "stale": [{"section": "overview", "reasons": ["fingerprint_changed"]}], "carried": ["details"], "noop": False},
    )
    write_text(work_dir / "sections" / "overview.md", "Widget is a tool. [RAG:111] <!-- c:aaaa1111 -->\n")

    result = merge_task(config, "m1", data["instances"], data["templates"])
    assert result["noop"] is False
    next_text = (work_dir / "next.md").read_text(encoding="utf-8")

    from scribe_lib.basedoc import split_by_section_id

    next_sections = split_by_section_id(next_text)
    assert next_sections["details"] == details_body

    merge_json = json.loads((work_dir / "merge.json").read_text(encoding="utf-8"))
    assert merge_json["sections"]["details"]["status"] == "carried"
    assert merge_json["sections"]["details"]["claims_before"] == 1
    assert merge_json["sections"]["details"]["kept"] == 1


def test_merge_carries_claim_id_on_reword_and_assigns_new_id_for_new_text(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    overview_body = "Widget is owned by the ops team. [RAG:111] <!-- c:aaaa1111 -->"
    _seed_published_v1(config, "m1", {"overview": overview_body, "details": "n/a"}, title="Mini m1")

    work_dir = config.work_dir / "m1"
    base_text = (config.out_root / "m1" / "_src" / "v001.md").read_text(encoding="utf-8")
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base_text, encoding="utf-8")
    write_json(work_dir / "base.json", {"base_version": 1, "base_edited": False, "human_added": [], "human_modified": []})
    write_json(
        work_dir / "pack" / "plan.json",
        {"task": "m1", "stale": [{"section": "overview", "reasons": ["fingerprint_changed"]}], "carried": ["details"], "noop": False},
    )
    write_text(
        work_dir / "sections" / "overview.md",
        """\
        Widget is owned by the operations team. [RAG:111]

        - Brand new fact about Widget. [RAG:222]
        """,
    )

    result = merge_task(config, "m1", data["instances"], data["templates"])
    assert result["noop"] is False
    next_text = (work_dir / "next.md").read_text(encoding="utf-8")
    assert "<!-- c:aaaa1111 -->" in next_text  # reworded claim kept its base id

    merge_json = json.loads((work_dir / "merge.json").read_text(encoding="utf-8"))
    overview = merge_json["sections"]["overview"]
    assert overview["status"] == "drafted"
    assert overview["claims_before"] == 1
    assert overview["claims_after"] == 2
    assert overview["reworded"] == 1
    assert overview["added"] == 1
    assert overview["kept"] == 0
    assert overview["dropped_by_check"] == 0
    assert overview["dropped_by_model"] == 0
    assert overview["superseded"] == 0

    from scribe_lib.basedoc import split_by_section_id

    new_id = claims.assign_claim_id("m1", "overview", "brand new fact about widget.")
    assert f"<!-- c:{new_id} -->" in split_by_section_id(next_text)["overview"]


def test_merge_dropped_vs_superseded_accounting(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    details_body = (
        "- Claim A original text. [RAG:1] <!-- c:aaaa0001 -->\n"
        "- Claim B original text. [RAG:2] <!-- c:aaaa0002 -->"
    )
    _seed_published_v1(config, "m1", {"overview": "n/a", "details": details_body}, title="Mini m1")

    work_dir = config.work_dir / "m1"
    base_text = (config.out_root / "m1" / "_src" / "v001.md").read_text(encoding="utf-8")
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base_text, encoding="utf-8")
    write_json(work_dir / "base.json", {"base_version": 1, "base_edited": False, "human_added": [], "human_modified": []})
    write_json(
        work_dir / "pack" / "plan.json",
        {"task": "m1", "stale": [{"section": "details", "reasons": ["fingerprint_changed"]}], "carried": ["overview"], "noop": False},
    )
    # B is silently dropped (never mentioned again); A is explicitly superseded.
    write_text(
        work_dir / "sections" / "details.md",
        "- **Superseded (2026-09-20):** Claim A original text. [RAG:1] <!-- c:aaaa0001 -->\n",
    )

    merge_task(config, "m1", data["instances"], data["templates"])
    merge_json = json.loads((work_dir / "merge.json").read_text(encoding="utf-8"))
    details = merge_json["sections"]["details"]
    assert details["claims_before"] == 2
    assert details["claims_after"] == 1
    assert details["superseded"] == 1
    assert details["dropped_by_check"] == 0
    assert details["dropped_by_model"] == 1
    assert details["kept"] == 0
    assert details["reworded"] == 0
    assert details["added"] == 0


def test_merge_kept_not_superseded_for_already_superseded_claim_carried_unchanged(tmp_path):
    """Review fix round 1, item 1: a base claim already superseded, carried
    forward unchanged, must count as `kept` this version — `superseded` only
    counts a claim becoming superseded THIS version. Probe from the review:
    base {A superseded, B}, draft = same two (unchanged) + new C ->
    expected kept=2, superseded=0, added=1 (previously: superseded=1, kept=1)."""
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    details_body = (
        "- **Superseded (2026-09-01):** Claim A original text. [RAG:1] <!-- c:aaaa0001 -->\n"
        "- Claim B original text. [RAG:2] <!-- c:aaaa0002 -->"
    )
    _seed_published_v1(config, "m1", {"overview": "n/a", "details": details_body}, title="Mini m1")

    work_dir = config.work_dir / "m1"
    base_text = (config.out_root / "m1" / "_src" / "v001.md").read_text(encoding="utf-8")
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base_text, encoding="utf-8")
    write_json(work_dir / "base.json", {"base_version": 1, "base_edited": False, "human_added": [], "human_modified": []})
    write_json(
        work_dir / "pack" / "plan.json",
        {"task": "m1", "stale": [{"section": "details", "reasons": ["fingerprint_changed"]}], "carried": ["overview"], "noop": False},
    )
    write_text(
        work_dir / "sections" / "details.md",
        "- **Superseded (2026-09-01):** Claim A original text. [RAG:1] <!-- c:aaaa0001 -->\n"
        "- Claim B original text. [RAG:2] <!-- c:aaaa0002 -->\n"
        "- Claim C brand new. [RAG:3]\n",
    )

    merge_task(config, "m1", data["instances"], data["templates"])
    merge_json = json.loads((work_dir / "merge.json").read_text(encoding="utf-8"))
    details = merge_json["sections"]["details"]
    assert details["claims_before"] == 2
    assert details["claims_after"] == 3
    assert details["kept"] == 2
    assert details["superseded"] == 0
    assert details["added"] == 1
    assert details["reworded"] == 0
    assert details["dropped_by_check"] == 0
    assert details["dropped_by_model"] == 0


def test_merge_noop_when_nothing_changed(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    sections = {"overview": "Widget overview text. [RAG:1] <!-- c:aaaa0001 -->", "details": "Widget details text. [RAG:2] <!-- c:aaaa0002 -->"}
    _seed_published_v1(config, "m1", sections, title="Mini m1")

    work_dir = config.work_dir / "m1"
    base_text = (config.out_root / "m1" / "_src" / "v001.md").read_text(encoding="utf-8")
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base_text, encoding="utf-8")
    write_json(work_dir / "base.json", {"base_version": 1, "base_edited": False, "human_added": [], "human_modified": []})
    # Nothing stale -> both sections carried byte-for-byte -> identical to v001 (ignoring header/changes).
    write_json(work_dir / "pack" / "plan.json", {"task": "m1", "stale": [], "carried": ["overview", "details"], "noop": True})

    result = merge_task(config, "m1", data["instances"], data["templates"])
    assert result["noop"] is True
    assert not (work_dir / "next.md").is_file()
    merge_json = json.loads((work_dir / "merge.json").read_text(encoding="utf-8"))
    assert merge_json["noop"] is True


def test_merge_noop_deletes_a_leftover_stale_next_md(tmp_path):
    """Review fix round 1, item 2: a next.md left over from an EARLIER run
    (e.g. one that stopped before publish, or a previous noop that predates
    this fix) must not survive a noop merge — publish keys off next.md's
    presence, so a stale one would let it re-publish an old version."""
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    sections = {"overview": "Widget overview text. [RAG:1] <!-- c:aaaa0001 -->", "details": "Widget details text. [RAG:2] <!-- c:aaaa0002 -->"}
    _seed_published_v1(config, "m1", sections, title="Mini m1")

    work_dir = config.work_dir / "m1"
    base_text = (config.out_root / "m1" / "_src" / "v001.md").read_text(encoding="utf-8")
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base_text, encoding="utf-8")
    write_json(work_dir / "base.json", {"base_version": 1, "base_edited": False, "human_added": [], "human_modified": []})
    write_json(work_dir / "pack" / "plan.json", {"task": "m1", "stale": [], "carried": ["overview", "details"], "noop": True})
    # Simulate the leftover artifact the old bug left behind.
    (work_dir / "next.md").write_text("stale leftover content from an earlier run\n", encoding="utf-8")
    assert (work_dir / "next.md").is_file()

    result = merge_task(config, "m1", data["instances"], data["templates"])
    assert result["noop"] is True
    assert not (work_dir / "next.md").is_file()


def test_merge_refuses_when_stale_section_has_no_draft(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    _seed_published_v1(config, "m1", {"overview": "x", "details": "y"}, title="Mini m1")
    work_dir = config.work_dir / "m1"
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text("", encoding="utf-8")
    write_json(
        work_dir / "pack" / "plan.json",
        {"task": "m1", "stale": [{"section": "overview", "reasons": ["first_run"]}], "carried": [], "noop": False},
    )
    from scribe_lib.config import ScribeError

    with pytest.raises(ScribeError, match="overview"):
        merge_task(config, "m1", data["instances"], data["templates"])


# ---------------------------------------------------------------- check-file --

def test_check_file_removes_claim_with_absent_quote(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    work_dir = config.work_dir / "m1"
    raw_dir = work_dir / "raw"
    write_text(raw_dir / "note.txt.md", "Widget adjustment recorded in Q1.\n")
    write_json(
        raw_dir / "manifest.json",
        [{"path": "note.txt", "sha256": "deadbeef", "md": "note.txt.md", "status": "ok", "reason": "test"}],
    )
    write_text(
        work_dir / "sections" / "overview.md",
        "- Widget adjustment recorded in Q1. [FILE:note.txt#L1]\n"
        "- Widget revenue doubled overnight. [FILE:note.txt#L2]\n",
    )
    write_json(
        work_dir / "sections" / "overview.evidence.json",
        [
            {"claim_ref": 0, "tag": "[FILE:note.txt#L1]", "quote": "Widget adjustment recorded in Q1."},
            {"claim_ref": 1, "tag": "[FILE:note.txt#L2]", "quote": "Widget revenue doubled overnight."},
        ],
    )

    result = check_file_task(config, "m1")
    assert result["checked"] == 2
    assert result["passed"] == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0] == {
        "section": "overview",
        "claim_ref": 1,
        "claim": None,
        "normalized": "widget revenue doubled overnight.",
        "tag": "[FILE:note.txt#L2]",
        "reason": "quote not found in note.txt",
    }

    rewritten = (work_dir / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "Widget adjustment recorded in Q1. [FILE:note.txt#L1]" in rewritten
    assert "Not modeled: quote not found in note.txt." in rewritten
    assert "Widget revenue doubled overnight" not in rewritten


def test_check_file_fails_claim_with_missing_evidence_entry(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    work_dir = config.work_dir / "m1"
    raw_dir = work_dir / "raw"
    write_text(raw_dir / "note.txt.md", "Some text.\n")
    write_json(
        raw_dir / "manifest.json",
        [{"path": "note.txt", "sha256": "x", "md": "note.txt.md", "status": "ok", "reason": "test"}],
    )
    write_text(work_dir / "sections" / "overview.md", "A claim with no sidecar entry. [FILE:note.txt#L1]\n")
    # No evidence.json at all.

    result = check_file_task(config, "m1")
    assert result["checked"] == 1
    assert result["passed"] == 0
    assert result["failed"][0]["reason"] == "missing evidence quote"


def test_check_file_is_idempotent_on_a_second_run(tmp_path):
    """Review fix round 1, item 3: check-file must be idempotent. Probe from
    the review: claims [bogus (bad quote), "beta fact." (good quote)] -> run
    1 fails claim 0 and passes claim 1; a second run on the unchanged section
    + evidence.json must NOT re-index claim 1 down to position 0 and destroy
    it as "missing evidence quote" — nothing about the file may change."""
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    work_dir = config.work_dir / "m1"
    raw_dir = work_dir / "raw"
    write_text(raw_dir / "note.txt.md", "Here is beta fact stated plainly.\n")
    write_json(
        raw_dir / "manifest.json",
        [{"path": "note.txt", "sha256": "x", "md": "note.txt.md", "status": "ok", "reason": "test"}],
    )
    write_text(
        work_dir / "sections" / "overview.md",
        "- A bogus claim with a bad quote. [FILE:note.txt#L1]\n"
        "- beta fact. [FILE:note.txt#L2]\n",
    )
    write_json(
        work_dir / "sections" / "overview.evidence.json",
        [
            {"claim_ref": 0, "tag": "[FILE:note.txt#L1]", "quote": "this text is not in the raw file at all"},
            {"claim_ref": 1, "tag": "[FILE:note.txt#L2]", "quote": "beta fact"},
        ],
    )

    result1 = check_file_task(config, "m1")
    assert result1["checked"] == 2
    assert result1["passed"] == 1
    assert [f["claim_ref"] for f in result1["failed"]] == [0]
    section_after_run1 = (work_dir / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "beta fact. [FILE:note.txt#L2]" in section_after_run1
    assert "<!-- cf:0 -->" in section_after_run1  # the stable-position marker was written

    result2 = check_file_task(config, "m1")
    assert result2["failed"] == []  # nothing NEW fails; the already-resolved claim is not re-checked
    assert result2["checked"] == 1
    assert result2["passed"] == 1
    section_after_run2 = (work_dir / "sections" / "overview.md").read_text(encoding="utf-8")
    assert section_after_run2 == section_after_run1  # byte-for-byte identical — nothing changed
    assert "beta fact. [FILE:note.txt#L2]" in section_after_run2  # still intact, not destroyed


# ------------------------------------------------------------------- accept --

def test_accept_flags_uncited_claim_and_missing_section(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    work_dir = config.work_dir / "m1"
    next_text = (
        "<!-- scribe: task=m1 version=1 built_at=2026-01-01T00:00:00 template=mini-profile@1 base=none -->\n\n"
        "# Mini m1\n\n"
        "## Changes in this version {#changes}\n\n- initial\n\n"
        "## Overview {#overview}\n\nAn uncited claim with no tag at all.\n\n"
    )
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "next.md").write_text(next_text, encoding="utf-8")

    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]
    result = accept_task(config, "m1", inst, template)
    assert result["status"] == "error"
    assert result["checks"]["sections_present"]["missing"] == ["details"]
    assert result["checks"]["zero_unverified"]["passed"] is False
    assert result["checks"]["diagrams_render"]["skipped"] is True


def test_accept_passes_a_clean_document(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    work_dir = config.work_dir / "m1"
    next_text = (
        "<!-- scribe: task=m1 version=1 built_at=2026-01-01T00:00:00 template=mini-profile@1 base=none -->\n\n"
        "# Mini m1\n\n"
        "## Changes in this version {#changes}\n\n- initial\n\n"
        "## Overview {#overview}\n\nA cited claim. [RAG:1] <!-- c:aaaa0001 -->\n\n"
        "## Details {#details}\n\nNot modeled: nothing found yet.\n\n"
    )
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "next.md").write_text(next_text, encoding="utf-8")

    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]
    result = accept_task(config, "m1", inst, template)
    assert result["status"] == "ok"
    assert result["passed"] is True


# ------------------------------------------------------------------ publish --

def _fake_next_md(task_id: str, version: int, title: str, sections: dict[str, str]) -> str:
    lines = [
        f"<!-- scribe: task={task_id} version={version} built_at=2026-01-15T00:00:00 template=mini-profile@1 base=v{version - 1:03d} -->",
        "",
        f"# {title}",
        "",
        "## Changes in this version {#changes}",
        "",
        "- 1 reworded",
        "",
    ]
    for sid, body in sections.items():
        lines += [f"## {sid.title()} {{#{sid}}}", "", body, ""]
    return "\n".join(lines).rstrip() + "\n"


def test_publish_auto_moves_old_version_and_keeps_stable_name(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-15")
    db_path = fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = setup_mini_project(tmp_path, title="Mini Profile", brain_db=db_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]

    out_dir = config.out_root / "m1"
    (out_dir).mkdir(parents=True, exist_ok=True)
    (out_dir / "Mini Profile.docx").write_bytes(b"old-docx")
    (out_dir / "Mini Profile.pdf").write_bytes(b"old-pdf")
    write_json(out_dir / "_src" / "state.json", {"version": 1, "built_at": "2026-01-01T00:00:00", "published": {"docx_sha256": "old", "pdf_sha256": "old", "md_sha256": "old"}})

    work_dir = config.work_dir / "m1"
    next_text = _fake_next_md("m1", 2, "Mini Profile", {"overview": "Widget overview. [RAG:1] <!-- c:aaaa0001 -->", "details": "n/a"})
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "next.md").write_text(next_text, encoding="utf-8")
    render_dir = work_dir / "render"
    render_dir.mkdir(parents=True)
    (render_dir / "Mini Profile.docx").write_bytes(b"new-docx")
    (render_dir / "Mini Profile.pdf").write_bytes(b"new-pdf")

    def fake_evidence(cfg, chunk_id):
        return {"chunk_id": str(chunk_id), "status": "ok", "text": "Widget overview text."}

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)

    result = publish_task(config, "m1", inst, template, data["instances"], data["edges"], no_render=False)
    assert result["status"] == "ok"
    assert result["published"] is True
    assert result["version"] == 2

    assert (out_dir / "_versions" / "v001_2026-01-15.docx").read_bytes() == b"old-docx"
    assert (out_dir / "_versions" / "v001_2026-01-15.pdf").read_bytes() == b"old-pdf"
    assert (out_dir / "Mini Profile.docx").read_bytes() == b"new-docx"
    assert (out_dir / "Mini Profile.pdf").read_bytes() == b"new-pdf"

    state = json.loads((out_dir / "_src" / "state.json").read_text(encoding="utf-8"))
    assert state["version"] == 2
    assert state["sections"]["overview"]["cited_chunks"] == {"1": brain_mod.text_hash("Widget overview text.")}
    assert (out_dir / "_src" / "v002.md").is_file()

    runs = json.loads((config.out_root / "_runs" / "2026-01-15.json").read_text(encoding="utf-8"))
    assert runs[-1]["status"] == "published"
    assert runs[-1]["version"] == 2


def test_publish_propose_does_not_advance_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-15")
    proj = setup_mini_project(
        tmp_path, publish="propose", title="Mini Propose", brain_db=fixture_brain_db(tmp_path / "k.sqlite")
    )
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]

    out_dir = config.out_root / "m1"
    write_json(out_dir / "_src" / "state.json", {"version": 1, "built_at": "2026-01-01T00:00:00", "published": {"docx_sha256": "old"}})
    (out_dir / "_src").mkdir(parents=True, exist_ok=True)
    (out_dir / "_src" / "v001.md").write_text("prior content\n", encoding="utf-8")

    work_dir = config.work_dir / "m1"
    next_text = _fake_next_md("m1", 2, "Mini Propose", {"overview": "New overview. [RAG:1] <!-- c:aaaa0001 -->", "details": "n/a"})
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "next.md").write_text(next_text, encoding="utf-8")

    # Task 12 review, Important #2: propose now computes and stages the full
    # state.json (same `_compute_new_state` an auto publish uses) so approve
    # never has to re-touch the Brain — that means propose itself resolves
    # the `[RAG:]` tag's evidence, same as an auto publish would.
    def fake_evidence(cfg, chunk_id):
        return {"chunk_id": str(chunk_id), "status": "ok", "text": "New overview text."}

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)

    result = publish_task(config, "m1", inst, template, data["instances"], data["edges"], no_render=True)
    assert result["status"] == "pending"
    assert result["published"] is False
    assert result["pending_version"] == 2

    state_after = json.loads((out_dir / "_src" / "state.json").read_text(encoding="utf-8"))
    assert state_after["version"] == 1  # unchanged

    pending_dir = out_dir / "_pending" / "v002"
    assert (pending_dir / "doc.md").read_text(encoding="utf-8") == next_text
    assert (pending_dir / "diff.md").is_file()
    pending_meta = json.loads((pending_dir / "pending.json").read_text(encoding="utf-8"))
    assert pending_meta["version"] == 2

    staged_state = json.loads((pending_dir / "state.json").read_text(encoding="utf-8"))
    assert staged_state["sections"]["overview"]["cited_chunks"] == {"1": brain_mod.text_hash("New overview text.")}
    assert not (out_dir / "Mini Propose.docx").exists()


def test_publish_refuses_a_stale_next_md_and_never_republishes(tmp_path, monkeypatch):
    """Review fix round 1, item 2: publish must never re-publish a next.md
    that does not name the version immediately after what is already
    published — e.g. a leftover next.md from a noop run that predates the
    merge-side fix. Probe from the review: v1 already published, a leftover
    next.md (still headed version=1) on disk -> publish must refuse, not
    silently republish v1 again (rewriting _src/v001.md, advancing state,
    logging a `published` run row)."""
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-16")
    db_path = fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = setup_mini_project(tmp_path, title="Mini Stale", brain_db=db_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]

    out_dir = config.out_root / "m1"
    src_dir = out_dir / "_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    original_v001 = "prior content, must not be overwritten\n"
    (src_dir / "v001.md").write_text(original_v001, encoding="utf-8")
    state_before = {
        "version": 1,
        "built_at": "2026-01-01T00:00:00",
        "published": {"docx_sha256": "old", "pdf_sha256": "old", "md_sha256": "old"},
    }
    write_json(src_dir / "state.json", state_before)

    work_dir = config.work_dir / "m1"
    stale_next = _fake_next_md("m1", 1, "Mini Stale", {"overview": "Stale leftover. [RAG:1] <!-- c:aaaa0001 -->", "details": "n/a"})
    (work_dir).mkdir(parents=True, exist_ok=True)
    (work_dir / "next.md").write_text(stale_next, encoding="utf-8")

    result = publish_task(config, "m1", inst, template, data["instances"], data["edges"], no_render=True)
    assert result["status"] == "error"
    assert "stale" in result["reason"].lower()

    # Nothing was touched: v001.md and state.json are exactly as before.
    assert (src_dir / "v001.md").read_text(encoding="utf-8") == original_v001
    assert json.loads((src_dir / "state.json").read_text(encoding="utf-8")) == state_before
    assert not (src_dir / "v002.md").is_file()

    runs = json.loads((config.out_root / "_runs" / "2026-01-16.json").read_text(encoding="utf-8"))
    assert runs[-1]["status"] == "failed"
    assert runs[-1]["version"] is None


# ------------------------------------------------------------------ lineage --

def test_lineage_carried_from_and_chunk_ids_as_strings(tmp_path, monkeypatch):
    proj = setup_mini_project(tmp_path, title="Mini Lineage")
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]

    def fake_evidence(cfg, chunk_id):
        return {"chunk_id": str(chunk_id), "status": "ok", "text": "Widget purpose text.", "source": "doc.md", "section": "Intro", "ord": 3}

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)

    out_dir = config.out_root / "m1"
    src_dir = out_dir / "_src"
    src_dir.mkdir(parents=True)
    v1_text = _fake_next_md("m1", 1, "Mini Lineage", {"overview": "Widget purpose. [RAG:999999999999999] <!-- c:aaaa1111 -->", "details": "n/a"})
    (src_dir / "v001.md").write_text(v1_text, encoding="utf-8")
    write_json(src_dir / "state.json", {"version": 1, "built_at": "2026-01-01T00:00:00", "published": {}})

    result1 = lineage_task(config, "m1", inst, template)
    assert result1["status"] == "ok"
    lineage1 = json.loads((src_dir / "v001.lineage.json").read_text(encoding="utf-8"))
    chunk_nodes = [n for n in lineage1["nodes"] if n["type"] == "chunk"]
    assert len(chunk_nodes) == 1
    assert chunk_nodes[0]["chunk_id"] == "999999999999999"
    assert isinstance(chunk_nodes[0]["chunk_id"], str)
    assert not any(e["type"] == "carried_from" for e in lineage1["edges"])

    # Version 2 carries the SAME claim id forward (as merge would for a
    # non-stale section).
    v2_text = _fake_next_md("m1", 2, "Mini Lineage", {"overview": "Widget purpose. [RAG:999999999999999] <!-- c:aaaa1111 -->", "details": "n/a"})
    (src_dir / "v002.md").write_text(v2_text, encoding="utf-8")
    write_json(src_dir / "state.json", {"version": 2, "built_at": "2026-01-02T00:00:00", "published": {}})

    result2 = lineage_task(config, "m1", inst, template)
    lineage2 = json.loads((src_dir / "v002.lineage.json").read_text(encoding="utf-8"))
    carried = [e for e in lineage2["edges"] if e["type"] == "carried_from"]
    assert len(carried) == 1
    assert carried[0]["from"] == "claim:m1:v002:overview:aaaa1111"
    assert carried[0]["to"] == "claim:m1:v001:overview:aaaa1111"

    sources = json.loads((src_dir / "v002.sources.json").read_text(encoding="utf-8"))
    assert any(s["tag"] == "RAG:999999999999999" and s["chunk_id"] == "999999999999999" for s in sources)


def test_lineage_tags_human_added_and_human_modified_claims_with_origin(tmp_path, monkeypatch):
    """Controller ruling, fix round 1 item 4: H5 ("human edits survive") is
    judged from lineage, so a human_added claim (no claim id at all) must
    still get a claim node — synthetic id, `origin: "human"` — and a
    human_modified claim (which DOES keep a real id) must be tagged
    `origin: "human_modified"` on its ordinary claim node."""
    proj = setup_mini_project(tmp_path, title="Mini Human")
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]

    def fake_evidence(cfg, chunk_id):
        return {"chunk_id": str(chunk_id), "status": "ok", "text": "Widget purpose text.", "source": "doc.md", "section": "Intro", "ord": 1}

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)

    out_dir = config.out_root / "m1"
    src_dir = out_dir / "_src"
    src_dir.mkdir(parents=True)
    human_added_text = "This is a brand new sentence added by a human with no citation at all."
    v1_text = _fake_next_md(
        "m1",
        1,
        "Mini Human",
        {
            "overview": f"Widget purpose. [RAG:1] <!-- c:aaaa0001 -->\n\n{human_added_text}",
            "details": "n/a",
        },
    )
    (src_dir / "v001.md").write_text(v1_text, encoding="utf-8")
    write_json(src_dir / "state.json", {"version": 1, "built_at": "2026-01-01T00:00:00", "published": {}})

    work_dir = config.work_dir / "m1"
    write_json(
        work_dir / "base.json",
        {
            "base_version": 0,
            "base_edited": True,
            "human_added": [{"section": "overview", "text": human_added_text}],
            "human_modified": [{"section": "overview", "claim_id": "aaaa0001", "text": "Widget purpose."}],
        },
    )

    result = lineage_task(config, "m1", inst, template)
    assert result["status"] == "ok"
    lineage = json.loads((src_dir / "v001.lineage.json").read_text(encoding="utf-8"))
    claim_nodes = [n for n in lineage["nodes"] if n["type"] == "claim" and n.get("version") == 1]

    modified_node = next(n for n in claim_nodes if n["claim_id"] == "aaaa0001")
    assert modified_node["origin"] == "human_modified"

    human_nodes = [n for n in claim_nodes if n.get("origin") == "human"]
    assert len(human_nodes) == 1
    assert human_nodes[0]["claim_id"] is None
    assert human_nodes[0]["id"].startswith("claim:m1:v001:overview:human:")
    assert any(e["type"] == "has_claim" and e["to"] == human_nodes[0]["id"] for e in lineage["edges"])


# --------------------------------------------------------------------- index --

def test_index_transitive_query_through_task_claim(tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    templates_dir = proj / "templates"
    write_text(templates_dir / "mini-profile.tmpl.md", MINI_TEMPLATE)
    mini_task(proj, "t1", out="t1", title="Task One")
    mini_task(proj, "t3", out="t3", title="Task Three")
    # t3 depends on t1 (inputs.tasks) so validate_all's DAG accepts both.
    (proj / "tasks" / "t3.task.md").write_text(
        (proj / "tasks" / "t3.task.md").read_text(encoding="utf-8").replace(
            "inputs:\n  raw:\n    exclude: []", "inputs:\n  raw:\n    exclude: []\n  tasks: [t1]"
        ),
        encoding="utf-8",
    )

    config, data = load(proj)

    def fake_evidence(cfg, chunk_id):
        return {"chunk_id": str(chunk_id), "status": "ok", "text": "T1 evidence text.", "source": "docT1.md", "section": "S", "ord": 1}

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)

    # t1 v1: one claim citing a Brain doc directly.
    t1_out = config.out_root / "t1"
    (t1_out / "_src").mkdir(parents=True)
    t1_text = _fake_next_md("t1", 1, "Task One", {"overview": "T1 claim. [RAG:1] <!-- c:aaaa0001 -->", "details": "n/a"})
    (t1_out / "_src" / "v001.md").write_text(t1_text, encoding="utf-8")
    write_json(t1_out / "_src" / "state.json", {"version": 1, "built_at": "2026-01-01T00:00:00", "published": {}})
    lineage_task(config, "t1", data["instances"]["t1"], data["templates"]["mini-profile"])

    # t3 v1: a claim that cites t1's claim via [TASK:].
    t3_out = config.out_root / "t3"
    (t3_out / "_src").mkdir(parents=True)
    t3_text = _fake_next_md("t3", 1, "Task Three", {"overview": "T3 claim derived from T1. [TASK:t1#c:aaaa0001] <!-- c:bbbb0001 -->", "details": "n/a"})
    (t3_out / "_src" / "v001.md").write_text(t3_text, encoding="utf-8")
    write_json(t3_out / "_src" / "state.json", {"version": 1, "built_at": "2026-01-01T00:00:00", "published": {}})
    lineage_task(config, "t3", data["instances"]["t3"], data["templates"]["mini-profile"])

    index = build_index(config)
    assert "docT1.md" in index
    tasks_citing = {e["task"] for e in index["docT1.md"]}
    assert tasks_citing == {"t1", "t3"}

    results = query_index(config, "docT1.md")
    t3_entry = next(e for e in results if e["task"] == "t3")
    assert t3_entry == {"task": "t3", "version": 1, "section": "overview", "claim": "bbbb0001"}
