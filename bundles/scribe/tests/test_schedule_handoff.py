import json
import plistlib
import shlex
import shutil
import subprocess
from pathlib import Path
from scribe_fixtures import fixture_brain_db, setup_mini_project, load, write_json
from scribe_lib import schedule
from scribe_lib.doctor import handoff_status


def test_schedule_prints_chained_cron_line(tmp_path):
    line = schedule.render(tmp_path / "scribe", tmp_path / "brain", time="02:30", kind="cron")
    assert line.startswith("30 2 * * * ")
    assert '/brain:brain-maintenance handoff' in line and '/scribe:run --due' in line
    assert line.count("--permission-mode bypassPermissions") == 2
    assert line.index("brain-maintenance") < line.index("scribe:run")


def test_schedule_launchd_is_a_plist(tmp_path):
    plist = schedule.render(tmp_path / "scribe", None, kind="launchd")
    assert plist.startswith("<?xml") and "<key>StartCalendarInterval</key>" in plist and "brain-maintenance" not in plist


def test_schedule_launchd_plist_is_well_formed_xml(tmp_path):
    """Review fix round 1, Critical #1: the chained command always contains
    `&&`, which is a raw ampersand — an un-escaped `<string>&&...</string>`
    is not well-formed XML, so every printed plist failed to load. Parse the
    output with plistlib (stdlib, no shelling out) and, when available,
    cross-check with plutil -lint (macOS's own parser)."""
    plist = schedule.render(tmp_path / "scribe", tmp_path / "brain", time="02:30", kind="launchd")
    data = plistlib.loads(plist.encode("utf-8"))
    assert data["Label"] == "ai.applied.scribe"
    command = data["ProgramArguments"][-1]
    assert "&&" in command and "brain-maintenance" in command and "scribe:run" in command
    assert data["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}

    plutil = shutil.which("plutil")
    if not plutil:
        return
    proc = subprocess.run([plutil, "-lint", "-"], input=plist.encode("utf-8"), capture_output=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_schedule_quotes_paths_containing_spaces(tmp_path):
    """Review fix round 1, Important #2: an unquoted `cd <path with a
    space> && ...` cds to the wrong place (or fails outright) under both cron
    and `/bin/sh -c`. Every project path must be shell-quoted."""
    scribe_dir = tmp_path / "My Drive" / "scribe"
    brain_dir = tmp_path / "My Drive" / "brain"

    line = schedule.render(scribe_dir, brain_dir, time="02:30", kind="cron")
    command = line[len("30 2 * * * "):].rstrip("\n")
    assert f"cd {shlex.quote(str(brain_dir))} &&" in command
    assert f"cd {shlex.quote(str(scribe_dir))} &&" in command
    assert str(brain_dir) not in command.replace(shlex.quote(str(brain_dir)), "")
    assert str(scribe_dir) not in command.replace(shlex.quote(str(scribe_dir)), "")

    plist = schedule.render(scribe_dir, None, kind="launchd")
    plist_command = plistlib.loads(plist.encode("utf-8"))["ProgramArguments"][-1]
    assert f"cd {shlex.quote(str(scribe_dir))} &&" in plist_command


def test_aborted_handoff_marks_brain_stale(tmp_path):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    hand = tmp_path / "brain" / "ops" / "handoff"
    write_json(hand / "2026-01-04.json", {"decision": "apply", "abort_reasons": []})
    write_json(hand / "2026-01-05.json", {"decision": "abort", "abort_reasons": ["root_unavailable: docs"]})
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text().replace(
        "[project]", f'[project]\nbrain_handoff_dir = "{hand.as_posix()}"', 1))
    config, _ = load(proj)
    s = handoff_status(config)
    assert s["stale_brain"] is True and s["latest"].endswith("2026-01-05.json")
    assert s["reasons"] == ["root_unavailable: docs"]
