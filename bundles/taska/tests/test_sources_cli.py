import json
import os

import pytest

from taska_lib import config as C, store as S
from taska_lib.sources import msgraph, meetings_folder
from conftest import VTT_DAY1, run_cli


def test_sync_is_incremental_and_idempotent(project):
    r = run_cli(project, "sync")
    assert len(r["sources"][0]["new"]) == 2 and r["pending"] == 2
    assert run_cli(project, "sync")["sources"][0]["new"] == []
    (project / "calls" / "2026-03-04 Daily sync.vtt").write_text(VTT_DAY1.replace("Morning everyone", "Hello all"))
    assert len(run_cli(project, "sync")["sources"][0]["new"]) == 1


def test_meeting_time_and_kind(project, tmp_path):
    (project / "calls" / "Mar 5 Harbor weekly status meeting.vtt").write_text(VTT_DAY1.replace("Morning", "Afternoon"))
    run_cli(project, "sync")
    items = run_cli(project, "pending")["items"]
    wk = next(x for x in items if "weekly status" in x["title"])
    assert wk["meeting_kind"] == "client" and wk["at"].startswith(f"{wk['at'][:4]}-03-05")
    assert [x["at"] for x in items] == sorted(x["at"] for x in items)


def test_same_meeting_from_two_sources_is_one_item(project):
    run_cli(project, "sync")
    cfg = C.load(project)
    item = json.loads(next((cfg.store / "evidence").glob("2026-03-02_*.json")).read_text())
    copy = dict(item, id="2026-03-02_teams_deadbeef", source="teams")
    assert S.write_evidence(cfg.store, copy) is False
    assert S.duplicate_of(cfg.store, copy) == item["id"]


def test_recording_pairs_with_teams_docx_by_title(tmp_path):
    from test_transcripts_dates_config import _docx
    d = tmp_path / "rec"
    d.mkdir()
    (d / "Kickoff-20260302_150000-Meeting Recording.mp4").write_bytes(b"\0")
    (d / "Kickoff meeting.docx").write_bytes(_docx([["Kickoff-20260302_150000-Meeting Recording"], ["March 2, 2026, 3:00PM"],
                                                    ["5m 0s"], ["Nora Quill", "0:01", "Welcome."]]))
    pairs = meetings_folder._pair(sorted(os.listdir(d)), str(d))
    assert pairs == [("Kickoff meeting.docx", "Kickoff-20260302_150000-Meeting Recording.mp4")]


def test_recording_without_transcript_waits_for_transcribe(project, monkeypatch):
    (project / "calls" / "standup.m4a").write_bytes(b"\0")
    monkeypatch.setattr(meetings_folder.media, "probe", lambda p: {"duration": 600, "has_audio": True})
    cfg = C.load(project)
    S.ensure(cfg.store)
    cur, rep = meetings_folder.sync(cfg, "daily", cfg.sources["daily"], cfg.store, {})
    p = next(x for x in rep["pending"] if x["file"] == "standup.m4a")
    assert "run `taska.py transcribe`" in p["reason"] and p["minutes"] == 10
    assert "standup.m4a" not in cur  # retried next time


def test_msgraph_listing_parser_dedupes_transcripts():
    text = """
Meeting: Harbor daily sync
Start  : 2026-03-02 09:00
Meeting ID: MID1
Transcript ID: T-A  Created: 2026-03-02 09:01
Transcript ID: T-B  Created: 2026-03-03 09:02

Meeting: Harbor daily sync
Start  : 2026-03-03 09:00
Meeting ID: MID1
Transcript ID: T-A  Created: 2026-03-02 09:01
Transcript ID: T-B  Created: 2026-03-03 09:02
"""
    got = msgraph.parse_listing(text)
    assert [(x["transcript"], x["created"]) for x in got] == [("T-A", "2026-03-02T09:01:00"), ("T-B", "2026-03-03T09:02:00")]


