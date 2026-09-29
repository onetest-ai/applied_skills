"""Smoke tests for render (Markdown -> docx + pdf + Mermaid) and doctor.

`scribe_lib.brain.evidence` is monkeypatched with a tiny fake, as in
test_prepare.py/test_merge_publish.py — no real Brain needed. Tests that
shell out to `pandoc`, `soffice`, or `npx @mermaid-js/mermaid-cli` skip (with
a reason) when the tool isn't on PATH, matching the house pattern in
`bundles/brain/tests/conftest.py` / `test_prepare.py`'s
`pandoc_available`/`soffice_available`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import zipfile
from pathlib import Path

import pytest

from scribe_lib import brain as brain_mod
from scribe_lib import doctor as doctor_mod
from scribe_lib.basedoc import _docx_to_gfm, _extract_footnotes, _reinsert_tags, split_sections
from scribe_lib.config import Config
from scribe_lib.render import _footnote_label, _tags_to_footnotes, render_task

pandoc_available = shutil.which("pandoc") is not None
soffice_available = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None
npx_available = shutil.which("npx") is not None


def _mermaid_available() -> bool:
    if not npx_available:
        return False
    try:
        proc = subprocess.run(
            ["npx", "-y", "@mermaid-js/mermaid-cli", "--version"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0


mermaid_available = _mermaid_available()

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "skills" / "run" / "templates"


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
    return {
        "chunk_id": str(chunk_id),
        "status": "ok",
        "source": "Docs__CX Compendium.md",
        "section": "p.19",
        "text": "hi",
    }


DOC_MD = textwrap.dedent(
    """\
    <!-- scribe: task=t1 version=1 built_at=2026-01-15T00:00:00 template=mini@1 base=none -->
    # Demo Title

    ## Overview {#overview}

    A claim about things. [RAG:3508352047104733571] <!-- c:1a2b3c4d -->

    - A file claim. [FILE:notes/meeting.vtt#L120] <!-- c:5e6f7a8b -->

    ## Context diagram {#context}

    ```mermaid
    flowchart LR
      A[Client] --> B[Service]
    ```

    ## Changes in this version {#changes}

    - initial
    """
)


def _write_next(config: Config, task_id: str, text: str = DOC_MD) -> Path:
    work_dir = config.work_dir / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "next.md"
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------- footnotes --

def test_tag_to_footnote_body_starts_with_the_tag_verbatim(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)
    instances = {"t1": {"title": "Demo Title"}}

    line = "A claim. [RAG:3508352047104733571] and [FILE:notes/meeting.vtt#L120] both cited."
    out = _tags_to_footnotes(line, config, instances)

    assert "^[RAG:3508352047104733571 ¦ CX Compendium, p.19]" in out
    assert "^[FILE:notes/meeting.vtt#L120 ¦ meeting.vtt, L120]" in out
    # every footnote body starts with exactly "<KIND>:<value>", no brackets
    for body in ("RAG:3508352047104733571", "FILE:notes/meeting.vtt#L120"):
        assert f"^[{body} ¦ " in out


def test_multiple_tags_on_one_claim_become_multiple_footnotes(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)
    instances = {"t1": {"title": "Demo Title"}}
    line = "Cited twice. [RAG:1] [MART:adjustments@division]"
    out = _tags_to_footnotes(line, config, instances)
    assert out.count("^[") == 2
    assert "^[MART:adjustments@division ¦ adjustments@division]" in out


def test_tag_body_containing_the_footnote_separator_still_renders(tmp_path, monkeypatch):
    """Minor (F3 re-review): a `[FILE:]` locator is a raw document's heading
    breadcrumb, so a source heading containing `claims.FOOTNOTE_SEP` (`¦`)
    must not raise and block that task's publish every night — render
    writes the footnote anyway (base's known-prefix recovery is exact for
    such a tag; see test_footnote_locator_recovery.py)."""
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)
    instances = {"t1": {"title": "Demo Title"}}
    line = "Bad tag. [GRAPH:contains ¦ broken bar]"
    out = _tags_to_footnotes(line, config, instances)
    assert "^[GRAPH:contains ¦ broken bar ¦ " in out


def test_task_tag_label_uses_upstream_title_and_claim_id():
    instances = {"t1": {"title": "Upstream Title"}}
    label = _footnote_label(None, instances, "TASK", "t1#c:abcd1234")
    assert label == "Upstream Title c:abcd1234"


# -------------------------------------------------------------- mermaid ok --

@pytest.mark.skipif(not mermaid_available, reason="npx @mermaid-js/mermaid-cli not available")
def test_mermaid_diagram_renders_and_replaces_fence(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)
    _write_next(config, "t1")
    instances = {"t1": {"title": "Demo Title"}}

    result = render_task(config, "t1", instances["t1"], instances)

    assert result["diagrams"] == [{"diagram": "context-1", "ok": True}]
    diagrams_dir = config.work_dir / "t1" / "render" / "diagrams"
    assert (diagrams_dir / "context-1.mmd").is_file()
    assert (diagrams_dir / "context-1.png").is_file()
    rendered = (config.work_dir / "t1" / "render" / "_render.md").read_text(encoding="utf-8")
    assert "![context diagram](diagrams/context-1.png)" in rendered
    assert "```mermaid" not in rendered


# --------------------------------------------------------- mermaid failure --

def test_mermaid_failure_keeps_the_code_block(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)
    _write_next(config, "t1")
    instances = {"t1": {"title": "Demo Title"}}

    import scribe_lib.render as render_mod

    def _boom(mmd_path, png_path):
        return False, "mmdc: chrome not found"

    monkeypatch.setattr(render_mod, "_run_mermaid", _boom)
    # avoid pandoc/soffice dependency for this test: point --md at a doc, then
    # only inspect the diagram bookkeeping + that the fence survived, not the
    # docx/pdf outcome.
    result = render_task(config, "t1", instances["t1"], instances)

    assert result["diagrams"] == [
        {"diagram": "context-1", "ok": False, "error": "mmdc: chrome not found"}
    ]
    assert any("context-1" in e for e in result["errors"])
    rendered = (config.work_dir / "t1" / "render" / "_render.md").read_text(encoding="utf-8")
    assert "```mermaid" in rendered
    assert "flowchart LR" in rendered
    assert "![context diagram]" not in rendered


# ------------------------------------------------------------ full render --

@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
@pytest.mark.skipif(not soffice_available, reason="soffice/libreoffice not installed")
def test_render_writes_docx_pdf_and_render_json(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)

    import scribe_lib.render as render_mod

    monkeypatch.setattr(render_mod, "_run_mermaid", lambda mmd, png: (False, "skip diagram in this test"))
    _write_next(config, "t1")
    instances = {"t1": {"title": "Demo Title"}}

    result = render_task(config, "t1", instances["t1"], instances)

    render_dir = config.work_dir / "t1" / "render"
    assert result["ok"] is True
    assert (render_dir / "Demo Title.docx").is_file()
    assert (render_dir / "Demo Title.pdf").is_file()
    on_disk = json.loads((render_dir / "render.json").read_text(encoding="utf-8"))
    assert on_disk == result


@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
@pytest.mark.skipif(not soffice_available, reason="soffice/libreoffice not installed")
def test_rendered_pdf_carries_scribe_marker(tmp_path, monkeypatch):
    import pymupdf

    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)

    import scribe_lib.render as render_mod

    monkeypatch.setattr(render_mod, "_run_mermaid", lambda mmd, png: (False, "skip diagram in this test"))
    _write_next(config, "t1")
    instances = {"t1": {"title": "Demo Title"}}

    result = render_task(config, "t1", instances["t1"], instances)

    pdf_path = config.work_dir / "t1" / "render" / result["pdf"]
    with pymupdf.open(pdf_path) as d:
        assert "scribe-task=t1" in d.metadata["keywords"]


@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
@pytest.mark.skipif(not soffice_available, reason="soffice/libreoffice not installed")
def test_pdf_stamp_failure_fails_the_render(tmp_path, monkeypatch):
    """The loop guard depends on every published pdf carrying scribe-task=<id>. A
    stamp failure must not leave an unmarked pdf behind as if the render succeeded —
    accept() only reads `ok`, never `errors`, so a silently-unmarked pdf would be
    publishable and would re-enter the corpus the moment it's copied out of an
    excluded dir."""
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)

    import scribe_lib.render as render_mod

    monkeypatch.setattr(render_mod, "_run_mermaid", lambda mmd, png: (False, "skip diagram in this test"))

    def _boom(pdf_path, task_id):
        raise RuntimeError("stamp exploded")

    monkeypatch.setattr(render_mod, "_stamp_pdf", _boom)
    _write_next(config, "t1")
    instances = {"t1": {"title": "Demo Title"}}

    result = render_task(config, "t1", instances["t1"], instances)

    assert result["ok"] is False
    assert result["pdf"] is None
    assert any("stamp" in e for e in result["errors"])
    pdf_path = config.work_dir / "t1" / "render" / "Demo Title.pdf"
    assert not pdf_path.is_file()


@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
def test_custom_property_lands_in_docx(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)

    import scribe_lib.render as render_mod

    monkeypatch.setattr(render_mod, "_run_mermaid", lambda mmd, png: (False, "skip diagram"))
    _write_next(config, "t1")
    instances = {"t1": {"title": "Demo Title"}}
    render_task(config, "t1", instances["t1"], instances)

    docx_path = config.work_dir / "t1" / "render" / "Demo Title.docx"
    assert docx_path.is_file()
    with zipfile.ZipFile(docx_path) as z:
        custom_xml = z.read("docProps/custom.xml").decode("utf-8")
    assert 'name="scribe-task"' in custom_xml
    assert "<vt:lpwstr>t1</vt:lpwstr>" in custom_xml


@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
def test_docx_keeps_straight_punctuation_for_a_straight_source(tmp_path, monkeypatch):
    """Fix A1's render side: pandoc's Markdown reader has smart typography ON
    by default, which silently turns `Bain's`/quotes/`--`/`...` into curly
    equivalents in the published docx. render now reads with `markdown-smart`
    (smart OFF) so the docx keeps exactly the source's punctuation."""
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)

    import scribe_lib.render as render_mod

    monkeypatch.setattr(render_mod, "_run_mermaid", lambda mmd, png: (False, "skip diagram in this test"))
    text = textwrap.dedent(
        """\
        <!-- scribe: task=t1 version=1 built_at=2026-01-15T00:00:00 template=mini@1 base=none -->
        # Demo Title

        ## Overview {#overview}

        Bain's plan uses "quoted" language, a -- b growth, and rollout continues... slowly. [RAG:3508352047104733571] <!-- c:1a2b3c4d -->

        ## Changes in this version {#changes}

        - initial
        """
    )
    _write_next(config, "t1", text)
    instances = {"t1": {"title": "Demo Title"}}

    render_task(config, "t1", instances["t1"], instances)

    docx_path = config.work_dir / "t1" / "render" / "Demo Title.docx"
    assert docx_path.is_file()
    plain = subprocess.run(["pandoc", "-t", "plain", str(docx_path)], capture_output=True, text=True).stdout
    claim = next(" ".join(block.split()) for block in plain.split("\n\n") if block.startswith("Bain's"))
    assert claim == (
        'Bain\'s plan uses "quoted" language, a -- b growth, and rollout '
        "continues... slowly. [1]"
    )
    # The em dash in the footnote LABEL (" — CX Compendium, p.19") is a
    # literal character scribe writes itself (render._footnote_label),
    # never claim text run through pandoc's reader — out of scope here.
    for curly in ("’", "‘", "“", "”", "–", "…"):
        assert curly not in claim


