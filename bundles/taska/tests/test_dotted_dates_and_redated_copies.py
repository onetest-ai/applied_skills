"""Re-synced transcripts named "MM.DD.YYYY_<title>.vtt" were dated by the day they arrived, so the same meeting entered
the ledger twice, under the wrong date. Invented data only (public repo).

1. A dotted date in the file name dates the meeting (month first, day first when the first number exceeds 12).
2. The same transcript on another date is still the same meeting (content, not date + opening words alone).
3. `repair_evidence` re-dates items whose date was only the file's mtime and marks later copies duplicate_of.
4. The fold dates every event by its evidence item, so a repaired item moves its events to the meeting's day.
"""
import datetime as dt
import json
import os

from taska_lib import ledger as L, store as S
from taska_lib.text import date_in_name
from taska_lib.sources import meetings_folder as MF


def _rec(tmp_path, name, copied=dt.datetime(2026, 10, 23, 13, 36)):
    f = tmp_path / name
    f.write_bytes(b"\x00")
    os.utime(f, (copied.timestamp(), copied.timestamp()))
    return f


def test_month_first_dotted_date_in_file_name(tmp_path):
    f = _rec(tmp_path, "10.19.2026_Harbor (Internal)_ Daily sync.vtt")
    at, src = MF.meeting_time_and_source(f.name, str(f), tz="Europe/Amsterdam")
    assert at[:10] == "2026-10-19" and src == "filename"


def test_day_first_dotted_date_when_first_number_exceeds_12(tmp_path):
    f = _rec(tmp_path, "19.10.2026 Harbor weekly.vtt")
    assert MF.meeting_time(f.name, str(f), tz="Europe/Amsterdam")[:10] == "2026-10-19"


def test_dotted_year_first_date(tmp_path):
    f = _rec(tmp_path, "2026.10.19 Harbor weekly.vtt")
    assert MF.meeting_time(f.name, str(f), tz="Europe/Amsterdam")[:10] == "2026-10-19"


TURNS = [{"speaker": "Nora Quill", "ts": f"00:0{i}:00", "text": t} for i, t in enumerate([
    "Good morning everyone, let us go stream by stream and start with the depot handover plan for this week.",
    "The dock schedule draft is ready and I will share it with the warehouse leads after this call today.",
    "We still need the forklift certification list from the client before the Thursday walkthrough can happen.",
    "I can take the follow up with the client on the certification list and report back tomorrow morning."])]


def _meeting(eid, at, turns=TURNS, **kw):
    return {"id": eid, "source": "meetings", "kind": "meeting", "meeting_kind": "internal", "at": at,
            "title": kw.pop("title", "Harbor (Internal) Daily sync"), "turns": turns, **kw}


def test_same_transcript_on_another_date_is_a_duplicate(tmp_path):
    S.ensure(tmp_path)
    S.write_evidence(tmp_path, _meeting("2026-10-19_meetings_aaaa1111", "2026-10-19T15:00:00", date_source="filename"))
    copy = _meeting("2026-10-23_meetings_bbbb2222", "2026-10-23T13:36:18", date_source="file_mtime")
    assert S.duplicate_of(tmp_path, copy) == "2026-10-19_meetings_aaaa1111"
    assert S.write_evidence(tmp_path, copy) is False


def test_a_different_meeting_with_the_same_greeting_is_not_a_duplicate(tmp_path):
    S.ensure(tmp_path)
    S.write_evidence(tmp_path, _meeting("2026-10-19_meetings_aaaa1111", "2026-10-19T15:00:00"))
    other = [TURNS[0]] + [dict(t, text=t["text"].replace("forklift", "crane").replace("dock", "yard")
                                           .replace("certification", "inspection").replace("walkthrough", "audit")
                                           .replace("share", "send").replace("follow up", "chase"))
                          for t in TURNS[1:]]
    assert S.duplicate_of(tmp_path, _meeting("2026-10-20_meetings_cccc3333", "2026-10-20T15:00:00", other,
                                             date_source="file_mtime")) is None


def test_same_text_under_two_explicit_dates_is_two_meetings(tmp_path):
    S.ensure(tmp_path)
    S.write_evidence(tmp_path, _meeting("2026-10-19_meetings_aaaa1111", "2026-10-19T15:00:00", date_source="filename"))
    assert S.duplicate_of(tmp_path, _meeting("2026-10-21_meetings_eeee5555", "2026-10-21T15:00:00",
                                             date_source="filename")) is None


