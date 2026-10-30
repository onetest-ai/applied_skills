"""Store snapshot: copy the store up to (exclusive) a given evidence item for a re-extraction run.
Invented data only (public repo).

1. Events at or after the cut point are excluded from the copy.
2. Plan events (evidence=None, by=plan) are always kept.
3. Human events dated strictly before the cut date are kept regardless of their evidence reference.
4. The source store is never written to.
5. Items at or after the cut point (kind != message) appear in `pending` of the result.
"""
import hashlib
import json
import textwrap

from taska_lib import store as S


# ------ shared test fixture helpers ------

EV1 = "2026-03-01_src_aaaaaaaa"
EV2 = "2026-03-02_src_bbbbbbbb"   # the cut point ("before")
EV3 = "2026-03-03_src_cccccccc"


def _ev(eid, date, kind="meeting"):
    """Minimal evidence item with enough turns to avoid the duplicate detector."""
    return {
        "id": eid, "kind": kind, "at": f"{date}T10:00:00",
        "title": f"Harbor daily sync {date}", "source": "src",
        "turns": [{"speaker": "Nora Quill", "ts": "00:01:00",
                   "text": f"Unique logistics update for {date}: reviewing the depot handover plan."}],
    }


def _store(tmp_path):
    """A store with three evidence items and a variety of event types."""
    store = tmp_path / ".taska"
    S.ensure(store)
    S.write_evidence(store, _ev(EV1, "2026-03-01"))
    S.write_evidence(store, _ev(EV2, "2026-03-02"))
    S.write_evidence(store, _ev(EV3, "2026-03-03"))

    S.append(store, [
        # plan event — always kept (no evidence reference)
        {"type": "create", "mid": "plan1", "id": "E01", "kind": "epic", "by": "plan",
         "evidence": None, "date": "2026-02-01", "fields": {"title": "Alpha"}},
        # agent event before the cut — kept
        {"type": "agent", "mid": "a1", "op": "note", "node": "E01", "by": "agent",
         "evidence": EV1, "date": "2026-03-01"},
        # verdict for the above — kept (mid belongs to a kept event)
        {"type": "verdict", "mid": "a1", "verdict": "supports"},
        # agent event AT the cut — dropped
        {"type": "agent", "mid": "a2", "op": "note", "node": "E01", "by": "agent",
         "evidence": EV2, "date": "2026-03-02"},
        # verdict for the above — dropped
        {"type": "verdict", "mid": "a2", "verdict": "supports"},
        # agent event AFTER the cut — dropped
        {"type": "agent", "mid": "a3", "op": "note", "node": "E01", "by": "agent",
         "evidence": EV3, "date": "2026-03-03"},
        # processed marker for EV1 — kept (evidence is in before_set)
        {"type": "processed", "evidence": EV1, "by": "agent", "at": "2026-03-01T12:00:00"},
        # human event strictly before the cut date — kept
        {"type": "human", "op": "edit", "mid": "h1", "by": "human", "node": "E01",
         "evidence": EV1, "date": "2026-03-01", "at": "2026-03-01T11:00:00",
         "set": {"title": "Alpha revised"}},
        # human event ON the cut date — dropped (not strictly before)
        {"type": "human", "op": "edit", "mid": "h2", "by": "human", "node": "E01",
         "evidence": EV2, "date": "2026-03-02", "at": "2026-03-02T11:00:00",
         "set": {"title": "Alpha revised again"}},
    ])
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
        side = "internal"
    """))
    return store


def test_snapshot_drops_events_from_and_after_the_cut(tmp_path):
    from taska_lib.snapshot import snapshot
    store = _store(tmp_path)
    out = tmp_path / "snap"
    r = snapshot(store, EV2, out)
    copy_evs = [json.loads(l) for l in (out / ".taska" / "events.jsonl").open()]
    mids = {e.get("mid") for e in copy_evs}
    # events at or after the cut must be absent
    assert "a2" not in mids, "event referencing the cut item must be dropped"
    assert "a3" not in mids, "event referencing a post-cut item must be dropped"
    # events before the cut must be present
    assert "a1" in mids, "event referencing a pre-cut item must be kept"
    assert r["events"] == len(copy_evs)


def test_snapshot_keeps_plan_events(tmp_path):
    from taska_lib.snapshot import snapshot
    store = _store(tmp_path)
    snapshot(store, EV2, tmp_path / "snap")
    copy_evs = [json.loads(l) for l in (tmp_path / "snap" / ".taska" / "events.jsonl").open()]
    plan = [e for e in copy_evs if e.get("by") == "plan"]
    assert len(plan) == 1
    assert plan[0]["mid"] == "plan1"


def test_snapshot_keeps_earlier_human_edits(tmp_path):
    from taska_lib.snapshot import snapshot
    store = _store(tmp_path)
    snapshot(store, EV2, tmp_path / "snap")
    copy_evs = [json.loads(l) for l in (tmp_path / "snap" / ".taska" / "events.jsonl").open()]
    human_mids = {e["mid"] for e in copy_evs if e.get("by") == "human"}
    assert "h1" in human_mids, "human edit dated before the cut must be kept"
    assert "h2" not in human_mids, "human edit dated on the cut must be dropped"


def test_snapshot_never_touches_the_source(tmp_path):
    from taska_lib.snapshot import snapshot
    store = _store(tmp_path)
    src_jsonl = store / "events.jsonl"
    sha_before = hashlib.sha256(src_jsonl.read_bytes()).hexdigest()
    snapshot(store, EV2, tmp_path / "snap")
    sha_after = hashlib.sha256(src_jsonl.read_bytes()).hexdigest()
    assert sha_before == sha_after, "snapshot must never write to the source events.jsonl"


def test_cut_items_are_pending_in_the_copy(tmp_path):
    from taska_lib.snapshot import snapshot
    store = _store(tmp_path)
    r = snapshot(store, EV2, tmp_path / "snap")
    # EV2 (the cut itself) and EV3 (after it) must be pending
    assert EV2 in r["pending"], "the cut item must be pending in the copy"
    assert EV3 in r["pending"], "items after the cut must be pending in the copy"
    # EV1 is before the cut and processed, so must not be pending
    assert EV1 not in r["pending"], "pre-cut processed item must not be pending"


def test_snapshot_keeps_work_read_from_a_copy_of_an_earlier_meeting(tmp_path):
    # a re-synced copy of a pre-cut meeting is not in the order, but what was read from it belongs to that meeting
    from taska_lib.snapshot import snapshot
    store = _store(tmp_path)
    cp = "2026-03-04_src_dddddddd"
    S.P(store, "evidence", cp + ".json").write_text(json.dumps(dict(_ev(cp, "2026-03-04"), duplicate_of=EV1)))
    S.append(store, [{"type": "agent", "mid": "a9", "op": "note", "node": "E01", "by": "agent", "evidence": cp,
                      "date": "2026-03-04"}])
    snapshot(store, EV2, tmp_path / "snap")
    mids = {json.loads(l).get("mid") for l in (tmp_path / "snap" / ".taska" / "events.jsonl").open()}
    assert "a9" in mids
