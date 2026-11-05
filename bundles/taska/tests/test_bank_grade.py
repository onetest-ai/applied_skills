"""evals/bank.py grading: spans from the ledger, the code-only checks, unscored and unlocated nodes. Invented data only."""
import json
import sys
import textwrap

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402
from taska_lib import config as C, ledger as L, store as S, text as TX  # noqa: E402

MEET = "2026-03-02_meetings_ab12cd34"
S1 = "Nora will draft the warehouse onboarding checklist for Opal and share it before the Thursday review meeting"
S2 = "We could maybe repaint the whole dock in harbor blue if the budget ever allows something like that"
S3 = "Ravi will rebuild the dock schedule export so the yard team can plan the night shift properly"
S4 = "Opal will collect the scanner serial numbers from both yards and send them to us next week"
FILL = " and then people talked about the weather and the lunch menu for quite a while. "
TEXT = FILL.join([S1, S2, S3, S4])
NORM = TX.norm(TEXT)


def span(s):
    a = NORM.find(TX.norm(s))
    return [a, a + len(TX.norm(s))]


def node(nid, kind, quote, owner="Nora Quill", by="agent", **fields):
    f = {"title": f"{nid} title", "parent": "E01", "internal_owner": owner, "client_owner": None, "client_owed": False,
         "ac": [], "due": None, "plan": None} | fields
    return {"type": "create", "mid": f"m{nid}", "id": nid, "kind": kind, "by": by, "evidence": MEET, "date": "2026-03-02",
            "quote": quote, "fields": f}


