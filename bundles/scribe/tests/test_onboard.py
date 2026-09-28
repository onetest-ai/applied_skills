import shutil
import pytest
from scribe_fixtures import fixture_brain_db, setup_mini_project, load, write_text
from scribe_lib import brain as brain_mod, onboard


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
    import subprocess
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    md = tmp_path / "ex.md"; md.write_text("# Report\n\n## Current State\n\ntext\n\n## Pain Points\n\ntext\n")
    docx = tmp_path / "ex.docx"; subprocess.run(["pandoc", str(md), "-o", str(docx)], check=True)
    assert onboard.sections_from_example(config, docx) == [
        {"id": "current-state", "title": "Current State"}, {"id": "pain-points", "title": "Pain Points"}]


def test_counted_acceptance_checks(tmp_path):
    from scribe_lib.accept import run_check
    body = {"overview": "A. [RAG:1] <!-- c:aaaa0001 -->\n\nB. [RAG:2] <!-- c:aaaa0002 -->"}
    assert run_check({"check": "min_claims:2"}, body)["passed"] is True
    assert run_check({"check": "min_claims:3"}, body)["passed"] is False
    assert run_check({"check": "max_words:3"}, body)["passed"] is False
