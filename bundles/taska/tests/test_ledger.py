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
    assert N["T001"]["state"] == "open"  # a closure waits for the user's Confirm, even when verified
    done = next(x for x in N["T001"]["suggestions"] if x["op"] == "status" and x["effect"] == "done")
    L.human_event(st, {"op": "accept", "target": done["mid"]})
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
    assert any(h.get("effect") == "held" and h["applied"] is False and h.get("status") == "pending" for h in t["history"])
    assert N["T002"]["suggestions"][0]["status"] == "pending" and N["T002"]["state"] != "merged"
    L.human_event(st, {"op": "accept", "target": N["T002"]["suggestions"][0]["mid"]})
    L.human_event(st, {"op": "undo", "target": e["mid"]})
    N, *_ = L.fold(st)
    assert N["T002"]["state"] == "merged" and N["T002"]["merged_into"] == "T001"
    assert N["T001"]["state"] == "open" and N["T001"]["title"] == "Walkthrough"  # pin undone: evidence applies
    held = next(x for x in N["T001"]["suggestions"] if x["op"] == "status" and x["effect"] == "held")
    L.human_event(st, {"op": "accept", "target": held["mid"]})  # the closure itself waits for Confirm
    N, *_ = L.fold(st)
    assert N["T001"]["state"] == "done"


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


Q_STAFF = "Ravi moves to half time from the 20th and Nora stays at 100% until May."
Q_ROLLOUT = "Move Checkout to 50% of users next sprint"
VTT_STAFFING = f"""WEBVTT

00:00:03.000 --> 00:00:12.000
<v Nora Quill>{Q_STAFF}</v>

00:00:13.000 --> 00:00:22.000
<v Nora Quill>{Q_ROLLOUT}, once the warehouse walkthrough session is booked.</v>
"""


@pytest.fixture
def staffing(project):
    (next(project.glob("calls")) / "2026-03-04 Staffing sync.vtt").write_text(VTT_STAFFING)
    run_cli(project, "setup")
    run_cli(project, "sync")
    cfg = C.load(project)
    ev = S.evidence(cfg.store)
    evid = next(k for k, v in ev.items() if "Staffing" in v["title"])
    return cfg, cfg.store, evid


def test_agent_create_of_a_people_matter_is_rejected(staffing):
    cfg, st, evid = staffing
    out = _apply(st, cfg, evid, [{"op": "create", "kind": "story", "parent": "E01", "ac": ["Decided"],
                                  "title": "Ravi moves to half time", "quote": Q_STAFF}])
    assert out["events"] == 0
    assert out["rejected"] == [{"i": 0, "why": "people_matter", "title": "Ravi moves to half time"}]


def test_agent_revise_into_a_people_matter_is_rejected(staffing):
    cfg, st, evid = staffing
    _apply(st, cfg, evid, [{"op": "create", "kind": "story", "parent": "E01", "ac": ["Rollout started"],
                            "title": "Checkout rollout", "quote": Q_ROLLOUT}])
    out = _apply(st, cfg, evid, [{"op": "revise", "node": "S001", "title": "Nora stays at 100%", "quote": Q_STAFF}])
    assert out["events"] == 0 and out["rejected"][0]["why"] == "people_matter"
    N, *_ = L.fold(st)
    assert N["S001"]["title"] == "Checkout rollout"


def test_human_edit_is_not_filtered(staffing):
    cfg, st, evid = staffing
    _apply(st, cfg, evid, [{"op": "create", "kind": "story", "parent": "E01", "ac": ["Rollout started"],
                            "title": "Checkout rollout", "quote": Q_ROLLOUT}])
    L.human_event(st, {"op": "edit", "node": "S001", "set": {"title": "Ravi moves to half time"}})
    N, *_ = L.fold(st)
    assert N["S001"]["title"] == "Ravi moves to half time"


def test_an_ordinary_percentage_decision_is_applied(staffing):
    cfg, st, evid = staffing
    out = _apply(st, cfg, evid, [{"op": "create", "kind": "story", "parent": "E01", "ac": ["Rollout started"],
                                  "title": "Move Checkout to 50% of users", "quote": Q_ROLLOUT}])
    assert out["rejected"] == [] and out["events"] == 1


def test_agent_propose_of_a_people_matter_is_rejected(staffing):
    cfg, st, evid = staffing
    out = _apply(st, cfg, evid, [{"op": "propose", "kind": "story", "parent": "E01", "ac": ["Decided"],
                                  "title": "Ravi moves to half time", "quote": Q_STAFF}])
    assert out["events"] == 0
    assert out["rejected"] == [{"i": 0, "why": "people_matter", "title": "Ravi moves to half time"}]
    out = _apply(st, cfg, evid, [{"op": "propose", "kind": "story", "parent": "E01", "ac": ["Rollout started"],
                                  "title": "Checkout rollout", "quote": Q_ROLLOUT, "why": "Nora stays at 100% until May"}])
    assert out["rejected"][0]["why"] == "people_matter"


