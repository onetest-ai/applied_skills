"""The weekly report (taska_lib/report.py): what goes in is decided from the tree (select), a stored sentence is reused
only while what it says is still true, the writer's text is checked against the cited facts, and the docx is built with
the client label, title, links and footer from [report] in taska.toml, never a name in the code. Moved from a project's
scripts (Oct 31) with these tests; invented data only."""
import datetime as dt
import json
import re
import textwrap
import zipfile

from taska_lib import config as C, report as H, store as S


def test_a_sentence_naming_a_stale_date_is_not_reused():
    it = {"id": "T101", "kind": "done", "state": "done", "date": "2026-03-04"}
    assert H.still_true("Held the depot workshop with the client on Mar 5 [T101].", it) is False
    assert H.still_true("Held the depot workshop with the client on Mar 4 [T101].", it) is True


def test_a_sentence_without_a_date_is_reused():
    it = {"id": "T101", "kind": "done", "state": "done", "date": "2026-03-04"}
    assert H.still_true("The depot workshop with the client took place [T101].", it) is True


def test_an_item_without_a_date_keeps_its_sentence():
    assert H.still_true("Due on Mar 9 [T101].", {"id": "T101", "kind": "overdue", "state": "open"}) is True


def test_a_past_week_is_reported_as_of_its_friday():
    import datetime as dt
    # Friday's data, built on the following Monday: the window ends on Friday, not on the build day
    assert H.as_of("2026-03-02", dt.date(2026, 3, 9)) == dt.date(2026, 3, 6)
    assert H.as_of("2026-03-02", dt.date(2026, 3, 4)) == dt.date(2026, 3, 4)   # the current week: as of today


def test_a_carried_row_follows_a_merge_to_the_surviving_item():
    N = {"T092": {"state": "merged", "merged_into": "T124"}, "T124": {"state": "open"}, "T200": {"state": "open"}}
    assert H.carry_ref(N, "T092", set()) == "T124"
    assert H.carry_ref(N, "T092", {"T124"}) is None          # the survivor already has its row: drop, never list twice
    assert H.carry_ref(N, "T200", set()) == "T200"
    assert H.carry_ref(N, None, set()) is None or H.carry_ref(N, None, set()) == None


def _n(i, kind="task", parent=None, due=None, news=None, state="open"):
    h = [{"date": news, "evidence": "m", "by": "agent"}] if news else []
    return {"id": i, "kind": kind, "parent": parent, "due": due, "state": state, "title": i, "history": h}


def _tree(*nodes):
    N = {"E1": _n("E1", "epic"), "E2": _n("E2", "epic"), "S1": _n("S1", "story", "E1"), "S2": _n("S2", "story", "E2")}
    N.update({n["id"]: n for n in nodes})
    return N


def test_priorities_are_due_soon_or_discussed_this_week_never_overdue():
    import datetime as dt
    N = _tree(_n("T1", parent="S1", due="2026-03-09"),                       # due next week
              _n("T2", parent="S1", news="2026-03-05"),                      # no date, discussed this week
              _n("T3", parent="S1", due="2026-03-03", news="2026-03-05"),    # overdue: confirm, not a priority
              _n("T4", parent="S1", news="2026-02-20"))                      # no date, not discussed lately
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), set())]
    assert ids == ["T1", "T2"]


def test_every_stream_with_live_work_gets_a_row():
    import datetime as dt
    many = [_n(f"T{i:02d}", parent="S1", due=f"2026-03-{9 + i % 5:02d}") for i in range(12)]
    N = _tree(*many, _n("S2x", kind="story", parent="E2", news="2026-03-03"))
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), set(), k=10)]
    assert "S2x" in ids and all(f"T{i:02d}" in ids for i in range(12))   # every deadline, and the second stream
    assert len(ids) == 13                                                  # 12 deadlines + 1 stream row (Oct 26 rule)


def test_a_hand_close_has_no_actual_date():
    # a hand edit carries the date of whatever evidence it was filed under, not when the work happened (Oct 26: T119
    # "actual Oct 24" was the date of a Saturday Teams message); only a meeting-evidenced close dates the milestone
    hand = {"history": [{"by": "human", "set": {"state": "done"}, "date": "2026-03-07"}]}
    met = {"history": [{"op": "status", "effect": "done", "applied": True, "by": "agent", "evidence": "m", "date": "2026-03-04"},
                       {"by": "human", "set": {"state": "done"}, "date": "2026-03-07"}]}
    assert H.closed_on(hand) is None
    assert H.closed_on(met) == "2026-03-04"


