import json

import pytest

from taska_lib import config as C, ledger as L, staleness as ST, store as S
from conftest import run_cli

Q_BOOKED = "warehouse walkthrough session with the client is booked for next Tuesday"
Q_WAIT = "we are waiting for Opal to reset the passwords before we can pull the stock reports"
Q_HELD = "the warehouse walkthrough session happened yesterday"
Q_LOGIN = "I successfully logged in with the new credentials this morning"


@pytest.fixture
def ledger(project):
    run_cli(project, "setup")
    run_cli(project, "sync")
    cfg = C.load(project)
    ids = [x["id"] for x in run_cli(project, "pending")["items"]]
    return project, cfg, cfg.store, ids


def _apply(store, cfg, evid, props):
    return L.apply(store, cfg, evid, props)


def _supports_all(store):
    pk = L.verify_prep(store)
    L.verify_apply(store, [{"mid": p["mid"], "verdict": "supports"} for p in pk])
    return pk


def test_epics_from_plan_csv(ledger):
    _, cfg, st, _ = ledger
    N, *_ = L.fold(st)
    assert [n["title"] for n in N.values() if n["kind"] == "epic"] == ["Onboarding", "Discovery"]


def test_level_rules_and_quote_rule(ledger):
    _, cfg, st, (d1, d2) = ledger
    r = _apply(st, cfg, d1, [
        {"op": "create", "kind": "task", "type": "session", "parent": "E01", "title": "x", "quote": Q_BOOKED},
        {"op": "create", "kind": "story", "parent": "E01", "title": "no ac", "quote": Q_BOOKED},
        {"op": "create", "kind": "story", "parent": "E01", "title": "too big", "ac": list("abcdefg"), "quote": Q_BOOKED},
        {"op": "create", "kind": "story", "parent": "E01", "title": "ok", "ac": ["a"], "quote": "words that were never said here"},
        {"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Warehouse understood",
         "ac": ["Walkthrough held"], "quote": Q_BOOKED},
        {"op": "create", "kind": "task", "type": "action", "parent": "s1", "title": "no owner no date", "quote": Q_BOOKED},
        {"op": "create", "kind": "task", "type": "session", "parent": "s1", "title": "Warehouse walkthrough",
         "due_text": "next Tuesday", "quote": Q_BOOKED},
    ])
    whys = [x["why"] for x in r["rejected"]]
    assert len(whys) == 5
    assert "parent of kind ['story']" in whys[0] and "acceptance criteria" in whys[1]
    assert "more than 6" in whys[2] and "not verbatim" in whys[3] and "admission bar" in whys[4]
    N, *_ = L.fold(st)
    t = next(n for n in N.values() if n["title"] == "Warehouse walkthrough")
    assert t["due"] == "2026-03-10" and t["parent"] == "S001"


def test_status_link_and_waiting_apply_only_when_verified(ledger):
    _, cfg, st, (d1, d2) = ledger
    _apply(st, cfg, d1, [
        {"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Stock reporting live",
         "ac": ["First stock report pulled"], "quote": Q_WAIT},
        {"op": "create", "ref": "t1", "kind": "task", "type": "action", "parent": "s1", "title": "Pull stock reports",
         "internal_owner": "Ravi Tamsin", "quote": Q_WAIT},
        {"op": "waiting", "node": "t1", "who": "Opal Brennick1", "what": "password reset", "quote": Q_WAIT},
    ])
    L.mark_processed(st, d1)
    N, *_ = L.fold(st)
    assert N["T001"]["waiting_for"] is None  # unverified
    _supports_all(st)
    N, *_ = L.fold(st)
    assert N["T001"]["waiting_for"]["who"] == "Opal Brennick"  # people directory resolved the Teams label
    _apply(st, cfg, d2, [{"op": "status", "node": "T001", "effect": "done", "quote": Q_LOGIN},
                         {"op": "ac_met", "node": "S001", "ac": 1, "quote": Q_LOGIN}])
    L.mark_processed(st, d2)
    N, *_ = L.fold(st)
    assert N["T001"]["state"] == "open"
    _supports_all(st)
    N, *_ = L.fold(st)
    assert N["T001"]["state"] == "done" and N["T001"]["waiting_for"] is None
    assert N["S001"]["state"] == "done" and N["S001"]["derived"] == "all acceptance criteria met"


