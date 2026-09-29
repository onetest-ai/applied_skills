"""F3 review fix round 1, Critical 1 (C1): a VTT-style `[FILE:]` locator
legitimately contains ` — ` (an em dash) — `00:17 — Speaker (cue 1) > 03:26
— Speaker (cue 2)` — so `basedoc._extract_footnotes`'s old `rest.split(" —
", 1)[0]` truncated every such tag to its first timestamp on a docx round
trip, collapsing distinct cues of the same file into duplicate tags.

Fix: `render._tags_to_footnotes` now separates a footnote's tag body from
its label with `claims.FOOTNOTE_SEP` (U+00A6 BROKEN BAR, which cannot occur
in a tag body this codebase generates); `basedoc._recover_tag_body` recovers
the tag body by (1) the longest known tag body (from the previous published
version) that is a prefix of the footnote text, else (2) splitting on the
new separator, else (3) splitting on the legacy `" — "` for a docx rendered
before this fix.
"""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from scribe_lib import brain as brain_mod
from scribe_lib.basedoc import (
    _extract_footnotes, _known_tag_bodies, _recover_tag_body, _reinsert_tags,
)
from scribe_lib.config import Config
from scribe_lib.render import render_task

pandoc_available = shutil.which("pandoc") is not None

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "skills" / "run" / "templates"

VTT_LOCATOR_1 = "00:17 — Tatiana Milova (cue 1) > 03:26 — Viachaslau Hurski (cue 31)"
VTT_LOCATOR_2 = "03:40 — Tatiana Milova (cue 32) > 05:12 — Viachaslau Hurski (cue 40)"


def _config(tmp_path: Path) -> Config:
    return Config(
        project_dir=tmp_path,
        brain_db=tmp_path / "nope.sqlite",
        brain_catalog=tmp_path / "nope.json",
        brain_skills=tmp_path,
        brain_mcp_dir=tmp_path,
        out_root=tmp_path / "out",
        tasks_dir=tmp_path / "tasks",
        templates_dirs=[TEMPLATES_DIR],
        raw_root=tmp_path / "raw-replay",
        work_dir=tmp_path / "work",
        top_k=8,
        now="2026-01-15",
    )


def _fake_evidence(cfg, chunk_id):
    return {"chunk_id": str(chunk_id), "status": "ok", "source": "doc.md", "section": "Intro", "text": "hi"}


# ----------------------------------------------------- _recover_tag_body --

def test_recover_tag_body_prefers_the_longest_known_body_over_any_split():
    """Case (1): even against a LEGACY-format footnote (old `" — "`
    separator) whose own locator contains ` — `, matching against the
    previous version's known tag bodies recovers the tag body intact —
    this is the actual fix for the audit's `#00:17` defect."""
    body = f"FILE:transcript.vtt#{VTT_LOCATOR_1}"
    footnote_text = f"{body} — transcript.vtt, {VTT_LOCATOR_1}"
    known = frozenset({body})
    assert _recover_tag_body(footnote_text, known) == body
    # Without the known-body set, the legacy split truncates it (case 3,
    # the pre-fix behaviour) — pinning that case (3) is still the fallback
    # it always was, not that it's now correct on its own.
    assert _recover_tag_body(footnote_text, frozenset()) == f"FILE:transcript.vtt#00:17"


def test_recover_tag_body_splits_on_the_current_separator_when_no_known_body_matches():
    """Case (2): a current-format footnote (¦), no matching known body —
    the em-dash-safe split."""
    body = f"FILE:transcript.vtt#{VTT_LOCATOR_1}"
    footnote_text = f"{body} ¦ transcript.vtt, {VTT_LOCATOR_1}"
    assert _recover_tag_body(footnote_text, frozenset()) == body


def test_recover_tag_body_falls_back_to_legacy_em_dash_split_for_a_plain_tag():
    """Case (3): an older-format footnote (no ¦, no matching known body,
    and no em dash inside the tag body itself) still parses exactly as
    before this fix."""
    assert _recover_tag_body("RAG:1 — doc, Intro", frozenset()) == "RAG:1"


def test_known_tag_bodies_collects_every_tag_across_every_section():
    prev = (
        "<!-- scribe: task=t1 version=1 built_at=2026-01-15T00:00:00 template=mini@1 base=none -->\n"
        "# Demo Title\n\n## Overview {#overview}\n\n"
        f"A claim. [FILE:transcript.vtt#{VTT_LOCATOR_1}] <!-- c:aaaa0001 -->\n\n"
        "## Details {#details}\n\nAnother. [RAG:9] <!-- c:bbbb0001 -->\n"
    )
    known = _known_tag_bodies(prev)
    assert f"FILE:transcript.vtt#{VTT_LOCATOR_1}" in known
    assert "RAG:9" in known


# ------------------------------------------------- render -> docx -> base --

@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
def test_vtt_locator_with_em_dash_survives_render_to_docx_and_back(tmp_path, monkeypatch):
    """End to end (minus the docx-editing step, which test_human_edits.py
    already covers for plain tags): render a claim whose `[FILE:]` locator
    contains ` — `, convert the resulting docx back to gfm, and recover the
    tag from its footnote — intact, not truncated at the first timestamp."""
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)
    text = textwrap.dedent(
        f"""\
        <!-- scribe: task=t1 version=1 built_at=2026-01-15T00:00:00 template=mini@1 base=none -->
        # Demo Title

        ## Overview {{#overview}}

        First cue. [FILE:transcript.vtt#{VTT_LOCATOR_1}] <!-- c:aaaa0001 -->

        - Second cue, same file. [FILE:transcript.vtt#{VTT_LOCATOR_2}] <!-- c:bbbb0001 -->

        ## Changes in this version {{#changes}}

        - initial
        """
    )
    work_dir = config.work_dir / "t1"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "next.md").write_text(text, encoding="utf-8")
    instances = {"t1": {"title": "Demo Title"}}

    import scribe_lib.render as render_mod
    monkeypatch.setattr(render_mod, "_run_mermaid", lambda mmd, png: (False, "skip diagram"))
    render_task(config, "t1", instances["t1"], instances)

    docx_path = work_dir / "render" / "Demo Title.docx"
    assert docx_path.is_file()

    from scribe_lib.basedoc import _docx_to_gfm
    gfm = _docx_to_gfm(docx_path)
    body, footnote_defs = _extract_footnotes(gfm)
    recovered = _reinsert_tags(body, footnote_defs)

    tag1 = f"[FILE:transcript.vtt#{VTT_LOCATOR_1}]"
    tag2 = f"[FILE:transcript.vtt#{VTT_LOCATOR_2}]"
    assert tag1 in recovered
    assert tag2 in recovered
    assert tag1 != tag2  # the two cues never collapse into one locator
