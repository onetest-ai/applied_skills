"""Reproduction for finding B4 (from the PR #47 review): after a human drops an acceptance
criterion, an agent `ac_met` on a *visible* criterion number marks the WRONG criterion. The fold
indexes the unfiltered `n["ac"]` during the event loop (dropped criteria are removed only after the
loop), while the agent and the verifier both use the visible numbering — so the number is off by
the count of earlier dropped criteria, and a story can auto-close on evidence about something else.

Driven through a real-shaped story (three criteria, one dropped). All names invented (the Harbor
Logistics world from conftest.py); never client-identifying — public repo.
"""
from taska_lib import config as C, ledger as L
from conftest import run_cli

Q_WAIT = "we are waiting for Opal to reset the passwords before we can pull the stock reports"
Q_LOGIN = "I successfully logged in with the new credentials this morning"


def test_ac_met_marks_the_visible_criterion_after_a_human_drop(project):
    run_cli(project, "setup")
    run_cli(project, "sync")
    cfg = C.load(project)
    st = cfg.store
    d1, d2 = [x["id"] for x in run_cli(project, "pending")["items"]]

    # A story with three acceptance criteria.
    r = L.apply(st, cfg, d1, [
        {"op": "create", "kind": "story", "parent": "E01", "title": "Stock report automated",
         "ac": ["Export runs with no manual steps",
                "Totals reconcile against the warehouse ledger",
                "Client signs off on the report"],
         "quote": Q_WAIT}])
    assert r["rejected"] == [], r["rejected"]
    L.mark_processed(st, d1)

    # The manager drops criterion #1 ("Export ..."). The visible list is now  1. Totals   2. Sign-off.
    L.human_event(st, {"op": "ac_set", "node": "S001", "ac": 1, "drop": True})

    # A later meeting: the agent reports the *visible* #2 (the sign-off) is met; it is verified.
    L.apply(st, cfg, d2, [{"op": "ac_met", "node": "S001", "ac": 2, "quote": Q_LOGIN}])
    L.mark_processed(st, d2)
    pk = L.verify_prep(st)
    L.verify_apply(st, [{"mid": p["mid"], "verdict": "supports"} for p in pk])

    N, *_ = L.fold(st)
    met = {x["text"]: x["met"] for x in N["S001"]["ac"]}

    # CORRECT: the sign-off (visible #2) is met; totals (visible #1) is not.
    assert met == {"Totals reconcile against the warehouse ledger": False,
                   "Client signs off on the report": True}, met
