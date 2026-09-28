import json, re
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


def test_skills_do_not_describe_a_permission_sandbox():
    text = (SCRIBE / "skills" / "run" / "SKILL.md").read_text()
    assert "rules allow only commands" not in text
