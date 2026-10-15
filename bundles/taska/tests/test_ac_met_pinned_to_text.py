"""An `ac_met` claim is pinned to the criterion the agent meant, not to a bare number that is re-counted
later: the verifier is briefed on that criterion's text even after a later drop, and a number outside
the visible criteria is rejected. All names are invented."""
from taska_lib import config as C, ledger as L
from conftest import run_cli

Q_WAIT = "we are waiting for Opal to reset the passwords before we can pull the stock reports"
Q_LOGIN = "I successfully logged in with the new credentials this morning"
AC = ["Export runs with no manual steps",
      "Totals reconcile against the warehouse ledger",
      "Client signs off on the report"]


def _setup(project):
    run_cli(project, "setup")
    run_cli(project, "sync")
    cfg = C.load(project)
    d1, d2 = [x["id"] for x in run_cli(project, "pending")["items"]]
    return cfg, cfg.store, d1, d2


def test_verifier_is_briefed_on_the_criterion_the_agent_meant_after_a_later_drop(project):
    cfg, st, d1, d2 = _setup(project)
    r = L.apply(st, cfg, d1, [{"op": "create", "kind": "story", "parent": "E01",
                               "title": "Stock report automated", "ac": AC, "quote": Q_WAIT}])
    assert r["rejected"] == [], r["rejected"]
    L.mark_processed(st, d1)

    # Agent reports the visible #2 ("Totals ...") met — before any drop.
    r2 = L.apply(st, cfg, d2, [{"op": "ac_met", "node": "S001", "ac": 2, "quote": Q_LOGIN}])
    assert r2["rejected"] == [], r2["rejected"]
    L.mark_processed(st, d2)

    # THEN the manager drops criterion #1 ("Export ..."), shifting the numbering.
    L.human_event(st, {"op": "ac_set", "node": "S001", "ac": 1, "drop": True})

    pk = L.verify_prep(st)
    acmet = next(p for p in pk if p["claimed"].startswith("acceptance criterion"))
    # CORRECT: the verifier is briefed about what the agent meant ("Totals ..."), not the shifted "sign off".
    assert "Totals reconcile against the warehouse ledger" in acmet["claimed"], acmet["claimed"]
    assert "Client signs off" not in acmet["claimed"], acmet["claimed"]


def test_ac_met_with_an_out_of_range_number_is_rejected(project):
    cfg, st, d1, d2 = _setup(project)
    L.apply(st, cfg, d1, [{"op": "create", "kind": "story", "parent": "E01", "title": "Two-criterion story",
                           "ac": AC[:2], "quote": Q_WAIT}])
    L.mark_processed(st, d1)

    r = L.apply(st, cfg, d2, [{"op": "ac_met", "node": "S001", "ac": 5, "quote": Q_LOGIN}])
    # CORRECT: ac 5 on a 2-criterion story is rejected at the gate, not silently recorded
    # (the gate's wording — "out of range" / "the 1-based number of one of its criteria" — may vary).
    assert r["rejected"] and r["by_op"].get("ac_met", 0) == 0, r
