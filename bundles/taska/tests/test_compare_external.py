"""Echo comparison tests. Invented data only (Harbor Logistics world).

Tests compare_external.compare_external using invented Echo-format data under tmp_path.
Never reads or writes ~/Echo or any real client data.
"""
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from taska_lib import config as C, ledger as L, store as S
from conftest import run_cli

# ── fixtures ──────────────────────────────────────────────────────────────────

MTG_ID = "2026-03-02_meetings_ab12cd34"
MTG_AT = "2026-03-02T10:00:00"
MTG_TITLE = "Harbor Logistics Daily Sync"

# Transcript long enough to align against with shingle matching.
# The two quoted passages (A and B) are separated by > 400 chars of filler so the
# match_item offset-within-400 check cannot bridge them when only one event exists.
_FILLER = ("the cargo manifests need to be updated in the warehouse management system every afternoon " * 6)
TRANSCRIPT = (
    "nora will prepare the onboarding checklist and share it with the client team before friday "
    + _FILLER
    + "ravi needs to book the warehouse site visit and confirm the date with opal by wednesday "
    "opal mentioned the data migration must be completed before the system goes live next month "
    "the team agreed to hold a review session to discuss the progress and blockers on thursday"
)
# Quotes used in tests
QUOTE_A = "nora will prepare the onboarding checklist and share it with the client team before friday"
QUOTE_B = "ravi needs to book the warehouse site visit and confirm the date with opal by wednesday"


def _cfg(tmp_path):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        timezone = "UTC"
        [[people]]
        name = "Nora Quill"
        [[people]]
        name = "Ravi Tamsin"
        [[people]]
        name = "Opal Brennick"
        side = "client"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    return cfg


def _meeting(cfg, ev_id=MTG_ID, at=MTG_AT, title=MTG_TITLE, transcript=TRANSCRIPT):
    turns = [{"speaker": "Nora Quill", "ts": "00:00:01", "text": transcript}]
    S.write_evidence(cfg.store, {
        "id": ev_id, "kind": "meeting", "at": at, "title": title,
        "source": "meetings", "text": "", "turns": turns,
    })
    return ev_id


def _task_event(cfg, ev_id, node_id, quote):
    """Add an agent task-create event to the store."""
    S.append(cfg.store, [{
        "type": "create", "mid": f"m_{node_id}", "id": node_id,
        "kind": "task", "by": "agent", "evidence": ev_id,
        "date": ev_id[:10], "quote": quote, "quote_norm": quote,
        "fields": {"title": f"Task {node_id}", "parent": None, "type": "action",
                   "internal_owner": None, "client_owner": None, "client_owed": False,
                   "ac": [], "due": None, "due_text": None, "plan": None},
    }])


def _decision_event(cfg, ev_id, node_id, quote):
    """Add an agent decision-create event to the store."""
    S.append(cfg.store, [{
        "type": "create", "mid": f"m_{node_id}", "id": node_id,
        "kind": "decision", "by": "agent", "evidence": ev_id,
        "date": ev_id[:10], "quote": quote, "quote_norm": quote,
        "fields": {"title": f"Decision {node_id}", "parent": None, "type": None,
                   "internal_owner": None, "client_owner": None, "client_owed": False,
                   "ac": [], "due": None, "due_text": None, "plan": None},
    }])


def _ext_meeting(at=MTG_AT, title=MTG_TITLE, actions=None, decisions=None):
    return {
        "meeting_at": at,
        "title": title,
        "actions": actions or [],
        "decisions": decisions or [],
    }


def _action(task, quote, owner=None, t=60):
    return {"task": task, "owner": owner, "due": None, "quote": quote, "t": t, "status": "open"}


# ── tests ─────────────────────────────────────────────────────────────────────