def test_ordinary_agent_propose_lands_as_a_card(staffing):
    cfg, st, evid = staffing
    out = _apply(st, cfg, evid, [{"op": "propose", "kind": "story", "parent": "E01", "ac": ["Rollout started"],
                                  "title": "Move Checkout to 50% of users", "quote": Q_ROLLOUT}])
    assert out["rejected"] == [] and out["events"] == 1


# ---------- a quote refused as not verbatim comes back with its closest span (Nov 2: three paraphrased quotes in one
# meeting were refused with no hint, and the reader gave the work up) ----------

ROLLOUT_SAID = "Move Checkout to 50% of users next sprint, once the warehouse walkthrough session is booked"
ROLLOUT_PARAPHRASE = "Move Checkout to half of users next sprint once the warehouse walkthrough is booked"


def _story(quote, title="Checkout rollout"):
    return {"op": "create", "kind": "story", "parent": "E01", "ac": ["Rollout started"], "title": title, "quote": quote}


def test_a_paraphrased_quote_comes_back_with_the_closest_verbatim_span(staffing):
    cfg, st, evid = staffing
    out = _apply(st, cfg, evid, [_story(ROLLOUT_PARAPHRASE)])
    assert out["events"] == 0 and len(out["rejected"]) == 1
    row = out["rejected"][0]
    assert "not verbatim" in row["why"] and row["closest"] == ROLLOUT_SAID
    item = S.evidence(st)[evid]
    assert any(row["closest"] in t["text"] for t in item["turns"])   # copied from the text, case and punctuation kept
    again = _apply(st, cfg, evid, [_story(row["closest"])])
    assert again["rejected"] == [] and again["events"] == 1


def test_a_quote_far_from_any_span_gets_no_closest(staffing):
    cfg, st, evid = staffing
    out = _apply(st, cfg, evid, [_story("the harbor crane tender closes on the first of June for Opal Brennick")])
    assert "not verbatim" in out["rejected"][0]["why"] and "closest" not in out["rejected"][0]


def test_closest_is_offered_from_sixty_percent_of_the_quote_words():
    from taska_lib.text import closest_span
    text = "Alpha bravo, charlie delta echo foxtrot golf hotel india juliet."
    assert closest_span(text, "alpha bravo charlie delta echo foxtrot kilo lima mike november") == \
        "Alpha bravo, charlie delta echo foxtrot golf hotel india juliet"
    assert closest_span(text, "alpha bravo charlie delta echo kilo lima mike november oscar") is None
    assert closest_span(text, "") is None and closest_span("", "alpha bravo charlie") is None


def test_closest_keeps_to_the_quote_length_within_two_words():
    from taska_lib.text import closest_span
    text = "Nora will send the forklift list to Harbor on Monday and Ravi will book the berth visit after that."
    span = closest_span(text, "Ravi books the berth visit after that")
    assert span is not None and span in text and abs(len(span.split()) - 7) <= 2
    assert span.endswith("after that") and "Harbor" not in span


def test_a_people_matter_re_applied_from_its_closest_span_is_still_refused(staffing):
    cfg, st, evid = staffing
    out = _apply(st, cfg, evid, [_story("Ravi moves to half time from the 20th and Nora stays full time until May",
                                        title="Ravi moves to half time")])
    row = out["rejected"][0]
    assert "not verbatim" in row["why"] and row["closest"] in Q_STAFF
    again = _apply(st, cfg, evid, [_story(row["closest"], title="Ravi moves to half time")])
    assert again["events"] == 0 and again["rejected"][0]["why"] == "people_matter"


def test_closest_is_never_taken_from_the_meeting_title(project):
    # review M2: full_text appends the title; a span copied from it would re-apply but quote nothing that was said
    run_cli(project, "setup")
    cfg = C.load(project)
    evid = "2026-03-05_meetings_title001"
    S.write_evidence(cfg.store, {"id": evid, "kind": "meeting", "at": "2026-03-05T10:00:00", "source": "meetings",
                                 "title": "Harbor crane tender weekly review with the berth planning team",
                                 "turns": [{"speaker": "Nora Quill", "ts": "00:00:01",
                                            "text": "Good morning everyone, the coffee machine is fixed again."}]})
    out = _apply(cfg.store, cfg, evid, [_story("Harbor crane tender weekly review with berth planning team")])
    assert "not verbatim" in out["rejected"][0]["why"] and "closest" not in out["rejected"][0]


