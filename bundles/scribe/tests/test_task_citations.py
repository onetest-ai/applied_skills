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


MINI_TASKS_TEMPLATE = """\
---
id: mini-tasks-profile
version: 1
goal: "A minimal 2-section template with an upstream-consuming section, for scribe fixture tests."
params: [name]
inputs:
  brain: {}
  raw: { match: [], globs: ["**/*"], exclude: [] }
  prior: latest
  tasks: []
output:
  formats: [docx, pdf]
  versioning: extend
  sections:
    - id: overview
      title: "Overview"
      intent: "What {{name}} is."
      queries: ["{{name}} overview"]
      lanes: [narrative, tasks]
    - id: details
      title: "Details"
      intent: "Details about {{name}}."
      queries: ["{{name}} details"]
      lanes: [narrative]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

Drafting guidance.
"""


def _consuming_two_tasks(tmp_path):
    """Like `_two_tasks`, but `down` uses a template whose `overview` section
    declares `lanes: [narrative, tasks]` — an upstream-consuming section per
    the controller ruling — while `details` stays a plain `[narrative]`
    section with no `tasks` lane and no `[TASK:]` citations."""
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    write_text(proj / "templates" / "mini-profile.tmpl.md", MINI_TEMPLATE)
    write_text(proj / "templates" / "mini-tasks-profile.tmpl.md", MINI_TASKS_TEMPLATE)
    mini_task(proj, "up")
    write_text(
        proj / "tasks" / "down.task.md",
        "---\ntemplate: mini-tasks-profile@1\nid: down\ntitle: \"Mini down\"\nparams:\n  name: \"Widget\"\n"
        "inputs:\n  raw:\n    exclude: []\n  tasks: [up]\naudience: \"team\"\ncadence: on-brain-update\n"
        "publish: auto\nout: \"down\"\n---\nnotes for down\n",
    )
    return load(proj)


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


def test_upstream_claim_becoming_superseded_marks_downstream_stale(tmp_path, monkeypatch):
    """Review fix round 1, Important 1: `normalize_text` strips the
    `**Superseded (...):**` marker, so a naive hash comparison would report
    "same" for a claim that became superseded, and the citing section would
    be carried byte-for-byte forever, never seen by `check-task`. up v1 has a
    live claim `down` cites; up v2 supersedes that SAME claim id (text
    otherwise unchanged) -> down's citing section must go stale with its own
    reason, independent of the hash."""
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    config, data = _two_tasks(tmp_path)
    publish_seed(config, data["instances"]["up"], doc("up", 1, "Mini up", {
        "overview": "Live claim. [RAG:1] <!-- c:aaaa0001 -->", "details": "D. [RAG:3] <!-- c:bbbb0001 -->"}),
        1, docx_sha=None)
    publish_seed(config, data["instances"]["down"], doc("down", 1, "Mini down", {
        "overview": "Uses live. [TASK:up#c:aaaa0001] <!-- c:cccc0001 -->", "details": "Own. [RAG:9] <!-- c:dddd0001 -->"}),
        1, docx_sha=None, extra_state={"upstream_versions": {"up": 1}, "sections": {
            "overview": {"cited_task_claims": {"up#aaaa0001": _h("Live claim.")}, "cited_chunks": {}, "cited_raw": {}},
            "details": {"cited_task_claims": {}, "cited_chunks": {}, "cited_raw": {}}}})
    publish_seed(config, data["instances"]["up"], doc("up", 2, "Mini up", {
        "overview": "**Superseded (2026-02-01):** Live claim. [RAG:1] <!-- c:aaaa0001 -->\n\n"
                    "Replacement claim. [RAG:4] <!-- c:aaaa0004 -->",
        "details": "D. [RAG:3] <!-- c:bbbb0001 -->"}), 2, docx_sha=None)
    fp = fingerprint_task(config, "down", data["instances"], data["templates"], data["edges"])
    overview = fp["sections"]["overview"]
    assert overview["cited_task_claim_status"]["up#aaaa0001"] == "superseded"
    assert "cited_task_claim_superseded" in overview["stale_reasons"]


def test_new_upstream_claim_marks_consuming_section_stale_only(tmp_path, monkeypatch):
    """Controller ruling on the reviewer's open question: a `tasks`-lane
    section must pick up NEW upstream material, even material it never
    cited before. `up` publishes a new claim; `down`'s `overview` (lanes
    include `tasks`) must go stale with `upstream_claims_changed` (a), while
    `details` (no `tasks` lane, no `[TASK:]` cites) is untouched (b)."""
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    config, data = _consuming_two_tasks(tmp_path)
    publish_seed(config, data["instances"]["up"], doc("up", 1, "Mini up", {
        "overview": "Live claim. [RAG:1] <!-- c:aaaa0001 -->", "details": "D. [RAG:3] <!-- c:bbbb0001 -->"}),
        1, docx_sha=None)
    snapshot_v1 = {"up#aaaa0001": _h("Live claim."), "up#bbbb0001": _h("D.")}
    publish_seed(config, data["instances"]["down"], doc("down", 1, "Mini down", {
        "overview": "Nothing new yet.", "details": "Own. [RAG:9] <!-- c:dddd0001 -->"}),
        1, docx_sha=None, extra_state={
            "upstream_versions": {"up": 1}, "upstream_claims_snapshot": snapshot_v1, "sections": {
                "overview": {"cited_task_claims": {}, "cited_chunks": {}, "cited_raw": {}},
                "details": {"cited_task_claims": {}, "cited_chunks": {}, "cited_raw": {}}}})
    publish_seed(config, data["instances"]["up"], doc("up", 2, "Mini up", {
        "overview": "Live claim. [RAG:1] <!-- c:aaaa0001 -->\n\nNew claim. [RAG:4] <!-- c:aaaa0003 -->",
        "details": "D. [RAG:3] <!-- c:bbbb0001 -->"}), 2, docx_sha=None)
    fp = fingerprint_task(config, "down", data["instances"], data["templates"], data["edges"])
    assert "upstream_claims_changed" in fp["sections"]["overview"]["stale_reasons"]          # (a)
    assert "upstream_claims_changed" not in fp["sections"]["details"]["stale_reasons"]        # (b)
