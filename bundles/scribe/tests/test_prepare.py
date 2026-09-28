"""Smoke tests for Brain access, gather-raw, fingerprint, base, and prepare.

`scribe_lib.brain.search`/`evidence` are monkeypatched with a tiny in-memory
fake store for every test — a real Brain (sqlite-vec + fastembed) is heavy
and these commands' contract only needs those two call shapes. `brain_skills`
points at the REAL `bundles/brain/skills` (read-only) so gather-raw can
import the real `parse_corpus.py` / `chunking.py`; `brain_mcp_dir` stays a
dummy path since nothing here imports `semantic_core` directly.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import textwrap
from pathlib import Path

import pytest

import scribe
from conftest import REPO_ROOT, TEMPLATES_DIR
from scribe_lib import brain as brain_mod
from scribe_lib import parsing as parsing_mod
from scribe_lib.basedoc import base_task
from scribe_lib.config import load_config, validate_all
from scribe_lib.fingerprint import _raw_hits, fingerprint_task
from scribe_lib.pack import prepare_task
from scribe_lib.raw import gather_raw_task

BRAIN_SKILLS = REPO_ROOT / "bundles" / "brain" / "skills"
DUMMY_ABS = "/nonexistent/scribe-poc-t2-tests"

pandoc_available = shutil.which("pandoc") is not None
soffice_available = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _fixture_brain_db(path: Path) -> Path:
    """A minimal sqlite with just `synced_files`, enough for compute_brain_delta."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE synced_files(doc_id TEXT, sha TEXT, bytes INT, mtime TEXT, updated_at TEXT, source_id TEXT)")
    con.commit()
    con.close()
    return path


def _make_project(tmp_path: Path, *, brain_db: Path | None = None) -> Path:
    proj = tmp_path / "proj"
    (proj / "tasks").mkdir(parents=True)
    (proj / "raw-replay").mkdir(parents=True)
    brain_db_path = brain_db if brain_db is not None else Path(f"{DUMMY_ABS}/knowledge.sqlite")
    _write(
        proj / "scribe.toml",
        f"""\
        [project]
        brain_db      = "{brain_db_path.as_posix()}"
        brain_catalog = "{DUMMY_ABS}/metrics.json"
        brain_skills  = "{BRAIN_SKILLS.as_posix()}"
        brain_mcp_dir = "{DUMMY_ABS}/mcp"
        out_root      = "out"
        tasks_dir     = "tasks"
        templates_dir = "{TEMPLATES_DIR.as_posix()}"
        raw_root      = "raw-replay"
        work_dir      = "work"
        [run]
        top_k = 8
        """,
    )
    return proj


def _domain_task(proj: Path, *, aliases=None, exclude=None, tags=None) -> None:
    aliases = aliases if aliases is not None else ["alpha", "beta"]
    exclude = exclude or []
    tags = ["Domain Tag A"] if tags is None else tags
    _write(
        proj / "tasks" / "t1.task.md",
        f"""\
        ---
        template: domain-profile@1
        id: t1
        title: "T1 Domain Profile"
        params:
          name: "Domain A"
          tags: {json.dumps(tags)}
          aliases: {json.dumps(aliases)}
        inputs:
          raw:
            exclude: {json.dumps(exclude)}
        audience: "engagement team"
        cadence: on-brain-update
        publish: auto
        out: "t1"
        ---
        Drafting notes for t1.
        """,
    )


def _load(proj: Path):
    config = load_config(proj)
    data = validate_all(config)
    return config, data


# ------------------------------------------------------------------ gather-raw --

def test_gather_raw_honours_exclude_and_alias_match(tmp_path):
    proj = _make_project(tmp_path)
    _domain_task(proj, aliases=["alpha"], exclude=["**/Internal Notes/**"])
    _write(proj / "raw-replay" / "Alpha" / "notes.txt", "Alpha overview text here.")
    _write(proj / "raw-replay" / "Internal Notes" / "daily.txt", "Alpha daily sync notes.")
    _write(proj / "raw-replay" / "Other" / "unrelated.txt", "Nothing relevant here.")

    config, data = _load(proj)
    inst = data["instances"]["t1"]
    template = data["templates"]["domain-profile"]
    from scribe_lib.config import resolve_instance_inputs

    raw_inputs = resolve_instance_inputs(inst, template)["raw"]
    result = gather_raw_task(config, "t1", inst, raw_inputs)

    paths = {m["path"] for m in result["files"]}
    assert paths == {"Alpha/notes.txt"}  # excluded dir + non-matching alias both dropped
    assert result["counts"]["ok"] == 1

    manifest = json.loads((config.work_dir / "t1" / "raw" / "manifest.json").read_text())
    assert manifest[0]["status"] == "ok"
    assert (config.work_dir / "t1" / "raw" / "Alpha" / "notes.txt.md").is_file()


