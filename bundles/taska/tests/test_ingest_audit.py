"""Ingest audit: flag meetings that are missing, misdated, or truncated.

Tests use the Harbor Logistics invented-name world from conftest.py.
All timestamps are naive local (no UTC offset) to keep timezone conversion
out of each individual test; UTC-offset parsing is covered in
test_expected_meeting_absent_is_reported (tz="UTC" → no conversion needed)
and the calendar fixture uses plain ISO strings throughout.
"""
import sys
import os

# Put taska's run dir on the path (conftest.py also does this, but explicit is fine)
_RUN = os.path.join(os.path.dirname(__file__), "..", "skills", "run")
if _RUN not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUN))

from taska_lib import ingest_audit as IA


# ── helpers ──────────────────────────────────────────────────────────────────

def _meeting(eid, at, title, turns=None, **kw):
    return {"id": eid, "kind": "meeting", "source": "meetings",
            "at": at, "title": title, "turns": turns or [], **kw}


def _audit(ev_list, expected=None, calendar=None, tz="UTC"):
    ev = {x["id"]: x for x in ev_list}
    return IA.audit_ingest(ev, expected or [], calendar or [], tz)


# ── A1: expected meetings ─────────────────────────────────────────────────────

def test_expected_meeting_absent_is_reported():
    """An expected meeting with no taska counterpart → A1_missing."""
    result = _audit(
        [],  # no taska meetings at all
        expected=[{"at": "2026-10-15T09:00:00", "title": "Harbor depot debrief", "source": "oracle"}],
    )
    assert len(result["A1_missing"]) == 1
    assert result["A1_missing"][0]["title"] == "Harbor depot debrief"


def test_expected_meeting_present_is_not_reported():
    """A taska meeting within ±20 min sharing ≥ 2 title tokens → expected NOT in A1_missing."""
    ev = [_meeting("2026-10-15_m_aaa11111", "2026-10-15T09:10:00",
                   "Harbor depot debrief internal")]
    result = _audit(
        ev,
        expected=[{"at": "2026-10-15T09:00:00", "title": "Harbor depot debrief", "source": "oracle"}],
    )
    assert result["A1_missing"] == []


def test_other_client_call_is_not_an_expected_meeting():
    """A taska meeting at the same hour but with no shared title tokens does not satisfy
    the expected meeting — the expected meeting is still reported in A1_missing."""
    # "yard team standup" shares no tokens with "harbor depot debrief"
    ev = [_meeting("2026-10-15_m_bbb22222", "2026-10-15T09:00:00",
                   "yard team standup")]
    result = _audit(
        ev,
        expected=[{"at": "2026-10-15T09:00:00", "title": "harbor depot debrief", "source": "oracle"}],
    )
    assert len(result["A1_missing"]) == 1


def test_time_gap_larger_than_20_min_is_not_a_match():
    """A taska meeting sharing tokens but starting 25 minutes away is not a match."""
    ev = [_meeting("2026-10-15_m_ccc33333", "2026-10-15T09:25:00",
                   "Harbor depot debrief")]
    result = _audit(
        ev,
        expected=[{"at": "2026-10-15T09:00:00", "title": "Harbor depot debrief", "source": "oracle"}],
    )
    assert len(result["A1_missing"]) == 1


def test_guessed_time_matches_on_same_day():
    """A taska meeting with date_source='file_mtime' at a very different time on the same
    calendar date satisfies an expected meeting — the ±20-min rule is relaxed to same-day
    when the meeting's recorded time is known to be a guess."""
    # at=13:36 (file arrival), expected at=09:00 — 4 h 36 min apart, but same day
    ev = [_meeting("2026-10-23_m_aaa11110", "2026-10-23T13:36:00",
                   "Harbor Ops internal sync", date_source="file_mtime")]
    result = _audit(
        ev,
        expected=[{"at": "2026-10-23T09:00:00", "title": "Harbor Ops internal sync",
                   "source": "oracle"}],
    )
    assert result["A1_missing"] == []


def test_exact_time_still_requires_20_minutes():
    """A taska meeting whose time is real (not a guess) still requires ±20 min even
    when the oracle expected meeting is on the same date."""
    # at=13:36, expected at=09:00, gap=276 min >> 20 min; date_source="filename" + real time
    ev = [_meeting("2026-10-23_m_bbb22220", "2026-10-23T13:36:00",
                   "Harbor Ops internal sync", date_source="filename")]
    result = _audit(
        ev,
        expected=[{"at": "2026-10-23T09:00:00", "title": "Harbor Ops internal sync",
                   "source": "oracle"}],
    )
    assert len(result["A1_missing"]) == 1