def test_counts_per_meeting(tmp_path):
    """Two Echo actions against one taska task → correct echo_actions and taska_items counts."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    ev_id = _meeting(cfg)
    _task_event(cfg, ev_id, "T001", QUOTE_A)

    ext = [_ext_meeting(actions=[
        _action("Prepare onboarding checklist", QUOTE_A),
        _action("Book warehouse visit", QUOTE_B),
    ])]

    result = compare_external(cfg.store, ext, None, "UTC")
    assert len(result["meetings"]) == 1
    m = result["meetings"][0]
    assert m["echo_actions"] == 2
    assert m["taska_items"] == 1
    assert m["gold_items"] is None
    assert m["taska_id"] == ev_id


def test_action_aligned_to_transcript_span(tmp_path):
    """An Echo action whose quote appears verbatim in the transcript gets a non-null span."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    ev_id = _meeting(cfg)
    # No task events → nothing tracked, any aligned action is a gap candidate
    ext = [_ext_meeting(actions=[_action("Prepare checklist", QUOTE_A)])]

    result = compare_external(cfg.store, ext, None, "UTC")
    m = result["meetings"][0]
    # Action has a span → goes to gap_candidates (nothing tracked it)
    assert len(m["gap_candidates"]) == 1
    assert m["gap_candidates"][0]["span"] is not None
    assert m["tracked"] == 0


def test_untracked_action_is_a_gap_candidate(tmp_path):
    """An Echo action with a span but no matching taska event → listed in gap_candidates."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    ev_id = _meeting(cfg)
    ext = [_ext_meeting(actions=[_action("Book warehouse visit", QUOTE_B, owner="Ravi")])]

    result = compare_external(cfg.store, ext, None, "UTC")
    m = result["meetings"][0]
    assert len(m["gap_candidates"]) == 1
    gap = m["gap_candidates"][0]
    assert gap["task"] == "Book warehouse visit"
    assert gap["span"] is not None
    assert gap["owner"] == "Ravi"
    assert m["tracked"] == 0


def test_unalignable_action_has_no_span(tmp_path):
    """An Echo action whose words don't appear in the transcript → no span, not a gap candidate."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    # This quote uses words completely absent from TRANSCRIPT
    QUOTE = "quantum entanglement protocol deployed across distributed ledger network infrastructure"
    ext = [_ext_meeting(actions=[_action("Some irrelevant task", QUOTE)])]

    result = compare_external(cfg.store, ext, None, "UTC")
    m = result["meetings"][0]
    assert m["gap_candidates"] == []
    assert m["tracked"] == 0


def test_other_client_call_unmatched(tmp_path):
    """An external meeting whose title shares < 2 tokens with any taska meeting → unmatched_meetings."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    # "Budget Review XYZ" shares 0 meaningful tokens with "Harbor Logistics Daily Sync"
    ext = [_ext_meeting(title="Budget Review XYZ")]

    result = compare_external(cfg.store, ext, None, "UTC")
    assert result["meetings"] == []
    assert len(result["unmatched_meetings"]) == 1
    assert "Budget Review XYZ" in result["unmatched_meetings"][0]["title"]


def test_actions_list_one_of_each_status(tmp_path):
    """Three actions produce exactly one tracked, one gap, one unaligned entry in actions list."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    ev_id = _meeting(cfg)

    # QUOTE_A is in the transcript AND has a matching taska event → tracked
    _task_event(cfg, ev_id, "T001", QUOTE_A)

    # QUOTE_B is in the transcript but has NO taska event → gap (> 400 chars from QUOTE_A)
    # QUOTE_C has words absent from transcript → unaligned
    QUOTE_C = "quantum entanglement protocol deployed across distributed ledger network infrastructure"

    ext = [_ext_meeting(actions=[
        _action("Prepare checklist", QUOTE_A, owner="Nora"),
        _action("Book warehouse visit", QUOTE_B, owner="Ravi"),
        _action("Irrelevant task", QUOTE_C),
    ])]

    result = compare_external(cfg.store, ext, None, "UTC")
    m = result["meetings"][0]

    # Three actions, each with a unique status
    acts = m["actions"]
    assert len(acts) == 3

    statuses = {a["status"] for a in acts}
    assert statuses == {"tracked", "gap", "unaligned"}

    tracked_act = next(a for a in acts if a["status"] == "tracked")
    assert tracked_act["node"] is not None
    assert tracked_act["node"] == "T001"
    assert tracked_act["aligned_by"] in ("quote", "task")
    assert tracked_act["span"] is not None

    gap_act = next(a for a in acts if a["status"] == "gap")
    assert gap_act["node"] is None
    assert gap_act["span"] is not None
    # gap_candidates is a subset of actions with status==gap
    assert len(m["gap_candidates"]) == 1
    assert m["gap_candidates"][0]["task"] == gap_act["task"]

    unaligned_act = next(a for a in acts if a["status"] == "unaligned")
    assert unaligned_act["node"] is None
    assert unaligned_act["span"] is None
    assert unaligned_act["aligned_by"] is None

    assert m["tracked"] == 1


