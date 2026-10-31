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


# ---------- the writer: [[schedules]] are the only part of taska.toml it rewrites ----------

TWO = [W.Schedule("Friday preview", "preview", (5,), "20:00"),
       W.Schedule("Monday send", "send", (1,), "14:00", ["reader@example.com"], ["lead@example.com"],
                  dict(W.INCLUDE, pptx=True, workbench=True), False)]


def test_to_toml_round_trips_through_parse():
    import tomllib
    text = W.to_toml(TWO)
    back = W.parse(tomllib.loads(text)["schedules"])
    assert back == TWO
    assert text.count("[[schedules]]") == 2 and 'name = "Monday send"' in text and "on = false" in text


def test_save_replaces_only_the_schedules_blocks(tmp_path):
    before = textwrap.dedent('''\
        [project]
        name = "Harbor Logistics"   # a comment that must survive

        [[schedules]]
        name = "old one"
        kind = "update"
        days = [2]
        time = "09:00"

        [plan]
        epics = ["A"]

        [[schedules]]
        name = "old two"
        kind = "preview"
        days = [5]
        time = "17:00"
        to = ["x@example.com"]
        [[people]]
        name = "Ada Example"
        ''')
    (tmp_path / "taska.toml").write_text(before)
    out = W.save(tmp_path, TWO)
    after = out.read_text()
    assert (tmp_path / "taska.toml.bak").read_text() == before
    assert "old one" not in after and "old two" not in after and 'name = "Harbor Logistics"   # a comment that must survive' in after
    assert after.index("[plan]") < after.index("[[people]]") < after.index("[[schedules]]")
    assert after.count("[[schedules]]") == 2
    assert C.load(tmp_path).schedules == TWO
    assert W.strip_blocks(after).rstrip() == W.strip_blocks(before).rstrip()   # one blank line separates the new blocks


def test_save_appends_when_there_were_none(tmp_path):
    (tmp_path / "taska.toml").write_text('[project]\nname = "Harbor Logistics"\n')
    W.save(tmp_path, TWO[:1])
    text = (tmp_path / "taska.toml").read_text()
    assert text.startswith('[project]\nname = "Harbor Logistics"\n') and text.count("[[schedules]]") == 1
    assert C.load(tmp_path).schedules == TWO[:1]


# ---------- the registrar: one launchd job per schedule, nothing launched from a test ----------

def _cfg(tmp_path, scheduled='[run]\nscheduled = "python3 ~/proj/bin/weekly.py {kind} --name {name}"\n'):
    return _project(tmp_path, scheduled + W.to_toml(TWO))


def test_label_is_stable_and_safe():
    assert W.label("Harbor Logistics", TWO[0]) == "com.taska.harbor-logistics.friday-preview"


def test_plist_has_one_calendar_entry_per_day_and_the_command():
    s = W.Schedule("Night update", "update", (1, 7), "02:30")
    text = W.plist("com.taska.p.night-update", "python3 run.py update", s, Path("/tmp/x.log"))
    assert text.count("<key>Weekday</key>") == 2 and "<integer>0</integer>" in text   # ISO 7 (Sunday) -> launchd 0
    assert "<integer>2</integer>" in text and "<integer>30</integer>" in text
    assert "<string>/bin/zsh</string>" in text and "<string>-lic</string>" in text and "<string>python3 run.py update</string>" in text
    assert "<string>/tmp/x.log</string>" in text and "<key>RunAtLoad</key>" in text


def test_register_writes_loads_and_unloads(tmp_path, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    cfg = _cfg(tmp_path)
    agents, calls = tmp_path / "agents", []
    out = W.register(cfg, agents_dir=agents, launchctl=lambda *a: calls.append(a))
    assert [o["action"] for o in out] == ["loaded", "unloaded"]            # Monday send is off
    on = agents / "com.taska.harbor-logistics.friday-preview.plist"
    assert on.is_file() and "weekly.py preview --name Friday preview" in on.read_text()
    assert not (agents / "com.taska.harbor-logistics.monday-send.plist").exists()
    assert ("unload", str(on)) in calls and ("load", str(on)) in calls   # reload = unload first, then load
    assert W.registered("Harbor Logistics", agents_dir=agents) == {"com.taska.harbor-logistics.friday-preview": str(on)}


def test_register_refuses_colliding_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    cfg = _cfg(tmp_path)
    cfg.schedules.append(W.Schedule("friday  Preview", "preview", (5,), "21:00"))
    with pytest.raises(ValueError, match="same label"):
        W.register(cfg, agents_dir=tmp_path / "a", launchctl=lambda *a: None)


def test_register_is_unsupported_off_mac_and_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "linux")
    out = W.register(_cfg(tmp_path), agents_dir=tmp_path / "a", launchctl=lambda *a: None)
    assert [o["action"] for o in out] == ["unsupported", "unsupported"] and not (tmp_path / "a").exists()


