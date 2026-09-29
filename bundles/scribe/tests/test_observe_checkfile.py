"""Smoke tests for two defects the live replay found: `observe` and carried
`check-file` claims.

Defect 1 (`observe`): a noop run (no stale sections from `prepare`, or a
byte-identical `next.md` from `merge`) never called `publish`, so
`state.json`'s observation fields (`raw_snapshot`, `brain_snapshot`,
`upstream_versions`, per-section `fingerprint`) stayed frozen at whatever the
last PUBLISH recorded — `plan` then re-reports the same `raw_changed` (or
`brain_changed`) reason every following night even though a run already
looked and found nothing worth drafting. `observe_task` records what a noop
run saw without touching `version`/`published`/`cited_*`.

Defect 2 (`check-file` carried claims): a `[FILE:]` claim drafted unchanged
from the base (same section, same normalized text, same tag) used to be
treated exactly like a brand-new claim — demanding a fresh sidecar quote even
though nothing about it changed. It is now "carried": verified by re-checking
the cited raw file's sha256 against what `state.json` recorded when it was
last published, not by re-demanding a quote.
"""
from __future__ import annotations

import json
from pathlib import Path

from scribe_lib.checkfile import check_file_task
from scribe_lib.config import sha256_file
from scribe_lib.observe import observe_task

import scribe
from scribe_fixtures import fixture_brain_db, load, setup_mini_project, write_json, write_text


def _run_cli(project: Path, *args: str) -> tuple[int, dict]:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = scribe.main(["--project", str(project), *args])
    out = buf.getvalue().strip()
    return code, json.loads(out) if out else {}


# ------------------------------------------------------------------ observe --

def test_observe_updates_snapshots_and_fingerprints_without_touching_publish_state(tmp_path, monkeypatch):
    """Unit-level check of the state.json diff `observe` is allowed to make."""
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-16")
    db_path = fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = setup_mini_project(tmp_path, brain_db=db_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]

    out_dir = config.out_root / "m1"
    published = {"docx_sha256": "abc", "pdf_sha256": "def", "md_sha256": "ghi"}
    state_before = {
        "version": 1,
        "built_at": "2026-01-01T00:00:00",
        "published": published,
        "sections": {
            "overview": {"fingerprint": "old-fp-overview", "cited_chunks": {"1": "h1"}, "cited_raw": {"note.txt": "oldsha"}},
            "details": {"fingerprint": "old-fp-details", "cited_chunks": {}, "cited_raw": {}},
        },
        "cited_chunks": {"1": "h1"},
        "cited_raw": {"note.txt": "oldsha"},
        "brain_snapshot": {},
        "raw_snapshot": {},
        "upstream_versions": {},
    }
    write_json(out_dir / "_src" / "state.json", state_before)

    raw_path = proj / "raw-replay" / "note.txt"
    raw_path.write_text("Some widget note.\n", encoding="utf-8")

    work_dir = config.work_dir / "m1"
    write_json(
        work_dir / "fingerprint.json",
        {
            "task": "m1",
            "sections": {
                "overview": {"fingerprint": "new-fp-overview"},
                "details": {"fingerprint": "new-fp-details"},
            },
        },
    )

    result = observe_task(config, "m1", inst, template, data["instances"], data["edges"])
    assert result["observed"] is True
    assert result["sections"] == ["details", "overview"]

    state_after = json.loads((out_dir / "_src" / "state.json").read_text(encoding="utf-8"))
    # Untouched: version, published, whole-document cited_*, per-section cited_*.
    assert state_after["version"] == 1
    assert state_after["published"] == published
    assert state_after["cited_chunks"] == {"1": "h1"}
    assert state_after["cited_raw"] == {"note.txt": "oldsha"}
    assert state_after["sections"]["overview"]["cited_chunks"] == {"1": "h1"}
    assert state_after["sections"]["overview"]["cited_raw"] == {"note.txt": "oldsha"}
    # Updated: fingerprints, snapshots, upstream_versions, last_checked.
    assert state_after["sections"]["overview"]["fingerprint"] == "new-fp-overview"
    assert state_after["sections"]["details"]["fingerprint"] == "new-fp-details"
    assert state_after["raw_snapshot"] == {"note.txt": sha256_file(raw_path)}
    assert state_after["brain_snapshot"] == {}
    assert state_after["upstream_versions"] == {}
    assert state_after["last_checked"].startswith("2026-01-16")


