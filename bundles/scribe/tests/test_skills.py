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
    assert {p.parent.name for p in SKILLS} == {"run", "onboard", "tasks", "review"}


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


def test_run_drafting_keeps_speaker_modality_from_transcripts():
    """F2: step 4's drafting rule — a guess/question/proposal from a
    transcript is attributed or moved to Open questions, never restated as
    a finding."""
    step4 = _step(RUN_SKILL.read_text(encoding="utf-8"), "### 4.", "### 5.")
    assert "keep the speaker's modality" in step4
    for token in ("asked whether", "suggested", "unconfirmed whether"):
        assert token in step4, token
    assert "never restated as a finding" in step4 or "it is never restated as a finding" in step4


def test_run_check_file_and_verifier_cover_modality(tmp_path):
    """F2: step 5 documents the revise-once loop over `check-file.json`'s
    `modality` entries; step 6's verifier dispatch documents the
    `overstated` rejection for a transcript claim that overstates a
    question/guess/proposal as fact."""
    text = RUN_SKILL.read_text(encoding="utf-8")
    step5 = _step(text, "### 5.", "### 6.")
    assert "modality" in step5

    step6 = _step(text, "### 6.", "### 7.")
    assert "overstated" in step6


TEMPLATES = sorted((SCRIBE_ROOT / "skills" / "run" / "templates").glob("*.tmpl.md"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_template_guidance_keeps_speaker_modality(path):
    """F2: every library template's drafting-guidance body carries the same
    one-sentence rule about not restating a transcript guess/question as a
    finding (kept consistent across templates)."""
    text = path.read_text(encoding="utf-8")
    assert "speaker's modality" in text


def test_every_documented_subcommand_and_flag_exists():
    import subprocess
    import sys

    from scribe_fixtures import REPO_ROOT as _REPO_ROOT

    skills_dir = _REPO_ROOT / "bundles" / "scribe" / "skills"
    scribe_py = skills_dir / "run" / "scribe.py"
    help_top = subprocess.run([sys.executable, str(scribe_py), "--help"], capture_output=True, text=True).stdout
    for md in skills_dir.glob("*/SKILL.md"):
        for sub, flags in re.findall(r'\$SCRIBE ([a-z-]+)((?: [^\n`]*)?)', md.read_text()):
            assert sub in help_top, f"{md.parent.name}: unknown subcommand {sub}"
            sub_help = subprocess.run(
                [sys.executable, str(scribe_py), sub, "--help"], capture_output=True, text=True
            ).stdout
            for flag in re.findall(r"(--[a-z-]+)", flags):
                assert flag in sub_help, f"{md.parent.name}: {sub} has no {flag}"