def test_calendar_without_past_range_is_reported_not_guessed(project, monkeypatch):
    cfg = C.load(project)
    S.ensure(cfg.store)
    monkeypatch.setattr(msgraph, "run", lambda scfg, *a, **k: json.dumps(
        [{"id": "e1", "subject": "Harbor sync", "start": {"dateTime": "2999-01-01T09:00:00"},
          "end": {"dateTime": "2999-01-01T09:30:00"}}]))
    _, rep = msgraph.sync_calendar(cfg, "cal", {"adapter": "msgraph_calendar"}, cfg.store, None)
    assert rep["status"] == "unsupported" and "--start/--end" in rep["reason"]


def test_calendar_events_become_evidence(project, monkeypatch):
    cfg = C.load(project)
    S.ensure(cfg.store)
    ev = [{"id": "e1", "subject": "Harbor warehouse walkthrough", "start": {"dateTime": "2020-03-10T10:00:00"},
           "end": {"dateTime": "2020-03-10T11:00:00"}, "isCancelled": False,
           "organizer": {"emailAddress": {"name": "Nora Quill"}},
           "attendees": [{"emailAddress": {"address": "opal@harbor.example"}}]}]
    monkeypatch.setattr(msgraph, "run", lambda scfg, *a, **k: json.dumps(ev))
    cur, rep = msgraph.sync_calendar(cfg, "cal", {"adapter": "msgraph_calendar", "subjects": ["Harbor"]}, cfg.store, None)
    assert len(rep["new"]) == 1
    item = S.evidence(cfg.store)[rep["new"][0]]
    assert item["kind"] == "event" and "Opal Brennick" in item["text"] and item["at"] == "2020-03-10T11:00:00"
    ev[0]["start"]["dateTime"], ev[0]["end"]["dateTime"] = "2020-03-12T10:00:00", "2020-03-12T11:00:00"
    _, rep2 = msgraph.sync_calendar(cfg, "cal", {"adapter": "msgraph_calendar", "subjects": ["Harbor"]}, cfg.store, cur)
    moved = S.evidence(cfg.store)[rep2["changed"][0]]
    assert moved["meta"]["what"] == "moved" and "Previously 2020-03-10 10:00" in moved["text"]


def test_cli_end_to_end(project, tmp_path):
    run_cli(project, "setup")
    run_cli(project, "sync")
    items = run_cli(project, "pending")["items"]
    props = tmp_path / "p.json"
    props.write_text(json.dumps([{"op": "create", "ref": "s1", "kind": "story", "parent": "E01",
                                  "title": "Warehouse understood", "ac": ["Walkthrough held"],
                                  "quote": "warehouse walkthrough session with the client is booked for next Tuesday"}]))
    r = run_cli(project, "apply", "--evidence", items[0]["id"], "--proposals", str(props))
    assert r["by_op"] == {"create_story": 1} and not r["rejected"]
    assert run_cli(project, "done", "--evidence", items[0]["id"])["pending"] == 1
    st = run_cli(project, "status")
    assert st["nodes"]["story"] == {"open": 1} and st["evidence_processed"] == 1
    assert run_cli(project, "export")["out"].endswith("workbench.html")
    pack = run_cli(project, "pack")
    assert os.path.exists(pack["out"]) and pack["words"] > 10
    bad = run_cli(project, "apply", "--evidence", "nope", "--proposals", str(props), check=False)
    assert bad["status"] == "error"


def test_schedule_prints_never_installs(project):
    r = run_cli(project, "schedule", "--time", "18:30")
    assert r["kind"] == "cron" and '/taska:run --due' in r["line"] and r["line"].startswith("30 18 * * 1-5")
    assert run_cli(project, "schedule", "--launchd")["plist"].startswith("<?xml")


