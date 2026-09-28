import shutil
import subprocess
import tomllib

import pytest
from scribe_fixtures import fixture_brain_db, setup_mini_project, load, write_text
from scribe_lib import brain as brain_mod, onboard
from scribe_lib.config import ScribeError


def test_guard_brain_adds_exclude_to_the_root_holding_out_root(tmp_path):
    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    toml.write_text('version = 1\n\n[sources.roots.docs]\npath = "drive"\nmode = "import"\ninclude = ["**/*.pdf"]\n\n'
                    '[sources.roots.other]\npath = "elsewhere"\nmode = "import"\ninclude = ["**/*"]\n')
    r = onboard.guard_brain(toml, drive / "_ai-docs")
    assert r == {"changed": True, "roots": ["docs"], "glob": "_ai-docs/**"}
    text = toml.read_text()
    assert 'include = ["**/*.pdf"]\nexclude = ["_ai-docs/**"]' in text
    assert text.count("exclude") == 1
    assert onboard.guard_brain(toml, drive / "_ai-docs")["changed"] is False   # idempotent


def test_guard_brain_result_reparses_with_source_registry(tmp_path):
    """The brief's non-negotiable: guard_brain edits brain.toml as text, but the
    result must still be a valid brain.toml — re-parse it with stdlib tomllib
    AND with brain's own `source_registry.load_config`, proving the surgical
    line-insert never corrupts the file or drops another root's config."""
    import importlib.util
    import tomllib

    from conftest import REPO_ROOT

    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    toml.write_text('version = 1\n\n[sources.roots.docs]\npath = "drive"\nmode = "import"\ninclude = ["**/*.pdf"]\n\n'
                    '[sources.roots.other]\npath = "elsewhere"\nmode = "import"\ninclude = ["**/*"]\n')
    onboard.guard_brain(toml, drive / "_ai-docs")

    with toml.open("rb") as fh:
        raw = tomllib.load(fh)
    assert raw["sources"]["roots"]["docs"]["exclude"] == ["_ai-docs/**"]
    assert raw["sources"]["roots"]["other"]["include"] == ["**/*"]  # untouched

    sr_path = REPO_ROOT / "bundles" / "brain" / "skills" / "knowledge-pipeline" / "source_registry.py"
    spec = importlib.util.spec_from_file_location("scribe_test_source_registry", sr_path)
    source_registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_registry)
    cfg = source_registry.load_config(toml)
    assert cfg["roots"]["docs"]["exclude"] == ["_ai-docs/**"]
    assert cfg["roots"]["other"]["exclude"] == []


# ------------------------------------------- fix round 1: guard_brain conservatism --


def test_guard_brain_handles_a_multiline_include_array(tmp_path):
    """Reviewer's Critical reproduction: `include` spans several lines. The
    old implementation inserted `exclude = [...]` right after `include`'s
    FIRST line — inside the array — corrupting the file. The new one only
    ever appends a brand-new key at the END of the table."""
    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    toml.write_text(
        '[sources.roots.docs]\n'
        'path = "drive"\n'
        'mode = "import"\n'
        'include = [\n'
        '  "**/*.pdf",\n'
        ']\n'
    )
    r = onboard.guard_brain(toml, drive / "_ai-docs")
    assert r == {"changed": True, "roots": ["docs"], "glob": "_ai-docs/**"}
    text = toml.read_text()
    parsed = tomllib.loads(text)
    assert parsed["sources"]["roots"]["docs"]["include"] == ["**/*.pdf"]
    assert parsed["sources"]["roots"]["docs"]["exclude"] == ["_ai-docs/**"]


def test_guard_brain_multiline_exclude_is_left_untouched_with_manual_hint(tmp_path):
    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    original = (
        '[sources.roots.docs]\n'
        'path = "drive"\n'
        'exclude = [\n'
        '  "tmp/**",\n'
        ']\n'
    )
    toml.write_text(original)
    r = onboard.guard_brain(toml, drive / "_ai-docs")
    assert r["changed"] is False
    assert r["manual"] == '    "_ai-docs/**",'
    assert toml.read_text() == original  # byte-for-byte untouched


def test_guard_brain_exclude_with_inline_comment_is_left_untouched_with_manual_hint(tmp_path):
    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    original = '[sources.roots.docs]\npath = "drive"\nexclude = ["tmp/**"]  # scratch\n'
    toml.write_text(original)
    r = onboard.guard_brain(toml, drive / "_ai-docs")
    assert r["changed"] is False
    assert "manual" in r
    assert toml.read_text() == original


def test_guard_brain_matches_a_quoted_root_key(tmp_path):
    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    toml.write_text('[sources.roots."docs"]\npath = "drive"\ninclude = ["**/*"]\n')
    r = onboard.guard_brain(toml, drive / "_ai-docs")
    assert r == {"changed": True, "roots": ["docs"], "glob": "_ai-docs/**"}
    assert tomllib.loads(toml.read_text())["sources"]["roots"]["docs"]["exclude"] == ["_ai-docs/**"]


