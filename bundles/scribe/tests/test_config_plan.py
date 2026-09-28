"""Smoke tests for the plugin scaffold, task format, and validate/plan/delta.

Fixture sqlite for the Brain is built inline (a `synced_files` table only —
that is all `delta`/`plan` need here). No real Brain is touched.
"""
from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

import scribe
from conftest import REPO_ROOT, TEMPLATES_DIR
from scribe_lib.config import compute_raw_delta, load_config, raw_snapshot, select_raw_files

DUMMY_ABS = "/nonexistent/scribe-poc-tests"  # never read; only path-joined
# select_raw_files' text `match` now parses via the real parse_corpus.py (fix
# round 1, task-2-review.md item 1), so brain_skills must point at the real
# bundles/brain/skills, unlike brain_catalog/brain_mcp_dir which stay dummy
# (never opened by validate/plan/delta).
BRAIN_SKILLS = REPO_ROOT / "bundles" / "brain" / "skills"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _make_project(tmp_path: Path, *, brain_db: Path | None = None) -> Path:
    """A minimal Scribe project pointed at the real library templates."""
    proj = tmp_path / "proj"
    (proj / "tasks").mkdir(parents=True)
    (proj / "raw-replay").mkdir(parents=True)
    brain_db_path = brain_db if brain_db is not None else (proj / "missing.sqlite")
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


def _three_task_project(tmp_path: Path, *, brain_db: Path | None = None) -> Path:
    proj = _make_project(tmp_path, brain_db=brain_db)
    _write(
        proj / "tasks" / "t1.task.md",
        """\
        ---
        template: domain-profile@1
        id: t1
        title: "T1 Domain Profile"
        params:
          name: "Domain A"
          tags: ["Tag A"]
          aliases: ["aliasa", "aliasb"]
        audience: "engagement team"
        cadence: on-brain-update
        publish: auto
        out: "t1"
        ---
        Drafting notes for t1.
        """,
    )
    _write(
        proj / "tasks" / "t2.task.md",
        """\
        ---
        template: subsystem-profile@1
        id: t2
        title: "T2 Subsystem Profile"
        params:
          name: "System B"
          tags: []
          aliases: ["sysb"]
        audience: "engagement team"
        cadence: on-brain-update
        publish: auto
        out: "t2"
        ---
        Drafting notes for t2.
        """,
    )
    _write(
        proj / "tasks" / "t3.task.md",
        """\
        ---
        template: discovery-digest@1
        id: t3
        title: "T3 Discovery Digest"
        params:
          name: "Program"
          aliases: []
        audience: "internal"
        cadence: manual
        publish: propose
        out: "t3"
        inputs:
          tasks: [t1, t2]
        ---
        Drafting notes for t3.
        """,
    )
    return proj


def _run(project: Path, *args: str) -> tuple[int, dict]:
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = scribe.main(["--project", str(project), *args])
    out = buf.getvalue().strip()
    return code, json.loads(out) if out else {}


# --------------------------------------------------------------------- validate --

def test_validate_accepts_three_templates_with_sample_instances(tmp_path):
    proj = _three_task_project(tmp_path)
    code, payload = _run(proj, "validate")
    assert code == 0, payload
    assert payload["status"] == "ok"
    assert sorted(payload["templates"]) == ["discovery-digest", "domain-profile", "subsystem-profile"]
    assert payload["tasks"] == ["t1", "t2", "t3"]


def test_validate_rejects_a_cycle(tmp_path):
    proj = _make_project(tmp_path)
    _write(
        proj / "tasks" / "a.task.md",
        """\
        ---
        template: discovery-digest@1
        id: a
        title: "A"
        params: { name: "A", aliases: [] }
        out: "a"
        inputs:
          tasks: [b]
        ---
        body
        """,
    )
    _write(
        proj / "tasks" / "b.task.md",
        """\
        ---
        template: discovery-digest@1
        id: b
        title: "B"
        params: { name: "B", aliases: [] }
        out: "b"
        inputs:
          tasks: [a]
        ---
        body
        """,
    )
    code, payload = _run(proj, "validate")
    assert code == 1
    assert payload["status"] == "error"
    assert "cycle" in payload["reason"]