def test_duplicate_of_meeting_is_skipped():
    """An evidence item with duplicate_of set is excluded from audit comparisons."""
    ev = [
        _meeting("2026-10-15_m_orig1111", "2026-10-15T09:00:00", "Harbor depot debrief"),
        _meeting("2026-10-15_m_copy2222", "2026-10-15T09:00:00", "Harbor depot debrief",
                 duplicate_of="2026-10-15_m_orig1111"),
    ]
    # Only the original counts; expected is satisfied once
    result = _audit(
        ev,
        expected=[{"at": "2026-10-15T09:00:00", "title": "Harbor depot debrief", "source": "oracle"}],
    )
    assert result["A1_missing"] == []


# ── A2: date mismatch ─────────────────────────────────────────────────────────

def test_calendar_date_differs_is_reported():
    """A taska meeting and a calendar event with ≥ 2 shared tokens, within ±7 days,
    but on different dates → A2_date_mismatch."""
    ev = [_meeting("2026-10-19_m_ddd44444", "2026-10-19T09:00:00",
                   "Harbor depot weekly sync")]
    cal = [{"start": "2026-10-20T09:00:00", "subject": "Harbor depot weekly sync"}]
    result = _audit(ev, calendar=cal)
    assert len(result["A2_date_mismatch"]) == 1
    row = result["A2_date_mismatch"][0]
    assert row["id"] == "2026-10-19_m_ddd44444"
    assert row["taska"] == "2026-10-19"
    assert row["calendar"] == "2026-10-20"


def test_calendar_same_date_is_not_reported():
    """A calendar match on the same date → NOT in A2_date_mismatch."""
    ev = [_meeting("2026-10-19_m_eee55555", "2026-10-19T09:00:00",
                   "Harbor depot weekly sync")]
    cal = [{"start": "2026-10-19T10:00:00", "subject": "Harbor depot weekly sync"}]
    result = _audit(ev, calendar=cal)
    assert result["A2_date_mismatch"] == []


def test_calendar_more_than_7_days_away_is_ignored():
    """A calendar event > 7 days from the taska meeting is not a candidate."""
    ev = [_meeting("2026-10-19_m_fff66666", "2026-10-19T09:00:00",
                   "Harbor depot weekly sync")]
    cal = [{"start": "2026-10-28T09:00:00", "subject": "Harbor depot weekly sync"}]
    result = _audit(ev, calendar=cal)
    assert result["A2_date_mismatch"] == []


def test_guessed_date_is_listed():
    """Meetings whose date_source is 'file_mtime' appear in A2_guessed."""
    ev = [
        _meeting("2026-10-23_m_ggg77777", "2026-10-23T09:00:00",
                 "Harbor depot sync", date_source="file_mtime"),
        _meeting("2026-10-19_m_hhh88888", "2026-10-19T09:00:00",
                 "Harbor weekly review", date_source="filename"),
    ]
    result = _audit(ev)
    assert "2026-10-23_m_ggg77777" in result["A2_guessed"]
    assert "2026-10-19_m_hhh88888" not in result["A2_guessed"]


# ── A6: truncated and unnamed share ──────────────────────────────────────────

def test_short_transcript_is_truncated():
    """A meeting whose last turn ends at 5 min but the calendar says 60 min is truncated
    (5 min < 0.8 × 60 min = 48 min)."""
    turns = [
        {"speaker": "Nora Quill", "ts": "00:01:00", "text": "Morning everyone."},
        {"speaker": "Ravi Tamsin", "ts": "00:05:00", "text": "Let us review the dock plan."},
    ]
    ev = [_meeting("2026-10-19_m_iii99999", "2026-10-19T09:00:00",
                   "Harbor dock review sync", turns=turns)]
    # Calendar has end so duration = 60 min
    cal = [{"start": "2026-10-19T09:00:00", "end": "2026-10-19T10:00:00",
            "subject": "Harbor dock review sync"}]
    result = _audit(ev, calendar=cal)
    assert "2026-10-19_m_iii99999" in result["A6_truncated"]