def test_guard_brain_matches_a_header_with_a_trailing_comment(tmp_path):
    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    toml.write_text('[sources.roots.docs]  # main drive\npath = "drive"\ninclude = ["**/*"]\n')
    r = onboard.guard_brain(toml, drive / "_ai-docs")
    assert r == {"changed": True, "roots": ["docs"], "glob": "_ai-docs/**"}
    assert tomllib.loads(toml.read_text())["sources"]["roots"]["docs"]["exclude"] == ["_ai-docs/**"]


def test_guard_brain_is_idempotent_and_reports_changed_accurately(tmp_path):
    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    toml.write_text('[sources.roots.docs]\npath = "drive"\ninclude = ["**/*"]\n')
    first = onboard.guard_brain(toml, drive / "_ai-docs")
    assert first["changed"] is True
    text_after_first = toml.read_text()
    second = onboard.guard_brain(toml, drive / "_ai-docs")
    assert second["changed"] is False
    assert toml.read_text() == text_after_first  # no bytes moved on the no-op call


def test_guard_brain_only_inserts_one_new_line_other_bytes_untouched(tmp_path):
    """`changed: True` must correspond to real bytes changing, and ONLY the
    inserted line — every other line (including a second, unmatched root)
    is untouched, byte for byte."""
    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    original = (
        'version = 1\n\n'
        '[sources.roots.docs]\n'
        'path = "drive"\n'
        'mode = "import"\n'
        'include = ["**/*.pdf"]\n\n'
        '[sources.roots.other]\n'
        'path = "elsewhere"\n'
        'mode = "import"\n'
        'include = ["**/*"]\n'
    )
    toml.write_text(original)
    onboard.guard_brain(toml, drive / "_ai-docs")
    before_lines = original.splitlines()
    after_lines = toml.read_text().splitlines()
    assert len(after_lines) == len(before_lines) + 1
    inserted = [ln for ln in after_lines if ln not in before_lines]
    assert inserted == ['exclude = ["_ai-docs/**"]']
    # every original line is still present, in order, elsewhere in the file
    other_after = [ln for ln in after_lines if ln != 'exclude = ["_ai-docs/**"]']
    assert other_after == before_lines


def test_guard_brain_out_root_equal_to_root_is_not_a_match(tmp_path):
    drive = tmp_path / "drive"; drive.mkdir()
    toml = tmp_path / "brain.toml"
    toml.write_text('[sources.roots.docs]\npath = "drive"\ninclude = ["**/*"]\n')
    r = onboard.guard_brain(toml, drive)
    assert r == {"changed": False, "roots": [], "glob": None}
    with pytest.raises(ScribeError):
        onboard.guard_brain(toml, drive, require=True)


def test_guard_brain_verifies_with_real_source_registry_when_brain_skills_given(tmp_path):
    from conftest import REPO_ROOT

    drive = tmp_path / "drive"; (drive / "_ai-docs").mkdir(parents=True)
    toml = tmp_path / "brain.toml"
    toml.write_text('version = 1\n\n[sources.roots.docs]\npath = "drive"\ninclude = ["**/*"]\n')
    brain_skills = REPO_ROOT / "bundles" / "brain" / "skills"
    r = onboard.guard_brain(toml, drive / "_ai-docs", brain_skills=brain_skills)
    assert r["changed"] is True
    assert tomllib.loads(toml.read_text())["sources"]["roots"]["docs"]["exclude"] == ["_ai-docs/**"]


# --------------------------------------------------------- fix round 1: coverage --