# ---------- the second read never restates tracked work (2 Nov paid runs: most of its extra items restated an item
# already recorded from the same meeting in other words) ----------

Q_RESET = "I will reset the passwords today and send the new credentials to each of you by email"


def _first_read(st, cfg, d1):
    _apply(st, cfg, d1, [
        {"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Stock reporting live",
         "ac": ["First stock report pulled"], "quote": Q_WAIT},
        {"op": "create", "ref": "t1", "kind": "task", "type": "action", "parent": "s1", "title": "Pull the stock reports",
         "internal_owner": "Ravi Tamsin", "quote": Q_WAIT},
        {"op": "propose", "kind": "task", "type": "action", "parent": "s1",
         "title": "Send new credentials to the team by email", "quote": Q_RESET}])


def test_the_second_read_refuses_a_reworded_item_already_recorded_from_the_meeting(ledger):
    _, cfg, st, (d1, d2) = ledger
    _first_read(st, cfg, d1)
    L.mark_processed(st, d1, decisions_pending=True)
    r = _apply(st, cfg, d1, [
        {"op": "create", "kind": "task", "type": "action", "parent": "S001", "title": "Pull stock reports from inventory",
         "internal_owner": "Ravi Tamsin", "quote": Q_RESET},
        {"op": "propose", "kind": "task", "type": "action", "parent": "S001",
         "title": "Email the new credentials to the team", "quote": Q_RESET}])
    assert r["events"] == 0
    card = next(e["mid"] for e in S.events(st) if e.get("op") == "propose")
    assert r["rejected"] == [
        {"i": 0, "why": "similar_exists", "node": "T001", "title": "Pull stock reports from inventory"},
        {"i": 1, "why": "similar_exists", "node": card, "title": "Email the new credentials to the team"}]


def test_the_second_read_refuses_a_restated_rejected_card(ledger):
    _, cfg, st, (d1, d2) = ledger
    _first_read(st, cfg, d1)
    L.mark_processed(st, d1, decisions_pending=True)
    card = next(e["mid"] for e in S.events(st) if e.get("op") == "propose")
    L.human_event(st, {"op": "reject", "target": card})
    r = _apply(st, cfg, d1, [{"op": "create", "kind": "task", "type": "action", "parent": "S001",
                              "title": "Send the new credentials by email", "internal_owner": "Ravi Tamsin",
                              "quote": Q_RESET}])
    assert r["rejected"] == [{"i": 0, "why": "similar_exists", "node": card,
                              "title": "Send the new credentials by email"}]


def test_extraction_still_applies_a_look_alike_and_reports_it(ledger):
    _, cfg, st, (d1, d2) = ledger
    _first_read(st, cfg, d1)
    r = _apply(st, cfg, d1, [{"op": "create", "kind": "task", "type": "action", "parent": "S001",
                              "title": "Pull stock reports from inventory", "internal_owner": "Ravi Tamsin",
                              "quote": Q_RESET}])
    assert r["rejected"] == [] and r["by_op"] == {"create_task": 1}
    assert r["similar"][0]["matches"][0]["id"] == "T001"


def test_the_second_read_applies_a_different_item_and_an_item_like_another_meetings(ledger):
    _, cfg, st, (d1, d2) = ledger
    _apply(st, cfg, d2, [{"op": "create", "kind": "risk", "parent": "E01", "title": "Photos of the picking process lost",
                          "quote": Q_HELD}])
    L.mark_processed(st, d2)
    _first_read(st, cfg, d1)
    L.mark_processed(st, d1, decisions_pending=True)
    r = _apply(st, cfg, d1, [
        {"op": "create", "kind": "risk", "parent": "E01", "title": "Passwords are shared across the team",
         "quote": Q_RESET},
        {"op": "create", "kind": "risk", "parent": "E01", "title": "Photos of the picking process lost again",
         "quote": Q_RESET}])
    assert r["rejected"] == [] and r["by_op"] == {"create_risk": 2}


def test_re_applying_a_second_read_batch_adds_nothing_and_refuses_nothing(ledger):
    _, cfg, st, (d1, d2) = ledger
    _first_read(st, cfg, d1)
    L.mark_processed(st, d1, decisions_pending=True)
    batch = [{"op": "create", "kind": "risk", "parent": "E01", "title": "Passwords are shared", "quote": Q_RESET},
             {"op": "propose", "kind": "decision", "parent": "E01", "title": "Credentials go out by email",
              "quote": Q_RESET}]
    assert _apply(st, cfg, d1, batch)["rejected"] == []
    again = _apply(st, cfg, d1, batch)
    assert again["rejected"] == [] and again["events"] == 0
