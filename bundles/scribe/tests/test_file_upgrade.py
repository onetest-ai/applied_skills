import json
import shutil
import subprocess

import pytest

from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed, plan_stale, draft)
from scribe_lib import brain as brain_mod
from scribe_lib.accept import accept_task
from scribe_lib.basedoc import base_task
from scribe_lib.checkfile import check_file_task
from scribe_lib.fingerprint import fingerprint_task
from scribe_lib.pack import prepare_task

FILE_CLAIM = "Accounts roll up to a master. [FILE:Calls/Session.docx#Page 1] <!-- c:aaaa0001 -->"
FILE_CLAIM_HUMAN = ("Accounts roll up to a master. [FILE:Calls/Session.docx#Page 1] "
                     "<!-- c:aaaa0001 origin=human_modified -->")


def test_doc_id_for_raw_uses_parse_corpus_slug():
    assert brain_mod.doc_id_for_raw("Calls/Session w_X.docx") == "Calls__Session w_X.docx.md"


def _setup(tmp_path, synced):
    db = fixture_brain_db(tmp_path / "k.sqlite", synced=synced)
    proj = setup_mini_project(tmp_path, brain_db=db)
    config, data = load(proj)
    publish_seed(config, data["instances"]["m1"],
                 doc("m1", 1, "Mini m1", {"overview": FILE_CLAIM, "details": "D. [RAG:2] <!-- c:bbbb0001 -->"}),
                 1, docx_sha=None)
    return config, data


def test_cited_file_that_entered_the_brain_marks_section_and_offers_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    monkeypatch.setattr(brain_mod, "chunks_for_doc", lambda cfg, doc_id, query, limit=3:
                        [{"chunk_id": "4242", "source": doc_id, "text": "master accounts roll up sub-accounts"}])
    config, data = _setup(tmp_path, {"Calls__Session.docx.md": "sha1"})
    fp = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    ov = fp["sections"]["overview"]
    assert "cited_raw_ingested" in ov["stale_reasons"]
    assert ov["ingested"] == [{"path": "Calls/Session.docx", "doc_id": "Calls__Session.docx.md", "claims": ["aaaa0001"]}]
    prepare_task(config, "m1")
    pack = (config.work_dir / "m1" / "pack" / "overview.pack.md").read_text()
    assert "[RAG:4242]" in pack and "c:aaaa0001" in pack


def test_not_ingested_file_is_not_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    config, data = _setup(tmp_path, {})
    fp = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    assert "cited_raw_ingested" not in fp["sections"]["overview"]["stale_reasons"]


def test_carried_file_claim_without_state_record_is_reported_not_rewritten(tmp_path):
    config, data = _setup(tmp_path, {})
    (config.work_dir / "m1").mkdir(parents=True, exist_ok=True)
    (config.work_dir / "m1" / "base.md").write_text(
        doc("m1", 1, "Mini m1", {"overview": FILE_CLAIM, "details": "D. [RAG:2] <!-- c:bbbb0001 -->"}))
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", FILE_CLAIM + "\n", evidence=[])
    r = check_file_task(config, "m1", data["instances"]["m1"])
    assert r["needs_quote"] == [{"section": "overview", "claim": "aaaa0001",
                                 "tag": "[FILE:Calls/Session.docx#Page 1]"}]
    assert r["failed"] == []
    assert (config.work_dir / "m1" / "sections" / "overview.md").read_text().strip() == FILE_CLAIM


# ------------------------------------------------------------- fix round 1 --

def _edited_docx(path, overview_md, details_md):
    src = path.parent / "edit.md"
    src.write_text(f"# Mini m1\n\n## Overview\n\n{overview_md}\n\n## Details\n\n{details_md}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pandoc", str(src), "-o", str(path)], check=True)


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc missing")
def test_human_deleted_ingested_file_claim_does_not_crash_and_is_not_offered(tmp_path, monkeypatch):
    """Important #1: `ingested` is computed from the task's last published
    `_src`, but the pack looks the claim's text up in `base.md`. A claim a
    person deleted from the docx (a `human_deleted` tombstone) is still in
    `_src` but absent from `base.md` — `prepare` must not crash querying the
    Brain with an empty string, and must never offer a tombstoned claim for
    re-citing."""
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    calls: list[str] = []
    monkeypatch.setattr(brain_mod, "chunks_for_doc",
                         lambda cfg, doc_id, query, limit=3: calls.append(query) or [])
    config, data = _setup(tmp_path, {"Calls__Session.docx.md": "sha1"})
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]

    state_path = config.out_root / "m1" / "_src" / "state.json"
    st = json.loads(state_path.read_text())
    st["published"]["docx_sha256"] = "sha-at-publish"
    state_path.write_text(json.dumps(st), encoding="utf-8")

    # The person deleted the FILE claim from "Overview" entirely in the docx.
    _edited_docx(config.out_root / "m1" / "Mini m1.docx", "Not modeled: nothing here.",
                 "D.^[RAG:2 — doc, Intro]")

    base_result = base_task(config, "m1", inst, tpl)
    assert base_result["base_edited"] is True
    assert [t["claim_id"] for t in base_result["human_deleted"]] == ["aaaa0001"]

    result = prepare_task(config, "m1")
    assert result["status"] == "ok"
    pack_text = (config.work_dir / "m1" / "pack" / "overview.pack.md").read_text()
    assert "aaaa0001" not in pack_text
    assert calls == []  # never queried with the tombstoned claim's (empty) text