def test_coverage_flags_sections_without_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag:
                        [{"chunk_id": "1", "source": "a.md", "text": "widget overview"}] if "overview" in q else [])
    monkeypatch.setattr(brain_mod, "evidence", lambda cfg, cid: {"status": "ok", "text": "widget overview"})
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    out = onboard.coverage(config, proj / "tasks" / "m1.task.md")
    by = {s["section"]: s for s in out["sections"]}
    assert by["overview"]["brain_hits"] == 1 and by["overview"]["no_evidence"] is False
    assert by["details"]["no_evidence"] is True
    assert not (config.out_root / "m1" / "_src" / "state.json").exists()


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc missing")
def test_sections_from_example_uses_h2_headings(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    md = tmp_path / "ex.md"; md.write_text("# Report\n\n## Current State\n\ntext\n\n## Pain Points\n\ntext\n")
    docx = tmp_path / "ex.docx"; subprocess.run(["pandoc", str(md), "-o", str(docx)], check=True)
    assert onboard.sections_from_example(config, docx) == [
        {"id": "current-state", "title": "Current State"}, {"id": "pain-points", "title": "Pain Points"}]


# ------------------------------------- fix round 1: sections_from_example (pandoc) --


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc missing")
def test_sections_from_example_docx_with_only_h2_headings(tmp_path):
    """Reviewer's reproduction: a docx with a SINGLE heading level plus body
    text used to make the font-size heuristic mistake body text for a
    'level 2' heading. `##`-only, no body-paragraph ids should ever appear."""
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    md = tmp_path / "only_h2.md"
    md.write_text("## Current State\n\nSome body text here.\n\n## Pain Points\n\nMore body text.\n")
    docx = tmp_path / "only_h2.docx"
    subprocess.run(["pandoc", str(md), "-o", str(docx)], check=True)
    assert onboard.sections_from_example(config, docx) == [
        {"id": "current-state", "title": "Current State"},
        {"id": "pain-points", "title": "Pain Points"},
    ]


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc missing")
def test_sections_from_example_docx_with_only_h1_headings_falls_back(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    md = tmp_path / "only_h1.md"
    md.write_text("# Overview\n\nSome body text here.\n\n# Risks\n\nMore body text.\n")
    docx = tmp_path / "only_h1.docx"
    subprocess.run(["pandoc", str(md), "-o", str(docx)], check=True)
    assert onboard.sections_from_example(config, docx) == [
        {"id": "overview", "title": "Overview"},
        {"id": "risks", "title": "Risks"},
    ]


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc missing")
def test_sections_from_example_pptx_uses_slide_titles(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    md = tmp_path / "slides.md"
    md.write_text("# Intro\n\ncontent one\n\n# Details\n\ncontent two\n")
    pptx = tmp_path / "slides.pptx"
    subprocess.run(["pandoc", str(md), "-o", str(pptx)], check=True)
    assert onboard.sections_from_example(config, pptx) == [
        {"id": "intro", "title": "Intro"},
        {"id": "details", "title": "Details"},
    ]


# ------------------------------------------- fix round 1: numbers_available --


def test_numbers_available_checks_brain_list_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    write_text(
        proj / "templates" / "mini-profile.tmpl.md",
        """\
        ---
        id: mini-profile
        version: 1
        goal: "A minimal template with a numbers-lane section."
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
            - id: metrics
              title: "Metrics"
              intent: "Figures about {{name}}."
              queries: ["{{name}} metrics"]
              lanes: [numbers]
        acceptance:
          - { check: sections_present }
        ---

        Drafting guidance.
        """,
    )
    config, _ = load(proj)

    monkeypatch.setattr(brain_mod, "list_metrics", lambda cfg: [])
    out = onboard.coverage(config, proj / "tasks" / "m1.task.md")
    assert out["sections"][0]["numbers_available"] is False

    monkeypatch.setattr(brain_mod, "list_metrics", lambda cfg: [{"name": "widget_count"}])
    out2 = onboard.coverage(config, proj / "tasks" / "m1.task.md")
    assert out2["sections"][0]["numbers_available"] is True


def test_counted_acceptance_checks(tmp_path):
    from scribe_lib.accept import run_check
    body = {"overview": "A. [RAG:1] <!-- c:aaaa0001 -->\n\nB. [RAG:2] <!-- c:aaaa0002 -->"}
    assert run_check({"check": "min_claims:2"}, body)["passed"] is True
    assert run_check({"check": "min_claims:3"}, body)["passed"] is False
    assert run_check({"check": "max_words:3"}, body)["passed"] is False


# --------------------------------------------------------- fix round 1: shared retrieval --


def test_coverage_uses_fingerprints_shared_retrieve_section(monkeypatch, tmp_path):
    """`coverage` must not carry its own copy of the tag/untagged retrieval
    or a private `_raw_hits` import — it calls `fingerprint.retrieve_section`
    (and `resolve_tags`/`build_searches`), the SAME helper `fingerprint_task`
    uses, so the two retrievals can never drift apart."""
    from scribe_lib import fingerprint as fingerprint_mod
    from scribe_lib import onboard as onboard_mod

    assert not hasattr(onboard_mod, "_raw_hits")

    calls = []
    real = fingerprint_mod.retrieve_section

    def spy(config, queries, searches, raw_db, *, include_raw):
        calls.append((tuple(queries), include_raw))
        return real(config, queries, searches, raw_db, include_raw=include_raw)

    monkeypatch.setattr(fingerprint_mod, "retrieve_section", spy)
    monkeypatch.setattr(onboard_mod.fingerprint_mod, "retrieve_section", spy)
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])

    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    onboard.coverage(config, proj / "tasks" / "m1.task.md")
    assert len(calls) == 2  # one call per mini-profile section (overview, details)
    assert all(include_raw is True for _q, include_raw in calls)


# ----------------------------------------------------------- fix round 1: doctor gate --


def test_doctor_reports_raw_root_availability(tmp_path):
    """The onboard SKILL's hard gate reads `doctor`'s `raw_root` field —
    `validate` never printed a top-level `notes` field at all."""
    from scribe_lib.doctor import run_doctor  # noqa: F401  (sanity: still importable)
    import scribe

    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    from scribe_lib.config import load_config

    config = load_config(proj)
    import io
    import contextlib
    import json as json_mod

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        scribe.cmd_doctor(config)
    payload = json_mod.loads(buf.getvalue())
    assert payload["raw_root"] == {"path": str(config.raw_root), "available": True}

    shutil.rmtree(config.raw_root)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        scribe.cmd_doctor(config)
    payload2 = json_mod.loads(buf2.getvalue())
    assert payload2["raw_root"]["available"] is False
