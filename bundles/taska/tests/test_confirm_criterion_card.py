"""A waiting "criterion met" card (pending: human) is confirmed against the criterion it was made about. If that
criterion was reworded or dropped since, or the manager set it by hand, Confirm refuses with a reason instead of
closing the card while nothing changes. Invented data only."""
import tempfile

import pytest

from taska_lib import ledger as L, store as S

EVID = "2026-03-04_files_aaaa2222"


def _store():
    d = tempfile.mkdtemp()
    S.ensure(d)
    S.write_evidence(d, {"id": EVID, "kind": "file", "at": "2026-03-04T10:00:00", "title": "Sign-off note",
                         "source": "files", "text": "The quarterly report was signed off today."})
    S.append(d, [
        {"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": None,
         "fields": {"title": "Reporting", "ac": [], "depends_on": []}},
        {"type": "create", "mid": "c2", "id": "S001", "kind": "story", "by": "plan", "evidence": None, "date": None,
         "fields": {"title": "Quarterly report", "parent": "E01", "depends_on": [],
                    "ac": ["Draft shared", "Report signed off"]}},
        {"type": "agent", "op": "ac_met", "mid": "m1", "node": "S001", "ac": 2, "ac_text": "Report signed off",
         "by": "agent", "evidence": EVID, "date": "2026-03-04", "quote": "The quarterly report was signed off today.",
         "pending": "human", "why": "the sign-off note says so"},
    ])
    return d


def _met(d):
    N, *_ = L.fold(d)
    return [a["met"] for a in N["S001"]["ac"] if not a.get("dropped")]


def test_confirm_marks_the_criterion_it_was_made_about():
    d = _store()
    L.human_event(d, {"op": "accept", "target": "m1"})
    assert _met(d) == [False, True]


def test_confirm_refuses_when_the_criterion_was_reworded():
    d = _store()
    L.human_event(d, {"op": "ac_set", "node": "S001", "ac": 2, "text": "Report signed off by the sponsor"})
    with pytest.raises(ValueError, match="changed since this card was made"):
        L.human_event(d, {"op": "accept", "target": "m1"})
    N, *_ = L.fold(d)
    assert [s["status"] for s in N["S001"]["suggestions"] if s["mid"] == "m1"] == ["pending"]   # still waiting


def test_confirm_refuses_when_the_manager_set_the_criterion():
    d = _store()
    L.human_event(d, {"op": "ac_set", "node": "S001", "ac": 2, "met": False})
    with pytest.raises(ValueError, match="you set"):
        L.human_event(d, {"op": "accept", "target": "m1"})


def test_reject_is_always_possible():
    d = _store()
    L.human_event(d, {"op": "ac_set", "node": "S001", "ac": 2, "text": "Report signed off by the sponsor"})
    L.human_event(d, {"op": "reject", "target": "m1", "reason": "reworded"})
    N, *_ = L.fold(d)
    assert [s["status"] for s in N["S001"]["suggestions"] if s["mid"] == "m1"] == ["reject"]
