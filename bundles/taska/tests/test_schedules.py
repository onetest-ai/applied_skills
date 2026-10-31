"""Schedules (taska_lib/schedules.py): a project's [[schedules]] (preview to the owner, send to the reader, update only),
and the decisions a scheduled run makes: which week it reports on, whether every ended meeting of the week has its
transcript, whether a preview that already went should go again. Moved from a project's scripts with these tests;
invented data only."""
import datetime as dt
import textwrap
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from taska_lib import config as C, schedules as W

NY = "America/New_York"
LOCAL = ZoneInfo("Europe/Kyiv")
COMMON = ("harbor", "logistics")


def _ev(subject, start_utc, end_utc):
    return {"subject": subject, "start": start_utc, "end": end_utc}


def test_ready_when_every_ended_meeting_has_its_transcript():
    events = [_ev("Harbor weekly sharing session", "2026-03-04T15:00:00+00:00", "2026-03-04T16:00:00+00:00"),
              _ev("Harbor depot review", "2026-03-06T18:00:00+00:00", "2026-03-06T19:00:00+00:00")]
    meetings = [{"date": "2026-03-04", "title": "Harbor_ Weekly Sharing Session"},
                {"date": "2026-03-06", "title": "Harbor depot review (internal)"}]
    now = dt.datetime(2026, 3, 6, 22, 0, tzinfo=LOCAL)
    assert W.ready(events, meetings, now, "2026-03-02", NY, common=COMMON) == (True, [])


def test_not_ready_while_an_ended_meeting_has_no_transcript_before_the_deadline():
    events = [_ev("Harbor depot review", "2026-03-06T12:00:00+00:00", "2026-03-06T13:00:00+00:00")]
    now = dt.datetime(2026, 3, 6, 19, 30, tzinfo=LOCAL)
    assert W.ready(events, [], now, "2026-03-02", NY, common=COMMON) == (False, ["Harbor depot review (Mar 6)"])


def test_the_deadline_runs_it_anyway_and_names_what_is_missing():
    events = [_ev("Harbor depot review", "2026-03-06T12:00:00+00:00", "2026-03-06T13:00:00+00:00")]
    now = dt.datetime(2026, 3, 6, 20, 0, tzinfo=LOCAL)   # the one scheduled Friday run
    assert W.ready(events, [], now, "2026-03-02", NY, common=COMMON) == (True, ["Harbor depot review (Mar 6)"])


def test_meetings_not_yet_ended_cancelled_or_outside_the_week_do_not_count():
    events = [_ev("Harbor depot review", "2026-03-06T23:00:00+00:00", "2026-03-07T00:00:00+00:00"),
              _ev("Canceled: Harbor yard walk", "2026-03-05T15:00:00+00:00", "2026-03-05T16:00:00+00:00"),
              _ev("Harbor kickoff", "2026-02-27T15:00:00+00:00", "2026-02-27T16:00:00+00:00")]
    now = dt.datetime(2026, 3, 6, 22, 0, tzinfo=LOCAL)
    assert W.ready(events, [], now, "2026-03-02", NY, common=COMMON) == (True, [])


def test_which_week_a_run_reports_on():
    assert W.send_week(dt.date(2026, 3, 9)) == "2026-03-02"
    assert W.preview_week(dt.date(2026, 3, 6)) == "2026-03-02" and W.preview_week(dt.date(2026, 3, 7)) == "2026-03-02"
    # a schedule run Fri–Sun reports the current week; Mon–Thu the previous one
    assert W.week_for(dt.date(2026, 3, 6)) == "2026-03-02" and W.week_for(dt.date(2026, 3, 8)) == "2026-03-02"
    assert W.week_for(dt.date(2026, 3, 9)) == "2026-03-02" and W.week_for(dt.date(2026, 3, 12)) == "2026-03-02"
    assert W.week_for(dt.date(2026, 3, 13)) == "2026-03-09"


def test_an_event_with_only_common_words_matches_on_them():
    events = [_ev("[Harbor AI] Weekly sync", "2026-03-02T14:00:00+00:00", "2026-03-02T14:30:00+00:00")]
    now = dt.datetime(2026, 3, 6, 19, 30, tzinfo=LOCAL)
    assert W.ready(events, [{"date": "2026-03-02", "title": "Harbor AI weekly sync"}], now, "2026-03-02", NY, common=COMMON) == (True, [])
    assert W.ready(events, [{"date": "2026-03-02", "title": "Daily sync"}], now, "2026-03-02", NY, common=COMMON)[0] is False


def test_the_projects_own_name_words_never_tell_meetings_apart():
    assert W.same_meeting("Harbor Logistics: Depot review", "Harbor Logistics daily sync", common=COMMON) is False
    assert W.same_meeting("Harbor Logistics: Depot review", "Depot review notes", common=COMMON) is True


def test_a_run_lock_held_by_a_live_process_is_busy_and_a_dead_ones_is_not(tmp_path):
    import os
    lock = tmp_path / "lock"
    assert W.lock_busy(lock) is False
    lock.mkdir(); (lock / "pid").write_text(str(os.getpid()))
    assert W.lock_busy(lock) is True
    (lock / "pid").write_text("999999")
    assert W.lock_busy(lock) is False


