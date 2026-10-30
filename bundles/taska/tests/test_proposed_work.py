"""Proposed work: someone in a meeting proposes concrete work ("a good project for the yard team") but nobody commits
an owner or a date. It must not vanish: it becomes a Confirm card on its parent, and only your Confirm creates the
node. A team ([[teams]]) counts as an owner, and Confirm can hand the task to one of its members.
Invented data only (public repo)."""
import textwrap

import pytest

from taska_lib import config as C, ledger as L, store as S
from conftest import run_cli

EV = "2026-03-02_meetings_ab12cd34"
LINE = "it is a good project for the yard guys to digitise the whiteboard photos automatically"


@pytest.fixture
def proj(tmp_path):
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
        [[teams]]
        name = "Yard team"
        members = ["Nora Quill", "Ravi Tamsin"]
        aliases = ["yard guys", "the yard people"]
        """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    S.write_evidence(cfg.store, {"id": EV, "kind": "meeting", "at": "2026-03-02T10:00:00", "title": "Daily sync",
                                 "source": "meetings", "text": "",
                                 "turns": [{"speaker": "Opal Brennick", "ts": "00:19:29", "text": LINE}]})
    S.append(cfg.store, [
        {"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Automation"}},
        {"type": "create", "mid": "s1", "id": "S001", "kind": "story", "by": "agent", "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Workshop boards digitised", "parent": "E01", "ac": ["Boards readable"]}}])
    return cfg


def propose(cfg, **kw):
    p = {"op": "propose", "kind": "task", "parent": "S001", "type": "action", "quote": LINE,
         "title": "Prototype automatic recognition of whiteboard photos", "internal_owner": "yard guys",
         "why": "Opal proposed it as a project for the yard team; nobody took it yet."} | kw
    return L.apply(cfg.store, cfg, EV, [p])


def cards(cfg, node="S001"):
    N, *_ = L.fold(cfg.store)
    return [s for s in N[node]["suggestions"] if s["op"] == "propose"]


def test_team_aliases_resolve_to_the_team(proj):
    assert proj.canonical("the yard people") == "Yard team"
    assert proj.team("Yard team").members == ["Nora Quill", "Ravi Tamsin"]
    assert proj.side("yard guys") == "internal"
    assert proj.canonical("The Yard Guys") == "Yard team"   # spoken with an article


def test_a_proposal_waits_as_a_card_and_creates_nothing(proj):
    r = propose(proj)
    assert r["events"] == 1, r
    N, *_ = L.fold(proj.store)
    assert [n for n in N if n.startswith("T")] == []
    c = cards(proj)
    assert len(c) == 1 and c[0]["status"] == "pending" and c[0]["owner"] == "Yard team"
    assert c[0]["owner_options"] == ["Nora Quill", "Ravi Tamsin"] and c[0]["title"].startswith("Prototype")


def test_rerunning_the_extraction_does_not_duplicate_the_card(proj):
    propose(proj)
    propose(proj)
    assert len(cards(proj)) == 1


def test_confirm_creates_the_task_once_with_the_chosen_member(proj):
    propose(proj)
    mid = cards(proj)[0]["mid"]
    L.human_event(proj.store, {"op": "accept", "target": mid, "owner": "Ravi Tamsin"})
    N, *_ = L.fold(proj.store)
    t = [n for n in N.values() if n["kind"] == "task"]
    assert len(t) == 1 and t[0]["parent"] == "S001" and t[0]["internal_owner"] == "Ravi Tamsin"
    assert t[0]["title"] == "Prototype automatic recognition of whiteboard photos"
    assert cards(proj)[0]["status"] == "accept"
    with pytest.raises(ValueError):
        L.human_event(proj.store, {"op": "accept", "target": mid})
    N, *_ = L.fold(proj.store)
    assert sum(n["kind"] == "task" for n in N.values()) == 1


def test_confirm_without_a_choice_keeps_the_team(proj):
    propose(proj)
    L.human_event(proj.store, {"op": "accept", "target": cards(proj)[0]["mid"]})
    N, *_ = L.fold(proj.store)
    assert next(n for n in N.values() if n["kind"] == "task")["internal_owner"] == "Yard team"


def test_confirm_refuses_an_owner_who_is_not_a_known_person(proj):
    propose(proj)
    with pytest.raises(ValueError):
        L.human_event(proj.store, {"op": "accept", "target": cards(proj)[0]["mid"], "owner": "Somebody Else"})


def test_reject_creates_nothing(proj):
    propose(proj)
    L.human_event(proj.store, {"op": "reject", "target": cards(proj)[0]["mid"], "reason": "not_agreed"})
    N, *_ = L.fold(proj.store)
    assert not any(n["kind"] == "task" for n in N.values()) and cards(proj)[0]["status"] == "reject"


def test_a_proposed_story_needs_criteria_and_an_epic(proj):
    r = propose(proj, kind="story", parent="E01", title="Boards digitised")
    assert r["events"] == 0 and "acceptance criteria" in r["rejected"][0]["why"]
    r = propose(proj, kind="story", parent="S001", title="Boards digitised", ac=["Readable"])
    assert r["events"] == 0
    assert propose(proj, kind="story", parent="E01", title="Boards digitised", ac=["Readable"])["events"] == 1


def test_a_team_owner_clears_the_admission_bar_for_a_plain_create(proj):
    r = L.apply(proj.store, proj, EV, [{"op": "create", "kind": "task", "parent": "S001", "type": "action",
                                        "title": "Digitise the boards", "internal_owner": "yard guys", "quote": LINE}])
    assert r["events"] == 1, r
    N, *_ = L.fold(proj.store)
    assert next(n for n in N.values() if n["kind"] == "task")["internal_owner"] == "Yard team"


def test_context_lists_the_teams_for_the_extractor(proj):
    ctx = L.context(proj.store, proj)
    assert {"name": "Yard team", "members": ["Nora Quill", "Ravi Tamsin"], "aliases": ["yard guys", "the yard people"]} \
        in ctx["teams"]


def test_cli_sync_still_loads_a_config_with_teams(proj):
    assert run_cli(proj.project_dir, "status")["status"] == "ok"


def test_undo_of_a_confirm_removes_the_task_and_reopens_the_card(proj):
    propose(proj)
    mid = cards(proj)[0]["mid"]
    L.human_event(proj.store, {"op": "accept", "target": mid})
    acc = next(e for e in S.events(proj.store) if e.get("op") == "accept" and e.get("target") == mid)
    L.human_event(proj.store, {"op": "undo", "target": acc["mid"]})
    N, *_ = L.fold(proj.store)
    assert not any(n["kind"] == "task" for n in N.values())
    assert cards(proj)[0]["status"] == "pending"


def test_a_risk_or_decision_can_be_proposed_and_is_created_only_on_confirm(proj):
    # backfilled risks and decisions wait for your Confirm like tasks do (spec: weekly-status gate, B1)
    r = propose(proj, kind="risk", parent="E01", type=None, title="Photo quality may defeat recognition")
    assert r["events"] == 1, r
    N, *_ = L.fold(proj.store)
    assert not any(n["kind"] == "risk" for n in N.values())
    card = next(s for s in N["E01"]["suggestions"] if s["op"] == "propose")
    L.human_event(proj.store, {"op": "accept", "target": card["mid"]})
    N, *_ = L.fold(proj.store)
    risk = next(n for n in N.values() if n["kind"] == "risk")
    assert risk["id"] == "R001" and risk["parent"] == "E01" and risk["title"] == "Photo quality may defeat recognition"


def test_a_proposed_decision_needs_an_epic_or_story_parent(proj):
    assert propose(proj, kind="decision", parent="S001", type=None, title="Use the yard scanner")["events"] == 1
    assert propose(proj, kind="decision", parent="T999", type=None, title="Use the old scanner")["events"] == 0


def test_apply_reports_a_similar_open_node_and_never_rejects_for_it(proj):
    # the duplicate check at the source (r2 R11): the agent learns about the look-alike and can link or merge instead
    r = propose(proj, kind="story", parent="E01", type=None, title="Workshop boards digitised again", ac=["Readable"])
    assert r["events"] == 1 and r["rejected"] == []
    assert r["similar"][0]["matches"][0]["id"] == "S001"


def test_apply_reports_nothing_for_new_work(proj):
    r = propose(proj, kind="story", parent="E01", type=None, title="Forklift certification list collected", ac=["Listed"])
    assert r["similar"] == []


def test_after_undoing_a_confirm_the_card_can_be_decided_again(proj):
    propose(proj)
    mid = cards(proj)[0]["mid"]
    acc = L.human_event(proj.store, {"op": "accept", "target": mid})
    accept_mid = next(e["mid"] for e in S.events(proj.store) if e.get("op") == "accept")
    L.human_event(proj.store, {"op": "undo", "target": accept_mid})
    assert cards(proj)[0]["status"] == "pending"
    L.human_event(proj.store, {"op": "reject", "target": mid, "reason": "not_agreed"})   # must not raise
    assert cards(proj)[0]["status"] == "reject"


def test_a_confirmed_card_create_keeps_its_meeting_in_history(proj):
    # the report dates "last discussed" by the meeting a confirmed card came from; a hand-made create must not borrow one
    propose(proj)
    L.human_event(proj.store, {"op": "accept", "target": cards(proj)[0]["mid"]})
    N, *_ = L.fold(proj.store)
    t = next(n for n in N.values() if n["kind"] == "task")
    h = next(h for h in t["history"] if h.get("type") == "create")
    assert h.get("from_proposal") and h["date"] == "2026-03-02" and h["evidence"] == EV
