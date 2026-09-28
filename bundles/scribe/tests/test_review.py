"""Task 12: propose mode (`publish.py`) + `review.py` (list/approve/reject).

`approve` must commit a proposed version through the SAME journaled publish
path task 9 built (`_src/.pending/journal.json`), never a second ad-hoc
writer — `test_approve_resumes_after_crash` proves a crash mid-`approve` is
resumable exactly like a crash mid-`auto`-publish. `test_approve_refuses_stale_proposal`
proves an intervening `auto` publish makes a leftover proposal unapprovable.
"""
import json

from scribe_fixtures import fixture_brain_db, setup_mini_project, load, doc, publish_seed, fake_evidence
from scribe_lib import brain as brain_mod, publish as publish_mod, review
from scribe_lib.config import read_state
from scribe_lib.publish import publish_task

DETAILS = "D. [RAG:2] <!-- c:bbbb0001 -->"


def _proposed(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"), publish="propose")
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}), 1, docx_sha=None)
    (config.work_dir / "m1").mkdir(parents=True, exist_ok=True)
    (config.work_dir / "m1" / "next.md").write_text(doc("m1", 2, "Mini m1", {"overview": "New. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}))
    r = publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)
    assert r["status"] == "pending"
    return config, data, inst, tpl


def test_propose_then_approve_publishes(tmp_path, monkeypatch):
    config, data, inst, tpl = _proposed(tmp_path, monkeypatch)
    [p] = review.list_pending(config)
    assert p["task"] == "m1" and p["version"] == 2 and "New." in p["diff"]
    assert read_state(config, inst)["version"] == 1
    assert review.approve(config, "m1")["status"] == "ok"
    assert read_state(config, inst)["version"] == 2 and review.list_pending(config) == []


def test_reject_discards_and_records_reason(tmp_path, monkeypatch):
    config, data, inst, tpl = _proposed(tmp_path, monkeypatch)
    review.reject(config, "m1", "wrong tone")
    assert review.list_pending(config) == [] and read_state(config, inst)["version"] == 1
    rows = json.loads(next((config.out_root / "_runs").glob("*.json")).read_text())
    assert {"task": "m1", "status": "rejected", "reason": "wrong tone"}.items() <= rows[-1].items()


def test_new_proposal_replaces_older_pending_one(tmp_path, monkeypatch):
    config, data, inst, tpl = _proposed(tmp_path, monkeypatch)
    [p1] = review.list_pending(config)
    assert p1["version"] == 2

    # A second draft for the same task, still un-approved v1 — proposing it
    # again (a stale v2 next.md would refuse; write a fresh v2 draft to
    # exercise "propose again before approving" honestly since propose never
    # advances state.json).
    (config.work_dir / "m1" / "next.md").write_text(
        doc("m1", 2, "Mini m1", {"overview": "Newer. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS})
    )
    r = publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)
    assert r["status"] == "pending"

    pending = review.list_pending(config)
    assert len(pending) == 1
    assert "Newer." in pending[0]["diff"]
    out_dir = config.out_root / inst["out"]
    assert sorted((out_dir / "_pending").iterdir()) == [out_dir / "_pending" / "v002"]


def test_approve_refuses_stale_proposal(tmp_path, monkeypatch):
    """An intervening auto publish (or observe) that advances state.json past
    what the proposal was built against must make `approve` refuse rather
    than silently commit a proposal whose base has moved."""
    config, data, inst, tpl = _proposed(tmp_path, monkeypatch)
    # Publish v2 directly through the (unrelated) auto path by hand-seeding
    # state.json to v2 — simulating "something else already published v2
    # while this proposal sat unreviewed".
    publish_seed(
        config, inst,
        doc("m1", 2, "Mini m1", {"overview": "Other. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
        2, docx_sha=None,
    )
    assert read_state(config, inst)["version"] == 2

    result = review.approve(config, "m1")
    assert result["status"] == "error"
    assert "stale" in result["reason"]
    # Refused, not silently applied — the pending proposal is left in place.
    assert review.list_pending(config) != []
    assert read_state(config, inst)["version"] == 2


def test_approve_resumes_after_crash(tmp_path, monkeypatch):
    """A crash inside `commit_fresh` (the same journaled path `auto`
    publish uses) during `approve` leaves a resumable journal — a retried
    `approve` call finishes it, exactly as a retried `publish` would for an
    `auto`-mode crash."""
    config, data, inst, tpl = _proposed(tmp_path, monkeypatch)

    real_write_state = publish_mod._write_state
    monkeypatch.setattr(
        publish_mod, "_write_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    try:
        review.approve(config, "m1")
        assert False, "expected the monkeypatched crash to propagate"
    except RuntimeError:
        pass
    monkeypatch.setattr(publish_mod, "_write_state", real_write_state)

    # state.json still at v1; a journal is left behind for the retry.
    assert read_state(config, inst)["version"] == 1
    out_dir = config.out_root / inst["out"]
    assert (out_dir / "_src" / ".pending" / "journal.json").is_file()

    result = review.approve(config, "m1")
    assert result["status"] == "ok"
    assert read_state(config, inst)["version"] == 2
    assert not (out_dir / "_src" / ".pending").exists()
    # The retry resumed the journal directly (never re-reading a possibly
    # different pending proposal) and the original proposal dir is gone too.
    assert review.list_pending(config) == []