def test_repair_redates_mtime_items_and_marks_copies(tmp_path):
    from taska_lib import repair as R
    S.ensure(tmp_path)
    S.write_evidence(tmp_path, _meeting("2026-10-19_meetings_aaaa1111", "2026-10-19T15:00:00", date_source="filename"))
    # written before the fix: dated by arrival, so the old dedup (same date + opening words) let it in
    for eid, name, turns in [("2026-10-23_meetings_bbbb2222", "10.19.2026_Harbor (Internal)_ Daily sync.vtt", TURNS),
                             ("2026-10-23_meetings_dddd4444", "10.16.2026_Harbor (Internal)_ Daily sync.vtt",
                              [dict(t, text="Unique meeting " + t["text"][::-1]) for t in TURNS])]:
        x = _meeting(eid, "2026-10-23T13:36:18", turns, date_source="file_mtime", meta={"file": name},
                     title=name[:-4], date="2026-10-23")
        S.P(tmp_path, "evidence", eid + ".json").write_text(json.dumps(x))
    dry = R.repair_evidence(tmp_path, apply=False)
    assert {(r["id"], r["date"]) for r in dry["redate"]} == {("2026-10-23_meetings_bbbb2222", "2026-10-19"),
                                                            ("2026-10-23_meetings_dddd4444", "2026-10-16")}
    assert dry["duplicates"] == [{"id": "2026-10-23_meetings_bbbb2222", "duplicate_of": "2026-10-19_meetings_aaaa1111"}]
    assert S.evidence(tmp_path)["2026-10-23_meetings_bbbb2222"]["date"] == "2026-10-23"   # dry run writes nothing
    R.repair_evidence(tmp_path, apply=True)
    ev = S.evidence(tmp_path)
    assert ev["2026-10-23_meetings_dddd4444"]["date"] == "2026-10-16"
    assert ev["2026-10-23_meetings_dddd4444"]["redated_from"] == "2026-10-23"
    assert ev["2026-10-23_meetings_bbbb2222"]["duplicate_of"] == "2026-10-19_meetings_aaaa1111"
    assert "2026-10-16" in S.P(tmp_path, "evidence", "2026-10-23_meetings_dddd4444.md").read_text()
    assert R.repair_evidence(tmp_path, apply=True) == {"redate": [], "duplicates": [], "applied": True}  # idempotent


def test_fold_dates_events_by_their_repaired_evidence(tmp_path):
    from taska_lib import repair as R
    S.ensure(tmp_path)
    name = "10.16.2026_Harbor (Internal)_ Daily sync.vtt"
    eid = "2026-10-23_meetings_dddd4444"
    S.P(tmp_path, "evidence", eid + ".json").write_text(json.dumps(
        _meeting(eid, "2026-10-23T13:36:18", date_source="file_mtime", meta={"file": name}, title=name[:-4], date="2026-10-23")))
    S.append(tmp_path, [
        {"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-09-01",
         "fields": {"title": "Depot"}},
        {"type": "create", "mid": "c2", "id": "S001", "kind": "story", "by": "agent", "evidence": eid, "date": "2026-10-23",
         "fields": {"title": "Depot handover", "parent": "E01", "ac": ["Plan shared"]}},
        {"type": "processed", "evidence": eid, "by": "agent"}])
    R.repair_evidence(tmp_path, apply=True)
    N, *_ = L.fold(tmp_path)
    assert N["S001"]["created"] == "2026-10-16"
    assert N["S001"]["history"][0]["date"] == "2026-10-16"


def _two_copies(tmp_path):
    from taska_lib import repair as R
    S.ensure(tmp_path)
    S.write_evidence(tmp_path, _meeting("2026-10-19_meetings_aaaa1111", "2026-10-19T15:00:00", date_source="filename"))
    name = "10.19.2026_Harbor (Internal)_ Daily sync.vtt"
    cp = "2026-10-23_meetings_bbbb2222"
    S.P(tmp_path, "evidence", cp + ".json").write_text(json.dumps(
        _meeting(cp, "2026-10-23T13:36:18", date_source="file_mtime", meta={"file": name}, title=name[:-4], date="2026-10-23")))
    S.append(tmp_path, [
        {"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-09-01",
         "fields": {"title": "Depot"}},
        {"type": "create", "mid": "c2", "id": "S001", "kind": "story", "by": "agent", "evidence": "2026-10-19_meetings_aaaa1111",
         "date": "2026-10-19", "fields": {"title": "Depot handover", "parent": "E01", "ac": ["Plan shared"]}},
        {"type": "processed", "evidence": "2026-10-19_meetings_aaaa1111", "by": "agent"},
        {"type": "create", "mid": "c3", "id": "S002", "kind": "story", "by": "agent", "evidence": cp, "date": "2026-10-23",
         "fields": {"title": "Depot handover plan (again)", "parent": "E01", "ac": ["Plan shared"]}},
        {"type": "processed", "evidence": cp, "by": "agent"},
        {"type": "human", "op": "edit", "mid": "h1", "by": "human", "evidence": cp, "date": "2026-10-23",
         "node": "S001", "set": {"title": "Depot handover agreed"}}])
    R.repair_evidence(tmp_path, apply=True)
    return cp


def test_duplicate_copy_not_counted_as_a_meeting(tmp_path):
    cp = _two_copies(tmp_path)
    assert cp not in S.order(S.evidence(tmp_path))
    _, _, order, _ = L.fold(tmp_path)
    assert cp not in order, "the copy never counts as a second meeting"


