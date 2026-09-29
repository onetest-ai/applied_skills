import json, shutil, subprocess
import pytest
from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed, plan_stale, draft,
                             fake_evidence, write_json)
from scribe_lib import brain as brain_mod, claims
from scribe_lib.basedoc import base_task, split_by_section_id
from scribe_lib.config import sha256_file
from scribe_lib.fingerprint import fingerprint_task
from scribe_lib.merge import merge_task
from scribe_lib.observe import observe_task
from scribe_lib.render import render_task

OVERVIEW = 'The "Alpha" plan costs $3. [RAG:1] <!-- c:aaaa0001 -->'
DETAILS = "D. [RAG:2] <!-- c:bbbb0001 -->"

pandoc_soffice_available = bool(shutil.which("pandoc") and shutil.which("soffice"))


def _mermaid_available() -> bool:
    if not shutil.which("npx"):
        return False
    try:
        proc = subprocess.run(
            ["npx", "-y", "@mermaid-js/mermaid-cli", "--version"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0


mermaid_available = pandoc_soffice_available and _mermaid_available()


def _resave_scenario(tmp_path, monkeypatch, details_body: str):
    """render `details_body` into a docx, publish it as v1, then pandoc
    "open and save" it in place (new bytes, same visible content) before
    running `base_task` — the Word re-save probe every resave test shares."""
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    config, data, inst, tpl = _project(tmp_path)
    v1 = doc("m1", 1, "Mini m1", {"overview": OVERVIEW, "details": details_body})
    (config.work_dir / "m1").mkdir(parents=True)
    (config.work_dir / "m1" / "next.md").write_text(v1)
    rendered = render_task(config, "m1", inst, tpl)
    stable = config.out_root / "m1" / "Mini m1.docx"
    stable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(config.work_dir / "m1" / "render" / rendered["docx"], stable)
    publish_seed(config, inst, v1, 1, docx_sha=sha256_file(stable))
    subprocess.run(["pandoc", str(stable), "-o", str(stable)], check=True)
    result = base_task(config, "m1", inst, tpl)
    return config, result, v1


def test_visible_text_ignores_comments_escapes_quotes_and_spacing():
    a = 'The "Alpha" plan costs $3. [RAG:1] <!-- c:aaaa0001 -->'
    b = "The “Alpha”  plan costs \\$3. [RAG:1] <!-- c:aaaa0001 origin=human -->"
    assert claims.visible_text(a) == claims.visible_text(b)


def test_normalize_text_folds_typography_like_visible_text(tmp_path):
    """Fix A1: a docx round trip turns `Bain's` into `Bain’s`, straight
    quotes into curly ones, `--`/`---` into en/em dash glyphs, `...` into a
    single ellipsis character, and a plain space next to it into an nbsp —
    none of that is a human edit, so `normalize_text` must fold it away the
    same as `visible_text` (they share `claims.fold_typography`)."""
    pairs = [
        ("Bain's plan.", "Bain’s plan."),                       # curly apostrophe
        ('The "Alpha" plan.', "The “Alpha” plan."),         # curly double quotes
        ('The "Alpha" plan.', "The „Alpha“ plan."),          # German-style low/high quotes
        ("Region a - b grew.", "Region a – b grew."),            # en dash
        ("Region a - b grew.", "Region a — b grew."),            # em dash
        ("Region a - b grew.", "Region a -- b grew."),                # literal double hyphen
        ("Region a - b grew.", "Region a --- b grew."),               # literal triple hyphen
        ("Rollout continues... slowly.", "Rollout continues… slowly."),  # ellipsis glyph
        ("A B", "A B"),                                          # nbsp vs plain space
    ]
    for straight, curly in pairs:
        assert claims.normalize_text(straight) == claims.normalize_text(curly), (straight, curly)


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


@pytest.mark.skipif(not pandoc_soffice_available, reason="pandoc/soffice missing")
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


@pytest.mark.skipif(not pandoc_soffice_available, reason="pandoc/soffice missing")
def test_word_resave_with_a_table_is_not_a_human_edit(tmp_path, monkeypatch):
    """Review fix round 1, Important 1: pandoc's docx round trip re-serializes
    a table's delimiter row (dash counts, alignment colons) and cell padding
    — that alone must not make a plain re-save look like a human edit."""
    details = DETAILS + "\n\n| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 40 |"
    config, result, v1 = _resave_scenario(tmp_path, monkeypatch, details)
    assert result["base_edited"] is False
    assert result["human_added"] == []
    assert result["human_modified"] == []
    assert (config.work_dir / "m1" / "base.md").read_text() == v1


@pytest.mark.skipif(not mermaid_available, reason="pandoc/soffice/mermaid-cli missing")
def test_word_resave_with_a_diagram_is_not_a_human_edit(tmp_path, monkeypatch):
    """Review fix round 1, Important 1: a docx round trip cannot recover a
    fenced mermaid diagram's source — it comes back as an embedded image
    (`<figure>`/`<img>`) — so a plain re-save of a section with a diagram
    must not record a spurious human_added claim for the image placeholder
    (the I1 failure A9 targets)."""
    details = DETAILS + "\n\n```mermaid\ngraph TD; A-->B\n```"
    config, result, v1 = _resave_scenario(tmp_path, monkeypatch, details)
    assert result["base_edited"] is False
    assert result["human_added"] == []
    assert result["human_modified"] == []
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


def test_observe_keeps_reporting_a_cited_chunk_that_disappeared(tmp_path, monkeypatch):
    """Review fix round 1, Important 2: a gone chunk must be recorded as
    `None` (not dropped) so the section stays stale — never silently
    "settle" while it still cites evidence that no longer exists."""
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])

    def _gone_evidence(cfg, chunk_id):
        if str(chunk_id) == "1":
            return {"chunk_id": "1", "status": "gone"}
        return fake_evidence(cfg, chunk_id)

    monkeypatch.setattr(brain_mod, "evidence", _gone_evidence)
    config, data, inst, tpl = _project(tmp_path)
    v1 = doc("m1", 1, "Mini m1", {"overview": OVERVIEW, "details": DETAILS})
    publish_seed(config, inst, v1, 1, docx_sha=None, extra_state={"sections": {
        "overview": {"cited_chunks": {"1": brain_mod.text_hash("evidence text 1")}, "cited_raw": {}},
        "details": {"cited_chunks": {"2": brain_mod.text_hash("evidence text 2")}, "cited_raw": {}}}})
    fp = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    assert "cited_chunk_gone" in fp["sections"]["overview"]["stale_reasons"]
    observe_task(config, "m1", inst, tpl, data["instances"], data["edges"])   # the redraft was a noop
    state = json.loads((config.out_root / "m1" / "_src" / "state.json").read_text())
    assert state["sections"]["overview"]["cited_chunks"] == {"1": None}
    fp2 = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    assert "cited_chunk_gone" in fp2["sections"]["overview"]["stale_reasons"]


