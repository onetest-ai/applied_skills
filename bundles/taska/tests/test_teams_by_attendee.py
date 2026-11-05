"""msgraph_transcripts can choose a project's Teams meetings by who attends (attendee_domains, organizer) and label them
client by attendance (client_domains), because titles are not unique: two series share one title, and a generic word
("Performance") matches other projects' meetings (Oct 2026). Invented data only (public repo)."""
import json
import os
import textwrap

from taska_lib import config as C, store as S
from taska_lib.sources import msgraph

VTT = textwrap.dedent("""\
    WEBVTT

    00:00:01.000 --> 00:00:09.000
    <v Nora Quill>The dock schedule moves to thursday because the forklift crew is short this week and the yard team wants the scanner trial first.</v>

    00:00:10.000 --> 00:00:19.000
    <v Omar Reyes>Then I send the updated plan to the client before friday and ask the depot manager to confirm the second shift for the pilot.</v>
    """)


def _ev(subject, start_utc, domains, organizer="Nora Quill", cancelled=False):
    return {"subject": subject, "start": {"dateTime": start_utc + ":00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": start_utc + ":00.0000000", "timeZone": "UTC"}, "isOnlineMeeting": True,
            "isCancelled": cancelled, "organizer": {"emailAddress": {"name": organizer}},
            "attendees": [{"emailAddress": {"address": f"someone@{d}"}} for d in domains]}


CAL = [_ev("[Load Testing] Weekly meeting", "2026-03-05T09:30", ["ourco.example", "harbor.example"]),     # the project
       _ev("[Load Testing] Weekly meeting", "2026-03-05T12:30", ["ourco.example"], organizer="Dana X"),     # same title
       _ev("[Load Testing] Findings", "2026-03-04T08:00", ["ourco.example"]),                                # no client
       _ev("[Load Testing] Sync-up", "2026-03-04T10:00", ["ourco.example", "other.example"])]             # other project

# the CLI's listing prints a meeting's start as UTC minus the local offset; with LISTING_TZ = "UTC" it is plain UTC
LISTING = {
    "[Load Testing] Weekly meeting": ("Meeting: [Load Testing] Weekly meeting\nStart  : 2026-03-05 09:30\nMeeting ID: mA\n"
                                      "Transcript ID: tA1  Created: 2026-03-05 09:31\n"
                                      "Meeting: [Load Testing] Weekly meeting\nStart  : 2026-03-05 12:30\nMeeting ID: mB\n"
                                      "Transcript ID: tB1  Created: 2026-03-05 12:31\n"),
    "[Load Testing] Findings": ("Meeting: [Load Testing] Findings\nStart  : 2026-03-04 08:00\nMeeting ID: mC\n"
                                "Transcript ID: tC1  Created: 2026-03-04 08:01\n"),
}


