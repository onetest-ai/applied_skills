"""Reproduction for finding D-1 (real-data: a recording exported/copied long after the meeting).

A manager drops a meeting recording into the meetings folder weeks after the call. Many exported
recordings carry NO embedded `creation_time` in their container, so `meeting_time()` must fall back
to the date in the file name — which these exports always carry ("25_Aug_<title>"). As submitted,
the file-name date parser only accepts whitespace between the day and the month, so an underscore-
or hyphen-separated name ("25_Aug", "06-Aug") is skipped and the meeting is dated by the file's
mtime instead — the day it was copied in. Every meeting then collapses onto the copy date, and that
wrong date flows into the evidence id, `date`, and everything staleness/prep compute from it.

This asserts the CORRECT behaviour (the meeting is dated from the file name). It FAILS on the
as-submitted code, which is the reproduction. All names are invented (the Harbor Logistics world
from conftest.py) and must never be client-identifying — this is a public repo.
"""
import datetime as dt
import os

from taska_lib.sources import meetings_folder as MF


def test_underscore_dated_filename_dates_the_meeting_not_the_copy_day(tmp_path):
    # An exported recording named with its meeting date, dropped into the folder on a later day.
    rec = tmp_path / "25_Aug_Quarterly Logistics Review.mp4"
    rec.write_bytes(b"\x00")  # contents irrelevant: meeting_time() only reads the mtime + name
    copy_day = dt.datetime(2026, 9, 24, 12, 0, 0).timestamp()  # the day it was copied in
    os.utime(rec, (copy_day, copy_day))

    # No embedded creation_time (true of many exported recordings); project timezone supplied.
    at = MF.meeting_time(rec.name, str(rec), None, None, "Europe/Amsterdam")

    # CORRECT: the meeting is 25 August (from the file name), not the 24 September copy day.
    assert at[:10] == "2026-08-25", f"meeting dated by the copy day, not the file-name date: {at!r}"


def test_hyphen_dated_filename_also_dates_the_meeting(tmp_path):
    # The same bug with a hyphen separator ("06-Aug"), the other real export style.
    rec = tmp_path / "06-Aug_Depot Handover Walkthrough.mp4"
    rec.write_bytes(b"\x00")
    copy_day = dt.datetime(2026, 9, 24, 12, 0, 0).timestamp()
    os.utime(rec, (copy_day, copy_day))

    at = MF.meeting_time(rec.name, str(rec), None, None, "Europe/Amsterdam")

    assert at[:10] == "2026-08-06", f"meeting dated by the copy day, not the file-name date: {at!r}"
