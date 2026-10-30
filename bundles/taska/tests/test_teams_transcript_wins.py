"""A Teams transcript (msgraph_transcripts) dates a meeting by when it happened; a synced folder file is named after
the day someone saved it. When both hold the same meeting, it is one meeting and the Teams time wins: the folder copy
is re-dated and keeps its id and events (roadmap C-09; measured on a live project, Oct 2026: two meetings saved the next
morning were stored a day late). Invented data only (public repo)."""
import json

from taska_lib import ledger as L, store as S
from taska_lib.sources import msgraph

WORDS = ("the dock schedule moves to thursday because the forklift crew is short and the yard team wants the new "
         "scanner trial finished first so nora will send the updated plan to the client before friday and ask the "
         "depot manager to confirm the second shift while omar checks the invoice export and the pallet counts for "
         "last month then we agree the pilot starts on monday with two trucks and a backup driver").split()
OTHER = ("budget review for the warehouse lighting with the facilities lead who asked for three quotes and a timeline "
         "for the night shift so we can decide by the end of the month whether the old fixtures stay or the whole "
         "floor is replaced in one go during the holiday closure when the building is empty").split()


def _turns(words, n=4):
    step = len(words) // n
    return [{"speaker": "Nora Quill", "ts": f"00:0{i}:00", "text": " ".join(words[i * step:(i + 1) * step])} for i in range(n)]


def _folder(store, at="2026-03-10T09:38:00", words=WORDS, eid="2026-03-10_meetings_aa000001"):
    item = {"id": eid, "source": "meetings", "kind": "meeting", "at": at, "title": "10.10.2026_Harbor_ Daily sync",
            "turns": _turns(words), "date_source": "filename"}
    assert S.write_evidence(store, item)
    return eid


def _teams(at="2026-03-09T15:02:00", words=WORDS, eid="2026-03-09_teams_harbor_bb000002"):
    return {"id": eid, "source": "teams_harbor", "kind": "meeting", "at": at, "title": "Harbor (Internal): Daily sync",
            "turns": _turns(words), "meta": {"transcript": "teams", "speakers": ["Nora Quill"]}}


def _ev(store, eid):
    return json.loads(S.P(store, "evidence", eid + ".json").read_text())


def test_a_teams_transcript_re_dates_the_folder_copy_of_the_same_meeting(tmp_path):
    store = tmp_path / "s"; S.ensure(store)
    fid = _folder(store)
    assert S.write_evidence(store, _teams()) is False                     # one meeting: the Teams copy is not stored
    x = _ev(store, fid)
    assert x["id"] == fid and x["date"] == "2026-03-09" and x["at"] == "2026-03-09T15:02:00"
    assert x["date_source"] == "teams" and x["redated_from"] == "2026-03-10"
    assert not S.P(store, "evidence", "2026-03-09_teams_harbor_bb000002.json").exists()


def test_two_meetings_with_the_same_title_on_two_days_stay_two(tmp_path):
    store = tmp_path / "s"; S.ensure(store)
    fid = _folder(store)
    assert S.write_evidence(store, _teams(at="2026-03-10T15:00:00", words=OTHER, eid="2026-03-10_teams_harbor_cc000003"))
    assert _ev(store, fid)["date"] == "2026-03-10"                          # untouched


def test_the_teams_copy_of_a_same_day_meeting_is_not_stored_and_changes_nothing(tmp_path):
    store = tmp_path / "s"; S.ensure(store)
    fid = _folder(store, at="2026-03-09T15:01:00", eid="2026-03-09_meetings_aa000001")
    assert S.write_evidence(store, _teams()) is False
    assert "redated_from" not in _ev(store, fid)


def test_a_folder_copy_arriving_after_teams_is_not_stored(tmp_path):
    store = tmp_path / "s"; S.ensure(store)
    assert S.write_evidence(store, _teams())
    item = {"id": "2026-03-10_meetings_aa000001", "source": "meetings", "kind": "meeting", "at": "2026-03-10T09:38:00",
            "title": "10.10.2026_Harbor_ Daily sync", "turns": _turns(WORDS), "date_source": "filename"}
    assert S.write_evidence(store, item) is False
    assert _ev(store, "2026-03-09_teams_harbor_bb000002")["date"] == "2026-03-09"


def test_a_recurring_series_yields_each_meeting_once():
    # Graph lists every transcript of a recurring series under each occurrence
    block = "Transcript ID: t1  Created: 2026-03-09 15:02\nTranscript ID: t2  Created: 2026-03-10 15:00\n"
    text = ("Meeting: Harbor (Internal): Daily sync\nMeeting ID: m1\n" + block) * 2
    got = sorted((t["transcript"], t["created"][:10]) for t in msgraph.parse_listing(text))
    assert got == [("t1", "2026-03-09"), ("t2", "2026-03-10")]


def test_events_on_a_re_dated_meeting_move_with_it_and_can_still_be_undone(tmp_path):
    store = tmp_path / "s"; S.ensure(store)
    fid = _folder(store)
    S.append(store, [
        {"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Ops"}},
        {"type": "create", "mid": "s1", "id": "S001", "kind": "story", "by": "agent", "evidence": fid, "date": "2026-03-10",
         "fields": {"title": "Dock plan", "parent": "E01", "ac": ["x"]}},
        {"type": "processed", "evidence": fid, "by": "agent"}])
    e = L.human_event(store, {"op": "edit", "node": "S001", "set": {"due": "2026-03-20"}})
    S.write_evidence(store, _teams())
    N = L.fold(store)[0]
    assert N["S001"]["created"] == "2026-03-09"                             # the story now dates from the real meeting
    L.human_event(store, {"op": "undo", "target": e["mid"]})
    assert L.fold(store)[0]["S001"].get("due") is None                      # mids stay valid after the re-date