def test_full_transcript_is_not_truncated():
    """A meeting whose last turn is at 50 min out of 60 min is NOT truncated (50/60 ≥ 0.8)."""
    turns = [
        {"speaker": "Nora Quill", "ts": "00:01:00", "text": "Morning."},
        {"speaker": "Ravi Tamsin", "ts": "00:50:00", "text": "That covers it."},
    ]
    ev = [_meeting("2026-10-19_m_jjjaaa00", "2026-10-19T09:00:00",
                   "Harbor dock review sync", turns=turns)]
    cal = [{"start": "2026-10-19T09:00:00", "end": "2026-10-19T10:00:00",
            "subject": "Harbor dock review sync"}]
    result = _audit(ev, calendar=cal)
    assert "2026-10-19_m_jjjaaa00" not in result["A6_truncated"]


def test_no_calendar_end_falls_back_to_meta_duration():
    """When calendar has no end, fall back to meta.duration_min."""
    turns = [
        {"speaker": "Nora Quill", "ts": "00:01:00", "text": "Morning."},
        {"speaker": "Ravi Tamsin", "ts": "00:05:00", "text": "That is all."},
    ]
    ev = [_meeting("2026-10-19_m_kkk11111", "2026-10-19T09:00:00",
                   "Harbor dock review", turns=turns,
                   meta={"duration_min": 60})]
    cal = [{"start": "2026-10-19T09:00:00", "subject": "Harbor dock review"}]  # no end
    result = _audit(ev, calendar=cal)
    assert "2026-10-19_m_kkk11111" in result["A6_truncated"]


def test_a6_20min_same_day_fallback_provides_duration():
    """When a meeting has no token-matching calendar event but a same-day event within
    ±20 min has an end time, that event's duration is used for the truncation check."""
    turns = [
        {"speaker": "Nora Quill", "ts": "00:01:00", "text": "Morning."},
        {"speaker": "Ravi Tamsin", "ts": "00:05:00", "text": "Quick review of the dock plan."},
    ]
    # Meeting title shares no tokens with the calendar subject ("standup" vs "something else")
    ev = [_meeting("2026-10-19_m_rrr11111", "2026-10-19T09:00:00",
                   "standup xyz", turns=turns)]
    # Calendar event at same time ±10 min with an end: 60-min meeting; no shared title tokens
    cal = [{"start": "2026-10-19T09:05:00", "end": "2026-10-19T10:05:00",
            "subject": "something completely different"}]
    # 5 min last turn < 0.8 × 60 min = 48 min → truncated via same-day fallback
    result = _audit(ev, calendar=cal)
    assert "2026-10-19_m_rrr11111" in result["A6_truncated"]


def test_no_duration_known_skips_truncation():
    """When no duration can be determined, the meeting is not in A6_truncated."""
    turns = [{"speaker": "Nora Quill", "ts": "00:05:00", "text": "Quick update."}]
    ev = [_meeting("2026-10-19_m_lll22222", "2026-10-19T09:00:00",
                   "Harbor review", turns=turns)]
    result = _audit(ev)  # no calendar, no meta.duration_min
    assert "2026-10-19_m_lll22222" not in result["A6_truncated"]


def test_unnamed_speaker_share():
    """Turns with '?' speaker are counted; share is per meeting, > 0 only."""
    turns = [
        {"speaker": "Nora Quill", "ts": "00:01:00", "text": "Morning."},
        {"speaker": "?", "ts": "00:02:00", "text": "Who is there?"},
        {"speaker": "?", "ts": "00:03:00", "text": "Not sure."},
        {"speaker": "Ravi Tamsin", "ts": "00:04:00", "text": "Got it."},
    ]
    ev = [_meeting("2026-10-15_m_mmm33333", "2026-10-15T09:00:00",
                   "Harbor daily sync", turns=turns)]
    result = _audit(ev)
    assert "2026-10-15_m_mmm33333" in result["A6_unnamed_share"]
    assert result["A6_unnamed_share"]["2026-10-15_m_mmm33333"] == round(2 / 4, 3)