def test_pinned_priorities_are_always_shown_as_extra_rows():
    import datetime as dt
    many = [_n(f"T{i:02d}", parent="S1", due=f"2026-03-{9 + i % 5:02d}") for i in range(12)]
    N = _tree(*many, _n("S9", kind="story", parent="E2", news="2026-02-10"),          # quiet for weeks: not picked
              _n("S8", kind="story", parent="E2", state="done"))
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), set(), k=10, pins=["S9", "S8", "ZZ"])]
    assert len(ids) == 13 and "S9" in ids                 # 12 deadlines + the pin: a pin never displaces a row
    assert "S8" not in ids and "ZZ" not in ids             # a closed or unknown pin is ignored


def test_priorities_are_sorted_latest_discussed_first():
    import datetime as dt
    card = {"id": "S5", "kind": "story", "parent": "E2", "due": None, "state": "open", "title": "S5",
            "history": [{"type": "create", "by": "human", "from_proposal": "p1", "evidence": "m", "date": "2026-03-04"}]}
    N = _tree(_n("T1", parent="S1", due="2026-03-09", news="2026-03-03"),
              _n("T2", parent="S1", news="2026-03-05"),
              _n("T3", parent="S1", due="2026-03-08", news="2026-03-05"), card)
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), set(), pins=["S5"])]
    assert ids == ["T3", "T2", "S5", "T1"]   # Mar 5 (due first, then undated), Mar 4 (card's meeting), Mar 3


def test_work_due_within_ten_days_is_never_crowded_out_by_stream_rows():
    import datetime as dt
    streams = {f"E{k}": _n(f"E{k}", "epic") for k in range(3, 13)}
    stories = {f"S{k}": _n(f"S{k}", "story", f"E{k}") for k in range(3, 13)}
    talk = [_n(f"X{k}", parent=f"S{k}", news="2026-03-05") for k in range(3, 13)]          # 10 streams, one row each
    N = _tree(_n("T1", parent="S1", due="2026-03-07", news="2026-03-05"),
              _n("T2", parent="S1", due="2026-03-12", news="2026-03-05"), *talk)
    N.update(streams); N.update(stories)
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), set(), k=10)]
    assert "T1" in ids and "T2" in ids                     # both deadlines kept, even with 11 streams competing


def test_a_far_deadline_is_not_a_due_soon_row():
    import datetime as dt
    # two items discussed this week but due in 8 weeks: neither is a deadline row; with room for one row, one is shown
    N = _tree(_n("T1", parent="S1", due="2026-04-30", news="2026-03-05"), _n("T3", parent="S1", due="2026-05-07", news="2026-03-05"))
    assert len(H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), set(), k=1)) == 1


def test_a_cached_sentence_is_redone_when_the_wait_changes():
    a = {"id": "T117", "kind": "priority", "state": "open", "due": "2026-10-26", "waiting": "Opal Brennick"}
    b = dict(a, waiting=None)
    assert H.key(a) != H.key(b)          # "waiting on Opal" must not survive the wait being cleared
    assert H.key(dict(a)) == H.key(a)


def test_work_closed_after_friday_is_shown_as_it_stood_on_friday():
    import datetime as dt
    # Friday's data built on Monday: the Part 2 workshop was held (and closed by hand) on Monday, so it vanished from the
    # report whose priorities were set on Friday (Oct 26). Anything closed after the cut-off shows as it stood then.
    late = _n("T1", parent="S1", due="2026-03-09", news="2026-03-05", state="done")
    late["history"].append({"by": "human", "set": {"state": "done"}, "date": "2026-03-05", "at": "2026-03-09T16:04:50"})
    early = _n("T2", parent="S1", due="2026-03-09", news="2026-03-05", state="done")
    early["history"].append({"by": "human", "set": {"state": "done"}, "date": "2026-03-05", "at": "2026-03-05T10:00:00"})
    rows = H.pick_priorities(_tree(late, early), "2026-03-02", dt.date(2026, 3, 6), set())
    assert [(p["id"], p["state"]) for p in rows] == [("T1", "open")]   # closed before Friday: not a priority