def test_files_folder_extracts_text_and_tracks_versions(project, tmp_path):
    import zipfile
    from taska_lib.sources import files_folder
    lib = tmp_path / "lib"
    (lib / "Decks").mkdir(parents=True)
    with zipfile.ZipFile(lib / "Decks" / "Current state map.pptx", "w") as z:
        z.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="p" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                   "<a:t>Current state</a:t><a:t>customer journey</a:t></p:sld>")
        z.writestr("ppt/slides/slide2.xml", '<p:sld xmlns:p="p" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                   "<a:t>Pain points in dispatch</a:t></p:sld>")
    (lib / "notes.md").write_text("# Fact sheet\nThe routing engine is batch-only.\n")
    (lib / "~$lock.docx").write_text("x")
    (lib / "broken.docx").write_bytes(b"PK\x03\x04nope")
    cfg = C.load(project)
    S.ensure(cfg.store)
    scfg = {"adapter": "files_folder", "path": str(lib)}
    cur, rep = files_folder.sync(cfg, "lib", scfg, cfg.store, {})
    assert len(rep["new"]) == 2 and rep["skipped"][0]["file"] == "broken.docx" and "broken.docx" not in cur
    items = S.evidence(cfg.store)
    deck = next(x for x in items.values() if x["title"] == "Current state map")
    assert deck["kind"] == "file" and "[slide 2] Pain points in dispatch" in deck["text"]
    assert deck["text"].startswith("File added: Decks/Current state map.pptx")
    assert files_folder.sync(cfg, "lib", scfg, cfg.store, cur)[1]["new"] == []
    os.utime(lib / "notes.md", (2_000_000_000, 2_000_000_000))
    (lib / "notes.md").write_text("# Fact sheet v2\n")
    os.utime(lib / "notes.md", (2_000_000_000, 2_000_000_000))
    _, rep3 = files_folder.sync(cfg, "lib", scfg, cfg.store, cur)
    v2 = S.evidence(cfg.store)[rep3["changed"][0]]
    assert v2["text"].startswith("File updated: notes.md")


def test_file_evidence_is_quotable(project, tmp_path):
    from taska_lib import ledger as L
    from taska_lib.sources import files_folder
    lib = tmp_path / "lib2"
    lib.mkdir()
    (lib / "architecture.md").write_text("Unified current-state architecture diagram, version for review.")
    run_cli(project, "setup")
    cfg = C.load(project)
    _, rep = files_folder.sync(cfg, "lib", {"adapter": "files_folder", "path": str(lib)}, cfg.store, {})
    r = L.apply(cfg.store, cfg, rep["new"][0], [
        {"op": "create", "kind": "story", "parent": "E02", "title": "As-is architecture documented",
         "ac": ["Unified diagram shared"], "quote": "Unified current-state architecture diagram, version for review"}])
    assert r["by_op"] == {"create_story": 1} and not r["rejected"]


def test_files_digest_groups_bulk_drops(project, tmp_path):
    from taska_lib.sources import files_folder
    inp = tmp_path / "INPUTS"
    (inp / "Billing").mkdir(parents=True)
    for i in range(5):
        (inp / "Billing" / f"export-{i}.csv").write_text(f"row {i}")
    (inp / "readme.md").write_text("inputs index")
    cfg = C.load(project)
    S.ensure(cfg.store)
    cur, rep = files_folder.sync(cfg, "inputs", {"adapter": "files_folder", "path": str(inp), "digest": True},
                                 cfg.store, {})
    items = [S.evidence(cfg.store)[i] for i in rep["new"]]
    billing = next(x for x in items if x["meta"]["folder"] == "Billing")
    assert len(items) == 2 and billing["meta"]["files"] == 5 and "- added: Billing/export-3.csv" in billing["text"]
    assert files_folder.sync(cfg, "inputs", {"adapter": "files_folder", "path": str(inp), "digest": True},
                             cfg.store, cur)[1]["new"] == []


def test_changed_evidence_is_requeued_and_redo_drop_rereads(project):
    run_cli(project, "setup")
    run_cli(project, "sync")
    first = run_cli(project, "pending")["items"][0]["id"]
    run_cli(project, "done", "--evidence", first)
    assert run_cli(project, "pending")["count"] == 1
    f = project / "calls" / "2026-03-02 Daily sync.vtt"
    f.write_text(f.read_text().replace("Morning everyone", "Good morning everyone"))
    r = run_cli(project, "sync")
    assert r["sources"][0]["requeued"] == [first] and r["pending"] == 2
    run_cli(project, "done", "--evidence", first)
    d = run_cli(project, "redo", "--evidence", first, "--drop")
    assert d["dropped"] == first
    assert run_cli(project, "sync")["sources"][0]["new"] == [first]  # re-read from the folder
