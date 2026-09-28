import json
from scribe_fixtures import (fixture_brain_db, make_project, write_text, MINI_TEMPLATE, mini_task, load, doc,
                             publish_seed, plan_stale, draft)
from scribe_lib import brain as brain_mod, claims
from scribe_lib.checktask import check_task_task
from scribe_lib.fingerprint import fingerprint_task
from scribe_lib.pack import prepare_task


def _two_tasks(tmp_path):
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    write_text(proj / "templates" / "mini-profile.tmpl.md", MINI_TEMPLATE)
    mini_task(proj, "up")
    mini_task(proj, "down", upstream=["up"])
    return load(proj)


def _h(text):
    return brain_mod.text_hash(claims.normalize_text(text))


def test_check_task_rewrites_dangling_and_superseded_citations(tmp_path):
    config, data = _two_tasks(tmp_path)
    publish_seed(config, data["instances"]["up"], doc("up", 1, "Mini up", {
        "overview": "Live claim. [RAG:1] <!-- c:aaaa0001 -->\n\n**Superseded (2026-01-02):** Old claim. [RAG:2] <!-- c:aaaa0002 -->",
        "details": "D. [RAG:3] <!-- c:bbbb0001 -->"}), 1, docx_sha=None)
    plan_stale(config, "down", ["overview"], ["details"])
    draft(config, "down", "overview",
          "Uses live. [TASK:up#c:aaaa0001]\n\nUses gone. [TASK:up#c:dead0000]\n\nUses old. [TASK:up#c:aaaa0002]\n")
    r = check_task_task(config, "down", data["instances"])
    assert (r["checked"], r["passed"]) == (3, 1)
    assert {f["reason"] for f in r["failed"]} == {"upstream_claim_gone", "upstream_claim_superseded"}
    body = (config.work_dir / "down" / "sections" / "overview.md").read_text()
    assert body.count("Not modeled:") == 2 and "[TASK:up#c:aaaa0001]" in body
    assert check_task_task(config, "down", data["instances"])["failed"] == []   # idempotent


def test_downstream_staleness_follows_consumed_claims_not_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    config, data = _two_tasks(tmp_path)
    publish_seed(config, data["instances"]["up"], doc("up", 2, "Mini up", {
        "overview": "Live claim, now corrected. [RAG:1] <!-- c:aaaa0001 -->", "details": "D. [RAG:3] <!-- c:bbbb0001 -->"}),
        2, docx_sha=None)
    publish_seed(config, data["instances"]["down"], doc("down", 1, "Mini down", {
        "overview": "Uses live. [TASK:up#c:aaaa0001] <!-- c:cccc0001 -->", "details": "Own. [RAG:9] <!-- c:dddd0001 -->"}),
        1, docx_sha=None, extra_state={"upstream_versions": {"up": 1}, "sections": {
            "overview": {"cited_task_claims": {"up#aaaa0001": _h("Live claim.")}, "cited_chunks": {}, "cited_raw": {}},
            "details": {"cited_task_claims": {}, "cited_chunks": {}, "cited_raw": {}}}})
    fp = fingerprint_task(config, "down", data["instances"], data["templates"], data["edges"])
    assert "cited_task_claim_changed" in fp["sections"]["overview"]["stale_reasons"]
    assert "upstream_published" not in fp["sections"]["details"]["stale_reasons"]


def test_pack_does_not_offer_superseded_upstream_claims(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    config, data = _two_tasks(tmp_path)
    publish_seed(config, data["instances"]["up"], doc("up", 1, "Mini up", {
        "overview": "Live claim. [RAG:1] <!-- c:aaaa0001 -->\n\n**Superseded (2026-01-02):** Old claim. [RAG:2] <!-- c:aaaa0002 -->",
        "details": "D. [RAG:3] <!-- c:bbbb0001 -->"}), 1, docx_sha=None)
    prepare_task(config, "down")
    packs = "".join(p.read_text() for p in (config.work_dir / "down" / "pack").glob("*.pack.md"))
    assert "[TASK:up#c:aaaa0001]" in packs and "[TASK:up#c:aaaa0002]" not in packs