def test_a_sentence_about_a_wait_is_not_reused_once_the_wait_is_cleared():
    # a sentence cached before the key carried the wait sat under the no-wait key and came back after you cleared the
    # wait (Oct 26: "Planned for Oct 26; waiting on Opal Brennick since Oct 23" with T117's wait cleared)
    it = {"id": "T117", "kind": "priority", "state": "open", "due": "2026-10-26", "waiting": None}
    assert H.still_true("Planned for Oct 26; waiting on Opal Brennick since Oct 23 [T117].", it) is False
    assert H.still_true("Planned for Oct 26 [T117].", it) is True
    assert H.still_true("Due Oct 26, waiting on Opal Brennick [T117].", dict(it, waiting="Opal Brennick")) is True
    assert H.still_true("Waiting on Opal since Oct 23 [T117].", dict(it, kind="waiting")) is True


def test_summary_may_cite_the_selections_own_items():
    N = {"T1": {"history": [{"date": "2026-03-03"}]}, "T2": {"history": []}, "T3": {"history": []}}
    P = {"priorities": [{"ref": "T3"}]}
    sel = {"items": [{"id": "T2", "kind": "overdue"}]}
    assert H.summary_allowed(N, P, sel, "2026-03-02", "2026-03-06", set()) == {"T1", "T2", "T3"}
    assert H.summary_allowed(N, P, None, "2026-03-02", "2026-03-06", set()) == {"T1", "T3"}


def test_history_dates_a_hand_edit_by_when_it_was_made():
    assert H.hist_date({"by": "human", "date": "2026-03-07", "at": "2026-03-09T14:06:19"}) == "2026-03-09"
    assert H.hist_date({"by": "agent", "date": "2026-03-04"}) == "2026-03-04"
    assert H.hist_date({"by": "human", "date": "2026-03-07"}) == "2026-03-07"   # older entries without `at`



# ---- on an invented project: select, apply, build ----

def _project(tmp_path):
    (tmp_path / "taska.toml").write_text(textwrap.dedent('''
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
        [[people]]
        name = "Opal Brennick"
        side = "client"
        [report]
        title = "Harbor Berth Plan"
        client_label = "Harbor Co"
        org = "Harbor Logistics"
    '''))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    S.write_evidence(cfg.store, {"id": "2026-03-04_meetings_aaaa0001", "kind": "meeting", "at": "2026-03-04T10:00:00",
                                 "title": "Depot review", "source": "meetings", "text": "the yard plan is done and the cranes wait on Opal"})
    S.append(cfg.store, [
        {"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": None, "fields": {"title": "Yard", "ac": [], "depends_on": []}},
        {"type": "create", "mid": "c2", "id": "S001", "kind": "story", "by": "plan", "evidence": None, "date": "2026-02-01", "fields": {"title": "Yard plan", "parent": "E01", "ac": ["Plan agreed"], "depends_on": []}},
        {"type": "create", "mid": "c3", "id": "T001", "kind": "task", "by": "agent", "evidence": "2026-03-04_meetings_aaaa0001", "date": "2026-03-04",
         "quote": "the yard plan is done", "fields": {"title": "Yard plan written", "parent": "S001", "type": "action", "internal_owner": "Nora Quill", "due": "2026-03-10", "ac": [], "depends_on": []}},
        {"type": "create", "mid": "c4", "id": "T002", "kind": "task", "by": "agent", "evidence": "2026-03-04_meetings_aaaa0001", "date": "2026-03-04",
         "quote": "the cranes wait on Opal", "fields": {"title": "Crane inspection report", "parent": "S001", "type": "dependency", "client_owed": True, "client_owner": "Opal Brennick", "ac": [], "depends_on": []}},
        {"type": "processed", "evidence": "2026-03-04_meetings_aaaa0001", "by": "agent", "at": "2026-03-04T11:00:00"},
    ])
    (tmp_path / "reports" / "2026-03-02").mkdir(parents=True)
    return cfg


def _docx_text(path):
    x = zipfile.ZipFile(path).read("word/document.xml").decode()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))


