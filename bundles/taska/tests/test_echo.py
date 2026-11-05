"""evals/echo.py: an independent note-taker's calls (Echo) as evidence for a meeting, matched by what was said, never by
title or clock. Invented data only."""
import json
import sys

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import echo as E  # noqa: E402

SAID = ("Nora will draft the warehouse onboarding checklist for Opal by Thursday and Ravi books the forklift audit "
        "then we review the dock schedule export together and decide which yard gets the new scanners first")


def _call(root, name, title, transcript, actions=None, decisions=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"started_at": f"{name[:10]}T18:00:12+03:00", "title": title}))
    (d / "transcript.md").write_text(transcript)
    if actions is not None:
        (d / "actions.json").write_text(json.dumps(actions))
        (d / "notes.md").write_text("# Notes\n\n## Decisions\n\n" + "\n".join(f"- {x}" for x in decisions or []) + "\n\n## Action items\n- x\n")
    return d


def test_calls_are_read_with_their_actions_and_decisions_and_timestamps(tmp_path):
    _call(tmp_path, "2026-03-02_180012-yard-sync", "Yard sync", SAID,
          [{"task": "Draft the onboarding checklist", "owner": "Nora", "due": "Thursday", "quote": "Nora will draft", "t": 95, "status": "todo"}],
          ["Scanners go to the north yard first [02:26].", "Move the audit a week [08:00, 10:12]"])
    _call(tmp_path, "2026-03-02_120000", "", "unrelated small talk about the weather and lunch plans for the team")
    calls = {c.id: c for c in E.load_calls(tmp_path)}
    c = calls["2026-03-02_180012-yard-sync"]
    assert c.processed and c.actions[0]["task"] == "Draft the onboarding checklist" and c.actions[0]["at"] == "01:35"
    assert c.decisions == [{"what": "Scanners go to the north yard first.", "at": "02:26", "t": 146},
                           {"what": "Move the audit a week", "at": "08:00, 10:12", "t": 480}]
    assert not calls["2026-03-02_120000"].processed and calls["2026-03-02_120000"].actions == []


def test_a_call_is_matched_to_a_meeting_by_what_was_said_not_by_title_or_clock(tmp_path):
    _call(tmp_path, "2026-03-02_180012-something-else-entirely", "Something else entirely", SAID, [], [])
    _call(tmp_path, "2026-03-02_120000", "Harbor daily sync", "unrelated small talk about the weather and lunch plans for the team today")
    meetings = {"2026-03-02_meetings_ab12cd34": {"at": "2026-03-02T12:00:00", "title": "Harbor daily sync", "text": SAID + " and so on"}}
    found = E.match_calls(meetings, E.load_calls(tmp_path))
    assert found["2026-03-02_meetings_ab12cd34"].id == "2026-03-02_180012-something-else-entirely"


def test_a_meeting_with_no_call_of_its_own_gets_none(tmp_path):
    _call(tmp_path, "2026-03-02_180012", "Harbor daily sync", "unrelated small talk about the weather and lunch plans for the team today")
    meetings = {"2026-03-02_meetings_ab12cd34": {"at": "2026-03-02T12:00:00", "title": "Harbor daily sync", "text": SAID}}
    assert E.match_calls(meetings, E.load_calls(tmp_path)) == {"2026-03-02_meetings_ab12cd34": None}


def test_a_call_a_day_away_from_a_misdated_meeting_still_matches(tmp_path):
    _call(tmp_path, "2026-03-09_180012", "", SAID, [], [])
    meetings = {"2026-03-09_meetings_ab12cd34": {"at": "2026-03-02T12:00:00", "title": "x", "text": SAID}}   # filed under the wrong day
    assert E.match_calls(meetings, E.load_calls(tmp_path))["2026-03-09_meetings_ab12cd34"].id == "2026-03-09_180012"
