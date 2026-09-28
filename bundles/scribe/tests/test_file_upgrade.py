import json
from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed, plan_stale, draft)
from scribe_lib import brain as brain_mod
from scribe_lib.checkfile import check_file_task
from scribe_lib.fingerprint import fingerprint_task
from scribe_lib.pack import prepare_task

FILE_CLAIM = "Accounts roll up to a master. [FILE:Calls/Session.docx#Page 1] <!-- c:aaaa0001 -->"


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
