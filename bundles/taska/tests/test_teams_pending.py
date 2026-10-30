"""Pending proposals: an agent status op flagged pending="human" changes nothing until a human accepts it.
Invented data only."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib import ledger as L, store as S

EVID = "2026-10-21_teams_abc12345"


def _store():
    d = tempfile.mkdtemp()
    S.ensure(d)
    S.write_evidence(d, {"id": EVID, "kind": "file", "at": "2026-10-21T16:42:00", "title": "list shared by Sam Lee",
                         "source": "teams", "text": "Final stakeholder list is in the folder, all contacts confirmed."})
    S.append(d, [
        {"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": None,
         "fields": {"title": "Discovery", "ac": [], "depends_on": []}},
        {"type": "create", "mid": "c2", "id": "S001", "kind": "story", "by": "plan", "evidence": None, "date": None,
         "fields": {"title": "Stakeholder list compiled", "parent": "E01", "ac": [], "depends_on": []}},
    ])
    op = {"type": "agent", "op": "status", "mid": "p1", "node": "S001", "effect": "done", "by": "agent",
          "evidence": EVID, "date": "2026-10-21", "quote": "Final stakeholder list is in the folder",
          "pending": "human", "why": "Sam Lee in Daily sync (Oct 21) says the list is final and shared.",
          "suggested": "state: open → done", "link": "https://teams.example/msg/1",
          "gate": {"model": "kev-4b", "score": 0.91}}
    S.append(d, [op])
    return d


def _node(d):
    N, *_ = L.fold(d)
    return N["S001"]


def test_pending_status_is_not_applied():
    n = _node(_store())
    assert n["state"] == "open"


def test_pending_status_is_listed_as_a_suggestion():
    n = _node(_store())
    s = [x for x in n["suggestions"] if x["mid"] == "p1"]
    assert s and s[0]["status"] == "pending"
    assert s[0]["op"] == "status" and s[0]["effect"] == "done"
    assert s[0]["why"].startswith("Sam Lee") and s[0]["suggested"] == "state: open → done"
    assert s[0]["link"] == "https://teams.example/msg/1" and s[0]["gate"]["score"] == 0.91


def test_pending_status_ignores_a_supports_verdict():
    d = _store()
    S.append(d, [{"type": "verdict", "mid": "p1", "verdict": "supports", "by": "verifier"}])
    assert _node(d)["state"] == "open"


def test_accept_applies_without_pinning():
    d = _store()
    L.human_event(d, {"op": "accept", "target": "p1"})
    n = _node(d)
    assert n["state"] == "done"
    assert "state" not in n["pinned"]
    assert [x["status"] for x in n["suggestions"] if x["mid"] == "p1"] == ["accept"]


def test_reject_keeps_reason_and_changes_nothing():
    d = _store()
    e = L.human_event(d, {"op": "reject", "target": "p1", "reason": "not_done_yet"})
    assert e["reason"] == "not_done_yet"
    n = _node(d)
    assert n["state"] == "open"
    assert [x["status"] for x in n["suggestions"] if x["mid"] == "p1"] == ["reject"]


def test_undo_of_accept_reverts():
    d = _store()
    acc = L.human_event(d, {"op": "accept", "target": "p1"})
    L.human_event(d, {"op": "undo", "target": acc["mid"]})
    assert _node(d)["state"] == "open"


def test_human_pin_wins_over_accepted_proposal():
    import pytest
    d = _store()
    L.human_event(d, {"op": "edit", "node": "S001", "set": {"state": "in_progress"}})
    with pytest.raises(ValueError):
        L.human_event(d, {"op": "accept", "target": "p1"})
    assert _node(d)["state"] == "in_progress"


def test_pending_proposal_is_not_sent_to_the_verifier():
    d = _store()
    packets = L.verify_prep(d)
    assert all(p.get("mid") != "p1" for p in packets)


class _Cfg:
    working_days = [1, 2, 3, 4, 5]

    def canonical(self, x):
        return x


def test_apply_carries_pending_fields_on_status():
    d = _store()
    r = L.apply(d, _Cfg(), EVID, [{"op": "status", "node": "S001", "effect": "progress",
                                    "quote": "Final stakeholder list is in the folder", "pending": "human",
                                    "why": "w", "suggested": "state: open → in progress", "link": "https://t/1",
                                    "gate": {"model": "m", "score": 0.8}}])
    assert r["events"] == 1, r
    e = [x for x in S.events(d) if x.get("op") == "status" and x.get("effect") == "progress"][0]
    assert e["pending"] == "human" and e["why"] == "w" and e["link"] == "https://t/1" and e["gate"]["score"] == 0.8


def test_apply_ignores_unknown_pending_values():
    d = _store()
    L.apply(d, _Cfg(), EVID, [{"op": "status", "node": "S001", "effect": "blocked",
                               "quote": "Final stakeholder list is in the folder", "pending": "maybe"}])
    e = [x for x in S.events(d) if x.get("effect") == "blocked"][0]
    assert "pending" not in e