def test_empty_and_none_speaker_counted_as_unnamed():
    """Turns with an empty string speaker or None speaker are treated the same as '?'."""
    turns = [
        {"speaker": "Nora Quill", "ts": "00:01:00", "text": "Morning."},
        {"speaker": "",           "ts": "00:02:00", "text": "Who is this?"},
        {"speaker": None,         "ts": "00:03:00", "text": "Unknown too."},
    ]
    ev = [_meeting("2026-10-15_m_sss22222", "2026-10-15T09:00:00",
                   "Harbor sync", turns=turns)]
    result = _audit(ev)
    assert "2026-10-15_m_sss22222" in result["A6_unnamed_share"]
    assert result["A6_unnamed_share"]["2026-10-15_m_sss22222"] == round(2 / 3, 3)


def test_fully_named_meeting_not_in_unnamed_share():
    """A meeting with all named speakers → not in A6_unnamed_share."""
    turns = [
        {"speaker": "Nora Quill", "ts": "00:01:00", "text": "Morning."},
        {"speaker": "Ravi Tamsin", "ts": "00:02:00", "text": "Ready."},
    ]
    ev = [_meeting("2026-10-15_m_nnn44444", "2026-10-15T09:00:00",
                   "Harbor sync", turns=turns)]
    result = _audit(ev)
    assert "2026-10-15_m_nnn44444" not in result["A6_unnamed_share"]


def test_utc_offset_in_expected_is_converted():
    """Expected 'at' with 'Z' suffix is converted to project tz before comparison.
    With tz='UTC', 09:00Z == 09:00 local → matches a taska meeting at 09:00."""
    ev = [_meeting("2026-10-15_m_ooo55555", "2026-10-15T09:05:00",
                   "Harbor depot debrief")]
    result = _audit(
        ev,
        expected=[{"at": "2026-10-15T09:00:00Z", "title": "Harbor depot debrief", "source": "oracle"}],
        tz="UTC",
    )
    assert result["A1_missing"] == []


def test_utc_offset_in_calendar_is_converted():
    """Calendar start with '+00:00' is converted to project tz before date comparison."""
    ev = [_meeting("2026-10-19_m_ppp66666", "2026-10-19T09:00:00",
                   "Harbor depot weekly sync")]
    cal = [{"start": "2026-10-19T09:00:00+00:00", "subject": "Harbor depot weekly sync"}]
    result = _audit(ev, calendar=cal, tz="UTC")
    assert result["A2_date_mismatch"] == []


def test_title_tokens_strip_digits_and_months():
    """Pure-digit tokens and month names are stripped before matching.
    Two meetings that share only 'Oct' and '2026' do not get a token match."""
    ev = [_meeting("2026-10-15_m_qqq77777", "2026-10-15T09:00:00",
                   "Oct 2026 standup")]
    result = _audit(
        ev,
        expected=[{"at": "2026-10-15T09:00:00", "title": "Oct 2026 planning", "source": "oracle"}],
    )
    # 'standup' and 'planning' don't share; 'oct' and '2026' are filtered → < 2 shared tokens → absent
    assert len(result["A1_missing"]) == 1


# ── A7: room audio (one speaker tag holds most of the words) ──────────────────

def _turns(shares, words=2000):
    # shares: {speaker: fraction of the words}
    return [{"speaker": s, "ts": f"00:{i:02d}:00", "text": " ".join(["word"] * int(words * f))}
            for i, (s, f) in enumerate(shares.items())]


def test_room_audio_flagged():
    m = _meeting("2026-03-02_meetings_aa000001", "2026-03-02T10:00:00", "Harbor executive readout",
                 turns=_turns({"Nora Quill": 0.9, "Ravi Tamsin": 0.1}))
    r = _audit([m])
    assert r["A7_room_audio"] == [{"id": "2026-03-02_meetings_aa000001", "title": "Harbor executive readout",
                                   "top_speaker": "Nora Quill", "share": 0.9, "words": 2000}]


def test_normal_call_not_flagged():
    m = _meeting("2026-03-02_meetings_aa000002", "2026-03-02T10:00:00", "Harbor daily sync",
                 turns=_turns({"Nora Quill": 0.6, "Ravi Tamsin": 0.3, "Opal Brennick": 0.1}))
    assert _audit([m])["A7_room_audio"] == []


def test_short_meeting_not_flagged():
    m = _meeting("2026-03-02_meetings_aa000003", "2026-03-02T10:00:00", "Harbor quick check",
                 turns=_turns({"Nora Quill": 1.0}, words=300))
    assert _audit([m])["A7_room_audio"] == []
