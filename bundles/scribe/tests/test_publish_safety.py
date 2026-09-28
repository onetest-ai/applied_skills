"""Task 9 (A10; PoC m1, m2, m3, m5): journaled atomic publish, a locked-docx
failure mode, an offline raw root that is never read as deletion, daily
cadence from `last_checked`, `<word>` survival in render, and non-ok evidence
recorded as null.

Field-name adaptation from the task-9 brief: the brief's
`test_daily_cadence_uses_last_checked` reads each `plan["tasks"]` entry by
`t["task"]`, but `scribe.compute_plan`'s entries (unchanged from `cmd_plan`,
which every other plan test already pins — see `test_config_plan.py`) key
each task by `"id"`, not `"task"`. Adapted to `t["id"]` here rather than
renaming the real field and breaking every other plan test.
"""
import json
import shutil
import pytest
from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed, fake_evidence)
from scribe_lib import brain as brain_mod, publish as publish_mod
from scribe_lib.config import sha256_file, read_state
from scribe_lib.fingerprint import fingerprint_task
from scribe_lib.render import render_task

DETAILS = "D. [RAG:2] <!-- c:bbbb0001 -->"


def _ready(tmp_path, monkeypatch, overview="New text. [RAG:1] <!-- c:aaaa0001 -->"):
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    out = config.out_root / "m1"; out.mkdir(parents=True)
    (out / "Mini m1.docx").write_bytes(b"v1-docx"); (out / "Mini m1.pdf").write_bytes(b"v1-pdf")
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
                 1, docx_sha=sha256_file(out / "Mini m1.docx"))
    render = config.work_dir / "m1" / "render"; render.mkdir(parents=True)
    (render / "Mini m1.docx").write_bytes(b"v2-docx"); (render / "Mini m1.pdf").write_bytes(b"v2-pdf")
    (render / "render.json").write_text(json.dumps({"ok": True, "docx": str(render / "Mini m1.docx"),
                                                      "pdf": str(render / "Mini m1.pdf"), "diagrams": [], "errors": []}))
    (config.work_dir / "m1" / "next.md").write_text(doc("m1", 2, "Mini m1", {"overview": overview, "details": DETAILS}))
    return config, data, inst, tpl, out


def test_crash_mid_publish_resumes_without_losing_the_previous_version(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch)
    real = publish_mod._write_state
    monkeypatch.setattr(publish_mod, "_write_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    monkeypatch.setattr(publish_mod, "_write_state", real)
    r = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r["status"] == "ok"
    archived = next((out / "_versions").glob("v001_*.docx"))
    assert archived.read_bytes() == b"v1-docx" and (out / "Mini m1.docx").read_bytes() == b"v2-docx"
    assert read_state(config, inst)["version"] == 2 and not (out / "_src" / ".pending").exists()


def test_publish_locked_docx_fails_cleanly_and_retry_completes(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch)
    real_replace = publish_mod._move
    def locked(src, dst):
        if str(src).endswith("Mini m1.docx") and "_versions" in str(dst):
            raise PermissionError(f"locked: {src}")
        return real_replace(src, dst)
    monkeypatch.setattr(publish_mod, "_move", locked)
    r = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r["status"] == "failed" and r["reason"].startswith("locked:")
    assert (out / "Mini m1.docx").read_bytes() == b"v1-docx" and read_state(config, inst)["version"] == 1
    monkeypatch.setattr(publish_mod, "_move", real_replace)
    assert publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)["status"] == "ok"


def test_non_ok_evidence_is_recorded_as_null(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch, overview="New. [RAG:9] <!-- c:aaaa0001 -->")
    monkeypatch.setattr(brain_mod, "evidence", lambda cfg, cid: {"status": "not_modeled", "chunk_id": str(cid)})
    publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert read_state(config, inst)["sections"]["overview"]["cited_chunks"] == {"9": None}


@pytest.mark.skipif(not (shutil.which("pandoc") and shutil.which("soffice")), reason="pandoc/soffice missing")
def test_angle_bracket_words_survive_render(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    (config.work_dir / "m1").mkdir(parents=True)
    (config.work_dir / "m1" / "next.md").write_text(doc("m1", 1, "Mini m1", {
        "overview": "Agents greet <Customer Name> first. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}))
    r = render_task(config, "m1", data["instances"]["m1"], data["templates"]["mini-profile"])
    # Adapted from the brief: `render_task`'s result carries the docx's
    # `.name` (relative), not a full path — join it onto the render dir
    # rather than passing a bare filename to a subprocess run from an
    # unrelated cwd.
    docx_path = config.work_dir / "m1" / "render" / r["docx"]
    text = subprocess.run(["pandoc", str(docx_path), "-t", "plain"], capture_output=True, text=True).stdout
    assert "<Customer Name>" in text


def test_unavailable_raw_root_is_not_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst = data["instances"]["m1"]
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": "A. [FILE:a.md#p1] <!-- c:aaaa0001 -->", "details": DETAILS}),
                 1, docx_sha=None, cited_raw={"a.md": "sha"}, extra_state={"raw_snapshot": {"a.md": "sha"}})
    shutil.rmtree(config.raw_root)   # the synced folder is offline
    fp = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    assert "raw_file_removed" not in fp["sections"]["overview"]["stale_reasons"]
    assert "raw_root_unavailable" in fp["notes"]
    assert read_state(config, inst)["raw_snapshot"] == {"a.md": "sha"}


def test_daily_cadence_uses_last_checked(tmp_path, monkeypatch):
    from scribe_fixtures import mini_task, make_project, write_text, MINI_TEMPLATE
    import scribe
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    write_text(proj / "templates" / "mini-profile.tmpl.md", MINI_TEMPLATE)
    mini_task(proj, "m1", cadence="daily")
    config, data = load(proj)
    publish_seed(config, data["instances"]["m1"], doc("m1", 1, "Mini m1", {"overview": "A. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
                 1, docx_sha=None, extra_state={"built_at": "2026-01-01T00:00:00", "last_checked": "2026-01-05T06:00:00"})
    plan = scribe.compute_plan(config, data)
    assert next(t for t in plan["tasks"] if t["id"] == "m1")["due"] is False