# ---------- Windows: a Task Scheduler task per schedule; the default command; one runner at a time ----------

def test_task_name_is_backslash_scoped():
    assert W.task_name("Harbor / Logistics 2", TWO[0]) == r"\taska\harbor-logistics-2\friday-preview"


def test_task_xml_runs_hidden_at_wake_on_each_day():
    s = W.Schedule("Night update", "update", (1, 5), "02:30")
    x = W.task_xml(r"\taska\p\night-update", '"C:\\Python\\pythonw.exe" "C:\\t\\taska.py" --project "C:\\p" run-schedule "Night update"', s)
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in x and "<Hidden>true</Hidden>" in x
    assert "<LogonType>InteractiveToken</LogonType>" in x and "<Monday />" in x and "<Friday />" in x and "<Tuesday />" not in x
    assert "T02:30:00</StartBoundary>" in x and "<WeeksInterval>1</WeeksInterval>" in x
    assert "<Command>C:\\Python\\pythonw.exe</Command>" in x and 'run-schedule &quot;Night update&quot;' in x


def test_default_command_runs_run_schedule(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, scheduled="")
    monkeypatch.setattr(W.sys, "platform", "win32"); monkeypatch.setattr(W.sys, "executable", r"C:\Python\python.exe")
    c = W.default_command(cfg, TWO[0])
    assert c.startswith(r'"C:\Python\pythonw.exe" "') and c.endswith(f'--project "{cfg.project_dir}" run-schedule "Friday preview"') and "taska.py" in c
    assert W.command_for(cfg, TWO[0]) == c   # no [run].scheduled: the default
    monkeypatch.setattr(W.sys, "platform", "darwin"); monkeypatch.setattr(W.sys, "executable", "/opt/homebrew/bin/python3")
    assert W.default_command(cfg, TWO[0]).startswith('"/opt/homebrew/bin/python3" "')


def test_register_on_windows_creates_from_xml_and_deletes_an_off_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "win32")
    cfg, calls = _cfg(tmp_path), []
    out = W.register(cfg, schtasks=lambda *a: calls.append(a), machine="pc-1", now=dt.datetime(2026, 3, 2, tzinfo=dt.timezone.utc))
    assert [o["action"] for o in out] == ["loaded", "unloaded"]
    assert calls[0][0] == "create" and calls[0][1] == r"\taska\harbor-logistics\friday-preview" and Path(calls[0][2]).read_text(encoding="utf-16").count("<Hidden>true") == 1
    assert ("delete", r"\taska\harbor-logistics\monday-send") in calls
    assert out[0]["path"].endswith("friday-preview.xml") and (cfg.store / "runner.json").exists()


def test_registered_on_windows_parses_the_query(monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "win32")
    csv = '"\\taska\\harbor-logistics\\friday-preview","06/11/2026 20:00:00","Ready"\r\n"\\other\\job","N/A","Ready"\r\n'
    assert W.registered("Harbor Logistics", schtasks=lambda *a: csv) == {r"\taska\harbor-logistics\friday-preview": "06/11/2026 20:00:00"}


def test_a_fresh_lock_of_another_machine_refuses_registration(tmp_path, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    cfg = _cfg(tmp_path)
    now = dt.datetime(2026, 3, 5, 12, 0, tzinfo=dt.timezone.utc)
    W.claim_runner(cfg, "mac-1", now - dt.timedelta(days=3))
    with pytest.raises(W.RunnerBusy, match="mac-1"):
        W.register(cfg, agents_dir=tmp_path / "a", launchctl=lambda *a: None, machine="mac-2", now=now)
    assert not (tmp_path / "a").exists() and W.runner_lock(cfg)["machine"] == "mac-1"


def test_a_stale_lock_is_taken_over(tmp_path, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    cfg = _cfg(tmp_path)
    now = dt.datetime(2026, 3, 10, 12, 0, tzinfo=dt.timezone.utc)
    W.claim_runner(cfg, "mac-1", now - dt.timedelta(days=8))
    W.register(cfg, agents_dir=tmp_path / "a", launchctl=lambda *a: None, machine="mac-2", now=now)
    assert W.runner_lock(cfg)["machine"] == "mac-2"
    W.claim_runner(cfg, "mac-3", now, force=True)   # --force: the owner says the old machine is gone
    assert W.runner_lock(cfg)["machine"] == "mac-3"


def test_unregister_removes_every_agent_and_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    cfg, calls, agents = _cfg(tmp_path), [], tmp_path / "a"
    W.register(cfg, agents_dir=agents, launchctl=lambda *a: calls.append(a), machine="mac-1")
    assert W.registered("Harbor Logistics", agents_dir=agents)
    out = W.unregister(cfg, agents_dir=agents, launchctl=lambda *a: calls.append(a))
    assert [o["action"] for o in out] == ["unloaded", "unloaded"]
    assert W.registered("Harbor Logistics", agents_dir=agents) == {} and W.runner_lock(cfg) is None
