"""Shared test fixtures for the scribe bundle's smoke tests.

Every helper here was originally private to one test module (duplicated, in
places, across several) and is now the single shared implementation every
other test module imports. Two constants (`REPO_ROOT`, `TEMPLATES_DIR`) are
re-exported from `conftest` so a test needs only one import line for the
common fixture surface.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import textwrap
from pathlib import Path

from conftest import REPO_ROOT, TEMPLATES_DIR
from scribe_lib import claims
from scribe_lib.accept import accept_task
from scribe_lib.basedoc import base_task, split_by_section_id
from scribe_lib.config import load_config, validate_all
from scribe_lib.merge import merge_task
from scribe_lib.publish import publish_task

BRAIN_SKILLS = REPO_ROOT / "bundles" / "brain" / "skills"
DUMMY_ABS = "/nonexistent/scribe-poc-t3-tests"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fixture_brain_db(path: Path, *, synced=None, chunks=None, nodes=None) -> Path:
    """A minimal sqlite with `synced_files`, `chunks` and `graph_nodes` —
    enough for `read_synced_files` plus whatever chunk/graph rows a test
    needs to seed."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE synced_files(doc_id TEXT, sha TEXT, bytes INT, mtime TEXT, updated_at TEXT, source_id TEXT)"
    )
    con.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, ord INT, text TEXT)")
    con.execute("CREATE TABLE graph_nodes(id TEXT PRIMARY KEY, label TEXT, kind TEXT, parent TEXT)")
    for doc_id, sha in (synced or {}).items():
        con.execute("INSERT INTO synced_files VALUES(?,?,0,'0','2026-01-01T00:00:00',NULL)", (doc_id, sha))
    con.executemany("INSERT INTO chunks VALUES(?,?,?,?)", chunks or [])
    con.executemany("INSERT INTO graph_nodes VALUES(?,?,?,?)", nodes or [])
    con.commit()
    con.close()
    return path