def project(tmp_path, nodes=(), processed=True, read=None, text=TEXT, evidence=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
        [[people]]
        name = "Ravi Tamsin"
        [[people]]
        name = "Opal Brennick"
        side = "client"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    S.write_evidence(cfg.store, evidence or {"id": MEET, "kind": "meeting", "at": "2026-03-02T12:00:00", "title": "Harbor daily sync",
                                             "source": "meetings", "text": text})
    S.append(cfg.store, [{"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None,
                          "date": "2026-03-01", "fields": {"title": "Onboarding"}}, *nodes])
    if processed:
        L.mark_processed(cfg.store, MEET, "read", decisions_read=read)
    return tmp_path


def task(**kw):
    sha = B.transcript_sha(S.evidence(C.load(kw.pop("copy")).store)[MEET]) if "copy" in kw else "x"
    t = {"task": "ab12cd34", "evidence": MEET, "transcript_sha": sha,
         "must_create": [{"id": "k-001", "span": span(S1), "quote": S1, "kind": "task", "commitment": "committed",
                          "owner": "Ravi Tamsin", "caps": ["committed"], "source": "invented"}],
         "must_not_create": [{"id": "n-001", "span": span(S2), "quote": S2, "why": "brainstorm", "caps": ["not_work"],
                              "source": "invented"}],
         "outcome": list(B.OUTCOMES), "budget": {"cost_usd": 1, "minutes": 5}}
    return t | kw


def grade(tmp_path, nodes=(), tk=None, **kw):
    p = project(tmp_path, nodes, **kw)
    t = tk(p) if tk else task(copy=p)
    return B.grade(t, p), p


def c(g, cid):
    return next(x for x in g["checks"] if x["id"] == cid)


def test_a_node_inside_the_span_passes_must_create_and_reports_kind_and_owner(tmp_path):
    g, _ = grade(tmp_path, [node("T001", "task", S1)], read={"1": "recorded"})
    assert c(g, "k-001")["result"] == "pass" and c(g, "k-001")["node"] == "T001" and c(g, "k-001")["owner_ok"] is False
    assert c(g, "k-001")["kind_ok"] is True and c(g, "k-001")["at"] is None
    assert g["pass"] is True and g["refused"] is None and g["unscored"] == []


def test_a_node_merely_near_the_span_does_not_pass(tmp_path):
    g, _ = grade(tmp_path, [node("T001", "task", "talked about the weather and the lunch menu for quite a while")],
                 read={"1": "recorded"})
    assert c(g, "k-001")["result"] == "fail" and c(g, "k-001")["node"] is None and g["pass"] is False


def test_a_node_quoting_a_must_not_span_fails_it_and_a_pending_card_does_not(tmp_path):
    g, _ = grade(tmp_path / "a", [node("T001", "task", S1), node("T002", "task", S2)], read={"1": "recorded"})
    assert c(g, "n-001")["result"] == "fail" and c(g, "n-001")["node"] == "T002" and g["pass"] is False
    card = {"type": "agent", "op": "propose", "mid": "p1", "node": "T001", "evidence": MEET, "date": "2026-03-02", "by": "agent",
            "quote": S2, "proposal": {"kind": "task", "title": "Repaint the dock"}}
    p = project(tmp_path / "b", [node("T001", "task", S1)], read={"1": "recorded"})
    S.append(C.load(p).store, [card])
    g = B.grade(task(copy=p), p)
    assert c(g, "n-001")["result"] == "pass" and g["pass"] is True


def test_two_live_tasks_for_one_item_fail_no_duplicate_but_a_story_holding_its_task_passes(tmp_path):
    g, _ = grade(tmp_path / "a", [node("T001", "task", S1), node("T002", "task", S1)], read={"1": "recorded"})
    assert c(g, "k-001:dup")["type"] == "no_duplicate" and c(g, "k-001:dup")["result"] == "fail"
    story = node("S001", "story", S1, parent="E01")
    t = node("T001", "task", S1, parent="S001")
    g, _ = grade(tmp_path / "b", [story, t], read={"1": "recorded"})
    assert c(g, "k-001:dup")["result"] == "pass" and c(g, "k-001")["node"] == "T001"


def test_outcome_checks_read_the_ledger(tmp_path):
    g, _ = grade(tmp_path / "a", [node("T001", "task", S1)], processed=False)
    assert c(g, "processed")["result"] == "fail" and c(g, "decisions_read")["result"] == "fail" and g["pass"] is False
    g, _ = grade(tmp_path / "b", [node("T001", "task", S1, title="Extend Ravi Tamsin contract and raise his rate")], read={"1": "recorded"})
    assert c(g, "no_people_matter")["result"] == "fail" and c(g, "processed")["result"] == "pass"
    assert c(g, "decisions_read")["result"] == "pass" and c(g, "pinned_untouched")["result"] == "pass"
    human = {"type": "human", "op": "edit", "mid": "h1", "node": "T001", "date": "2026-03-03", "by": "human", "set": {"title": "My title"}}
    agent = {"type": "agent", "op": "revise", "mid": "r1", "node": "T001", "evidence": MEET, "date": "2026-03-04", "by": "agent",
             "title": "Agent title"}
    p = project(tmp_path / "c", [node("T001", "task", S1)], read={"1": "recorded"})
    S.append(C.load(p).store, [human, agent])
    assert c(B.grade(task(copy=p), p), "pinned_untouched")["result"] == "pass"


def test_an_applied_agent_write_to_a_pinned_field_is_a_violation():
    n = {"history": [{"by": "human", "set": {"title": "Mine"}, "applied": True},
                     {"by": "agent", "op": "revise", "title": "Theirs", "applied": True}]}
    assert B.pinned_violations({"T001": n}) == ["T001"]
    n["history"][1]["applied"] = False
    assert B.pinned_violations({"T001": n}) == []


def test_decisions_read_is_na_on_code_before_the_second_read(tmp_path):
    p = project(tmp_path, [node("T001", "task", S1)])
    g = B.grade(task(copy=p), p, code_has_second_read=False)
    assert c(g, "decisions_read")["result"] == "n/a" and g["pass"] is True


def test_a_node_no_check_speaks_about_is_unscored_and_never_fails(tmp_path):
    g, _ = grade(tmp_path, [node("T001", "task", S1), node("T004", "task", S4, owner="Opal Brennick")], read={"1": "recorded"})
    assert g["unscored"] == [{"node": "T004", "quote": S4}] and g["pass"] is True


def test_a_node_only_found_without_backchannel_is_unlocated(tmp_path):
    ev = {"id": MEET, "kind": "meeting", "at": "2026-03-02T12:00:00", "title": "Harbor daily sync", "source": "meetings",
          "turns": [{"speaker": "Nora Quill", "text": "Nora will draft the checklist", "ts": "00:01:00"},
                    {"speaker": "Ravi Tamsin", "text": "yeah", "ts": "00:01:02", "bc": True},
                    {"speaker": "Nora Quill", "text": "for Opal before Thursday", "ts": "00:01:03"}]}
    q = "Nora will draft the checklist for Opal before Thursday"
    p = project(tmp_path, [node("T001", "task", q)], evidence=ev, read={"1": "recorded"})
    assert S.quote_offset(S.evidence(C.load(p).store)[MEET], q) == -1
    t = task(copy=p, must_create=[], must_not_create=[])
    g = B.grade(t, p)
    assert g["unlocated"] == [{"node": "T001", "quote": q}] and g["unscored"] == [] and g["pass"] is True


def test_a_changed_transcript_refuses_the_task(tmp_path):
    p = project(tmp_path, [node("T001", "task", S1)])
    g = B.grade(dict(task(copy=p), transcript_sha="0" * 64), p)
    assert g["refused"] and "transcript" in g["refused"] and g["checks"] == [] and g["pass"] is False


def test_node_spans_are_offsets_in_the_normalised_text(tmp_path):
    p = project(tmp_path, [node("T001", "task", S1), node("T009", "task", "never said anywhere in the call")])
    cfg = C.load(p)
    evs, ev = S.events(cfg.store), S.evidence(cfg.store)
    N, *_ = L.fold(cfg.store, evs=evs, ev=ev)
    sp = B.node_spans(cfg.store, ev, evs, N)
    a, b = span(S1)
    assert sp["T001"] == (MEET, a, b) and sp["T009"] is None and "E01" not in sp
    assert B.transcript_sha(ev[MEET]) == B.transcript_sha(ev[MEET]) and len(B.transcript_sha(ev[MEET])) == 64


def test_must_create_boundary_is_half_the_check_span_or_node_inside_it():
    chk = {"span": [100, 120]}
    hit = lambda a, b: B._hits_create(chk, ("e", a, b))
    assert hit(110, 130) == 10 and hit(90, 110) == 10      # exactly half of the check: pass
    assert hit(111, 131) == 9                              # just under half, node not inside: caller rejects
    assert B._passes_create(chk, ("e", 110, 130)) and B._passes_create(chk, ("e", 90, 110))
    assert not B._passes_create(chk, ("e", 111, 131))
    assert not B._passes_create(chk, ("e", 119, 200))      # a larger node overlapping under half
    assert B._passes_create(chk, ("e", 105, 108))          # inside the check, under half of it: inside rule


def test_must_not_boundary_is_half_the_node_span():
    chk = {"span": [100, 120]}
    assert B._violates_not(chk, ("e", 110, 130))           # overlap 10 of a 20-long node: exactly half
    assert not B._violates_not(chk, ("e", 111, 131))       # 9 of 20
    assert B._violates_not(chk, ("e", 105, 108))           # inside, whole node overlaps
    assert not B._violates_not(chk, ("e", 120, 130))


def test_a_node_overlapping_exactly_half_the_check_passes_end_to_end_and_just_under_fails(tmp_path):
    a, b = span(S3)
    n = b - a
    for i, (lo, hi, want) in enumerate([(b - 10, b + 10, "pass"), (b - 9, b + 11, "fail")]):
        tk = lambda p: task(copy=p, must_not_create=[], must_create=[
            {"id": "k-9", "span": [lo, hi], "quote": S3, "kind": "task", "commitment": "committed", "owner": "Ravi Tamsin",
             "caps": [], "source": "invented"}])
        g, _ = grade(tmp_path / str(i), [node("T003", "task", S3)], tk=tk, read={"1": "recorded"})
        assert c(g, "k-9")["result"] == want


def test_a_create_event_without_by_is_a_live_agent_node(tmp_path):
    n = node("T001", "task", S1)
    del n["by"]
    g, _ = grade(tmp_path, [n], read={"1": "recorded"})
    assert c(g, "k-001")["result"] == "pass" and c(g, "k-001")["node"] == "T001" and c(g, "no_people_matter")["result"] == "pass"


def test_the_dup_check_is_na_when_nothing_hit_the_span_so_shares_are_not_inflated(tmp_path):
    g, _ = grade(tmp_path, [], read={"1": "recorded"})
    assert c(g, "k-001")["result"] == "fail" and c(g, "k-001:dup")["result"] == "n/a"
    trial = {"task": "ab12cd34", "trial": 1, "status": "done", "pass": g["pass"], "checks": g["checks"]}
    assert B.aggregate([trial])["caps"]["committed"] == {"ok": 0, "n": 1}


def test_failed_checks_carry_the_key_quote_and_a_must_not_failure_the_hitting_nodes_quote(tmp_path):
    inner = "repaint the whole dock in harbor blue"
    g, _ = grade(tmp_path / "a", [node("T002", "task", inner)], read={"1": "recorded"})
    assert c(g, "k-001")["result"] == "fail" and c(g, "k-001")["quote"] == S1
    assert c(g, "n-001")["result"] == "fail" and c(g, "n-001")["quote"] == S2 and c(g, "n-001")["node_quote"] == inner
    g, _ = grade(tmp_path / "b", [node("T001", "task", S1)], read={"1": "recorded"})
    assert c(g, "n-001")["quote"] == S2 and c(g, "n-001").get("node_quote") is None


def _card(mid, quote, kind="task"):
    return {"type": "agent", "op": "propose", "mid": mid, "node": "T900", "evidence": MEET, "date": "2026-03-02", "by": "agent",
            "quote": quote, "proposal": {"kind": kind, "title": "Maybe"}}


def _proposed_grade(tmp_path, nodes=(), cards=()):
    p = project(tmp_path, nodes, read={"1": "recorded"})
    S.append(C.load(p).store, list(cards))
    mc = dict(task()["must_create"][0], commitment="proposed")
    return B.grade(task(copy=p, must_create=[mc], must_not_create=[]), p)


def test_a_proposed_row_passes_on_a_pending_card_or_a_node_and_fails_on_neither(tmp_path):
    g = _proposed_grade(tmp_path / "a", cards=[_card("p1", S1)])
    assert c(g, "k-001")["result"] == "pass" and c(g, "k-001")["by"] == "card" and c(g, "k-001:dup")["result"] == "pass"
    g = _proposed_grade(tmp_path / "b", nodes=[node("T001", "task", S1)])
    assert c(g, "k-001")["result"] == "pass" and c(g, "k-001")["by"] == "node" and c(g, "k-001")["node"] == "T001"
    g = _proposed_grade(tmp_path / "c")
    assert c(g, "k-001")["result"] == "fail" and c(g, "k-001")["by"] is None and c(g, "k-001:dup")["result"] == "n/a"
    g = _proposed_grade(tmp_path / "d", nodes=[node("T001", "task", S1)], cards=[_card("p1", S1)])
    assert c(g, "k-001")["by"] == "node" and c(g, "k-001:dup")["result"] == "pass"   # one hit, not a duplicate
    g = _proposed_grade(tmp_path / "e", cards=[_card("p1", S2)])
    assert c(g, "k-001")["result"] == "fail"
    p = project(tmp_path / "f", read={"1": "recorded"})   # a decided card is not pending
    S.append(C.load(p).store, [_card("p1", S1), {"type": "human", "op": "reject", "mid": "r1", "target": "p1", "by": "human"}])
    mc = dict(task()["must_create"][0], commitment="proposed")
    assert c(B.grade(task(copy=p, must_create=[mc], must_not_create=[]), p), "k-001")["result"] == "fail"


def test_a_declined_row_passes_only_on_a_decision_or_risk_node(tmp_path):
    mc = dict(task()["must_create"][0], commitment="declined", kind="decision")
    for name, kind, want in (("a", "task", "fail"), ("b", "decision", "pass"), ("c", "risk", "pass")):
        p = project(tmp_path / name, [node("T001", kind, S1)], read={"1": "recorded"})
        S.append(C.load(p).store, [_card("p1", S1)])
        g = B.grade(task(copy=p, must_create=[mc], must_not_create=[]), p)
        assert c(g, "k-001")["result"] == want, name


def test_a_task_level_no_duplicate_row_fails_on_a_new_node_restating_tracked_work(tmp_path):
    dup = [{"id": "d-001", "span": span(S3), "quote": S3, "why": "duplicate", "caps": ["continuity"], "source": "invented"}]
    g, _ = grade(tmp_path / "a", [node("T001", "task", S1), node("T002", "task", S3)], read={"1": "recorded"},
                 tk=lambda p: task(copy=p, no_duplicate=dup))
    assert c(g, "d-001")["type"] == "no_duplicate" and c(g, "d-001")["result"] == "fail" and c(g, "d-001")["node"] == "T002"
    assert g["pass"] is False and g["unscored"] == []
    g, _ = grade(tmp_path / "b", [node("T001", "task", S1)], read={"1": "recorded"},
                 tk=lambda p: task(copy=p, no_duplicate=dup))
    assert c(g, "d-001")["result"] == "pass" and g["pass"] is True


def test_a_pending_card_on_a_resynced_copy_counts_for_the_original_meeting(tmp_path):
    p = project(tmp_path, read={"1": "recorded"})
    store = C.load(p).store
    copy_id = "2026-03-02_teams_ff00ee11"
    (store / "evidence" / f"{copy_id}.json").write_text(json.dumps(
        {"id": copy_id, "kind": "meeting", "at": "2026-03-02T12:00:00", "date": "2026-03-02", "title": "Harbor daily sync",
         "source": "teams", "text": TEXT, "duplicate_of": MEET}))
    S.append(store, [_card("p1", S1) | {"evidence": copy_id}])
    mc = dict(task()["must_create"][0], commitment="proposed")
    g = B.grade(task(copy=p, must_create=[mc], must_not_create=[]), p)
    assert c(g, "k-001")["result"] == "pass" and c(g, "k-001")["by"] == "card"
