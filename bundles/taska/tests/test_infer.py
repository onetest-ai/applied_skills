"""Inferred closures: a done/held task proposes what it logically implies; every proposal waits for Confirm.
Invented data only."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))
sys.path.insert(0, os.path.dirname(__file__))

from taska_lib import infer as I, judge as J, ledger as L, store as S

EV = "2026-10-21_meetings_ws000001"
QUOTE = "we made it through every topic we hoped to cover today"


class Cfg:
    working_days = [1, 2, 3, 4, 5]
    name = "Demo"
    people = []

    def canonical(self, x):
        return x


def _create(i, kind, title, parent=None, created="2026-10-01", ac=(), depends_on=(), type_=None):
    return {"type": "create", "mid": "c" + i, "id": i, "kind": kind, "by": "plan", "evidence": None, "date": created,
            "fields": {"title": title, "parent": parent, "ac": list(ac), "depends_on": list(depends_on), "type": type_}}


def store():
    d = __import__("tempfile").mkdtemp()
    S.ensure(d)
    S.write_evidence(d, {"id": EV, "kind": "meeting", "at": "2026-10-21T16:00:00", "title": "Workshop", "source": "meetings",
                         "text": "", "turns": [{"speaker": "Sam Lee", "ts": "01:10:00", "text": QUOTE}]})
    S.append(d, [
        _create("E01", "epic", "Roadmap"),
        _create("S001", "story", "Client workshop held", "E01", ac=["Contacts collected", "The workshop is held with the client"]),
        _create("T010", "task", "Client workshop", "S001", depends_on=["T011"], type_="session"),
        _create("T011", "task", "Send the workshop invite", "S001", type_="action"),
        _create("T012", "task", "Send the client an overview of the workshop flow", "S001", type_="action"),
        _create("T013", "task", "Plan the follow-up session", "S001", created="2026-10-22", type_="session"),
        {"type": "agent", "op": "status", "mid": "h1", "node": "T010", "effect": "held", "by": "agent", "evidence": EV,
         "date": "2026-10-21", "quote": QUOTE, "source": "meetings"},
        {"type": "verdict", "mid": "h1", "verdict": "supports", "by": "verifier"},
    ])
    return d


def fake_ask(scores):
    def ask(state, question, url=None, timeout=None):
        for (frag, task), p in scores.items():
            if frag in question and task in json.dumps(state):
                return p
        return 0.1
    return ask


MUST = "finished before"
CRIT = "criterion"


def sugg(d, node):
    N, *_ = L.fold(d)
    return [s for s in N[node]["suggestions"] if s["status"] == "pending"]


# ---- Task 1: pending ac_met + cause ----
def test_pending_ac_met_waits_and_accept_meets_it():
    d = store()
    r = L.apply(d, Cfg(), EV, [{"op": "ac_met", "node": "S001", "ac": 2, "quote": QUOTE, "pending": "human",
                                "cause": "T010", "why": "w", "suggested": "criterion 2 → met"}])
    assert r["events"] == 1, r
    N, *_ = L.fold(d)
    assert not N["S001"]["ac"][1]["met"]
    s = [x for x in N["S001"]["suggestions"] if x["op"] == "ac_met"][0]
    assert s["status"] == "pending" and s["cause"] == "T010" and s["ac"] == 2
    L.human_event(d, {"op": "accept", "target": s["mid"]})
    N, *_ = L.fold(d)
    assert N["S001"]["ac"][1]["met"]


# ---- Task 2: judge.ask ----
def test_ask_returns_the_noul_probability(monkeypatch):
    class R_:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"answers": {"q": {"type": "noul", "noul": 0.42}}}).encode()
    monkeypatch.setattr(J.urllib.request, "urlopen", lambda *a, **k: R_())
    assert J.ask({"a": 1}, "Is it?", url="http://127.0.0.1:1/v1/systemone") == 0.42


def test_ask_unreachable_raises():
    with pytest.raises(J.JudgeUnavailable):
        J.ask({"a": 1}, "Is it?", url="http://127.0.0.1:9/v1/systemone", timeout=1)


# ---- Task 3: infer ----
def test_rule_proposes_open_prerequisite_of_a_held_task():
    d = store()
    rep = I.run(d, Cfg(), ask=fake_ask({}))
    s = sugg(d, "T011")
    assert s and s[0]["effect"] == "done" and s[0]["cause"] == "T010" and s[0]["quote"] == QUOTE
    assert "T010" in s[0]["why"] and rep["proposed"] >= 1


def test_model_proposes_a_missing_prerequisite_above_threshold():
    d = store()
    I.run(d, Cfg(), ask=fake_ask({(MUST, "overview of the workshop flow"): 0.82}))
    s = sugg(d, "T012")
    assert s and s[0]["cause"] == "T010" and s[0]["gate"]["score"] == 0.82


def test_model_below_threshold_proposes_nothing():
    d = store()
    I.run(d, Cfg(), ask=fake_ask({(MUST, "overview of the workshop flow"): 0.6}))
    assert not sugg(d, "T012")


def test_task_created_after_the_cause_is_not_a_candidate():
    d = store()
    I.run(d, Cfg(), ask=fake_ask({(MUST, "follow-up session"): 0.99}))
    assert not sugg(d, "T013")


def test_criterion_best_child_only_and_at_least_090():
    d = store()
    I.run(d, Cfg(), ask=fake_ask({(CRIT, "workshop is held"): 0.95}))
    s = [x for x in sugg(d, "S001") if x["op"] == "ac_met"]
    assert len(s) == 1 and s[0]["ac"] == 2 and s[0]["cause"] == "T010"
    d2 = store()
    I.run(d2, Cfg(), ask=fake_ask({(CRIT, "workshop is held"): 0.85}))
    assert not [x for x in sugg(d2, "S001") if x["op"] == "ac_met"]


def test_rerun_does_not_duplicate():
    d = store()
    ask = fake_ask({(MUST, "overview of the workshop flow"): 0.82})
    I.run(d, Cfg(), ask=ask)
    n = len(S.events(d))
    I.run(d, Cfg(), ask=ask)
    assert len(S.events(d)) == n


def test_human_closed_cause_is_skipped_with_reason():
    d = store()
    S.append(d, [_create("T020", "task", "Kickoff call", "S001", depends_on=["T011"])])
    L.human_event(d, {"op": "edit", "node": "T020", "set": {"state": "done"}})
    rep = I.run(d, Cfg(), ask=fake_ask({}))
    assert any(x["node"] == "T020" and "no evidence" in x["reason"] for x in rep["skipped"])


def test_model_down_still_runs_the_rule():
    def down(*a, **k):
        raise J.JudgeUnavailable("decision model not reachable at http://127.0.0.1:8009")
    d = store()
    rep = I.run(d, Cfg(), ask=down)
    assert sugg(d, "T011") and rep["model"].startswith("skipped")


def test_closures_never_apply_by_themselves():
    d = store()
    I.run(d, Cfg(), ask=fake_ask({(MUST, "overview of the workshop flow"): 0.9, (CRIT, "workshop is held"): 0.95}))
    N, *_ = L.fold(d)
    assert N["T011"]["state"] == "open" and N["T012"]["state"] == "open" and not N["S001"]["ac"][1]["met"]


def test_cli_infer_on_an_empty_project(tmp_path):
    import subprocess
    taska = os.path.join(os.path.dirname(__file__), "../skills/run/taska.py")
    p = tmp_path / "p"; p.mkdir()
    subprocess.run([sys.executable, taska, "--project", str(p), "init", "--name", "Demo"], check=True, capture_output=True)
    r = subprocess.run([sys.executable, taska, "--project", str(p), "infer"], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert out["status"] == "ok" and out["proposed"] == 0


def test_a_pending_progress_proposal_does_not_hide_the_closure():
    d = store()
    S.append(d, [{"type": "agent", "op": "status", "mid": "pg1", "node": "T011", "effect": "progress", "by": "agent",
                  "evidence": EV, "date": "2026-10-21", "quote": QUOTE, "pending": "human", "source": "teams"}])
    I.run(d, Cfg(), ask=fake_ask({}))
    assert any(s["effect"] == "done" and s.get("cause") == "T010" for s in sugg(d, "T011"))
