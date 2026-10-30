"""Every closure waits for the user's Confirm, whatever the source (user, 2026-10-23). Invented data only."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))
sys.path.insert(0, os.path.dirname(__file__))

from taska_lib import ledger as L, store as S
import test_teams_pending as TP

MEET = "2026-10-21_meetings_aaaa1111"


class Cfg(TP._Cfg):
    name = "Demo"
    people = []


def _store_with_meeting():
    d = TP._store()
    S.write_evidence(d, {"id": MEET, "kind": "meeting", "at": "2026-10-21T10:00:00", "title": "Weekly sync",
                         "source": "meetings", "text": "",
                         "turns": [{"speaker": "Sam Lee", "ts": "00:05:10",
                                    "text": "The stakeholder contact list is finished and shared with the client"}]})
    return d


def _apply(d, effect):
    return L.apply(d, Cfg(), MEET, [{"op": "status", "node": "S001", "effect": effect,
                                     "quote": "The stakeholder contact list is finished and shared"}])


def _ev(d, effect):
    return [e for e in S.events(d) if e.get("evidence") == MEET and e.get("effect") == effect][0]


def test_meeting_done_waits_for_confirm():
    d = _store_with_meeting()
    _apply(d, "done")
    assert _ev(d, "done")["pending"] == "human"
    N, *_ = L.fold(d)
    assert N["S001"]["state"] == "open"
    assert any(s["effect"] == "done" and s["source"] == "meetings" and s["status"] == "pending" for s in N["S001"]["suggestions"])


def test_held_and_cancelled_also_wait():
    for eff in ("held", "cancelled"):
        d = _store_with_meeting()
        _apply(d, eff)
        assert _ev(d, eff)["pending"] == "human"


def test_progress_still_goes_through_the_verifier():
    d = _store_with_meeting()
    _apply(d, "progress")
    e = _ev(d, "progress")
    assert "pending" not in e and L.needs_verdict(e)


def test_closures_are_not_sent_to_the_verifier():
    d = _store_with_meeting()
    _apply(d, "done")
    assert all(p.get("node") != "S001" or p.get("claimed", "").find("done") < 0 for p in L.verify_prep(d))
    assert not any(L.needs_verdict(e) for e in S.events(d) if e.get("effect") == "done")


def test_older_closures_without_the_flag_still_apply():
    d = _store_with_meeting()
    S.append(d, [{"type": "agent", "op": "status", "mid": "old1", "node": "S001", "effect": "done", "by": "agent",
                  "evidence": MEET, "date": "2026-10-21", "quote": "finished and shared"},
                 {"type": "verdict", "mid": "old1", "verdict": "supports", "by": "verifier"}])
    N, *_ = L.fold(d)
    assert N["S001"]["state"] == "done"


def test_context_lists_closures_awaiting_confirm():
    d = _store_with_meeting()
    _apply(d, "done")
    c = json.dumps(L.context(d, Cfg()))
    assert "awaiting_confirm" in c and "unverified_claims" not in c