def test_human_pins_undo_merge_and_suggestions(ledger):
    _, cfg, st, (d1, d2) = ledger
    _apply(st, cfg, d1, [
        {"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Warehouse understood",
         "ac": ["Walkthrough held"], "quote": Q_BOOKED},
        {"op": "create", "ref": "t1", "kind": "task", "type": "session", "parent": "s1", "title": "Walkthrough",
         "due_text": "next Tuesday", "quote": Q_BOOKED},
        {"op": "create", "ref": "t2", "kind": "task", "type": "session", "parent": "s1", "title": "Site visit",
         "due_text": "next Tuesday", "quote": Q_BOOKED},
    ])
    L.mark_processed(st, d1)
    e = L.human_event(st, {"op": "edit", "node": "T001", "set": {"state": "blocked", "title": "Walkthrough (mine)"}})
    _apply(st, cfg, d2, [{"op": "status", "node": "T001", "effect": "held", "quote": Q_HELD},
                         {"op": "suggest_merge", "node": "T002", "into": "T001", "quote": Q_HELD}])
    L.mark_processed(st, d2)
    _supports_all(st)
    N, *_ = L.fold(st)
    t = N["T001"]
    assert t["state"] == "blocked" and t["title"] == "Walkthrough (mine)"
    assert any(h.get("effect") == "held" and h["applied"] is False and h["verdict"] == "supports" for h in t["history"])
    assert N["T002"]["suggestions"][0]["status"] == "pending" and N["T002"]["state"] != "merged"
    L.human_event(st, {"op": "accept", "target": N["T002"]["suggestions"][0]["mid"]})
    L.human_event(st, {"op": "undo", "target": e["mid"]})
    N, *_ = L.fold(st)
    assert N["T002"]["state"] == "merged" and N["T002"]["merged_into"] == "T001"
    assert N["T001"]["state"] == "done" and N["T001"]["title"] == "Walkthrough"  # pin undone: evidence applies


def test_node_cannot_be_touched_before_it_existed(ledger):
    _, cfg, st, (d1, d2) = ledger
    _apply(st, cfg, d2, [{"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Late",
                          "ac": ["x"], "quote": Q_HELD}])
    r = _apply(st, cfg, d1, [{"op": "status", "node": "S001", "effect": "mention", "quote": Q_BOOKED}])
    assert "did not exist yet" in r["rejected"][0]["why"]


def test_staleness_tiers_and_asks(ledger):
    _, cfg, st, (d1, d2) = ledger
    _apply(st, cfg, d1, [
        {"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Access",
         "ac": ["Everyone logged in"], "quote": Q_WAIT},
        {"op": "create", "ref": "t1", "kind": "task", "type": "dependency", "parent": "s1",
         "title": "Client resets passwords", "client_owner": "Opal Brennick", "client_owed": True,
         "due_text": "March 2", "quote": Q_WAIT},
        {"op": "create", "ref": "t2", "kind": "task", "type": "action", "parent": "s1", "title": "Pull reports",
         "internal_owner": "Ravi Tamsin", "depends_on": ["t1"], "quote": Q_WAIT},
    ])
    L.mark_processed(st, d1)
    L.mark_processed(st, d2)
    _supports_all(st)
    rows, N = ST.classify(st, cfg)
    r = next(r for r in rows if r["node"]["id"] == "T001")
    assert "overdue" in r["flags"] and r["top"] and r["tier"] == "ask"   # client-owed: always asked
    assert "T002" in N["T001"]["blocks"]
    q = ST.question(r, N)
    assert "holds up" in q and "Pull reports" in q
    down = next(r for r in rows if r["node"]["id"] == "T002")
    assert down["shadow"]  # asked once, at the root


def test_waiting_is_cleared_when_it_resolves(ledger):
    _, cfg, st, (d1, d2) = ledger
    _apply(st, cfg, d1, [
        {"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Stock reporting live",
         "ac": ["First stock report pulled"], "quote": Q_WAIT},
        {"op": "create", "ref": "t1", "kind": "task", "type": "action", "parent": "s1", "title": "Pull stock reports",
         "internal_owner": "Ravi Tamsin", "quote": Q_WAIT},
        {"op": "waiting", "node": "t1", "who": "Opal Brennick", "what": "password reset", "quote": Q_WAIT}])
    L.mark_processed(st, d1)
    _apply(st, cfg, d2, [{"op": "waiting", "node": "T001", "what": None, "quote": Q_LOGIN}])
    L.mark_processed(st, d2)
    pk = _supports_all(st)
    assert any("no longer waiting" in p["claimed"] for p in pk)
    N, *_ = L.fold(st)
    assert N["T001"]["waiting_for"] is None and N["T001"]["state"] == "open"


def test_context_is_compact_registers(ledger):
    _, cfg, st, (d1, d2) = ledger
    _apply(st, cfg, d1, [{"op": "create", "kind": "risk", "parent": "E01", "title": "Passwords are shared", "quote": Q_WAIT}])
    L.mark_processed(st, d1)
    ctx = L.context(st, cfg)
    e = next(x for x in ctx["epics"] if x["id"] == "E01")
    assert e["risks"] == ["R001 Passwords are shared"]
    assert "E02 Discovery" in ctx["epics_without_work"] and ctx["evidence_processed"]["count"] == 1