def test_select_apply_and_build_use_the_configured_labels_never_a_client_name(tmp_path):
    cfg = _project(tmp_path)
    wk = tmp_path / "reports" / "2026-03-02"
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    (wk / "highlights.json").write_text(json.dumps(sel))
    assert [p["id"] for p in sel["priorities"]] == ["T001", "T002"] and sel["slide5"][0]["owner"] == "Opal Brennick"
    prose = {"title": "Harbor weekly report", "lead": "The yard plan is written [T001].", "titles": {"status": "x", "client_asks": "Client asks"},
             "slide3": {"summary": [], "milestones": [], "decisions": [], "risks": []},
             "priorities": [{"ref": "T001", "why": "Due Mar 10 [T001]."}, {"ref": "T002", "why": "Still waits on the inspection [T002]."}],
             "slide5": [], "open": []}
    (wk / "prose.json").write_text(json.dumps(prose))
    H.apply(cfg, wk / "prose.json", wk / "highlights.json")
    r = H.build(cfg, "2026-03-02", wk / "prose.json", wk / "out.docx")
    text = _docx_text(wk / "out.docx")
    assert r["errors"] == [] and "Waiting on Harbor Co" in text and "Crane inspection report" in text and "Opal Brennick" in text
    assert "Generated by taska from internal meetings" in text   # the generic footer: no organisation named unless [report].footer says so
    assert "Harbor Berth Plan weekly report" in zipfile.ZipFile(wk / "out.docx").read("docProps/core.xml").decode()


def test_report_uses_the_configured_client_label(tmp_path):
    cfg = _project(tmp_path)
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    assert [x for x in sel["items"] if x["kind"] == "waiting"][0]["who"] == "Opal Brennick"
    cfg.report.pop("client_label")
    N = __import__("taska_lib.ledger", fromlist=["fold"]).fold(cfg.store)[0]
    N["T002"]["client_owner"] = None
    tables, _ = H.fixed_tables(N, cfg, "2026-03-02", dt.date(2026, 3, 6), {}, [], tmp_path / "reports", set(), set())
    assert tables["slide5"][0]["owner"] == "the client"


def test_an_empty_decisions_table_says_so_and_names_the_decisions_in_force(tmp_path):
    # Nov 2: the weekly report showed "Key decisions and alignments" as a header with no rows and no sentence
    cfg = _project(tmp_path)
    S.write_evidence(cfg.store, {"id": "2026-02-20_meetings_aaaa0002", "kind": "meeting", "at": "2026-02-20T10:00:00",
                                 "title": "Quay review", "source": "meetings", "text": "the yard stays on the north quay, we decided"})
    S.append(cfg.store, [{"type": "create", "mid": "c5", "id": "D001", "kind": "decision", "by": "agent", "evidence": "2026-02-20_meetings_aaaa0002",
                          "date": "2026-02-20", "quote": "the yard stays on the north quay", "fields": {"title": "Yard stays on the north quay", "parent": "E01"}},
                         {"type": "processed", "evidence": "2026-02-20_meetings_aaaa0002", "by": "agent", "at": "2026-02-20T11:00:00"}])
    wk = tmp_path / "reports" / "2026-03-02"
    prose = {"title": "t", "lead": "", "titles": {"status": "x", "client_asks": "Client asks"},
             "slide3": {"summary": [], "milestones": [], "decisions": [], "risks": []}, "priorities": [], "slide5": [], "open": []}
    (wk / "prose.json").write_text(json.dumps(prose))
    H.build(cfg, "2026-03-02", wk / "prose.json", wk / "out.docx")
    text = _docx_text(wk / "out.docx")
    assert "No new decisions recorded this week; 1 decision in force, the latest on Feb 20." in text


def test_closed_decisions_are_not_called_none_recorded():
    N = {"D1": {"kind": "decision", "state": "cancelled", "created": "2026-02-20"}}
    assert H.no_new_decisions(N, frozenset()) == "No new decisions recorded this week; none in force (1 closed)."


# ---- people matters never reach the client report (Nov 2) ----

def _with_decisions(tmp_path):
    cfg = _project(tmp_path)
    cfg.people.append(C.Person(name="Ravi Tamsin"))
    S.write_evidence(cfg.store, {"id": "2026-03-05_meetings_aaaa0003", "kind": "meeting", "at": "2026-03-05T10:00:00",
                                 "title": "Staffing chat", "source": "meetings", "text": "Ravi moves to half time from the 20th. Move Checkout to 50% of users."})
    S.append(cfg.store, [
        {"type": "create", "mid": "c6", "id": "D002", "kind": "decision", "by": "agent", "evidence": "2026-03-05_meetings_aaaa0003", "date": "2026-03-05",
         "quote": "Ravi moves to half time from the 20th", "fields": {"title": "Ravi moves to half time from the 20th", "parent": "E01"}},
        {"type": "create", "mid": "c7", "id": "D003", "kind": "decision", "by": "agent", "evidence": "2026-03-05_meetings_aaaa0003", "date": "2026-03-05",
         "quote": "Move Checkout to 50% of users", "fields": {"title": "Move Checkout to 50% of users", "parent": "E01"}},
        {"type": "create", "mid": "c8", "id": "T003", "kind": "task", "by": "agent", "evidence": "2026-03-05_meetings_aaaa0003", "date": "2026-03-05",
         "quote": "Ravi moves to half time from the 20th", "fields": {"title": "Update the plan", "parent": "S001", "type": "action", "due": "2026-03-12", "ac": [], "depends_on": []}},
        {"type": "processed", "evidence": "2026-03-05_meetings_aaaa0003", "by": "agent", "at": "2026-03-05T11:00:00"}])
    return cfg


