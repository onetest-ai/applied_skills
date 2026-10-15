"""Bundle conventions: factory/plugin/marketplace agree, skills follow the repo rules, every documented command
exists, and nothing project-specific ships in the bundle."""
import json
import os
import re
from pathlib import Path

import pytest
import yaml

import taska
from conftest import REPO_ROOT, TASKA_ROOT

SKILLS = sorted((TASKA_ROOT / "skills").glob("*/SKILL.md"))


def _fm(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m
    return yaml.safe_load(m.group(1))


def test_factory_lists_every_skill_dir():
    f = json.loads((TASKA_ROOT / "factory.json").read_text())
    assert sorted(f["skills"]) == sorted(p.parent.name for p in SKILLS) and f["id"] == "taska" and f["entrypoint"] == "run"


def test_marketplace_lists_taska_with_plugin_version():
    m = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    p = json.loads((TASKA_ROOT / ".claude-plugin" / "plugin.json").read_text())
    e = next(x for x in m["plugins"] if x["name"] == "taska")
    assert e["source"] == "./bundles/taska" and e["version"] == p["version"]


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_frontmatter(path):
    fm = _fm(path.read_text())
    assert fm["name"] == path.parent.name
    assert fm["description"].startswith("Use when ")
    assert "allowed-tools" not in fm


def test_documented_commands_exist():
    parser_cmds = set(re.findall(r'sp\.add_parser\("([a-z-]+)"', (TASKA_ROOT / "skills" / "run" / "taska.py").read_text()))
    docs = [p.read_text() for p in SKILLS] + [(TASKA_ROOT / "skills" / "run" / f).read_text()
                                              for f in ("EXTRACT.md", "DEPS.md", "VERIFY.md")]
    used = {c for d in docs for c in re.findall(r"\$TASKA ([a-z][a-z-]+)", d)}
    assert used and used <= parser_cmds, used - parser_cmds


def test_stdlib_only():
    reqs = [l for l in (TASKA_ROOT / "requirements.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert reqs == []
    src = "\n".join(p.read_text() for p in (TASKA_ROOT / "skills").rglob("*.py"))
    for mod in ("yaml", "requests", "fitz", "pandas", "numpy"):
        assert not re.search(rf"^\s*(import|from) {mod}\b", src, re.M), mod


def _private_terms():
    """Client names, people and systems that must never ship, read from a file OUTSIDE the repo (naming them here
    would leak them): $APPLIED_SKILLS_PRIVATE_TERMS or ~/.config/applied-skills/private-terms.txt, one term per
    line. Absent on CI and other machines: the test then checks only generic leaks."""
    f = Path(os.environ.get("APPLIED_SKILLS_PRIVATE_TERMS", Path.home() / ".config/applied-skills/private-terms.txt"))
    if not f.is_file():
        return []
    return [t.strip() for t in f.read_text().splitlines() if t.strip() and not t.startswith("#")]


def test_nothing_project_specific_ships():
    terms = _private_terms()
    private = re.compile("|".join(r"\b" + re.escape(t) + r"\b" for t in terms), re.I) if terms else None
    generic = re.compile(r"/Users/[a-z]|/private/tmp/claude|CloudStorage/OneDrive-(?!Company)"
                         r"|@(?!example\b|harbor\.example)[\w-]+\.(com|net|org)")
    files = [p for p in TASKA_ROOT.rglob("*") if p.is_file() and p.suffix in (".py", ".md", ".html", ".json", ".toml")
             and "__pycache__" not in p.parts]
    files += [REPO_ROOT / "docs" / "taska-guide.md"]
    for p in files:
        text = p.read_text(errors="replace")
        if p.resolve() != Path(__file__).resolve():  # this file spells the generic patterns out
            assert not generic.search(text), (p, generic.search(text).group(0))
        if private:
            m = private.search(text)
            assert not m, (p, m.group(0))


def test_docs_and_claude_md():
    assert (TASKA_ROOT / "README.md").is_file() and (REPO_ROOT / "docs" / "taska-guide.md").is_file()
    c = (REPO_ROOT / "CLAUDE.md").read_text()
    assert "bundles/taska" in c and "pytest bundles/taska/tests" in c
