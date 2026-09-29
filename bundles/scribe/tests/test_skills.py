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
    """F2 (fix round 1, Important #1): step 4's drafting rule — a
    guess/question/proposal from a transcript is attributed or moved to
    Open questions, never restated as a finding — and is scoped to ANY
    conversational evidence, not just `[FILE:]`: a `[RAG:]` chunk from a
    transcript source is covered too, so #10/#15-style claims (cited
    `[RAG:]`) are in scope."""
    step4 = _step(RUN_SKILL.read_text(encoding="utf-8"), "### 4.", "### 5.")
    assert "keep the speaker's modality" in step4
    assert "conversational" in step4
    assert "[RAG:]" in step4 and "[FILE:]" in step4
    for token in ("asked whether", "suggested", "unconfirmed whether"):
        assert token in step4, token
    assert "never restated as a finding" in step4 or "it is never restated as a finding" in step4


def test_run_check_file_modality_loop_revises_in_place_only(tmp_path):
    """F2 (fix round 1, Important #3, controller ruling — binding): the
    revise-once loop over `check-file.json.modality` may only reword a
    flagged claim IN PLACE (same position, same tags, same evidence.json
    entry). It must never delete or move a claim in this loop — `claim_ref`
    is positional, so a delete/move would shift every later claim's index
    and the re-run would wipe them as `missing evidence quote`. The "move
    to Open questions" escape hatch from step 4's drafting rule is
    explicitly NOT offered here."""
    text = RUN_SKILL.read_text(encoding="utf-8")
    step5 = _step(text, "### 5.", "### 6.")
    assert "modality" in step5
    assert "in place" in step5
    assert "Do not delete the claim and do not move it" in step5
    assert "move it to Open questions" not in step5
    assert "or delete it" not in step5


def test_run_verifier_dispatch_gives_paths_and_scope_for_file_modality(tmp_path):
    """F2 (fix round 1, Important #1 and #4): step 6's verifier dispatch
    documents the `overstated` rejection for ANY conversational claim
    (`[FILE:]` or transcript-sourced `[RAG:]`), and passes the paths the
    verifier needs to resolve a `[FILE:]` tag's cited passage and read the
    turns that follow it: each stale section's `evidence.json`,
    `raw/manifest.json`, and the raw dir itself."""
    step6 = _step(RUN_SKILL.read_text(encoding="utf-8"), "### 6.", "### 7.")
    assert "overstated" in step6
    assert "conversational" in step6
    assert "[RAG:]" in step6
    assert "evidence.json" in step6
    assert "raw/manifest.json" in step6
    assert "raw/" in step6
    assert "turns that follow" in step6


def test_run_drafting_notes_locator_precision():
    """F3: step 4 tells the agent to cite the section/cue that actually
    holds the quoted text, and that a wrong locator gets fixed by
    check-file rather than left to the agent to catch."""
    step4 = _step(RUN_SKILL.read_text(encoding="utf-8"), "### 4.", "### 5.")
    assert "actually contains the quoted text" in step4
    assert "check-file" in step4


def test_run_check_file_documents_relocated_and_deduped_locators():
    """F3: step 5 documents that check-file corrects a `[FILE:]` tag's
    locator and removes an exact-duplicate tag, and that the agent must not
    revert either fix."""
    step5 = _step(RUN_SKILL.read_text(encoding="utf-8"), "### 5.", "### 6.")
    assert "check-file.json.relocated" in step5
    assert "check-file.json.deduped" in step5
    assert "not something to revert" in step5 or "leave" in step5


TEMPLATES = sorted((SCRIBE_ROOT / "skills" / "run" / "templates").glob("*.tmpl.md"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_template_guidance_keeps_speaker_modality(path):
    """F2 (fix round 1, Important #1): every library template's
    drafting-guidance body carries the same one-sentence rule, scoped to
    any conversational evidence (`[FILE:]` or `[RAG:]`) — matching step 4's
    SKILL.md rule rather than disagreeing with it."""
    text = path.read_text(encoding="utf-8")
    assert "speaker's modality" in text
    assert "conversational" in text
    assert "[RAG:]" in text and "[FILE:]" in text


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
