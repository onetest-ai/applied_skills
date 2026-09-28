"""Skill-structure tests for the scribe bundle.

- Both SKILL.md files inline kb's Brain contract byte-for-byte (drift test,
  same rule as bundles/kb: a SKILL.md cannot depend on ../_shared/ for rules).
- Every description opens with `Use when `; no SKILL.md declares allowed-tools.
- Every `scribe.py <subcommand>` a SKILL.md documents exists in the CLI.
- No SKILL.md hardcodes an MCP server name (Brains are found by tool surface).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import scribe
from conftest import REPO_ROOT

SCRIBE_ROOT = Path(__file__).resolve().parents[1]
DOCTRINE = REPO_ROOT / "bundles" / "kb" / "skills" / "_shared" / "doctrine.md"
SKILLS = sorted((SCRIBE_ROOT / "skills").glob("*/SKILL.md"))
START, END = "<!-- BRAIN-CONTRACT:START -->", "<!-- BRAIN-CONTRACT:END -->"


def _frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "SKILL.md has no YAML frontmatter"
    return yaml.safe_load(m.group(1)) or {}


def _contract(text: str) -> str:
    assert text.count(START) == 1 and text.count(END) == 1, "contract markers missing or duplicated"
    return text[text.index(START) : text.index(END) + len(END)]


def test_both_skills_exist():
    assert {p.parent.name for p in SKILLS} == {"run", "onboard", "tasks"}


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_brain_contract_is_inlined_verbatim(path):
    canonical = _contract(DOCTRINE.read_text(encoding="utf-8"))
    assert _contract(path.read_text(encoding="utf-8")) == canonical


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_description_opens_with_use_when(path):
    desc = _frontmatter(path.read_text(encoding="utf-8")).get("description", "")
    assert desc.startswith("Use when "), desc[:40]


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_no_allowed_tools(path):
    fm = _frontmatter(path.read_text(encoding="utf-8"))
    assert "allowed-tools" not in fm and "allowed_tools" not in fm


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_documented_subcommands_exist(path):
    parser = scribe.build_parser()
    sub_action = next(a for a in parser._actions if a.dest == "command")
    known = set(sub_action.choices)
    documented = set(re.findall(r"scribe\.py (?:--project \. )?([a-z][a-z-]+)", path.read_text(encoding="utf-8")))
    documented.discard("subcommand")
    assert documented, "no scribe.py commands documented"
    assert documented <= known, sorted(documented - known)


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_no_hardcoded_mcp_server_name(path):
    text = path.read_text(encoding="utf-8")
    assert "mcp__" not in text


RUN_SKILL = SCRIBE_ROOT / "skills" / "run" / "SKILL.md"


def _step(text: str, heading: str, next_heading: str) -> str:
    return text[text.index(heading) : text.index(next_heading)]


def test_run_step7_order_render_before_accept_before_publish():
    """accept must see tonight's render.json (diagrams_render), and publish only runs after accept."""
    step7 = _step(RUN_SKILL.read_text(encoding="utf-8"), "### 7.", "### 8.")
    order = [step7.index(f"`scribe.py {cmd} <id>`") for cmd in ("merge", "render", "accept", "publish", "lineage")]
    order.append(step7.index("`scribe.py index`"))
    assert order == sorted(order)
    assert "later commands in this list are not run" in step7


def test_run_verifier_matches_by_content_not_position():
    step6 = _step(RUN_SKILL.read_text(encoding="utf-8"), "### 6.", "### 7.")
    assert "CLAIM | <section> | <c:xxxxxxxx id" in step6
    assert "never by" in step6 and "position" in step6
    assert "ignore every" in step6  # non-CLAIM lines (summaries) are ignored
    assert "after any leading `- `" in step6


def test_run_drafting_forbids_uncited_structure():
    step4 = _step(RUN_SKILL.read_text(encoding="utf-8"), "### 4.", "### 5.")
    for token in ("No sub-headings", "no tables", "no lead-in", "blank line between a"):
        assert token in step4, token
