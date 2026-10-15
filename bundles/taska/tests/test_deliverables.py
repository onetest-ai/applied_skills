"""The Deliverables lens: a read-only view over existing data. `deliverables(N, ev)` turns the folded
tree into one row per story (the shippable unit), carrying its status/owner/due/AC progress and
whether a *file*-kind evidence item has landed for it (an actual artifact appeared in the document
source). No new stored concept — everything is derived from the node and the evidence already there.

All names invented (Harbor Logistics); public repo.
"""
from taska_lib.deliverables import deliverables

EV = {
    "m1": {"kind": "meeting", "date": "2026-03-02", "title": "Daily sync"},
    "f1": {"kind": "file", "date": "2026-03-10", "title": "Stock report v1.xlsx"},
    "f2": {"kind": "file", "date": "2026-03-14", "title": "Stock report v2.xlsx"},
}


def _story(**kw):
    """`changed_in` is shorthand here for applied agent changes from those evidence items."""
    base = {"kind": "story", "state": "open", "internal_owner": None, "due": None, "ac": [], "changed_in": []}
    s = {**base, **kw}
    s.setdefault("history", [{"evidence": e, "applied": True, "by": "agent"} for e in s["changed_in"]])
    return s


def test_only_stories_are_listed_with_ac_progress():
    N = {
        "S001": _story(id="S001", title="Automate the stock report", internal_owner="Nora Quill",
                       due="2026-03-20", ac=[{"text": "a", "met": True}, {"text": "b", "met": False}],
                       changed_in=["m1"]),
        "T001": {"id": "T001", "kind": "task", "title": "Pull data", "changed_in": ["f1"]},
        "E01": {"id": "E01", "kind": "epic", "title": "Onboarding", "changed_in": []},
    }
    out = deliverables(N, EV)
    assert [d["id"] for d in out] == ["S001"]            # stories only (no task, no epic)
    d = out[0]
    assert d["title"] == "Automate the stock report" and d["internal_owner"] == "Nora Quill"
    assert d["due"] == "2026-03-20" and d["state"] == "open"
    assert d["ac_met"] == 1 and d["ac_total"] == 2


def test_landed_is_the_latest_file_evidence_that_changed_the_story():
    N = {"S001": _story(id="S001", title="Stock report", changed_in=["m1", "f1", "f2"])}
    d = deliverables(N, EV)[0]
    # a file-kind evidence changed this story -> the artifact landed; the newest file wins
    assert d["landed"] == {"date": "2026-03-14", "title": "Stock report v2.xlsx"}


def test_a_story_with_no_file_evidence_has_not_landed():
    N = {"S001": _story(id="S001", title="Stock report", changed_in=["m1"])}
    assert deliverables(N, EV)[0]["landed"] is None
