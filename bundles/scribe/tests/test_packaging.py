import json, re
from pathlib import Path

from scribe_fixtures import REPO_ROOT

SCRIBE = REPO_ROOT / "bundles" / "scribe"


def _req_lines(path):
    return {l.split("#")[0].strip() for l in path.read_text().splitlines() if l.split("#")[0].strip()}


def test_requirements_cover_brain_requirements():
    brain = _req_lines(REPO_ROOT / "bundles" / "brain" / "requirements.txt")
    scribe = _req_lines(SCRIBE / "requirements.txt")
    assert brain <= scribe, sorted(brain - scribe)


def test_no_cross_plugin_relative_paths():
    for p in list((SCRIBE / "skills").rglob("*.md")) + list((SCRIBE / "skills").rglob("*.py")):
        assert "../../../brain" not in p.read_text(), p


def test_factory_lists_every_skill_dir():
    factory = json.loads((SCRIBE / "factory.json").read_text())
    dirs = sorted(d.name for d in (SCRIBE / "skills").iterdir() if (d / "SKILL.md").is_file())
    assert sorted(factory["skills"]) == dirs
    assert factory["id"] == "scribe" and factory["entrypoint"] == "run"


def test_marketplace_lists_scribe_with_plugin_version():
    market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((SCRIBE / ".claude-plugin" / "plugin.json").read_text())
    entry = next(p for p in market["plugins"] if p["name"] == "scribe")
    assert entry["source"] == "./bundles/scribe" and entry["version"] == plugin["version"]


def test_mermaid_cli_is_pinned():
    from scribe_lib import render
    assert render.MERMAID_CLI == "@mermaid-js/mermaid-cli@12.0.0"
    src = (SCRIBE / "skills" / "run" / "scribe_lib" / "render.py").read_text() + \
          (SCRIBE / "skills" / "run" / "scribe_lib" / "doctor.py").read_text()
    assert not re.search(r'"@mermaid-js/mermaid-cli"', src)


def test_docs_exist_and_versions_agree():
    assert (SCRIBE / "README.md").is_file() and (REPO_ROOT / "docs" / "scribe-guide.md").is_file()
    market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    brain = json.loads((REPO_ROOT / "bundles" / "brain" / ".claude-plugin" / "plugin.json").read_text())
    assert next(p for p in market["plugins"] if p["name"] == "brain")["version"] == brain["version"] == "0.11.0"
    assert market["metadata"]["version"] == "0.11.0"
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text()
    assert "bundles/scribe" in claude_md and "pytest bundles/scribe/tests" in claude_md


def test_skills_do_not_describe_a_permission_sandbox():
    text = (SCRIBE / "skills" / "run" / "SKILL.md").read_text()
    assert "rules allow only commands" not in text


def test_scribe_paths_in_skill_md_resolve_to_real_files():
    """Every SKILL.md defining `$SCRIBE` names a scribe.py path (and a
    requirements.txt fallback path) relative to `<skill-dir>` (that SKILL.md's own
    directory). Resolve both and assert the files they name actually exist — the
    onboard/run regression this guards against: onboard's `<skill-dir>` is
    `skills/onboard`, which has no `scribe.py` of its own (it lives in the sibling
    `run` skill), so a `$SCRIBE` macro copied verbatim from run/SKILL.md silently
    points at a nonexistent file.
    """
    scribe_re = re.compile(r'\$SCRIBE.*?means.*?"\$PY"\s+([^\s`]+?)\s+--project')
    req_re = re.compile(r'--with-requirements\s+([^\s`]+?)\s+python\s+([^\s`]+?)\s+--project')

    checked = 0
    for skill_dir in (SCRIBE / "skills").iterdir():
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            continue
        text = md.read_text()
        if "$SCRIBE" not in text:
            continue

        m = scribe_re.search(text)
        assert m, f"{md}: defines $SCRIBE but no resolvable scribe.py path found"
        script_path = m.group(1).replace("<skill-dir>", str(skill_dir))
        assert Path(script_path).resolve().is_file(), f"{md}: $SCRIBE script {script_path} does not exist"

        m2 = req_re.search(text)
        assert m2, f"{md}: no `uv run` fallback with a requirements path found"
        req_path = m2.group(1).replace("<skill-dir>", str(skill_dir))
        fallback_script = m2.group(2).replace("<skill-dir>", str(skill_dir))
        assert Path(req_path).resolve().is_file(), f"{md}: fallback requirements {req_path} does not exist"
        assert Path(fallback_script).resolve().is_file(), f"{md}: fallback script {fallback_script} does not exist"
        checked += 1

    assert checked >= 2
