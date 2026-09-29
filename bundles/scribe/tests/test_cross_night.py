"""Final-review I1/I2: what happens to the published docx BETWEEN two nights.

I1 — a person's edit to the live docx is never archived away: not by a journal resume
(a locked docx failed last night's publish, then the person saved edits), and not by
`review approve` (a reviewer fixed something in place, then approved). A resumed
journal writes the fingerprints frozen at stage time, never a later prepare's.

I2 — a Word re-save without edits is recorded (`docx_seen_sha256`) so the task is not
`base_edited`-due every night forever.
"""
import json
import shutil
import subprocess

import pytest
from scribe_fixtures import (
    doc, fake_evidence, fixture_brain_db, load, publish_seed, setup_mini_project, write_json,
)
from scribe_lib import brain as brain_mod, publish as publish_mod, review
from scribe_lib.basedoc import base_task
from scribe_lib.config import read_state, sha256_file
from scribe_lib.observe import observe_task
from scribe_lib.publish import publish_task
from scribe_lib.render import render_task

import scribe

DETAILS = "D. [RAG:2] <!-- c:bbbb0001 -->"


def _ready(tmp_path, monkeypatch, publish="auto"):
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"), publish=publish)
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    out = config.out_root / "m1"
    out.mkdir(parents=True)
    (out / "Mini m1.docx").write_bytes(b"v1-docx")
    (out / "Mini m1.pdf").write_bytes(b"v1-pdf")
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
                 1, docx_sha=sha256_file(out / "Mini m1.docx"))
    work = config.work_dir / "m1"
    render = work / "render"
    render.mkdir(parents=True)
    (render / "Mini m1.docx").write_bytes(b"v2-docx")
    (render / "Mini m1.pdf").write_bytes(b"v2-pdf")
    write_json(work / "fingerprint.json", {"sections": {"overview": {"fingerprint": "fp-night1-overview"},
                                                        "details": {"fingerprint": "fp-night1-details"}}})
    (work / "next.md").write_text(doc("m1", 2, "Mini m1", {"overview": "New. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}))
    return config, data, inst, tpl, out


def _lock_archive(monkeypatch):
    real = publish_mod._move

    def locked(src, dst):
        if str(src).endswith("Mini m1.docx") and "_versions" in str(dst):
            raise PermissionError(f"locked: {src}")
        return real(src, dst)

    monkeypatch.setattr(publish_mod, "_move", locked)
    return real


def _rows(config):
    return json.loads(next((config.out_root / "_runs").glob("*.json")).read_text())


def test_resume_refuses_when_a_person_edited_the_docx_after_a_locked_publish(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch)
    real = _lock_archive(monkeypatch)
    r = publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r["status"] == "failed" and r["reason"].startswith("locked:")
    monkeypatch.setattr(publish_mod, "_move", real)

    # Between nights: the person who had it open saves edits into the live docx.
    (out / "Mini m1.docx").write_bytes(b"v1-docx + a human edit")

    r2 = publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r2["status"] == "failed" and r2["reason"] == "human_edit_during_publish"
    assert (out / "Mini m1.docx").read_bytes() == b"v1-docx + a human edit"
    assert not (out / "_versions").exists() or not list((out / "_versions").iterdir())
    assert not (out / "_src" / ".pending").exists()
    assert read_state(config, inst)["version"] == 1
    assert "human_edit_during_publish" in _rows(config)[-1]["reasons"]
    # ...and the next plan still sees the edit, so the next prepare drafts from it.
    task = next(t for t in scribe.compute_plan(config, data)["tasks"] if t["id"] == "m1")
    assert "base_edited" in task["reasons"]


def test_resume_after_a_later_prepare_writes_the_journaled_fingerprints(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch)
    real = publish_mod._write_state
    monkeypatch.setattr(publish_mod, "_write_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    monkeypatch.setattr(publish_mod, "_write_state", real)

    # Night 2's prepare overwrites work/m1/fingerprint.json before publish resumes.
    write_json(config.work_dir / "m1" / "fingerprint.json",
               {"sections": {"overview": {"fingerprint": "fp-night2-overview"},
                             "details": {"fingerprint": "fp-night2-details"}}})
    r = publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r["status"] == "ok" and r["version"] == 2
    state = read_state(config, inst)
    assert state["sections"]["overview"]["fingerprint"] == "fp-night1-overview"
    assert state["sections"]["details"]["fingerprint"] == "fp-night1-details"
    assert state["published"]["docx_sha256"] == sha256_file(out / "Mini m1.docx")


def test_approve_refuses_when_the_live_docx_was_edited_since_the_proposal(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch, publish="propose")
    assert publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)["status"] == "pending"

    (out / "Mini m1.docx").write_bytes(b"v1-docx + reviewer fix")

    [p] = review.list_pending(config)
    assert p["stale"] is True and p["stale_reason"] == "human_edit_since_proposal"
    r = review.approve(config, "m1")
    assert r["status"] == "error" and r["reason"] == "human_edit_since_proposal"
    assert (out / "Mini m1.docx").read_bytes() == b"v1-docx + reviewer fix"
    assert read_state(config, inst)["version"] == 1
    assert (out / "_pending" / "v002").is_dir()
    assert json.loads((out / "_pending" / "v002" / "pending.json").read_text())["stale_reason"] == "human_edit_since_proposal"
    assert "human_edit_since_proposal" in _rows(config)[-1]["reasons"]


def test_approve_still_commits_when_the_live_docx_is_untouched(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch, publish="propose")
    publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert review.approve(config, "m1")["status"] == "ok"
    assert (out / "Mini m1.docx").read_bytes() == b"v2-docx"
    assert next((out / "_versions").glob("v001_*.docx")).read_bytes() == b"v1-docx"


@pytest.mark.skipif(not (shutil.which("pandoc") and shutil.which("soffice")), reason="pandoc/soffice missing")
def test_word_resave_without_edits_is_not_due_the_next_night(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    v1 = doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS})
    (config.work_dir / "m1").mkdir(parents=True)
    (config.work_dir / "m1" / "next.md").write_text(v1)
    rendered = render_task(config, "m1", inst, tpl)
    stable = config.out_root / "m1" / "Mini m1.docx"
    stable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(config.work_dir / "m1" / "render" / rendered["docx"], stable)
    publish_seed(config, inst, v1, 1, docx_sha=sha256_file(stable),
                 extra_state={"brain_snapshot": {}, "raw_snapshot": {}, "upstream_versions": {}})
    subprocess.run(["pandoc", str(stable), "-o", str(stable)], check=True)   # open + save, no edits

    def reasons():
        return next(t for t in scribe.compute_plan(config, data)["tasks"] if t["id"] == "m1")["reasons"]

    assert "base_edited" in reasons()
    # The night's noop pipeline: base reads the re-save as unedited, observe settles it.
    assert base_task(config, "m1", inst, tpl)["base_edited"] is False
    observe_task(config, "m1", inst, tpl, data["instances"], data["edges"])
    state = read_state(config, inst)
    assert state["docx_seen_sha256"] == sha256_file(stable)
    assert state["published"]["docx_sha256"] != sha256_file(stable)   # observe never touches published
    assert "base_edited" not in reasons()

    # A real edit after that is still an edit.
    stable.write_bytes(stable.read_bytes() + b"\0")
    assert "base_edited" in reasons()
