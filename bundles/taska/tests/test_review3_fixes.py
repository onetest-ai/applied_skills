"""Review-3 fixes (spec: review3-fixes-design). Invented data; public repo."""
import pytest

from taska_lib.text import month_day, resolve_due


@pytest.mark.parametrize("s", ["Sprint-12-Decisions", "Phase_2_Marketing", "Decision_2_notes",
                               "decision-3 by friday", "Juneteenth 4 plan", "Mayday 5 drill", "Marching 3 orders"])
def test_month_day_ignores_words_that_only_start_like_a_month(s):
    assert month_day(s) is None, s


@pytest.mark.parametrize("s,want", [("Recording 25_Aug", (8, 25)), ("06-Aug standup", (8, 6)), ("March 14", (3, 14)),
                                    ("14th of October", (10, 14)), ("Aug 25", (8, 25)), ("Sept 3 sync", (9, 3)),
                                    ("3 Sep. review", (9, 3)), ("may 5", (5, 5)), ("DEC-1", (12, 1))])
def test_month_day_still_reads_real_dates(s, want):
    assert month_day(s) == want, s


def test_resolve_due_does_not_invent_a_date_from_a_word():
    assert resolve_due("decision-3 by friday", "2026-03-02") != "2026-12-03"


from taska_lib.deliverables import deliverables

EV = {"f1": {"kind": "file", "date": "2026-03-04", "title": "Stock report v1"},
      "f2": {"kind": "file", "date": "2026-03-06", "title": "Stock report v2"},
      "m1": {"kind": "meeting", "date": "2026-03-05", "title": "Daily"}}


def _story(sid, history, state="in_progress"):
    return {"id": sid, "kind": "story", "title": sid, "state": state, "ac": [], "history": history,
            "changed_in": [h["evidence"] for h in history if h.get("evidence")]}


def test_file_landed_needs_an_applied_agent_change():
    N = {"S1": _story("S1", [{"evidence": "f1", "applied": True, "by": "agent"},
                             {"evidence": "f2", "applied": False, "by": "agent"}]),       # unverified: changed nothing
         "S2": _story("S2", [{"evidence": "f2", "applied": False, "by": "agent"}]),
         "S3": _story("S3", [{"evidence": "f2", "applied": True, "by": "human"}])}       # your edit, stamped on a file
    got = {d["id"]: d["landed"] for d in deliverables(N, EV)}
    assert got["S1"] == {"date": "2026-03-04", "title": "Stock report v1"}, got
    assert got["S2"] is None and got["S3"] is None, got


def test_merged_and_deleted_stories_are_not_deliverables():
    N = {"S1": _story("S1", []), "S2": _story("S2", [], "merged"), "S3": _story("S3", [], "deleted")}
    assert [d["id"] for d in deliverables(N, EV)] == ["S1"]


from taska_lib import config as C, ledger as L, store as S
from conftest import run_cli

Q_WAIT = "we are waiting for Opal to reset the passwords before we can pull the stock reports"
Q_LOGIN = "I successfully logged in with the new credentials this morning"
ACS = ["Export runs with no manual steps", "Totals reconcile against the warehouse ledger", "Client signs off on the report"]


def _three_ac_story(project):
    run_cli(project, "setup"); run_cli(project, "sync")
    cfg = C.load(project); st = cfg.store
    d1, d2 = [x["id"] for x in run_cli(project, "pending")["items"]]
    L.apply(st, cfg, d1, [{"op": "create", "kind": "story", "parent": "E01", "title": "Stock report automated",
                           "ac": ACS, "quote": Q_WAIT}])
    L.mark_processed(st, d1)
    return cfg, st, d2


def _met(st):
    N, *_ = L.fold(st)
    return {x["text"]: x["met"] for x in N["S001"]["ac"]}


def test_ac_met_follows_the_criterion_text_when_a_drop_is_undone(project):
    cfg, st, d2 = _three_ac_story(project)
    drop = L.human_event(st, {"op": "ac_set", "node": "S001", "ac": 1, "drop": True})   # visible: Totals, Sign-off
    L.apply(st, cfg, d2, [{"op": "ac_met", "node": "S001", "ac": 2, "quote": Q_LOGIN}])  # the sign-off
    L.mark_processed(st, d2)
    L.verify_apply(st, [{"mid": p["mid"], "verdict": "supports"} for p in L.verify_prep(st)])
    L.human_event(st, {"op": "undo", "target": drop["mid"]})                             # Export is back at #1
    assert _met(st) == {ACS[0]: False, ACS[1]: False, ACS[2]: True}, _met(st)


def test_ac_met_with_text_the_agent_saw_is_rejected_when_numbering_moved(project):
    cfg, st, d2 = _three_ac_story(project)
    # the agent read context (1.Export 2.Totals 3.Sign-off) and means Totals; meanwhile the manager drops Export
    L.human_event(st, {"op": "ac_set", "node": "S001", "ac": 1, "drop": True})
    r = L.apply(st, cfg, d2, [{"op": "ac_met", "node": "S001", "ac": 2, "ac_text": ACS[1], "quote": Q_LOGIN}])
    assert r["rejected"] and "context" in r["rejected"][0]["why"], r
    ok = L.apply(st, cfg, d2, [{"op": "ac_met", "node": "S001", "ac": 1, "ac_text": ACS[1], "quote": Q_LOGIN}])
    assert not ok["rejected"], ok


def test_workbench_calls_carry_their_date_source(project):
    """F7: the page can only flag an uncertain date (dated by the file copy) if each call says where its date came
    from; the fixture's files are named with their date, so they read 'filename'."""
    from taska_lib import workbench as W
    run_cli(project, "setup"); run_cli(project, "sync")
    cfg = C.load(project); st = cfg.store
    for d in [x["id"] for x in run_cli(project, "pending")["items"]]:
        L.mark_processed(st, d)
    calls = W.data(st, cfg)["calls"]
    assert calls and all(c.get("date_source") == "filename" for c in calls), calls
