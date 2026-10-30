"""Two status cards on one item are one decision: each names the other as an alternative, confirming one supersedes
the rest (undo brings them back), and a second source for the same change joins the same decision, never dropped. Invented data only."""
import tempfile

import pytest

from taska_lib import ledger as L, store as S

E1, E2, E3 = "2026-03-04_teams_aaaa0001", "2026-03-04_teams_aaaa0002", "2026-03-05_teams_aaaa0003"


class Cfg:
    working_days = [1, 2, 3, 4, 5]
    name = "Demo"
    people = []

    def canonical(self, x):
        return x

    def team(self, x):
        return None


def _store():
    d = tempfile.mkdtemp()
    S.ensure(d)
    for i, at, text in ((E1, "2026-03-04T10:00:00", "ok then I will assume I can ignore the vendor call, thanks"),
                        (E2, "2026-03-04T11:00:00", "this vendor call is for you two, to get what you need for the pilot"),
                        (E3, "2026-03-05T09:00:00", "the vendor call is happening, the two of them are on it now")):
        S.write_evidence(d, {"id": i, "kind": "message", "at": at, "title": "Daily sync", "source": "teams", "text": text})
    S.append(d, [
        {"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": None,
         "fields": {"title": "Pilot", "ac": [], "depends_on": []}},
        {"type": "create", "mid": "c2", "id": "T001", "kind": "task", "by": "plan", "evidence": None, "date": None,
         "fields": {"title": "Call with the vendor on what they offer", "parent": "E01", "ac": [], "depends_on": [],
                    "type": "session"}},
    ])
    return d


def _card(d, evid, effect, quote):
    return L.apply(d, Cfg(), evid, [{"op": "status", "node": "T001", "effect": effect, "quote": quote, "pending": "human",
                                     "why": "from Teams"}])


def _cards(d):
    N, *_ = L.fold(d)
    return {s["effect"]: s for s in N["T001"]["suggestions"] if s["op"] == "status"}, N["T001"]


def _two(d):
    _card(d, E1, "done", "I will assume I can ignore the vendor call")
    _card(d, E2, "progress", "this vendor call is for you two")
    c, _ = _cards(d)
    return c["done"]["mid"], c["progress"]["mid"]


def test_two_cards_on_one_item_name_each_other():
    d = _store()
    done, prog = _two(d)
    c, n = _cards(d)
    assert c["done"]["alternatives"] == [prog] and c["progress"]["alternatives"] == [done]
    assert n["state"] == "open"


def test_confirming_one_supersedes_the_other_and_undo_brings_it_back():
    d = _store()
    done, prog = _two(d)
    acc = L.human_event(d, {"op": "accept", "target": prog})
    c, n = _cards(d)
    assert n["state"] == "in_progress" and c["progress"]["status"] == "accept" and c["done"]["status"] == "superseded"
    L.human_event(d, {"op": "undo", "target": acc["mid"]})
    c, n = _cards(d)
    assert n["state"] == "open" and {c["done"]["status"], c["progress"]["status"]} == {"pending"}


def test_a_second_source_for_the_same_change_joins_the_same_decision():
    # never dropped: the second quote is kept and shown in the same entry; one Confirm decides both
    d = _store()
    _card(d, E2, "progress", "this vendor call is for you two")
    r = _card(d, E3, "progress", "the vendor call is happening")
    assert r["by_op"].get("status") == 1
    N, *_ = L.fold(d)
    a, b = [s for s in N["T001"]["suggestions"] if s["op"] == "status"]
    assert a["alternatives"] == [b["mid"]] and b["alternatives"] == [a["mid"]]


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_a_card_that_arrives_after_a_decision_still_waits(decision):
    d = _store()
    _card(d, E1, "done", "I will assume I can ignore the vendor call")
    first = _cards(d)[0]["done"]["mid"]
    L.human_event(d, {"op": decision, "target": first})
    _card(d, E2, "progress", "this vendor call is for you two")
    c, _ = _cards(d)
    assert "alternatives" not in c["progress"] and c["progress"]["status"] == "pending"   # news after the decision


def test_the_workbench_shows_cards_on_one_item_as_one_decision():
    from test_workbench_timeline import _js, HTML
    x = {"id": "T001"}
    pend = [[x, {"mid": "a", "op": "status", "alternatives": ["b"]}], [{"id": "T002"}, {"mid": "c", "op": "status"}],
            [x, {"mid": "b", "op": "status", "alternatives": ["a"]}]]
    got = _js(f"decisionsOf({__import__('json').dumps(pend)}).map(d => d.map(([x, s]) => s.mid))")
    assert got == [["a", "b"], ["c"]]
    page = HTML.read_text()
    assert "disagree" in page and "Confirm this" in page and "data-rejall" in page
    assert "items.filter(d => d.length === 1)" in page   # Confirm all never takes one side of a disagreement
