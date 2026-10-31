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
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6))]
    assert ids == ["T1", "T2"]


def test_every_stream_with_live_work_gets_a_row():
    import datetime as dt
    many = [_n(f"T{i:02d}", parent="S1", due=f"2026-03-{9 + i % 5:02d}") for i in range(12)]
    N = _tree(*many, _n("S2x", kind="story", parent="E2", news="2026-03-03"))
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), k=10)]
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
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), k=10, pins=["S9", "S8", "ZZ"])]
    assert len(ids) == 13 and "S9" in ids                 # 12 deadlines + the pin: a pin never displaces a row
    assert "S8" not in ids and "ZZ" not in ids             # a closed or unknown pin is ignored


def test_priorities_are_sorted_latest_discussed_first():
    import datetime as dt
    card = {"id": "S5", "kind": "story", "parent": "E2", "due": None, "state": "open", "title": "S5",
            "history": [{"type": "create", "by": "human", "from_proposal": "p1", "evidence": "m", "date": "2026-03-04"}]}
    N = _tree(_n("T1", parent="S1", due="2026-03-09", news="2026-03-03"),
              _n("T2", parent="S1", news="2026-03-05"),
              _n("T3", parent="S1", due="2026-03-08", news="2026-03-05"), card)
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), pins=["S5"])]
    assert ids == ["T3", "T2", "S5", "T1"]   # Mar 5 (due first, then undated), Mar 4 (card's meeting), Mar 3


def test_work_due_within_ten_days_is_never_crowded_out_by_stream_rows():
    import datetime as dt
    streams = {f"E{k}": _n(f"E{k}", "epic") for k in range(3, 13)}
    stories = {f"S{k}": _n(f"S{k}", "story", f"E{k}") for k in range(3, 13)}
    talk = [_n(f"X{k}", parent=f"S{k}", news="2026-03-05") for k in range(3, 13)]          # 10 streams, one row each
    N = _tree(_n("T1", parent="S1", due="2026-03-07", news="2026-03-05"),
              _n("T2", parent="S1", due="2026-03-12", news="2026-03-05"), *talk)
    N.update(streams); N.update(stories)
    ids = [p["id"] for p in H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), k=10)]
    assert "T1" in ids and "T2" in ids                     # both deadlines kept, even with 11 streams competing


def test_a_far_deadline_is_not_a_due_soon_row():
    import datetime as dt
    # two items discussed this week but due in 8 weeks: neither is a deadline row; with room for one row, one is shown
    N = _tree(_n("T1", parent="S1", due="2026-04-30", news="2026-03-05"), _n("T3", parent="S1", due="2026-05-07", news="2026-03-05"))
    assert len(H.pick_priorities(N, "2026-03-02", dt.date(2026, 3, 6), k=1)) == 1


def test_a_cached_sentence_is_redone_when_the_wait_changes():
    a = {"id": "T117", "kind": "priority", "state": "open", "due": "2026-10-26", "waiting": "Wendy Grant"}
    b = dict(a, waiting=None)
    assert H.key(a) != H.key(b)          # "waiting on Wendy" must not survive the wait being cleared
    assert H.key(dict(a)) == H.key(a)


def test_work_closed_after_friday_is_shown_as_it_stood_on_friday():
    import datetime as dt
    # Friday's data built on Monday: the Part 2 workshop was held (and closed by hand) on Monday, so it vanished from the
    # report whose priorities were set on Friday (Oct 26). Anything closed after the cut-off shows as it stood then.
    late = _n("T1", parent="S1", due="2026-03-09", news="2026-03-05", state="done")
    late["history"].append({"by": "human", "set": {"state": "done"}, "date": "2026-03-05", "at": "2026-03-09T16:04:50"})
    early = _n("T2", parent="S1", due="2026-03-09", news="2026-03-05", state="done")
    early["history"].append({"by": "human", "set": {"state": "done"}, "date": "2026-03-05", "at": "2026-03-05T10:00:00"})
    rows = H.pick_priorities(_tree(late, early), "2026-03-02", dt.date(2026, 3, 6))
    assert [(p["id"], p["state"]) for p in rows] == [("T1", "open")]   # closed before Friday: not a priority


def test_a_sentence_about_a_wait_is_not_reused_once_the_wait_is_cleared():
    # a sentence cached before the key carried the wait sat under the no-wait key and came back after you cleared the
    # wait (Oct 26: "Planned for Oct 26; waiting on Wendy Grant since Oct 23" with T117's wait cleared)
    it = {"id": "T117", "kind": "priority", "state": "open", "due": "2026-10-26", "waiting": None}
    assert H.still_true("Planned for Oct 26; waiting on Wendy Grant since Oct 23 [T117].", it) is False
    assert H.still_true("Planned for Oct 26 [T117].", it) is True
    assert H.still_true("Due Oct 26, waiting on Wendy Grant [T117].", dict(it, waiting="Wendy Grant")) is True
    assert H.still_true("Waiting on Wendy since Oct 23 [T117].", dict(it, kind="waiting")) is True


def test_summary_may_cite_the_selections_own_items():
    N = {"T1": {"history": [{"date": "2026-03-03"}]}, "T2": {"history": []}, "T3": {"history": []}}
    P = {"priorities": [{"ref": "T3"}]}
    sel = {"items": [{"id": "T2", "kind": "overdue"}]}
    assert H.summary_allowed(N, P, sel, "2026-03-02", "2026-03-06") == {"T1", "T2", "T3"}
    assert H.summary_allowed(N, P, None, "2026-03-02", "2026-03-06") == {"T1", "T3"}


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
        title = "Harbor North Star"
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
    assert "Harbor North Star weekly report" in zipfile.ZipFile(wk / "out.docx").read("docProps/core.xml").decode()


def test_report_uses_the_configured_client_label(tmp_path):
    cfg = _project(tmp_path)
    sel = H.select(cfg, "2026-03-02", tmp_path / "reports", today=dt.date(2026, 3, 6))
    assert [x for x in sel["items"] if x["kind"] == "waiting"][0]["who"] == "Opal Brennick"
    cfg.report.pop("client_label")
    N = __import__("taska_lib.ledger", fromlist=["fold"]).fold(cfg.store)[0]
    N["T002"]["client_owner"] = None
    tables, _ = H.fixed_tables(N, cfg, "2026-03-02", dt.date(2026, 3, 6), {}, [], tmp_path / "reports")
    assert tables["slide5"][0]["owner"] == "the client"
