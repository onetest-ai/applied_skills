"""The client deck data (`taska pack --json`) hides the same people matters as the weekly report. Invented data only."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from taska_lib import pack as PK, report as H, store as S
import test_report as TR


def _project(tmp_path):
    cfg = TR._with_decisions(tmp_path)
    S.write_evidence(cfg.store, {"id": "2026-03-06_meetings_aaaa0004", "kind": "meeting", "at": "2026-03-06T10:00:00",
                                 "title": "Roster call", "source": "meetings", "text": "Ravi rolls off the Harbor yard team. Tamsin sign-off owed."})
    S.append(cfg.store, [
        {"type": "create", "mid": "c9", "id": "S002", "kind": "story", "by": "agent", "evidence": "2026-03-06_meetings_aaaa0004", "date": "2026-03-06",
         "quote": "Ravi rolls off the Harbor yard team", "fields": {"title": "Ravi rolls off the yard team", "parent": "E01", "ac": [], "depends_on": []}},
        {"type": "create", "mid": "c10", "id": "T004", "kind": "task", "by": "agent", "evidence": "2026-03-06_meetings_aaaa0004", "date": "2026-03-06",
         "quote": "Ravi moves to half time from the 20th", "fields": {"title": "Ravi half time paperwork", "parent": "S001", "type": "dependency",
                                                                  "client_owed": True, "client_owner": "Opal Brennick", "ac": [], "depends_on": []}},
        {"type": "processed", "evidence": "2026-03-06_meetings_aaaa0004", "by": "agent", "at": "2026-03-06T11:00:00"}])
    return cfg


def test_client_deck_data_has_no_trace_of_a_people_matter(tmp_path):
    cfg = _project(tmp_path)
    out = PK.build_client(cfg.store, cfg, 7)
    blob = json.dumps(out)
    assert "Move Checkout to 50% of users" in blob
    for trace in ("Ravi", "half time", "rolls off", "D002", "T003", "T004", "S002"):
        assert trace not in blob, trace
    assert any(m["title"] == "Yard plan" for m in out["slide3"]["milestones"])


def test_the_status_pack_markdown_hides_them_too(tmp_path):
    cfg = _project(tmp_path)
    md = PK.build(cfg.store, cfg, 7)
    assert "Move Checkout to 50% of users" in md and "Ravi" not in md and "half time" not in md


def test_report_and_pack_share_one_definition(tmp_path):
    cfg = _project(tmp_path)
    N = H.L.fold(cfg.store)[0]
    assert H.hidden_ids is PK.hidden_ids
    assert {"D002", "T003", "T004", "S002"} <= H.hidden_ids(cfg, N) and "D003" not in H.hidden_ids(cfg, N)


EV, STAFF = "2026-03-06_meetings_aaaa0004", "Ravi moves to half time from the 20th"


def _create(mid, i, kind, title, quote, **fields):
    return {"type": "create", "mid": mid, "id": i, "kind": kind, "by": "agent", "evidence": EV, "date": "2026-03-06", "quote": quote,
            "fields": {"title": title, "parent": fields.pop("parent", "E01"), "ac": [], "depends_on": [], **fields}}


def _leaky(tmp_path):
    cfg = _project(tmp_path)
    S.append(cfg.store, [
        _create("d1", "D005", "decision", "Zebra handover pack", STAFF, due="2026-03-01"),
        _create("d2", "S003", "story", "Pilot launch", "pilot launch is next", depends_on=["D005"], due="2026-03-20"),
        _create("d3", "S004", "story", "Dock schedule", "the dock schedule is due", due="2026-03-01"),
        {"type": "agent", "op": "status", "mid": "u1", "by": "agent", "node": "S004", "effect": "progress", "evidence": EV, "date": "2026-03-06", "quote": STAFF},
        _create("d4", "T007", "task", "Customs paperwork", "customs paperwork waits on Opal", parent="S002", type="dependency",
                client_owed=True, client_owner="Opal Brennick"),
        _create("d5", "T009", "task", "Quokka cover plan", STAFF, parent="S001", type="action", depends_on=["T007"]),
        _create("d6", "T010", "task", "Roof inspection", "the roof inspection is booked", parent="S001", type="action"),
        {"type": "agent", "op": "status", "mid": "x1", "by": "agent", "node": "T010", "effect": "done", "evidence": EV, "date": "2026-03-06", "quote": STAFF},
        {"type": "verdict", "mid": "x1", "verdict": "weak", "by": "verifier"},
        _create("d7", "T011", "task", "Berth repaint", "the berth repaint is set", parent="S001", type="action", due="2026-03-10"),
        {"type": "agent", "op": "status", "mid": "x2", "by": "agent", "node": "T011", "effect": "delayed", "evidence": EV, "date": "2026-03-06", "quote": STAFF},
        {"type": "verdict", "mid": "x2", "verdict": "supports", "by": "verifier"},
        {"type": "human", "op": "edit", "by": "human", "mid": "w1", "node": "T007", "evidence": "2026-03-04_meetings_aaaa0001", "date": "2026-03-04",
         "at": "2026-03-04T12:00:00", "set": {"waiting_for": {"who": "Opal Brennick", "what": "Ravi roll-off sign-off"}}}])
    return cfg


def test_a_story_waiting_on_a_hidden_task_names_nothing_hidden(tmp_path):
    cfg = _leaky(tmp_path)
    blob = json.dumps(PK.build_client(cfg.store, cfg, 7)) + PK.build(cfg.store, cfg, 7)
    assert "Pilot launch" in blob and "Zebra" not in blob and "Quokka" not in blob


def test_a_clean_node_whose_newest_quote_is_a_staffing_sentence_leaves_no_trace(tmp_path):
    cfg = _leaky(tmp_path)
    blob = json.dumps(PK.build_client(cfg.store, cfg, 7)) + PK.build(cfg.store, cfg, 7)
    assert "Dock schedule" in blob and "half time" not in blob and "Ravi" not in blob


def test_a_staffing_wait_text_is_dropped_from_the_waiting_section(tmp_path):
    cfg = _leaky(tmp_path)
    md = PK.build(cfg.store, cfg, 7)
    line = next(l for l in md.splitlines() if "Customs paperwork" in l)
    assert "roll-off" not in md and "since" in line


def test_holds_up_counts_only_visible_blockers(tmp_path):
    cfg = _leaky(tmp_path)
    md = PK.build(cfg.store, cfg, 7)
    assert "holds up" not in next(l for l in md.splitlines() if "Customs paperwork" in l)


def test_a_staffing_closing_quote_leaves_the_clean_item_and_its_ask(tmp_path):
    cfg = _leaky(tmp_path)
    out = PK.build_client(cfg.store, cfg, 7)
    blob = json.dumps(out) + PK.build(cfg.store, cfg, 7)
    assert "Roof inspection" in json.dumps(out["slide4"]["priorities"])
    assert "Can we close “Roof inspection”?" in PK.build(cfg.store, cfg, 7)
    assert "half time" not in blob and "Ravi" not in blob


def test_a_staffing_delay_quote_keeps_the_slipped_line_without_it(tmp_path):
    cfg = _leaky(tmp_path)
    md = PK.build(cfg.store, cfg, 7)
    line = next(l for l in md.splitlines() if "Slipped" in l and "Berth repaint" in l)
    assert "half time" not in line