def test_people_matter_decision_is_withheld(tmp_path):
    cfg = _with_decisions(tmp_path)
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    ids = [x["id"] for x in sel["items"]] + sel["decisions"] + [x["id"] for x in sel["priorities"]]
    assert "D002" not in ids and "T003" not in ids and "D003" in sel["decisions"]
    assert sel["withheld"] == [{"id": "D002", "why": "people_matter"}, {"id": "T003", "why": "people_matter"}]


def test_an_ordinary_percentage_decision_stays(tmp_path):
    cfg = _with_decisions(tmp_path)
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    assert any(x["id"] == "D003" for x in sel["items"]) and "D003" not in [w["id"] for w in sel["withheld"]]


def test_withheld_decision_is_not_counted_in_force(tmp_path):
    cfg = _with_decisions(tmp_path)
    N = H.L.fold(cfg.store)[0]
    hidden = H.hidden_ids(cfg, N)
    assert {"D002", "T003"} <= hidden and "D003" not in hidden
    assert "2 decisions in force" in H.no_new_decisions(N, set())
    assert "1 decision in force" in H.no_new_decisions(N, hidden)


def test_the_decisions_table_lists_every_decision_of_the_week_while_highlights_keep_their_cap(tmp_path):
    cfg = _with_decisions(tmp_path)
    ev = [_mk(f"m{k}", f"D1{k}", "decision", f"Harbor decision number {k}", "Move Checkout to 50% of users") for k in range(6)]
    S.write_evidence(cfg.store, {"id": "2026-02-10_meetings_aaaa0001", "kind": "meeting", "at": "2026-02-10T10:00:00",
                                 "title": "Older call", "source": "meetings", "text": "Move Checkout to 50% of users."})
    ev.append({**_mk("mo", "D199", "decision", "Harbor decision from an older week", "Move Checkout to 50% of users"), "evidence": "2026-02-10_meetings_aaaa0001", "date": "2026-02-10"})
    S.append(cfg.store, ev)
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    shown = [f"D1{k}" for k in range(6)] + ["D003"]
    assert sorted(sel["decisions"]) == sorted(shown)
    assert len([x for x in sel["items"] if x["kind"] == "decision"]) == H.MAX["decision"]
    assert "D199" not in sel["decisions"] and "D002" not in sel["decisions"]
    assert {"id": "D002", "why": "people_matter"} in sel["withheld"]


def test_docx_never_prints_withheld(tmp_path):
    cfg = _with_decisions(tmp_path)
    wk = tmp_path / "reports" / "2026-03-02"
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    (wk / "highlights.json").write_text(json.dumps(sel))
    assert json.loads((wk / "highlights.json").read_text())["withheld"]
    prose = {"title": "t", "lead": "", "titles": {"status": "x", "client_asks": "Client asks"},
             "slide3": {"summary": [], "milestones": [], "decisions": ["D002", "D003"], "risks": []},
             "priorities": [{"ref": "T003", "why": "Soon [T003]."}], "slide5": [], "open": []}
    (wk / "prose.json").write_text(json.dumps(prose))
    H.build(cfg, "2026-03-02", wk / "prose.json", wk / "out.docx")
    text = _docx_text(wk / "out.docx")
    assert "half time" not in text and "Ravi" not in text and "withheld" not in text and "Move Checkout to 50% of users" in text


# ---- fix round 1: one hidden set guards every path ----

EV3 = "2026-03-05_meetings_aaaa0003"
HQ = "Ravi moves to half time from the 20th"


def _mk(mid, i, kind, title, quote, **fields):
    return {"type": "create", "mid": mid, "id": i, "kind": kind, "by": "agent", "evidence": EV3, "date": "2026-03-05",
            "quote": quote, "fields": {"title": title, "parent": "E01", **fields}}