def test_observe_clears_raw_changed_from_next_plan_a_noop_used_to_repeat_forever(tmp_path, monkeypatch):
    """End-to-end reproduction of the replay defect: an unchanged raw file
    kept tripping `raw_changed` night after night because a noop run never
    refreshed `raw_snapshot`. After `observe`, the same unchanged file no
    longer makes the task due."""
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-16")
    db_path = fixture_brain_db(tmp_path / "knowledge.sqlite")
    proj = setup_mini_project(tmp_path, brain_db=db_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    template = data["templates"]["mini-profile"]

    out_dir = config.out_root / "m1"
    published = {"docx_sha256": "abc", "pdf_sha256": "def", "md_sha256": "ghi"}
    write_json(
        out_dir / "_src" / "state.json",
        {
            "version": 1,
            "built_at": "2026-01-01T00:00:00",
            "published": published,
            "sections": {
                "overview": {"fingerprint": "old-fp", "cited_chunks": {}, "cited_raw": {}},
                "details": {"fingerprint": "old-fp", "cited_chunks": {}, "cited_raw": {}},
            },
            "cited_chunks": {},
            "cited_raw": {},
            "brain_snapshot": {},
            "raw_snapshot": {},  # the bug precondition: never refreshed by a noop
            "upstream_versions": {},
        },
    )
    (proj / "raw-replay" / "note.txt").write_text("Some widget note, unchanged night over night.\n", encoding="utf-8")

    code, payload = _run_cli(proj, "plan", "--task", "m1")
    assert code == 0, payload
    assert "raw_changed" in payload["tasks"][0]["reasons"]

    work_dir = config.work_dir / "m1"
    write_json(
        work_dir / "fingerprint.json",
        {"task": "m1", "sections": {"overview": {"fingerprint": "fp1"}, "details": {"fingerprint": "fp2"}}},
    )
    observe_task(config, "m1", inst, template, data["instances"], data["edges"])

    state_after = json.loads((out_dir / "_src" / "state.json").read_text(encoding="utf-8"))
    assert state_after["version"] == 1  # a noop must never look like a publish
    assert state_after["published"] == published

    code, payload = _run_cli(proj, "plan", "--task", "m1")
    assert code == 0, payload
    assert "raw_changed" not in payload["tasks"][0]["reasons"]


def test_cmd_observe_cli_refuses_unknown_task(tmp_path):
    proj = setup_mini_project(tmp_path)
    code, payload = _run_cli(proj, "observe", "nope")
    assert code == 1
    assert "unknown task" in payload["reason"]


# --------------------------------------------------------------- check-file --

def _seed_state_with_cited_raw(out_dir: Path, section: str, path: str, sha: str) -> None:
    write_json(
        out_dir / "_src" / "state.json",
        {
            "version": 1,
            "built_at": "2026-01-01T00:00:00",
            "published": {"docx_sha256": "x", "pdf_sha256": "y", "md_sha256": "z"},
            "sections": {section: {"fingerprint": "fp", "cited_chunks": {}, "cited_raw": {path: sha}}},
        },
    )


def test_check_file_carried_claim_with_no_quote_passes(tmp_path):
    """Both matching paths: an id-matched carried claim and a
    normalized-text-matched carried claim (no id comment at all)."""
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    out_dir = config.out_root / "m1"
    work_dir = config.work_dir / "m1"

    raw_path = proj / "raw-replay" / "note.txt"
    raw_path.write_text("Widget adjustment recorded in Q1.\n", encoding="utf-8")
    sha = sha256_file(raw_path)
    _seed_state_with_cited_raw(out_dir, "overview", "note.txt", sha)

    base_body = (
        "Widget adjustment recorded in Q1. [FILE:note.txt#L1] <!-- c:aaaa1111 -->\n\n"
        "- A second carried fact, no id at all. [FILE:note.txt#L2] <!-- c:bbbb2222 -->\n"
    )
    write_text(
        work_dir / "base.md",
        f"## Overview {{#overview}}\n\n{base_body}\n## Details {{#details}}\n\nn/a\n",
    )

    # Draft: the id-matched claim keeps its id comment verbatim; the second
    # is carried by normalized-text equality with NO id comment written at
    # all (the agent-drafted form for a claim it copied without an id).
    write_text(
        work_dir / "sections" / "overview.md",
        "Widget adjustment recorded in Q1. [FILE:note.txt#L1] <!-- c:aaaa1111 -->\n\n"
        "- A second carried fact, no id at all. [FILE:note.txt#L2]\n",
    )
    # No evidence.json at all — carried claims need no fresh quote.

    inst = data["instances"]["m1"]
    result = check_file_task(config, "m1", inst)
    assert result["checked"] == 2
    assert result["passed"] == 2
    assert result["failed"] == []
    rewritten = (work_dir / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "Not modeled" not in rewritten


def test_check_file_reworded_claim_with_no_quote_fails(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    out_dir = config.out_root / "m1"
    work_dir = config.work_dir / "m1"

    raw_path = proj / "raw-replay" / "note.txt"
    raw_path.write_text("Widget adjustment recorded in Q1.\n", encoding="utf-8")
    sha = sha256_file(raw_path)
    _seed_state_with_cited_raw(out_dir, "overview", "note.txt", sha)

    write_text(
        work_dir / "base.md",
        "## Overview {#overview}\n\n"
        "Widget adjustment recorded in Q1. [FILE:note.txt#L1] <!-- c:aaaa1111 -->\n\n"
        "## Details {#details}\n\nn/a\n",
    )
    write_json(
        work_dir / "raw" / "manifest.json",
        [{"path": "note.txt", "sha256": sha, "md": "note.txt.md", "status": "ok", "reason": "ok"}],
    )
    write_text(work_dir / "raw" / "note.txt.md", "Widget adjustment recorded in Q1.\n")

    # Wording changed (Q1 -> Q2): normalized text no longer matches the base
    # claim, so this is NOT carried and needs its own quote — none supplied.
    write_text(
        work_dir / "sections" / "overview.md",
        "Widget adjustment recorded in Q2. [FILE:note.txt#L1]\n",
    )

    inst = data["instances"]["m1"]
    result = check_file_task(config, "m1", inst)
    assert result["checked"] == 1
    assert result["passed"] == 0
    assert result["failed"][0]["reason"] == "missing evidence quote"
    rewritten = (work_dir / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "Not modeled: missing evidence quote." in rewritten


def test_check_file_carried_claim_fails_when_raw_file_sha_changed(tmp_path):
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    out_dir = config.out_root / "m1"
    work_dir = config.work_dir / "m1"

    raw_path = proj / "raw-replay" / "note.txt"
    raw_path.write_text("Widget adjustment recorded in Q1.\n", encoding="utf-8")
    # state records the sha from BEFORE the file changed underneath it.
    _seed_state_with_cited_raw(out_dir, "overview", "note.txt", "stale-sha-does-not-match")

    write_text(
        work_dir / "base.md",
        "## Overview {#overview}\n\n"
        "Widget adjustment recorded in Q1. [FILE:note.txt#L1] <!-- c:aaaa1111 -->\n\n"
        "## Details {#details}\n\nn/a\n",
    )
    write_text(
        work_dir / "sections" / "overview.md",
        "Widget adjustment recorded in Q1. [FILE:note.txt#L1] <!-- c:aaaa1111 -->\n",
    )
    # No evidence.json — this claim is carried, so it would normally need
    # none; the point of this test is that the sha mismatch still fails it.

    inst = data["instances"]["m1"]
    result = check_file_task(config, "m1", inst)
    assert result["checked"] == 1
    assert result["passed"] == 0
    assert result["failed"][0]["reason"] == "cited raw file changed since publish: note.txt"
    rewritten = (work_dir / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "Not modeled: cited raw file changed since publish: note.txt." in rewritten