def test_validate_rejects_unknown_param(tmp_path):
    proj = _make_project(tmp_path)
    _write(
        proj / "tasks" / "bad.task.md",
        """\
        ---
        template: domain-profile@1
        id: bad
        title: "Bad"
        params:
          name: "X"
          tags: []
          aliases: []
          not_a_real_param: "oops"
        out: "bad"
        ---
        body
        """,
    )
    code, payload = _run(proj, "validate")
    assert code == 1
    assert payload["status"] == "error"
    assert "unknown params" in payload["reason"]
    assert "not_a_real_param" in payload["reason"]


def test_validate_rejects_missing_upstream_task(tmp_path):
    proj = _make_project(tmp_path)
    _write(
        proj / "tasks" / "only.task.md",
        """\
        ---
        template: discovery-digest@1
        id: only
        title: "Only"
        params: { name: "X", aliases: [] }
        out: "only"
        inputs:
          tasks: [ghost]
        ---
        body
        """,
    )
    code, payload = _run(proj, "validate")
    assert code == 1
    assert "unknown upstream task" in payload["reason"]
    assert "ghost" in payload["reason"]


# ------------------------------------------------------------------------- plan --

def test_plan_orders_dag_and_reports_first_run(tmp_path):
    proj = _three_task_project(tmp_path)
    code, payload = _run(proj, "plan")
    assert code == 0, payload
    assert payload["order"] == ["t1", "t2", "t3"]

    by_id = {t["id"]: t for t in payload["tasks"]}
    assert by_id["t1"]["due"] is True
    assert by_id["t1"]["reasons"] == ["first_run"]
    assert by_id["t2"]["due"] is True
    assert by_id["t2"]["reasons"] == ["first_run"]
    # t3 is cadence:manual and not named via --task, so first_run alone is not
    # enough to make it due even though it reports the reason.
    assert by_id["t3"]["cadence"] == "manual"
    assert by_id["t3"]["due"] is False
    assert by_id["t3"]["not_due_reason"] == "cadence manual"
    assert by_id["t3"]["upstream"] == ["t1", "t2"]


def test_plan_task_flag_forces_manual_cadence_due(tmp_path):
    proj = _three_task_project(tmp_path)
    code, payload = _run(proj, "plan", "--task", "t3")
    assert code == 0, payload
    assert len(payload["tasks"]) == 1
    assert payload["tasks"][0]["id"] == "t3"
    assert payload["tasks"][0]["due"] is True


def test_plan_due_flag_filters(tmp_path):
    proj = _three_task_project(tmp_path)
    code, payload = _run(proj, "plan", "--due")
    assert code == 0, payload
    ids = {t["id"] for t in payload["tasks"]}
    assert ids == {"t1", "t2"}  # t3 is cadence:manual, not due


def test_plan_daily_cadence_due_reason_when_day_elapsed(tmp_path, monkeypatch):
    db_path = tmp_path / "empty-brain.sqlite"
    _make_brain_db(db_path, [])
    proj = _make_project(tmp_path, brain_db=db_path)
    _write(
        proj / "tasks" / "d.task.md",
        """\
        ---
        template: discovery-digest@1
        id: d
        title: "D"
        params: { name: "D", aliases: [] }
        cadence: daily
        out: "d"
        ---
        body
        """,
    )
    state_dir = proj / "out" / "d" / "_src"
    state_dir.mkdir(parents=True)
    state = {
        "version": 1,
        "built_at": "2026-01-01T00:00:00",
        "brain_snapshot": {},
        "raw_snapshot": {},
        "upstream_versions": {},
    }
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    monkeypatch.setenv("SCRIBE_NOW", "2026-01-02")
    code, payload = _run(proj, "plan")
    assert code == 0, payload
    entry = payload["tasks"][0]
    assert entry["due"] is True
    assert entry["reasons"] == ["cadence"]


# -------------------------------------------------------------------------- raw --