def test_creates_on_a_duplicate_copy_survive_under_the_original(tmp_path):
    # work extracted from a re-synced copy is never dropped: it is placed at the original meeting, with its date
    _two_copies(tmp_path)
    N, *_ = L.fold(tmp_path)
    assert "S002" in N
    assert N["S002"]["created"] == "2026-10-19"
    assert N["S002"]["changed_in"] == ["2026-10-19_meetings_aaaa1111"]
    assert N["S001"]["title"] == "Depot handover agreed"   # nothing else moves


def test_an_agent_status_on_a_copy_applies_at_the_original(tmp_path):
    cp = _two_copies(tmp_path)
    S.append(tmp_path, [{"type": "agent", "op": "status", "mid": "a1", "by": "agent", "evidence": cp, "date": "2026-10-23",
                         "node": "S001", "effect": "progress", "quote": "x"}])
    N, *_ = L.fold(tmp_path)
    assert any(h.get("op") == "status" for h in N["S001"]["history"])


def test_a_human_edit_citing_a_duplicate_still_applies(tmp_path):
    _two_copies(tmp_path)
    N, *_ = L.fold(tmp_path)
    assert N["S001"]["title"] == "Depot handover agreed"


def test_pending_never_lists_a_duplicate(tmp_path):
    import subprocess, sys
    cp = _two_copies(tmp_path)
    from taska import _pending
    assert cp not in [p["id"] for p in _pending(tmp_path)]


# ── A2: a meeting dated only by its file's arrival takes its calendar event's date ──

CAL = [{"subject": "Canceled: [Placeholder] Harbor: Depot Workshop", "start": "2026-03-04T14:00:00Z", "end": "2026-03-04T17:00:00Z"},
       {"subject": "Harbor: Depot Workshop for Yard and Dock", "start": "2026-03-04T14:00:00Z", "end": "2026-03-04T17:00:00Z"}]


def _arrived(tmp_path, eid="2026-03-05_meetings_ffff0001", title="Harbor_ Depot Workshop for Yard and Dock - Part-1"):
    S.ensure(tmp_path)
    x = _meeting(eid, "2026-03-05T03:24:56", date_source="file_mtime", meta={"file": title + ".vtt"}, title=title,
                 date="2026-03-05")
    S.P(tmp_path, "evidence", eid + ".json").write_text(json.dumps(x))
    return eid


def test_calendar_match_redates_guessed_item(tmp_path):
    from taska_lib import repair as R
    eid = _arrived(tmp_path)
    dry = R.repair_evidence(tmp_path, apply=False, calendar=CAL, tz="America/New_York")
    assert dry["redate"] == [{"id": eid, "date": "2026-03-04", "from": "2026-03-05", "by": "calendar",
                              "event": "Harbor: Depot Workshop for Yard and Dock"}]
    R.repair_evidence(tmp_path, apply=True, calendar=CAL, tz="America/New_York")
    x = S.evidence(tmp_path)[eid]
    assert (x["date"], x["at"], x["date_source"], x["redated_from"]) == ("2026-03-04", "2026-03-04T09:00:00", "calendar", "2026-03-05")


def test_no_calendar_match_leaves_item_and_reports_it(tmp_path):
    from taska_lib import repair as R
    eid = _arrived(tmp_path, title="Harbor_ Crane inspection debrief")
    r = R.repair_evidence(tmp_path, apply=True, calendar=CAL, tz="America/New_York")
    assert r["redate"] == [] and r["guessed_unmatched"] == [eid]
    assert S.evidence(tmp_path)[eid]["date"] == "2026-03-05"


def test_a_canceled_event_never_dates_a_meeting(tmp_path):
    from taska_lib import repair as R
    _arrived(tmp_path, title="Harbor_ Placeholder")
    assert R.repair_evidence(tmp_path, apply=False, calendar=CAL[:1], tz="UTC")["redate"] == []


def test_a_hand_edit_in_history_keeps_when_you_made_it(tmp_path):
    _two_copies(tmp_path)
    e = L.human_event(tmp_path, {"op": "edit", "node": "S001", "set": {"state": "done"}})   # as the workbench records it
    N, *_ = L.fold(tmp_path)
    h = next(h for h in N["S001"]["history"] if h.get("mid") == e["mid"])
    assert h["at"] == e["at"]   # the date it is filed under is not when you edited it; the report needs both


def test_a_leap_day_rolled_into_a_non_leap_year_is_no_date_not_a_crash():
    # "Feb 29" in a name, seen in January 2028, would be the previous year's Feb 29, which 2027 does not have:
    # main's meetings_folder skipped it (falling back to the file time); a crash here aborts the folder's whole sync
    assert date_in_name("Feb 29 sync.vtt", dt.date(2028, 1, 15)) is None
    assert date_in_name("Feb 29 sync.vtt", dt.date(2028, 3, 2)) == dt.date(2028, 2, 29)