def test_gather_raw_builds_fts_index_and_handles_vtt(tmp_path):
    proj = _make_project(tmp_path)
    _domain_task(proj, aliases=[], exclude=[])
    _write(
        proj / "raw-replay" / "call.vtt",
        """\
        WEBVTT

        00:00:00.000 --> 00:00:02.000
        Alice: Let's talk about alpha adjustments today.
        """,
    )
    config, data = _load(proj)
    inst = data["instances"]["t1"]
    template = data["templates"]["domain-profile"]
    from scribe_lib.config import resolve_instance_inputs

    raw_inputs = resolve_instance_inputs(inst, template)["raw"]
    result = gather_raw_task(config, "t1", inst, raw_inputs)
    assert result["counts"]["ok"] == 1
    assert result["counts"]["error"] == 0

    db = config.work_dir / "t1" / "raw" / "raw.sqlite"
    assert db.is_file()
    con = sqlite3.connect(db)
    rows = con.execute("SELECT path, locator, text FROM raw_fts").fetchall()
    con.close()
    assert rows, "expected at least one FTS row for the parsed .vtt"
    assert any("alpha" in (r[2] or "").lower() for r in rows)


@pytest.mark.skipif(not soffice_available, reason="soffice/libreoffice not installed")
def test_gather_raw_handles_docx(tmp_path):
    """Format coverage (task-2-review.md item 2): `.docx` must both MATCH
    (select_raw_files' text check, now parse-based) and PARSE through
    gather-raw — `.docx` needs soffice (LibreOffice) via parse_office_pymupdf."""
    proj = _make_project(tmp_path)
    _domain_task(proj, aliases=["alpha"], exclude=[])
    src_md = tmp_path / "src.md"
    src_md.write_text("# Notes\n\nAlpha adjustment discussion for the workshop.\n", encoding="utf-8")
    docx_path = proj / "raw-replay" / "notes.docx"
    subprocess.run(["pandoc", "-f", "markdown", "-t", "docx", "-o", str(docx_path), str(src_md)], check=True)

    config, data = _load(proj)
    inst = data["instances"]["t1"]
    template = data["templates"]["domain-profile"]
    from scribe_lib.config import resolve_instance_inputs

    raw_inputs = resolve_instance_inputs(inst, template)["raw"]
    result = gather_raw_task(config, "t1", inst, raw_inputs)
    assert result["counts"]["ok"] == 1, result["files"]
    entry = result["files"][0]
    assert entry["path"] == "notes.docx"
    assert entry["reason"] == "soffice+pymupdf"

    parsed_md = (config.work_dir / "t1" / "raw" / "notes.docx.md").read_text(encoding="utf-8")
    assert "alpha" in parsed_md.lower()

    con = sqlite3.connect(config.work_dir / "t1" / "raw" / "raw.sqlite")
    rows = con.execute("SELECT text FROM raw_fts").fetchall()
    con.close()
    assert any("alpha" in (r[0] or "").lower() for r in rows)


