import json, shutil, subprocess
import pytest
from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed, plan_stale, draft,
                             fake_evidence)
from scribe_lib import brain as brain_mod, claims
from scribe_lib.basedoc import base_task
from scribe_lib.config import sha256_file
from scribe_lib.fingerprint import fingerprint_task
from scribe_lib.merge import merge_task
from scribe_lib.observe import observe_task
from scribe_lib.render import render_task

OVERVIEW = 'The "Alpha" plan costs $3. [RAG:1] <!-- c:aaaa0001 -->'
DETAILS = "D. [RAG:2] <!-- c:bbbb0001 -->"


def test_visible_text_ignores_comments_escapes_quotes_and_spacing():
    a = 'The "Alpha" plan costs $3. [RAG:1] <!-- c:aaaa0001 -->'
    b = "The “Alpha”  plan costs \\$3. [RAG:1] <!-- c:aaaa0001 origin=human -->"
    assert claims.visible_text(a) == claims.visible_text(b)


def _project(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    return config, data, data["instances"]["m1"], data["templates"]["mini-profile"]


def test_comment_or_quote_only_change_is_a_noop(tmp_path):
    config, data, inst, tpl = _project(tmp_path)
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": OVERVIEW, "details": DETAILS}), 1, docx_sha=None)
    base_task(config, "m1", inst, tpl)
    plan_stale(config, "m1", ["overview"], ["details"])
    draft(config, "m1", "overview", "The “Alpha” plan costs \\$3. [RAG:1] <!-- c:aaaa0001 -->\n")
    assert merge_task(config, "m1", data["instances"], data["templates"])["noop"] is True
    assert not (config.work_dir / "m1" / "next.md").exists()


@pytest.mark.skipif(not (shutil.which("pandoc") and shutil.which("soffice")), reason="pandoc/soffice missing")
def test_word_resave_without_edit_is_not_a_human_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    config, data, inst, tpl = _project(tmp_path)
    v1 = doc("m1", 1, "Mini m1", {"overview": OVERVIEW, "details": DETAILS})
    (config.work_dir / "m1").mkdir(parents=True)
    (config.work_dir / "m1" / "next.md").write_text(v1)
    rendered = render_task(config, "m1", inst, tpl)
    stable = config.out_root / "m1" / "Mini m1.docx"
    stable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(config.work_dir / "m1" / "render" / rendered["docx"], stable)
    publish_seed(config, inst, v1, 1, docx_sha=sha256_file(stable))
    subprocess.run(["pandoc", str(stable), "-o", str(stable)], check=True)   # "open and save": new bytes, same content
    assert sha256_file(stable) != json.loads((config.out_root / "m1" / "_src" / "state.json").read_text())["published"]["docx_sha256"]
    result = base_task(config, "m1", inst, tpl)
    assert result["base_edited"] is False
    assert (config.work_dir / "m1" / "base.md").read_text() == v1


def test_observe_settles_a_section_whose_cited_chunk_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    config, data, inst, tpl = _project(tmp_path)
    v1 = doc("m1", 1, "Mini m1", {"overview": OVERVIEW, "details": DETAILS})
    publish_seed(config, inst, v1, 1, docx_sha=None, extra_state={"sections": {
        "overview": {"cited_chunks": {"1": "stale-hash"}, "cited_raw": {}},
        "details": {"cited_chunks": {"2": brain_mod.text_hash("evidence text 2")}, "cited_raw": {}}}})
    fp = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    assert "cited_chunk_changed" in fp["sections"]["overview"]["stale_reasons"]
    observe_task(config, "m1", inst, tpl, data["instances"], data["edges"])   # the redraft was a noop
    fp2 = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    assert "cited_chunk_changed" not in fp2["sections"]["overview"]["stale_reasons"]