def test_human_origin_file_claim_is_never_ingested(tmp_path, monkeypatch):
    """Important #2: a human-authored `[FILE:]` claim is copied verbatim per
    the drafting contract and is never re-cited by the agent, so it must
    never be offered under "Now in the Brain" (which would also keep the
    section stale every night for a claim nothing will ever re-cite)."""
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    calls: list[str] = []
    monkeypatch.setattr(brain_mod, "chunks_for_doc",
                         lambda cfg, doc_id, query, limit=3: calls.append(query) or [])
    db = fixture_brain_db(tmp_path / "k.sqlite", synced={"Calls__Session.docx.md": "sha1"})
    proj = setup_mini_project(tmp_path, brain_db=db)
    config, data = load(proj)
    publish_seed(config, data["instances"]["m1"],
                 doc("m1", 1, "Mini m1", {"overview": FILE_CLAIM_HUMAN, "details": "D. [RAG:2] <!-- c:bbbb0001 -->"}),
                 1, docx_sha=None)

    fp = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    ov = fp["sections"]["overview"]
    assert "cited_raw_ingested" not in ov["stale_reasons"]
    assert ov["ingested"] == []

    prepare_task(config, "m1")
    pack_text = (config.work_dir / "m1" / "pack" / "overview.pack.md").read_text()
    assert "Now in the Brain" not in pack_text
    assert calls == []


def test_pack_lists_carried_file_claim_needing_quote(tmp_path, monkeypatch):
    """Controller ruling on A7's ⚠️: a carried, non-human `[FILE:]` claim
    with no `cited_raw` state record is surfaced in the pack so the agent
    adds a quote while drafting, ahead of check-file reporting it."""
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    config, data = _setup(tmp_path, {})
    prepare_task(config, "m1")
    pack_text = (config.work_dir / "m1" / "pack" / "overview.pack.md").read_text()
    assert "## Carried [FILE:] claims needing a quote" in pack_text
    after = pack_text.split("## Carried [FILE:] claims needing a quote", 1)[1]
    assert "c:aaaa0001" in after.split("## Evidence", 1)[0]


def _next_md_with_file_claim() -> str:
    return (
        "<!-- scribe: task=m1 version=1 built_at=2026-01-01T00:00:00 template=mini-profile@1 base=none -->\n\n"
        "# Mini m1\n\n"
        "## Changes in this version {#changes}\n\n- initial\n\n"
        f"## Overview {{#overview}}\n\n{FILE_CLAIM}\n\n"
        "## Details {#details}\n\nNot modeled: nothing found yet.\n\n"
    )


def test_accept_fails_when_needs_quote_nonempty(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    work_dir = config.work_dir / "m1"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "next.md").write_text(_next_md_with_file_claim(), encoding="utf-8")
    (work_dir / "check-file.json").write_text(json.dumps({
        "task": "m1", "checked": 0, "passed": 0, "human_origin_skipped": 0, "failed": [],
        "needs_quote": [{"section": "overview", "claim": "aaaa0001", "tag": "[FILE:Calls/Session.docx#Page 1]"}],
    }), encoding="utf-8")

    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]
    result = accept_task(config, "m1", inst, template)
    assert result["status"] == "error"
    assert result["checks"]["zero_unverified"]["passed"] is False
    assert any(c["reason"] == "needs_quote" for c in result["checks"]["zero_unverified"]["claims"])


def test_accept_passes_when_needs_quote_empty(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    work_dir = config.work_dir / "m1"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "next.md").write_text(_next_md_with_file_claim(), encoding="utf-8")
    (work_dir / "check-file.json").write_text(json.dumps({
        "task": "m1", "checked": 1, "passed": 1, "human_origin_skipped": 0, "failed": [], "needs_quote": [],
    }), encoding="utf-8")

    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]
    result = accept_task(config, "m1", inst, template)
    assert result["status"] == "ok"
    assert result["passed"] is True