def _extra(tmp_path, *events):
    cfg = _with_decisions(tmp_path)
    S.append(cfg.store, list(events))
    return cfg


def _build_text(cfg, tmp_path, prose_over=None, sel=True):
    wk = tmp_path / "reports" / "2026-03-02"
    if sel:
        (wk / "highlights.json").write_text(json.dumps(H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))))
    prose = {"title": "t", "lead": "", "titles": {"status": "x", "client_asks": "Client asks"},
             "slide3": {"summary": [], "milestones": [], "decisions": [], "risks": []}, "priorities": [], "slide5": [], "open": []}
    prose.update(prose_over or {})
    (wk / "prose.json").write_text(json.dumps(prose))
    r = H.build(cfg, "2026-03-02", wk / "prose.json", wk / "out.docx")
    return _docx_text(wk / "out.docx"), r


def test_holds_up_column_never_names_a_hidden_task(tmp_path):
    cfg = _extra(tmp_path,
                 _mk("e1", "T004", "task", "Client sends the access list", "waiting on the access list", type="dependency", client_owed=True, ac=[], depends_on=[]),
                 _mk("e2", "T005", "task", "Plan the rota", HQ, type="action", ac=[], depends_on=["T004"]))
    text, _ = _build_text(cfg, tmp_path)
    assert "Client sends the access list" in text and "Plan the rota" not in text and "half time" not in text


def test_references_never_name_a_hidden_parent(tmp_path):
    cfg = _extra(tmp_path,
                 _mk("e1", "S002", "story", "Rota changes", HQ, ac=[], depends_on=[]),
                 {**_mk("e2", "T006", "task", "Send the badge list", "badge list goes out", type="action", ac=[], depends_on=[]),
                  "fields": {"title": "Send the badge list", "parent": "S002", "type": "action", "ac": [], "depends_on": []}})
    text, _ = _build_text(cfg, tmp_path, {"priorities": [{"ref": "T006", "why": "Soon [T006]."}]})
    assert "Send the badge list" in text and "Rota changes" not in text and "S002" not in text


def test_a_lead_or_slot_citing_a_hidden_id_is_not_printed(tmp_path):
    cfg = _extra(tmp_path, _mk("e1", "T006", "task", "Send the badge list", "badge list goes out", type="action", ac=[], depends_on=[]))
    text, r = _build_text(cfg, tmp_path, {
        "lead": "Ravi moves to half time from the 20th [D002].",
        "priorities": [{"ref": "T006", "why": "Because Ravi moves to half time [D002]."}],
        "slide3": {"summary": ["Ravi moves to half time [D002]."], "milestones": [], "decisions": [],
                   "risks": [{"id": "R-01", "identified": "3/2/26", "desc": "x", "status": "Open", "ref": "D003",
                              "mitigation": "Ravi at half time [D002]"}]},
        "slide5": [{"sr": "1", "decision": "x", "status": "Open", "owner": "o", "target": "", "requested": "", "ref": "D003",
                    "comment": "Ravi at half time [D002]"}]})
    assert "half time" not in text and len(r["withheld"]) == 5 and r["errors"] == []
    assert all("half time" not in json.dumps(s) for s in r["slots"])


def test_a_stale_cached_lead_citing_a_hidden_id_is_dropped(tmp_path):
    cfg = _with_decisions(tmp_path)
    key = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))["lead_key"]
    cache = H._cache_path(cfg)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({key: "Ravi moves to half time [D002].", "mitigation|x": "[D002]"}))
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    assert sel["lead"] is None


def test_apply_never_writes_a_sentence_citing_a_hidden_id(tmp_path):
    cfg = _with_decisions(tmp_path)
    wk = tmp_path / "reports" / "2026-03-02"
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    (wk / "highlights.json").write_text(json.dumps(sel))
    (wk / "prose.json").write_text(json.dumps({"title": "t", "lead": "Ravi at half time [D002].", "titles": {}, "priorities": [],
                                               "slide3": {"summary": ["Ravi at half time [D002]."]}}))
    H.apply(cfg, wk / "prose.json", wk / "highlights.json")
    assert "half time" not in (wk / "prose.json").read_text()