def test_gather_raw_handles_xlsx(tmp_path):
    """Format coverage: `.xlsx` must both MATCH (via parsed text, not raw
    bytes) and PARSE through gather-raw. Also the fix-round-1 regression this
    review flagged: an alias present only inside a workbook's cells (not its
    path) must select the file."""
    openpyxl = pytest.importorskip("openpyxl")
    proj = _make_project(tmp_path)
    _domain_task(proj, aliases=["alpha"], exclude=[])
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Item", "Note"])
    for i in range(5):
        ws.append([f"row {i}", "Alpha adjustment recorded here"])
    xlsx_path = proj / "raw-replay" / "workbook.xlsx"
    wb.save(xlsx_path)

    config, data = _load(proj)
    inst = data["instances"]["t1"]
    template = data["templates"]["domain-profile"]
    from scribe_lib.config import resolve_instance_inputs, select_raw_files

    raw_inputs = resolve_instance_inputs(inst, template)["raw"]
    # Selection alone (plan/delta's code path) must find it via PARSED text —
    # nothing in the path or filename mentions "alpha".
    selected = select_raw_files(config, raw_inputs)
    assert [p.name for p in selected] == ["workbook.xlsx"]

    result = gather_raw_task(config, "t1", inst, raw_inputs)
    assert result["counts"]["ok"] == 1, result["files"]
    entry = result["files"][0]
    assert entry["path"] == "workbook.xlsx"
    assert entry["reason"] == "openpyxl-structure"

    parsed_md = (config.work_dir / "t1" / "raw" / "workbook.xlsx.md").read_text(encoding="utf-8")
    assert "alpha" in parsed_md.lower()

    con = sqlite3.connect(config.work_dir / "t1" / "raw" / "raw.sqlite")
    rows = con.execute("SELECT text FROM raw_fts").fetchall()
    con.close()
    assert any("alpha" in (r[0] or "").lower() for r in rows)


def test_gather_raw_handles_pdf(tmp_path):
    """Format coverage: `.pdf` must both MATCH and PARSE through gather-raw."""
    pymupdf = pytest.importorskip("pymupdf")
    proj = _make_project(tmp_path)
    _domain_task(proj, aliases=["alpha"], exclude=[])
    pdf_path = proj / "raw-replay" / "report.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Alpha adjustment summary for the report.")
    doc.save(pdf_path)
    doc.close()

    config, data = _load(proj)
    inst = data["instances"]["t1"]
    template = data["templates"]["domain-profile"]
    from scribe_lib.config import resolve_instance_inputs

    raw_inputs = resolve_instance_inputs(inst, template)["raw"]
    result = gather_raw_task(config, "t1", inst, raw_inputs)
    assert result["counts"]["ok"] == 1, result["files"]
    entry = result["files"][0]
    assert entry["path"] == "report.pdf"
    assert entry["reason"] == "pymupdf"

    parsed_md = (config.work_dir / "t1" / "raw" / "report.pdf.md").read_text(encoding="utf-8")
    assert "alpha" in parsed_md.lower()

    con = sqlite3.connect(config.work_dir / "t1" / "raw" / "raw.sqlite")
    rows = con.execute("SELECT text FROM raw_fts").fetchall()
    con.close()
    assert any("alpha" in (r[0] or "").lower() for r in rows)


# ------------------------------------------------------------------ fingerprint --