def make_project(tmp_path: Path, *, brain_db: Path | None = None) -> Path:
    proj = tmp_path / "proj"
    (proj / "tasks").mkdir(parents=True)
    (proj / "raw-replay").mkdir(parents=True)
    brain_db_path = brain_db if brain_db is not None else Path(f"{DUMMY_ABS}/knowledge.sqlite")
    write_text(
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


MINI_TEMPLATE = """\
---
id: mini-profile
version: 1
goal: "A minimal 2-section template for scribe fixture tests."
params: [name]
inputs:
  brain: {}
  raw: { match: [], globs: ["**/*"], exclude: [] }
  prior: latest
  tasks: []
output:
  formats: [docx, pdf]
  versioning: extend
  sections:
    - id: overview
      title: "Overview"
      intent: "What {{name}} is."
      queries: ["{{name}} overview"]
      lanes: [narrative]
    - id: details
      title: "Details"
      intent: "Details about {{name}}."
      queries: ["{{name}} details"]
      lanes: [narrative]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

Drafting guidance for the mini profile.
"""


def mini_task(
    proj: Path,
    tid: str,
    *,
    out: str | None = None,
    publish: str = "auto",
    title: str | None = None,
    upstream: list[str] | None = None,
    cadence: str = "on-brain-update",
) -> None:
    out = out or tid
    title = title or f"Mini {tid}"
    tasks_line = f"  tasks: [{', '.join(upstream)}]\n" if upstream else ""
    write_text(
        proj / "tasks" / f"{tid}.task.md",
        f"---\ntemplate: mini-profile@1\nid: {tid}\ntitle: \"{title}\"\nparams:\n  name: \"Widget\"\n"
        f"inputs:\n  raw:\n    exclude: []\n{tasks_line}audience: \"team\"\ncadence: {cadence}\n"
        f"publish: {publish}\nout: \"{out}\"\n---\nnotes for {tid}\n",
    )


def load(proj: Path):
    config = load_config(proj)
    data = validate_all(config)
    return config, data


def setup_mini_project(tmp_path: Path, tid: str = "m1", *, brain_db: Path | None = None, **kw) -> Path:
    proj = make_project(tmp_path, brain_db=brain_db)
    templates_dir = proj / "templates"
    write_text(templates_dir / "mini-profile.tmpl.md", MINI_TEMPLATE)
    mini_task(proj, tid, **kw)
    return proj


def fake_evidence(cfg, chunk_id) -> dict:
    return {
        "chunk_id": str(chunk_id),
        "status": "ok",
        "text": f"evidence text {chunk_id}",
        "source": "doc.md",
        "section": "Intro",
        "ord": 1,
    }


def doc(task_id: str, version: int, title: str, sections: dict[str, str], base: str = "none") -> str:
    lines = [
        f"<!-- scribe: task={task_id} version={version} built_at=2026-01-0{version}T00:00:00 "
        f"template=mini-profile@1 base={base} -->",
        "",
        f"# {title}",
        "",
        "## Changes in this version {#changes}",
        "",
        "- initial",
        "",
    ]
    for sid, body in sections.items():
        lines += [f"## {sid.title()} {{#{sid}}}", "", body.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def publish_seed(
    config,
    inst,
    text: str,
    version: int,
    *,
    docx_sha: str | None,
    cited_raw: dict | None = None,
    extra_state: dict | None = None,
) -> None:
    src_dir = config.out_root / inst["out"] / "_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / f"v{version:03d}.md").write_text(text, encoding="utf-8")
    sections = {"overview": {"cited_raw": cited_raw or {}}, "details": {"cited_raw": {}}}
    state = {
        "version": version,
        "built_at": f"2026-01-0{version}T00:00:00",
        "published": {"docx_sha256": docx_sha, "pdf_sha256": None, "md_sha256": None},
        "sections": sections,
        "cited_raw": cited_raw or {},
    }
    if extra_state:
        state.update(extra_state)
    write_json(src_dir / "state.json", state)


def plan_stale(config, task_id: str, stale: list[str], carried: list[str]) -> None:
    write_json(
        config.work_dir / task_id / "pack" / "plan.json",
        {"task": task_id, "stale": [{"section": s, "reasons": ["test"]} for s in stale], "carried": carried, "noop": False},
    )


def draft(config, task_id: str, sid: str, body: str, evidence: list | None = None) -> None:
    sec_dir = config.work_dir / task_id / "sections"
    sec_dir.mkdir(parents=True, exist_ok=True)
    (sec_dir / f"{sid}.md").write_text(body, encoding="utf-8")
    if evidence is not None:
        (sec_dir / f"{sid}.evidence.json").write_text(json.dumps(evidence), encoding="utf-8")


def claim_blocks(path: Path) -> dict[str, list[dict]]:
    return {
        sid: [b for b in claims.parse_blocks(body) if claims.is_claim(b)]
        for sid, body in split_by_section_id(path.read_text(encoding="utf-8")).items()
    }


def run_version(config, data, inst, template, stale: list[str], drafts: dict[str, str], task_id: str = "m1") -> dict:
    """prepare's base step + agent drafts + merge + accept + publish + lineage."""
    from scribe_lib.lineage import lineage_task

    base_task(config, task_id, inst, template)
    carried = [s for s in ("overview", "details") if s not in stale]
    plan_stale(config, task_id, stale, carried)
    sec_dir = config.work_dir / task_id / "sections"
    if sec_dir.is_dir():
        shutil.rmtree(sec_dir)
    for sid, body in drafts.items():
        draft(config, task_id, sid, body)
    merged = merge_task(config, task_id, data["instances"], data["templates"])
    assert merged["noop"] is False
    accepted = accept_task(config, task_id, inst, template)
    published = publish_task(config, task_id, inst, template, data["instances"], data["edges"], no_render=True)
    assert published["status"] == "ok", published
    lineage_task(config, task_id, inst, template, data["instances"])
    return accepted