def test_the_readiness_check_syncs_every_transcript_source_and_the_calendar():
    sources = {"meetings": {"adapter": "meetings_folder"}, "calendar": {"adapter": "msgraph_calendar"},
               "teams_a": {"adapter": "msgraph_transcripts"}, "teams_b": {"adapter": "msgraph_transcripts"},
               "chat": {"adapter": "teams_chat"}}
    assert W.sync_sources(sources) == ["meetings", "teams_a", "teams_b", "calendar"]


def test_a_failed_preview_is_retried_at_most_three_times():
    assert W.again({"run_exit": 1, "attempts": 1, "meetings": ["m1"]}, ["m1"]) == (True, "the last run failed")
    assert W.again({"run_exit": 1, "attempts": 3, "meetings": ["m1"]}, ["m1"])[0] is False
    assert W.again({"run_exit": 1}, [])[0] is True


def test_a_meeting_that_arrives_after_the_preview_sends_an_updated_one():
    go, why = W.again({"run_exit": 0, "attempts": 1, "meetings": ["m1"]}, ["m1", "m2"])
    assert go and why == "new meetings since the last preview: m2"
    assert W.again({"run_exit": 0, "attempts": 1, "meetings": ["m1", "m2"]}, ["m1", "m2"]) == (False, "already sent")


def test_a_preview_waiting_only_for_your_choices_is_not_sent_again():
    go, why = W.again({"run_exit": 1, "attempts": 2, "meetings": ["m1"], "failed": ["presend_gate"]}, ["m1"])
    assert not go and "pre-send" in why


# ---- [[schedules]] in taska.toml ----

def _project(tmp_path, body):
    (tmp_path / "taska.toml").write_text(textwrap.dedent('[project]\nname = "Harbor Logistics"\ntimezone = "Europe/Kyiv"\n' + body))
    return C.load(tmp_path)


def test_schedules_are_read_with_their_defaults(tmp_path):
    cfg = _project(tmp_path, '''
        [[schedules]]
        name = "Friday preview"
        kind = "preview"
        days = [5]
        time = "20:00"
        [[schedules]]
        name = "Monday send"
        kind = "send"
        days = [1]
        time = "14:00"
        to = ["opal@example.com"]
        cc = ["ravi@example.com"]
        include = { pptx = true }
    ''')
    a, b = cfg.schedules
    assert a.name == "Friday preview" and a.kind == "preview" and a.days == (5,) and a.time == "20:00" and a.to == [] and a.on
    assert a.include == {"body": True, "docx": True, "link": True, "pptx": False, "workbench": False}
    assert b.to == ["opal@example.com"] and b.cc == ["ravi@example.com"] and b.include["pptx"] is True and b.include["docx"] is True


@pytest.mark.parametrize("bad, why", [
    ('[[schedules]]\nname = "x"\nkind = "mail"\ndays = [1]\ntime = "09:00"\n', "kind"),
    ('[[schedules]]\nname = "x"\nkind = "send"\ndays = [8]\ntime = "09:00"\n', "days"),
    ('[[schedules]]\nname = "x"\nkind = "send"\ndays = [1]\ntime = "9pm"\n', "time"),
    ('[[schedules]]\nname = "x"\nkind = "send"\ndays = [1]\ntime = "09:00"\nto = ["not an address"]\n', "to"),
    ('[[schedules]]\nname = "x"\nkind = "send"\ndays = [1]\ntime = "09:00"\n[[schedules]]\nname = "x"\nkind = "preview"\ndays = [1]\ntime = "09:00"\n', "name"),
])
def test_a_bad_schedule_is_refused_with_its_field_named(tmp_path, bad, why):
    with pytest.raises(C.ConfigError, match=why):
        _project(tmp_path, bad)


def test_the_next_run_of_a_schedule():
    s = W.Schedule(name="Monday send", kind="send", days=(1,), time="14:00")
    assert W.next_run(s, dt.datetime(2026, 3, 6, 9, 0, tzinfo=LOCAL)) == dt.datetime(2026, 3, 9, 14, 0, tzinfo=LOCAL)
    assert W.next_run(s, dt.datetime(2026, 3, 9, 14, 0, tzinfo=LOCAL)) == dt.datetime(2026, 3, 16, 14, 0, tzinfo=LOCAL)
    assert W.next_run(W.Schedule(name="off", kind="send", days=(1,), time="14:00", on=False), dt.datetime(2026, 3, 6, tzinfo=LOCAL)) is None


def test_the_cli_lists_schedules_with_their_next_run(tmp_path):
    from conftest import run_cli
    cfg = _project(tmp_path, '[[schedules]]\nname = "Monday send"\nkind = "send"\ndays = [1]\ntime = "14:00"\nto = ["opal@example.com"]\n')
    r = run_cli(tmp_path, "schedules")
    assert r["schedules"][0]["name"] == "Monday send" and r["schedules"][0]["next"].endswith("14:00:00+02:00") and r["schedules"][0]["to"] == ["opal@example.com"]