def _project(tmp_path, extra):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        timezone = "Europe/Zurich"
        [[people]]
        name = "Nora Quill"
        [[people]]
        name = "Omar Reyes"
        [sources.teams_harbor]
        adapter = "msgraph_transcripts"
        since = "2026-03-01"
    """) + extra)
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    return cfg


def _fake(monkeypatch):
    monkeypatch.setattr(msgraph, "LISTING_TZ", "UTC")
    asked = []
    def run(scfg, *args, timeout=120):
        if args[0] == "calendar":
            return json.dumps(CAL)
        if "--vtt" in args:
            with open(args[args.index("--output") + 1], "w") as f:
                f.write(VTT)
            return ""
        subj = args[args.index("--subject") + 1] if "--subject" in args else None
        asked.append(subj)
        return LISTING.get(subj, "") if subj else "".join(LISTING.values())
    monkeypatch.setattr(msgraph, "run", run)
    return asked


def _titles(cfg):
    return sorted((x["title"], x["at"][:16], x.get("meeting_kind")) for x in S.evidence(cfg.store).values())


def test_a_meeting_with_an_attendee_from_the_domain_is_kept_and_a_same_titled_one_is_dropped(tmp_path, monkeypatch):
    cfg = _project(tmp_path, 'attendee_domains = ["harbor.example"]\n')
    _fake(monkeypatch)
    _, rep = msgraph.sync_transcripts(cfg, "teams_harbor", cfg.sources["teams_harbor"], cfg.store, None)
    assert [t[:2] for t in _titles(cfg)] == [("[Load Testing] Weekly meeting", "2026-03-05T09:31")]
    assert rep["status"] == "ok" and len(rep["new"]) == 1


def test_another_projects_meeting_is_dropped(tmp_path, monkeypatch):
    cfg = _project(tmp_path, 'attendee_domains = ["harbor.example"]\n')
    asked = _fake(monkeypatch)
    msgraph.sync_transcripts(cfg, "teams_harbor", cfg.sources["teams_harbor"], cfg.store, None)
    assert "[Load Testing] Sync-up" not in asked and "[Load Testing] Findings" not in asked   # never even listed


def test_an_organizer_filter_keeps_the_organizers_meetings(tmp_path, monkeypatch):
    cfg = _project(tmp_path, 'organizer = ["Nora Quill"]\n')
    _fake(monkeypatch)
    msgraph.sync_transcripts(cfg, "teams_harbor", cfg.sources["teams_harbor"], cfg.store, None)
    assert [t[0] for t in _titles(cfg)] == ["[Load Testing] Findings", "[Load Testing] Weekly meeting"]


def test_a_meeting_with_a_client_domain_attendee_is_a_client_meeting(tmp_path, monkeypatch):
    cfg = _project(tmp_path, 'organizer = ["Nora Quill"]\nclient_domains = ["harbor.example"]\n')
    _fake(monkeypatch)
    msgraph.sync_transcripts(cfg, "teams_harbor", cfg.sources["teams_harbor"], cfg.store, None)
    kinds = {t[0]: t[2] for t in _titles(cfg)}
    assert kinds == {"[Load Testing] Weekly meeting": "client", "[Load Testing] Findings": "internal"}


def test_listed_start_times_are_read_back_as_utc():
    # the CLI prints UTC minus the local offset: 09:30 UTC shows as 07:30 in Zurich summer time (+2)
    assert msgraph.listing_to_utc("2026-09-03 07:30", "Europe/Zurich") == "2026-09-03T09:30"
    assert msgraph.listing_to_utc("2026-11-05 09:30", "Europe/Zurich") == "2026-11-05T10:30"   # winter time (+1)


def test_a_cli_whose_times_no_longer_match_selects_nothing(tmp_path, monkeypatch):
    cfg = _project(tmp_path, 'attendee_domains = ["harbor.example"]\n')
    _fake(monkeypatch)
    monkeypatch.setattr(msgraph, "LISTING_TZ", "Asia/Tokyo")   # every listed time now reads 9 h off: fail closed
    _, rep = msgraph.sync_transcripts(cfg, "teams_harbor", cfg.sources["teams_harbor"], cfg.store, None)
    assert rep["new"] == [] and rep["unmatched_events"]


def test_a_chosen_meeting_whose_series_has_no_transcript_that_day_is_reported(tmp_path, monkeypatch):
    # Oct 2026: a Findings series' 20 transcripts were all from April-May; the September sessions were never
    # transcribed, yet the report said nothing was missing, because the series thread matched
    cfg = _project(tmp_path, 'organizer = ["Nora Quill"]\n')
    asked = _fake(monkeypatch)
    old = ("Meeting: [Load Testing] Findings\nStart  : 2026-03-04 08:00\nMeeting ID: mC\n"
           "Transcript ID: tOld  Created: 2026-01-12 08:01\n")
    monkeypatch.setitem(LISTING, "[Load Testing] Findings", old)
    _, rep = msgraph.sync_transcripts(cfg, "teams_harbor", cfg.sources["teams_harbor"], cfg.store, None)
    assert any("[Load Testing] Findings" in x for x in rep["no_transcript"])
    assert not any("Weekly meeting" in x for x in rep["no_transcript"])     # that one has its transcript