def test_legacy_human_modified_origin_survives_a_later_docx_edit(tmp_path, monkeypatch):
    """Review fix round 1, Important 3: `prev_sections` on the docx-edited
    path must also go through `_restore_legacy_origins`, or a legacy
    `human_modified` origin (recorded only in a v003 lineage sidecar, never
    in the published comment) is lost the moment the next visible change
    arrives as a human docx edit to a DIFFERENT claim."""
    import scribe_lib.basedoc as basedoc_mod

    config, data, inst, tpl = _project(tmp_path)
    legacy_claim = "Senior staff prefer the legacy tool. [RAG:5] <!-- c:eeee0001 -->"
    v3 = doc("m1", 3, "Mini m1", {"overview": f"{OVERVIEW}\n\n{legacy_claim}", "details": DETAILS})
    publish_seed(config, inst, v3, 3, docx_sha="sha-of-a-different-docx")  # => treated as human-edited
    src_dir = config.out_root / "m1" / "_src"
    write_json(src_dir / "v003.lineage.json", {"nodes": [
        {"id": "claim:m1:v003:overview:eeee0001", "type": "claim", "claim_id": "eeee0001", "origin": "human_modified"},
    ]})

    # Simulate the pandoc docx->gfm recovery directly: `overview` reads back
    # unchanged (no comments — a real docx never carries them), `details`
    # holds a genuinely different claim, so this exercises the real
    # edited-recovery path (not the resave fallback).
    recovered = (
        "# Mini m1\n\n## Overview\n\n"
        'The "Alpha" plan costs $3. [RAG:1]\n\nSenior staff prefer the legacy tool. [RAG:5]'
        "\n\n## Details\n\nA genuinely different detail. [RAG:2]\n"
    )
    monkeypatch.setattr(basedoc_mod, "_docx_to_gfm", lambda path: recovered)
    stable = config.out_root / "m1" / "Mini m1.docx"
    stable.parent.mkdir(parents=True, exist_ok=True)
    stable.write_bytes(b"not a real docx, _docx_to_gfm is monkeypatched")

    result = base_task(config, "m1", inst, tpl)
    assert result["base_edited"] is True
    base_overview = split_by_section_id((config.work_dir / "m1" / "base.md").read_text(encoding="utf-8"))["overview"]
    assert "<!-- c:eeee0001 origin=human_modified -->" in base_overview