def test_withheld_counts_milestones_carried_rows_and_pins_not_other_weeks(tmp_path):
    cfg = _with_decisions(tmp_path)
    S.write_evidence(cfg.store, {"id": "2026-02-10_meetings_aaaa0009", "kind": "meeting", "at": "2026-02-10T10:00:00",
                                 "title": "Early chat", "source": "meetings", "text": HQ})
    S.append(cfg.store, [
                 _mk("e1", "T007", "task", "Rota session", HQ, type="session", due="2026-03-04", ac=[], depends_on=[]),
                 _mk("e2", "R001", "risk", "Ravi at half time risk", HQ, ac=[], depends_on=[]),
                 _mk("e3", "T008", "task", "Old rota change", HQ, type="action", ac=[], depends_on=[]),
                 _mk("e4", "T009", "task", "Rota pin", HQ, type="action", ac=[], depends_on=[]),
                 {"type": "agent", "op": "status", "mid": "st1", "node": "T008", "effect": "done", "by": "agent",
                  "evidence": "2026-02-10_meetings_aaaa0009", "date": "2026-02-10", "quote": HQ},
                 {"type": "verdict", "mid": "st1", "verdict": "supports", "by": "verifier"}])
    prev = tmp_path / "reports" / "2026-02-23"
    prev.mkdir(parents=True)
    (prev / "prose.json").write_text(json.dumps({"slide3": {"risks": [{"id": "R-01", "identified": "2/20/26", "desc": "d", "status": "Open",
                                                                      "mitigation": "", "ref": "R001"}]}, "slide5": []}))
    (tmp_path / "reports" / "2026-03-02" / "presend-choices.json").write_text(json.dumps({"pin_priorities": ["T009"]}))
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    ids = [w["id"] for w in sel["withheld"]]
    assert "T007" in ids and "R001" in ids and "T009" in ids and "T008" not in ids
    assert len(ids) == len(set(ids))


# ---- fix round 2 ----

def test_priorities_withheld_counts_only_hidden_rows_that_made_the_cut():
    many = [_n(f"T{i:02d}", parent="S1", news="2026-03-05") for i in range(12)]
    withheld: set[str] = set()
    rows = H.pick_priorities(_tree(*many), "2026-03-02", dt.date(2026, 3, 6), {"T01", "T09"}, k=3, withheld=withheld)
    assert withheld == {"T01"} and "T01" not in [r["id"] for r in rows] and len(rows) == 3


def _paragraphs(path):
    x = zipfile.ZipFile(path).read("word/document.xml").decode()
    return [re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r"<w:p>.*?</w:p>", x, re.S)]


def test_a_withheld_sentence_leaves_no_gap_in_the_docx(tmp_path):
    cfg = _extra(tmp_path, _mk("e1", "T006", "task", "Send the badge list", "badge list goes out", type="action", ac=[], depends_on=[]))
    text, r = _build_text(cfg, tmp_path, {
        "lead": "Ravi moves to half time from the 20th [D002].",
        "priorities": [{"ref": "T006", "why": "Because Ravi moves to half time [D002]."}],
        "slide3": {"summary": ["Ravi moves to half time [D002].", "The badge list goes out [T006]."], "milestones": [], "decisions": [], "risks": []}})
    paras = _paragraphs(tmp_path / "reports" / "2026-03-02" / "out.docx")
    assert "•" not in paras and any(x.startswith("• ") and "badge list" in x for x in paras), paras
    assert paras[1] != "" and "Priorities for next week" in paras[1:3]   # no empty lead paragraph under the title
    assert "—" in text
    assert "withheld" not in text   # the count is the owner's: check.json and the gates, never the docx (spec §5.3)
    check = json.loads((tmp_path / "reports" / "2026-03-02" / "out.docx.check.json").read_text())
    assert len(check["withheld"]) == 3
    assert r["errors"] == [] and len(r["withheld"]) == 3 and all("half time" not in json.dumps(w) for w in r["withheld"])
    assert "failed and are marked (confirm)" not in text


# ---- final review: every sentence and quote passes the people check, not only a cited id ----

EV5 = "2026-03-05_meetings_aaaa0005"
LQ = "Ravi moves to half time so the yard plan slips"


