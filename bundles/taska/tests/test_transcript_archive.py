"""msgraph_transcripts can keep each newly stored Teams transcript in an archive folder, named with the meeting's date,
so a project's shared transcript folder stays the team's complete record (roadmap C-24). A meeting the store already
has is not archived again, and an archived file that comes back through a folder source is not a second meeting.
Invented data only (public repo)."""
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

    00:00:20.000 --> 00:00:29.000
    <v Nora Quill>Agreed, the pilot starts on monday with two trucks and a backup driver, and we check the pallet counts from last month together.</v>
    """)
LISTING = ("Meeting: Harbor: Depot review\nMeeting ID: m1\n"
           "Transcript ID: t1  Created: 2026-03-09 14:00\n")


def _project(tmp_path, archive):
    (tmp_path / "taska.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "Harbor Logistics"
        timezone = "America/New_York"
        [[people]]
        name = "Nora Quill"
        [[people]]
        name = "Omar Reyes"
        [sources.teams_harbor]
        adapter = "msgraph_transcripts"
        subject = "Harbor"
        since = "2026-03-01"
        archive = "{archive}"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    return cfg


def _fake_graph(monkeypatch):
    def run(scfg, *args, timeout=120):
        if "--vtt" in args:
            out = args[args.index("--output") + 1]
            with open(out, "w") as f:
                f.write(VTT)
            return ""
        return LISTING
    monkeypatch.setattr(msgraph, "run", run)


def _sync(cfg):
    return msgraph.sync_transcripts(cfg, "teams_harbor", cfg.sources["teams_harbor"], cfg.store, None)


def test_a_new_teams_transcript_is_archived_with_the_meeting_date(tmp_path, monkeypatch):
    arch = tmp_path / "archive"
    cfg = _project(tmp_path, arch)
    _fake_graph(monkeypatch)
    _, rep = _sync(cfg)
    assert len(rep["new"]) == 1
    files = sorted(os.listdir(arch))
    assert files == ["03.09.2026_Harbor_ Depot review.vtt"]          # meeting day, ':' made file-safe as Teams does
    assert (arch / files[0]).read_text() == VTT                        # the transcript as Teams gave it
    assert rep["archived"] == files


def test_a_meeting_the_store_already_has_is_not_archived(tmp_path, monkeypatch):
    arch = tmp_path / "archive"
    cfg = _project(tmp_path, arch)
    _fake_graph(monkeypatch)
    _sync(cfg)
    os.remove(arch / "03.09.2026_Harbor_ Depot review.vtt")
    cfg2 = _project(tmp_path, arch)
    _, rep = msgraph.sync_transcripts(cfg2, "teams_harbor", cfg2.sources["teams_harbor"], cfg2.store, None)   # no cursor
    assert rep["new"] == [] and not os.listdir(arch)


def test_without_an_archive_setting_nothing_is_kept(tmp_path, monkeypatch):
    cfg = _project(tmp_path, "")
    _fake_graph(monkeypatch)
    _, rep = _sync(cfg)
    assert len(rep["new"]) == 1 and rep.get("archived", []) == []


def test_an_archived_file_coming_back_through_a_folder_is_not_a_second_meeting(tmp_path, monkeypatch):
    arch = tmp_path / "archive"
    cfg = _project(tmp_path, arch)
    _fake_graph(monkeypatch)
    _sync(cfg)
    n = len(S.evidence(cfg.store))
    f = arch / "03.09.2026_Harbor_ Depot review.vtt"
    from taska_lib import transcripts as TR   # the folder item as the folder source builds it: dated by its name
    turns = TR.stitch(TR.read_cues(str(f)))
    item = {"id": S.make_id("2026-03-09T12:00:00", "folder", f.name), "source": "folder", "kind": "meeting",
            "at": "2026-03-09T12:00:00", "title": f.stem, "turns": turns, "date_source": "filename"}
    assert S.write_evidence(cfg.store, item) is False
    assert len(S.evidence(cfg.store)) == n
