"""Task 12: propose mode (`publish.py`) + `review.py` (list/approve/reject).

`approve` must commit a proposed version through the SAME journaled publish
path task 9 built (`_src/.pending/journal.json`), never a second ad-hoc
writer — `test_approve_resumes_after_crash` proves a crash mid-`approve` is
resumable exactly like a crash mid-`auto`-publish. `test_approve_refuses_stale_proposal`
proves an intervening `auto` publish makes a leftover proposal unapprovable.
"""
import contextlib
import io
import json

from scribe_fixtures import fixture_brain_db, setup_mini_project, load, doc, publish_seed, fake_evidence, write_json
from scribe_lib import brain as brain_mod, publish as publish_mod, review
from scribe_lib.config import read_state
from scribe_lib.publish import publish_task

import scribe

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


def test_resumed_journal_uses_its_own_recorded_no_render(tmp_path, monkeypatch):
    """A journal must carry its own `no_render` (task 12) — a resume call
    that passes a DIFFERENT `no_render` than the journal's original commit
    used (exactly what `review.approve` does, since it cannot know in
    advance what a leftover journal was originally rendered with) must
    still behave as the ORIGINAL commit intended, not try to move a
    docx/pdf that was never staged."""
    from scribe_lib.merge import parse_header

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    publish_seed(
        config, inst,
        doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
        1, docx_sha=None,
    )

    out_dir = config.out_root / inst["out"]
    src_dir = out_dir / "_src"
    state = read_state(config, inst)
    next_text = doc("m1", 2, "Mini m1", {"overview": "New. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS})
    header = parse_header(next_text)

    real_mark_step = publish_mod._mark_step

    def crash_before_place_new(pending_dir, journal, step):
        if step == "place_new":
            raise RuntimeError("crash before place_new mark")
        return real_mark_step(pending_dir, journal, step)

    monkeypatch.setattr(publish_mod, "_mark_step", crash_before_place_new)
    try:
        publish_mod.commit_fresh(
            config, "m1", inst, tpl, data["instances"], data["edges"], out_dir, src_dir, state, 2,
            next_text, header, out_dir / "nonexistent.docx", out_dir / "nonexistent.pdf",
            True,  # this commit's own no_render — never staged a docx/pdf
        )
        assert False, "expected the monkeypatched crash to propagate"
    except RuntimeError:
        pass
    monkeypatch.setattr(publish_mod, "_mark_step", real_mark_step)

    journal = json.loads((src_dir / ".pending" / "journal.json").read_text(encoding="utf-8"))
    assert journal["no_render"] is True and "place_new" not in journal["steps_done"]

    # Resume with a MISMATCHED no_render=False (what `review.approve`
    # always passes to `resume_leftover_journal`, since it cannot know the
    # leftover journal's own history) — the journal's recorded True must
    # win, so `place_new` never tries to move a docx/pdf that was never
    # staged.
    result = publish_mod.resume_leftover_journal(
        config, "m1", inst, tpl, data["instances"], data["edges"], out_dir, src_dir, state, no_render=False
    )
    assert result is not None and result["status"] == "ok"
    assert read_state(config, inst)["version"] == 2
    assert not (out_dir / "Mini m1.docx").exists()
    assert not (src_dir / ".pending").exists()


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


# ------------------------------------------------------------- fix round 1 --


def test_cli_publish_pending_exits_0_and_records_pending_row(tmp_path, monkeypatch):
    """Review fix round 1, Critical #1: `scribe.py publish` for a
    propose-mode task must exit 0, not 1 — run/SKILL.md step 7.4 already
    reads `"published": false` as "this task is done, skip lineage/index",
    a normal successful outcome. Before the fix, `cmd_publish` exited 1 for
    anything but `"ok"`, so every propose task in an unattended run was
    recorded as failed and its dependents starved as `upstream_failed`."""
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"), publish="propose")
    config, data = load(proj)
    inst = data["instances"]["m1"]
    publish_seed(
        config, inst,
        doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
        1, docx_sha=None,
    )
    (config.work_dir / "m1").mkdir(parents=True, exist_ok=True)
    (config.work_dir / "m1" / "next.md").write_text(
        doc("m1", 2, "Mini m1", {"overview": "New. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS})
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = scribe.main(["--project", str(proj), "publish", "m1", "--no-render"])
    assert code == 0
    out = json.loads(buf.getvalue().strip())
    assert out["status"] == "pending" and out["published"] is False

    rows = json.loads(next((config.out_root / "_runs").glob("*.json")).read_text(encoding="utf-8"))
    assert rows[-1]["task"] == "m1" and rows[-1]["status"] == "pending"


def test_reject_with_no_pending_proposal_refuses(tmp_path, monkeypatch):
    """Review fix round 1, Minor: rejecting a task with nothing pending
    (mistyped id, or already approved/rejected) must not write a false
    'rejected' audit row."""
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)

    result = review.reject(config, "m1", "nothing to reject")
    assert result["status"] == "error"

    runs_dir = config.out_root / "_runs"
    if runs_dir.is_dir():
        for path in runs_dir.glob("*.json"):
            rows = json.loads(path.read_text(encoding="utf-8"))
            assert all(row.get("status") != "rejected" for row in rows)


def test_list_pending_flags_a_stale_proposal(tmp_path, monkeypatch):
    """Review fix round 1, Minor: `list_pending` must flag a proposal made
    stale by an intervening publish, so the user learns it before asking to
    approve rather than only from `approve`'s refusal."""
    config, data, inst, tpl = _proposed(tmp_path, monkeypatch)
    [p] = review.list_pending(config)
    assert p["stale"] is False

    # An intervening auto publish moves state.json past v1 -> v2, the same
    # version the pending proposal targets.
    publish_seed(
        config, inst,
        doc("m1", 2, "Mini m1", {"overview": "Other. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
        2, docx_sha=None,
    )
    [p2] = review.list_pending(config)
    assert p2["stale"] is True


def test_approve_builds_state_from_the_staged_proposal_not_live_work(tmp_path, monkeypatch):
    """Review fix round 1, Important #2: `approve` must build `state.json`
    (fingerprints, tombstones, merge counts) from what was staged AT
    PROPOSE TIME, never from `work/<task>/` as it stands at approve time —
    a proposal can sit through a later `prepare` that overwrites
    `fingerprint.json`/`base.json`/`merge.json`."""
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"), publish="propose")
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    publish_seed(
        config, inst,
        doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
        1, docx_sha=None,
    )

    work_dir = config.work_dir / "m1"
    work_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        work_dir / "fingerprint.json",
        {"sections": {"overview": {"fingerprint": "fp-at-propose"}, "details": {"fingerprint": "fp2-at-propose"}}},
    )
    write_json(work_dir / "merge.json", {"sections": {"overview": {"kept": 1}}})
    (work_dir / "next.md").write_text(
        doc("m1", 2, "Mini m1", {"overview": "New. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS})
    )

    r = publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)
    assert r["status"] == "pending"

    pending_dir = config.out_root / inst["out"] / "_pending" / "v002"
    staged_state = json.loads((pending_dir / "state.json").read_text(encoding="utf-8"))
    assert staged_state["sections"]["overview"]["fingerprint"] == "fp-at-propose"
    staged_merge = json.loads((pending_dir / "pending.json").read_text(encoding="utf-8"))["merge"]
    assert staged_merge == {"overview": {"kept": 1}}

    # A later `prepare` overwrites work/ with DIFFERENT content while the
    # proposal sits unreviewed.
    write_json(
        work_dir / "fingerprint.json",
        {"sections": {"overview": {"fingerprint": "fp-at-approve-DIFFERENT"}, "details": {"fingerprint": "x"}}},
    )
    write_json(work_dir / "merge.json", {"sections": {"overview": {"kept": 999}}})
    write_json(
        work_dir / "base.json",
        {"human_deleted": [{"section": "overview", "normalized": "a ghost tombstone never in the proposal"}]},
    )

    result = review.approve(config, "m1")
    assert result["status"] == "ok"

    published_state = read_state(config, inst)
    assert published_state["sections"]["overview"]["fingerprint"] == "fp-at-propose"
    assert published_state["human_deleted"] == staged_state["human_deleted"]

    rows = json.loads(next((config.out_root / "_runs").glob("*.json")).read_text(encoding="utf-8"))
    published_row = next(row for row in rows if row["status"] == "published")
    assert published_row["merge"] == {"overview": {"kept": 1}}


def test_approve_builds_lineage_and_index_for_the_approved_version(tmp_path, monkeypatch):
    """Final-review I5: propose-mode versions never got lineage, so `index --query`
    silently missed every claim of every propose-mode task. Approve builds it."""
    from scribe_lib.index import query_index

    config, data, inst, tpl = _proposed(tmp_path, monkeypatch)
    src = config.out_root / inst["out"] / "_src"
    r = review.approve(config, "m1")
    assert r["status"] == "ok" and "lineage_error" not in r, r
    assert (src / "v002.lineage.json").is_file() and (src / "v002.sources.json").is_file()
    lineage = json.loads((src / "v002.lineage.json").read_text())
    doc_ids = {n["doc_id"] for n in lineage["nodes"] if n["type"] == "chunk" and n.get("doc_id")}
    assert doc_ids
    hits = [e for d in doc_ids for e in query_index(config, d)]
    assert any(e["task"] == "m1" and e["version"] == 2 for e in hits), hits


def test_proposal_freezes_the_lineage_inputs_it_was_built_from(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"), publish="propose")
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}), 1, docx_sha=None)
    work = config.work_dir / "m1"
    write_json(work / "fingerprint.json", {"sections": {"overview": {"fingerprint": "night1"}}})
    write_json(work / "base.json", {"base_version": 1, "base_edited": False, "human_added": [], "human_modified": []})
    (work / "next.md").write_text(doc("m1", 2, "Mini m1", {"overview": "New. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}))
    assert publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)["status"] == "pending"
    pending = config.out_root / "m1" / "_pending" / "v002"
    assert json.loads((pending / "fingerprint.json").read_text())["sections"]["overview"]["fingerprint"] == "night1"
    assert (pending / "base.json").is_file()
    write_json(work / "fingerprint.json", {"sections": {"overview": {"fingerprint": "night2"}}})
    assert review.approve(config, "m1")["status"] == "ok"