def test_decision_tracked_and_untracked(tmp_path):
    """One decision node with an overlapping quote → tracked; one Echo decision with no node → untracked."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    ev_id = _meeting(cfg)

    # A decision node was created from a quote that overlaps DEC_A
    DEC_A = "the team agreed to hold a review session to discuss the progress and blockers on thursday"
    _decision_event(cfg, ev_id, "D001", DEC_A)

    # Echo has two decisions: DEC_A overlaps D001's create-event quote; DEC_B has no matching node
    DEC_B = "the warehouse onboarding process will be postponed until the system credentials are fixed"
    ext = [_ext_meeting(decisions=[DEC_A, DEC_B])]

    result = compare_external(cfg.store, ext, None, "UTC")
    m = result["meetings"][0]

    assert DEC_B in m["decisions_untracked"]
    assert DEC_A not in m["decisions_untracked"]
    assert len(m["decisions_untracked"]) == 1


def test_compare_external_never_writes(tmp_path):
    """compare_external never writes to the taska store or to any Echo-format files passed in."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    _meeting(cfg)

    # Create fake echo-format files to track their mtimes
    echo_dir = tmp_path / "fake_echo"
    echo_dir.mkdir()
    meta_file = echo_dir / "meta.json"
    actions_file = echo_dir / "actions.json"
    meta_file.write_text(json.dumps({"started_at": MTG_AT, "title": MTG_TITLE}))
    actions_file.write_text(json.dumps([]))

    events_file = cfg.store / "events.jsonl"
    before_meta = meta_file.stat().st_mtime
    before_actions = actions_file.stat().st_mtime
    before_events = events_file.stat().st_mtime if events_file.exists() else None

    ext = [_ext_meeting()]
    compare_external(cfg.store, ext, None, "UTC")

    # Echo-format files untouched
    assert meta_file.stat().st_mtime == before_meta
    assert actions_file.stat().st_mtime == before_actions
    # Store not written (if it existed before)
    if before_events is not None:
        assert events_file.stat().st_mtime == before_events
    else:
        assert not events_file.exists()


def test_propose_card_counts_as_tracked(tmp_path):
    """An Echo action that taska holds as a pending proposal card is tracked (status "card"), not a gap."""
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    ev_id = _meeting(cfg)
    S.append(cfg.store, [{"type": "agent", "op": "propose", "mid": "p1", "by": "agent", "evidence": ev_id,
                          "date": "2026-03-02", "node": "E01", "quote": QUOTE_B, "quote_norm": QUOTE_B, "pending": "human",
                          "proposal": {"kind": "task", "title": "Book the site visit", "type": "action"}}])
    ext = [_ext_meeting(actions=[_action("Book warehouse visit", QUOTE_B, owner="Ravi")])]
    m = compare_external(cfg.store, ext, None, "UTC")["meetings"][0]
    assert m["gap_candidates"] == []
    assert m["tracked"] == 1 and m["carded"] == 1
    assert m["actions"][0]["status"] == "card"


def test_unaligned_rate_reported(tmp_path):
    from taska_lib.compare_external import compare_external
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    ext = [_ext_meeting(actions=[_action("Book warehouse visit", QUOTE_B),
                                 _action("Irrelevant", "quantum entanglement protocol deployed across distributed ledger network")])]
    m = compare_external(cfg.store, ext, None, "UTC")["meetings"][0]
    assert m["unaligned"] == 1 and m["unaligned_rate"] == 0.5


def test_cli_reads_a_key_with_its_header(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    (tmp_path / "ext.json").write_text(json.dumps([_ext_meeting(actions=[_action("Book", QUOTE_B)])]))
    (tmp_path / "items.jsonl").write_text("# gold v1 2026-03-06 n=1\n" + json.dumps(
        {"evidence": MTG_ID, "quote": QUOTE_B, "commitment": "committed", "kind": "task"}) + "\n")
    r = run_cli(tmp_path, "compare-external", "--file", str(tmp_path / "ext.json"), "--gold", str(tmp_path / "items.jsonl"))
    assert r["status"] == "ok" and r["meetings"][0]["gold_items"] == 1