def _write_raw(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _populate_raw_fixture(raw_root: Path) -> None:
    """Generic (non-client) raw-file layout exercising glob/exclude/match rules.

    docs/alpha-notes.txt          -- "alpha" in the path
    docs/beta-notes.txt           -- "gizmo" only in the text, not the path
    docs/nested/gamma-notes.txt   -- plain, nested (exercises glob '**')
    Internal Meeting Notes/standup-01.txt -- excluded dir; "alpha" only in text
    other/readme.txt              -- no keywords, outside docs/
    """
    _write_raw(raw_root, "docs/alpha-notes.txt", "Alpha widget rollout notes.")
    _write_raw(raw_root, "docs/beta-notes.txt", "Beta system overview. Mentions Gizmo integration.")
    _write_raw(raw_root, "docs/nested/gamma-notes.txt", "Gamma release notes.")
    _write_raw(
        raw_root,
        "Internal Meeting Notes/standup-01.txt",
        "Standup notes mention Alpha widget only in this excluded file.",
    )
    _write_raw(raw_root, "other/readme.txt", "Readme file, no keywords.")


def test_select_raw_files_glob_with_double_star_is_recursive(tmp_path):
    proj = _make_project(tmp_path)
    _populate_raw_fixture(proj / "raw-replay")
    config = load_config(proj)
    selected = select_raw_files(config, {"globs": ["docs/**/*.txt"], "exclude": [], "match": []})
    rels = sorted(p.relative_to(config.raw_root).as_posix() for p in selected)
    assert rels == ["docs/alpha-notes.txt", "docs/beta-notes.txt", "docs/nested/gamma-notes.txt"]


def test_select_raw_files_exclude_removes_matching_dir(tmp_path):
    proj = _make_project(tmp_path)
    _populate_raw_fixture(proj / "raw-replay")
    config = load_config(proj)
    selected = select_raw_files(
        config,
        {"globs": ["**/*.txt"], "exclude": ["**/Internal Meeting Notes/**"], "match": []},
    )
    rels = sorted(p.relative_to(config.raw_root).as_posix() for p in selected)
    assert "Internal Meeting Notes/standup-01.txt" not in rels
    assert rels == [
        "docs/alpha-notes.txt",
        "docs/beta-notes.txt",
        "docs/nested/gamma-notes.txt",
        "other/readme.txt",
    ]


def test_select_raw_files_match_is_case_insensitive_on_path_and_text(tmp_path):
    proj = _make_project(tmp_path)
    _populate_raw_fixture(proj / "raw-replay")
    config = load_config(proj)
    # "ALPHA" (uppercase) matches docs/alpha-notes.txt via its PATH, and matches
    # Internal Meeting Notes/standup-01.txt via its TEXT ("Alpha widget") only --
    # neither match would work with a case-sensitive substring check.
    selected = select_raw_files(config, {"globs": ["**/*.txt"], "exclude": [], "match": ["ALPHA"]})
    rels = sorted(p.relative_to(config.raw_root).as_posix() for p in selected)
    assert rels == ["Internal Meeting Notes/standup-01.txt", "docs/alpha-notes.txt"]


def test_select_raw_files_text_only_match(tmp_path):
    proj = _make_project(tmp_path)
    _populate_raw_fixture(proj / "raw-replay")
    config = load_config(proj)
    # "gizmo" appears only in docs/beta-notes.txt's TEXT, never in any file's path.
    selected = select_raw_files(config, {"globs": ["docs/**/*.txt"], "exclude": [], "match": ["gizmo"]})
    rels = [p.relative_to(config.raw_root).as_posix() for p in selected]
    assert rels == ["docs/beta-notes.txt"]


def test_select_raw_files_empty_match_selects_everything_globs_allow(tmp_path):
    proj = _make_project(tmp_path)
    _populate_raw_fixture(proj / "raw-replay")
    config = load_config(proj)
    selected = select_raw_files(config, {"globs": ["**/*.txt"], "exclude": [], "match": []})
    rels = sorted(p.relative_to(config.raw_root).as_posix() for p in selected)
    assert rels == [
        "Internal Meeting Notes/standup-01.txt",
        "docs/alpha-notes.txt",
        "docs/beta-notes.txt",
        "docs/nested/gamma-notes.txt",
        "other/readme.txt",
    ]


def test_compute_raw_delta_reports_new_changed_removed(tmp_path):
    proj = _make_project(tmp_path)
    raw_root = proj / "raw-replay"
    _write_raw(raw_root, "a.txt", "hello")
    _write_raw(raw_root, "b.txt", "world")
    _write_raw(raw_root, "c.txt", "foo")
    config = load_config(proj)
    raw_inputs = {"globs": ["**/*.txt"], "exclude": [], "match": []}

    prior_snapshot = raw_snapshot(config, raw_inputs)
    state = {"raw_snapshot": prior_snapshot}

    # No change yet -> empty delta.
    delta = compute_raw_delta(config, raw_inputs, state)
    assert delta["new"] == delta["changed"] == delta["removed"] == []

    # Mutate: change a.txt, remove b.txt, add d.txt; c.txt untouched.
    _write_raw(raw_root, "a.txt", "hello, changed")
    (raw_root / "b.txt").unlink()
    _write_raw(raw_root, "d.txt", "new file")

    delta = compute_raw_delta(config, raw_inputs, state)
    assert delta["new"] == ["d.txt"]
    assert delta["changed"] == ["a.txt"]
    assert delta["removed"] == ["b.txt"]


# ------------------------------------------------------------------------ delta --

def _make_brain_db(path: Path, rows: list[tuple[str, str]]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE synced_files(doc_id TEXT PRIMARY KEY, sha TEXT, bytes INT, "
        "mtime REAL, updated_at TEXT, source_id TEXT)"
    )
    con.executemany(
        "INSERT INTO synced_files(doc_id, sha, bytes, mtime, updated_at, source_id) "
        "VALUES (?, ?, 0, 0, '2026-01-01T00:00:00', NULL)",
        rows,
    )
    con.commit()
    con.close()


def test_delta_first_run_reports_all_docs_as_added(tmp_path):
    db_path = tmp_path / "brain.sqlite"
    _make_brain_db(db_path, [("doc-a.md", "sha-a"), ("doc-b.md", "sha-b")])
    proj = _three_task_project(tmp_path, brain_db=db_path)
    code, payload = _run(proj, "delta", "t1")
    assert code == 0, payload
    assert payload["first_run"] is True
    assert sorted(payload["brain"]["added"]) == ["doc-a.md", "doc-b.md"]
    assert payload["brain"]["changed"] == []
    assert payload["brain"]["removed"] == []


def test_delta_reports_changed_added_removed_against_state(tmp_path):
    db_path = tmp_path / "brain.sqlite"
    _make_brain_db(db_path, [("doc-a.md", "sha-a-v2"), ("doc-c.md", "sha-c")])
    proj = _three_task_project(tmp_path, brain_db=db_path)
    state_dir = proj / "out" / "t1" / "_src"
    state_dir.mkdir(parents=True)
    state = {
        "version": 1,
        "built_at": "2026-01-01T00:00:00",
        "brain_snapshot": {"doc-a.md": "sha-a-v1", "doc-b.md": "sha-b"},
        "raw_snapshot": {},
        "upstream_versions": {},
    }
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    code, payload = _run(proj, "delta", "t1")
    assert code == 0, payload
    assert payload["first_run"] is False
    assert payload["brain"]["changed"] == ["doc-a.md"]  # sha-a-v1 -> sha-a-v2
    assert payload["brain"]["added"] == ["doc-c.md"]
    assert payload["brain"]["removed"] == ["doc-b.md"]


def test_delta_unknown_task_refused(tmp_path):
    proj = _three_task_project(tmp_path)
    code, payload = _run(proj, "delta", "nope")
    assert code == 1
    assert payload["status"] == "error"
    assert "nope" in payload["reason"]


# ------------------------------------------------------------- not implemented --

def test_render_refuses_with_exit_1_when_no_next_md(tmp_path):
    # `render` is implemented; every command in the contract table is now
    # implemented. It still refuses (exit 1, not the "not implemented" exit
    # 2 this test used to check) when there is nothing to render yet (no
    # `work/<task>/next.md`, i.e. `merge` never ran) — see
    # scribe_lib.render.render_task.
    proj = _three_task_project(tmp_path)
    code, payload = _run(proj, "render", "t1")
    assert code == 1
    assert "no markdown to render" in payload["reason"]