# --------------------------------------------------------------- round trip --

@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
def test_round_trip_render_to_docx_recovers_tags_and_section_titles(tmp_path, monkeypatch):
    """H5's mechanism: render's footnotes must be exactly what basedoc's
    docx-recovery parser expects, and section headings must survive
    by TITLE (docx bookmarks don't carry `{#id}`)."""
    config = _config(tmp_path)
    monkeypatch.setattr(brain_mod, "evidence", _fake_evidence)

    import scribe_lib.render as render_mod

    monkeypatch.setattr(render_mod, "_run_mermaid", lambda mmd, png: (False, "skip diagram"))
    _write_next(config, "t1")
    instances = {"t1": {"title": "Demo Title"}}
    render_task(config, "t1", instances["t1"], instances)

    docx_path = config.work_dir / "t1" / "render" / "Demo Title.docx"
    gfm = _docx_to_gfm(docx_path)
    body, footnote_defs = _extract_footnotes(gfm)
    body = _reinsert_tags(body, footnote_defs)

    assert "[RAG:3508352047104733571]" in body
    assert "[FILE:notes/meeting.vtt#L120]" in body

    titles = [title for level, title, _ in split_sections(body) if level == 2]
    assert "Overview" in titles
    assert "Context diagram" in titles
    assert "Changes in this version" in titles


# -------------------------------------------------------------------- doctor --

def test_doctor_reports_all_three_tools():
    result = doctor_mod.run_doctor()
    tool_names = {c["tool"] for c in result["checks"]}
    assert tool_names == {"pandoc", "soffice", "mermaid"}
    assert result["all_found"] == all(c["found"] for c in result["checks"])


def test_doctor_fails_when_a_tool_is_missing(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda tool: None)
    result = doctor_mod.run_doctor()
    assert result["status"] == "error"
    assert result["all_found"] is False
    assert all(not c["found"] for c in result["checks"])
    assert all(c.get("hint") for c in result["checks"])