def _later_quote(tmp_path):
    """A clean node (T001, Yard plan written) whose later status entry quotes a people matter."""
    cfg = _project(tmp_path)
    cfg.people.append(C.Person(name="Ravi Tamsin", aliases=["Rav"]))
    S.write_evidence(cfg.store, {"id": EV5, "kind": "meeting", "at": "2026-03-05T10:00:00", "title": "Yard sync",
                                 "source": "meetings", "text": f"{LQ}. The yard plan is done."})
    S.append(cfg.store, [
        {"type": "agent", "op": "status", "mid": "st5", "node": "T001", "effect": "done", "by": "agent",
         "evidence": EV5, "date": "2026-03-05", "quote": LQ},
        {"type": "verdict", "mid": "st5", "verdict": "supports", "by": "verifier"},
        {"type": "processed", "evidence": EV5, "by": "agent", "at": "2026-03-05T11:00:00"}])
    return cfg


def test_a_people_matter_sentence_citing_a_clean_id_is_withheld_not_failed(tmp_path):
    cfg = _later_quote(tmp_path)
    text, r = _build_text(cfg, tmp_path, {
        "lead": "Ravi moves to half time, so the yard plan slips [T001].",
        "priorities": [{"ref": "T001", "why": "Rav moves to half time from the 20th [T001]."}],
        "slide3": {"summary": ["Ravi moves to half time [T001].", "The yard plan is written [T001]."],
                   "milestones": [], "decisions": [], "risks": []}})
    assert "half time" not in text and "The yard plan is written" in text and "Yard plan written" in text
    assert r["errors"] == [] and len(r["withheld"]) == 3
    assert all("half time" not in json.dumps(s) for s in r["slots"])


def test_a_slot_never_hands_the_writer_a_people_matter_quote(tmp_path):
    cfg = _project(tmp_path)
    cfg.people.append(C.Person(name="Ravi Tamsin"))
    hist = lambda q: [{"mid": "m1", "op": "note", "evidence": EV5, "by": "agent", "applied": True, "date": "2026-03-04", "quote": q}]
    N = {"R002": {"id": "R002", "kind": "risk", "state": "open", "title": "Crane delay", "history": hist(LQ)},
         "R003": {"id": "R003", "kind": "risk", "state": "open", "title": "Quay delay", "history": hist("the quay crane is late again")}}
    prev = tmp_path / "reports" / "2026-02-23"
    prev.mkdir(parents=True)
    rows = [{"id": f"R-0{k}", "identified": "2/20/26", "desc": "d", "status": "Open", "mitigation": "", "ref": ref}
            for k, ref in ((1, "R002"), (2, "R003"))]
    (prev / "prose.json").write_text(json.dumps({"slide3": {"risks": rows}, "slide5": []}))
    _, slots = H.fixed_tables(N, cfg, "2026-03-02", dt.date(2026, 3, 6), {}, [], tmp_path / "reports", set(), set())
    quotes = {s["ref"]: s["latest"]["quote"] for s in slots}
    assert quotes == {"R002": "", "R003": "the quay crane is late again"}


def test_references_skip_a_later_people_matter_quote_on_a_clean_node(tmp_path):
    cfg = _later_quote(tmp_path)
    text, _ = _build_text(cfg, tmp_path, {"priorities": [{"ref": "T001", "why": "Due soon [T001]."}]})
    assert "Yard plan written" in text and "the yard plan is done" in text and "status: done" in text
    assert "half time" not in text


def test_the_workbench_export_never_prints_a_people_matter_quote(tmp_path):
    from taska_lib import workbench as W
    cfg = _later_quote(tmp_path)
    exported = W.export_html(cfg.store, cfg)
    assert "Yard plan written" in exported and "the yard plan is done" in exported
    assert "half time" not in exported
    live = json.dumps(W.data(cfg.store, cfg), ensure_ascii=False)
    assert "half time" in live   # the owner's live workbench is unchanged


# ---- a date outside the report week's year carries its year (Nov 3: "the latest on Oct 6" was Oct 6, 2023) ----

def test_a_decision_from_another_year_prints_its_year():
    N = {"D1": {"kind": "decision", "state": "open", "created": "2023-10-06"}}
    assert H.no_new_decisions(N, frozenset(), year=2026) == \
        "No new decisions recorded this week; 1 decision in force, the latest on Oct 6, 2023."


def test_a_decision_from_the_report_year_prints_no_year():
    N = {"D1": {"kind": "decision", "state": "open", "created": "2026-10-06"}}
    assert H.no_new_decisions(N, frozenset(), year=2026).endswith("the latest on Oct 6.")


def test_md_adds_the_year_only_when_it_differs():
    assert H.md("2023-10-06", year=2026) == "Oct 6, 2023" and H.md("2026-10-06", year=2026) == "Oct 6"
    assert H.md("2023-10-06") == "Oct 6"