def _seed_raw_sqlite(db_path: Path, rows: list[tuple[str, str, str]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("CREATE VIRTUAL TABLE raw_fts USING fts5(path, locator, text)")
    con.executemany("INSERT INTO raw_fts(path, locator, text) VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()


def test_raw_relevance_floor_drops_single_token_matches(tmp_path):
    db = tmp_path / "raw.sqlite"
    _seed_raw_sqlite(
        db,
        [
            ("a.md", "§1", "Alpha adjustments were discussed at length in this meeting."),
            ("b.md", "§1", "This transcript only mentions alpha once, nothing else relevant."),
            ("c.md", "§1", "alpha adjustment process"),
        ],
    )
    hits = _raw_hits(db, "alpha adjustment", top_k=8)
    paths = {h["path"] for h in hits}
    # "a.md" has only 1 distinct WORD-bounded token ("alpha" -- \badjustment\b
    # does NOT match "adjustments", no boundary between "t" and "s"), but the
    # phrase check is a plain (non-word-bounded) substring test, and "alpha
    # adjustment" IS literally a substring of "alpha adjustments were..." ->
    # kept via the phrase branch, not the distinct-token-count branch.
    assert "a.md" in paths
    assert "c.md" in paths  # contains the exact phrase "alpha adjustment"
    assert "b.md" not in paths  # only one distinct token ("alpha") present, and no phrase match


def test_fingerprint_stable_across_two_runs(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    _domain_task(proj)
    config, data = _load(proj)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]

    def fake_search(cfg, query, limit, tag):
        return [
            {"chunk_id": "111", "source": "doc.md", "section": "Overview", "score": 0.9, "text": "Alpha overview text."},
        ]

    monkeypatch.setattr(brain_mod, "search", fake_search)

    fp1 = fingerprint_task(config, "t1", instances, templates, edges)
    fp2 = fingerprint_task(config, "t1", instances, templates, edges)
    for sid in fp1["sections"]:
        assert fp1["sections"][sid]["fingerprint"] == fp2["sections"][sid]["fingerprint"]
    assert (config.work_dir / "t1" / "fingerprint.json").is_file()


def test_fingerprint_unions_tag_filtered_and_untagged_brain_search(tmp_path, monkeypatch):
    """R10: a chunk only the untagged search finds still appears in `considered`
    and in the pack, ordered after tag-filtered hits."""
    db_path = _fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = _make_project(tmp_path, brain_db=db_path)
    _domain_task(proj, aliases=[], exclude=[])  # tags: ["Domain Tag A"]
    config, data = _load(proj)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]

    def fake_search(cfg, query, limit, tag):
        if tag == "Domain Tag A":
            return [
                {"chunk_id": "111", "source": "tagged.md", "section": "Overview", "score": 0.9, "text": "Tagged evidence."},
            ]
        assert tag is None
        return [
            {"chunk_id": "222", "source": "fresh.md", "section": "Overview", "score": 0.99, "text": "Untagged fresh evidence."},
        ]

    monkeypatch.setattr(brain_mod, "search", fake_search)

    fp = fingerprint_task(config, "t1", instances, templates, edges)
    overview = fp["sections"]["overview"]
    by_id = {h["chunk_id"]: h for h in overview["brain_hits"]}
    assert set(by_id) == {"111", "222"}
    assert by_id["111"]["via"] == ["tag:Domain Tag A"]
    assert by_id["222"]["via"] == ["untagged"]
    considered_ids = {cid for cid, _ in overview["considered"]["brain"]}
    assert considered_ids == {"111", "222"}

    prepare_task(config, "t1")
    pack_text = (config.work_dir / "t1" / "pack" / "overview.pack.md").read_text(encoding="utf-8")
    assert "[RAG:111]" in pack_text
    assert "[RAG:222]" in pack_text
    # Tag-filtered hit ordered before the untagged-only hit despite its lower score.
    assert pack_text.index("[RAG:111]") < pack_text.index("[RAG:222]")


def test_fingerprint_no_tags_issues_exactly_one_search_per_query(tmp_path, monkeypatch):
    """A task with no tags already searched untagged only — unchanged: one
    `search` call per query, never a duplicate untagged call."""
    proj = _make_project(tmp_path)
    _domain_task(proj, tags=[])
    config, data = _load(proj)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]

    calls: list[tuple[str, str | None]] = []

    def fake_search(cfg, query, limit, tag):
        calls.append((query, tag))
        return []

    monkeypatch.setattr(brain_mod, "search", fake_search)

    fp = fingerprint_task(config, "t1", instances, templates, edges)
    overview_queries = fp["sections"]["overview"]["queries"]
    overview_calls = [c for c in calls if c[0] in overview_queries]
    assert len(overview_calls) == len(overview_queries)
    assert all(tag is None for _, tag in overview_calls)


def test_fingerprint_marks_section_stale_when_cited_chunk_text_changed(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    _domain_task(proj)
    config, data = _load(proj)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]

    state_dir = config.out_root / "t1" / "_src"
    state_dir.mkdir(parents=True)
    state = {
        "version": 1,
        "built_at": "2026-01-01T00:00:00Z",
        "published": {"docx_sha256": "deadbeef"},
        "sections": {
            "overview": {
                "fingerprint": "unchanged-marker",
                "cited_chunks": {"111": brain_mod.text_hash("Old alpha overview text.")},
                "cited_raw": {},
            }
        },
        "upstream_versions": {},
    }
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def fake_search(cfg, query, limit, tag):
        return []

    def fake_evidence(cfg, chunk_id):
        return {"chunk_id": chunk_id, "status": "ok", "text": "New alpha overview text — changed."}

    monkeypatch.setattr(brain_mod, "search", fake_search)
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)

    result = fingerprint_task(config, "t1", instances, templates, edges)
    overview = result["sections"]["overview"]
    assert overview["stale"] is True
    assert "cited_chunk_changed" in overview["stale_reasons"]
    assert overview["cited_chunk_status"]["111"] == "changed"


# ------------------------------------------------------------------------ base --

def test_base_with_no_published_version(tmp_path):
    proj = _make_project(tmp_path)
    _domain_task(proj)
    config, data = _load(proj)
    inst = data["instances"]["t1"]
    template = data["templates"]["domain-profile"]

    result = base_task(config, "t1", inst, template)
    assert result == {"base_version": None, "base_edited": False, "human_added": [], "human_modified": []}
    assert (config.work_dir / "t1" / "base.md").read_text(encoding="utf-8") == ""


@pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
def test_base_recovers_footnote_tags_and_carries_claim_ids(tmp_path):
    proj = _make_project(tmp_path)
    _domain_task(proj)
    config, data = _load(proj)
    inst = data["instances"]["t1"]
    template = data["templates"]["domain-profile"]

    out_dir = config.out_root / "t1"
    src_dir = out_dir / "_src"
    src_dir.mkdir(parents=True)
    prev_md = """\
<!-- scribe: task=t1 version=1 built_at=2026-01-01T00:00:00Z template=domain-profile@1 base=none -->
# T1 Domain Profile

## Changes in this version {#changes}
- initial version

## Overview {#overview}
Alpha is owned by the ops team. [RAG:12345] <!-- c:aaaa1111 -->

- Invoicing runs monthly and is reconciled weekly by the finance team. [FILE:docs/invoice.md#section] <!-- c:bbbb2222 -->
"""
    (src_dir / "v001.md").write_text(prev_md, encoding="utf-8")

    src_md = tmp_path / "human_edit.md"
    src_md.write_text(
        textwrap.dedent(
            """\
            # T1 Domain Profile

            ## Overview

            Alpha is owned by the ops team.[^1]

            - Invoicing runs monthly.[^2]

            This is a brand new sentence added by a human with no prior claim.[^3]

            [^1]: RAG:12345 — Alpha runbook
            [^2]: FILE:docs/invoice.md#section — Invoice doc
            [^3]: RAG:99999 — New source
            """
        ),
        encoding="utf-8",
    )
    docx_path = out_dir / "T1 Domain Profile.docx"
    subprocess.run(
        ["pandoc", "-f", "markdown", "-t", "docx", "-o", str(docx_path), str(src_md)],
        check=True,
    )

    from scribe_lib.config import sha256_file

    (src_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "published": {"docx_sha256": "not-the-real-sha-so-edited-is-detected"},
            }
        ),
        encoding="utf-8",
    )
    # sanity: the docx sha really does differ from the recorded (stale) one
    assert sha256_file(docx_path) != "not-the-real-sha-so-edited-is-detected"

    result = base_task(config, "t1", inst, template)
    assert result["base_edited"] is True
    assert result["base_version"] == 1

    base_md = (config.work_dir / "t1" / "base.md").read_text(encoding="utf-8")
    assert "[RAG:12345]" in base_md
    assert "[FILE:docs/invoice.md#section]" in base_md
    assert "<!-- c:aaaa1111 -->" in base_md  # unchanged claim carried verbatim

    human_added_texts = [h["text"] for h in result["human_added"]]
    assert any("brand new sentence" in t for t in human_added_texts)

    human_modified = result["human_modified"]
    assert len(human_modified) == 1
    assert human_modified[0]["claim_id"] == "bbbb2222"


# --------------------------------------------------------------------- prepare --

def test_prepare_first_run_marks_every_section_stale_and_writes_pack(tmp_path, monkeypatch):
    db_path = _fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = _make_project(tmp_path, brain_db=db_path)
    _domain_task(proj, aliases=[], exclude=[])
    _write(proj / "raw-replay" / "notes.txt", "Some alpha narrative text for context.")

    def fake_search(cfg, query, limit, tag):
        return [
            {"chunk_id": "111", "source": "doc.md", "section": "Overview", "score": 0.9, "text": "Alpha overview text."},
        ]

    monkeypatch.setattr(brain_mod, "search", fake_search)

    config = load_config(proj)
    result = prepare_task(config, "t1")
    assert result["status"] == "ok"
    assert result["plan"]["noop"] is False
    stale_ids = {e["section"] for e in result["plan"]["stale"]}
    assert stale_ids  # every section stale on a first run
    for entry in result["plan"]["stale"]:
        assert "first_run" in entry["reasons"]

    pack_dir = config.work_dir / "t1" / "pack"
    assert (pack_dir / "plan.json").is_file()
    assert (pack_dir / "base.md").is_file()
    for sid in stale_ids:
        pack_file = pack_dir / f"{sid}.pack.md"
        assert pack_file.is_file()
        text = pack_file.read_text(encoding="utf-8")
        assert "[RAG:111]" in text  # tag ready to cite is present verbatim
