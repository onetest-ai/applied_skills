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
    # Neither `cd` target is ever used RAW (unquoted) — the log path further down
    # the line legitimately repeats scribe_dir too, double-quoted rather than via
    # shlex.quote, so this checks the specific unquoted-`cd` shape rather than
    # asserting the bare path string never reappears anywhere in the line.
    assert f"cd {brain_dir} &&" not in command
    assert f"cd {scribe_dir} &&" not in command

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


def _configure_handoff_dir(proj, hand_dir):
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text().replace(
        "[project]", f'[project]\nbrain_handoff_dir = "{hand_dir.as_posix()}"', 1))


def test_no_report_for_todays_date_marks_brain_stale(tmp_path, monkeypatch):
    """Review fix round 2, Important #1(b): a report that exists but is dated
    BEFORE today (scribe's own run date, `config.now`) means nobody has
    evidence the Brain was refreshed today — e.g. the brain-maintenance leg
    of the chained schedule never ran at all (cron misconfigured, machine
    asleep, ...), not just that `handoff` classified an abort. Must be
    reported the same way an abort is: `stale_brain=True` with a
    `no hand-off report for <today>` reason."""
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    hand = tmp_path / "brain" / "ops" / "handoff"
    write_json(hand / "2026-01-04.json", {"decision": "apply", "abort_reasons": []})
    _configure_handoff_dir(proj, hand)
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-06")  # a day the brain leg never ran
    config, _ = load(proj)
    s = handoff_status(config)
    assert s["stale_brain"] is True
    assert s["reasons"] == ["no hand-off report for 2026-01-06"]
    assert s["decision"] == "apply"  # the report itself was a clean apply — just not today's


def test_fresh_report_dated_today_is_not_stale(tmp_path, monkeypatch):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    hand = tmp_path / "brain" / "ops" / "handoff"
    write_json(hand / "2026-01-06.json", {"decision": "apply", "abort_reasons": []})
    _configure_handoff_dir(proj, hand)
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-06")
    config, _ = load(proj)
    s = handoff_status(config)
    assert s["stale_brain"] is False
    assert s["reasons"] == []


def test_configured_but_empty_handoff_dir_is_stale(tmp_path, monkeypatch):
    """No report at all (e.g. the brain-maintenance leg has literally never
    run once) is the same failure mode as a too-old one — silence is not
    evidence of freshness."""
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    hand = tmp_path / "brain" / "ops" / "handoff"
    hand.mkdir(parents=True)
    _configure_handoff_dir(proj, hand)
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-06")
    config, _ = load(proj)
    s = handoff_status(config)
    assert s["stale_brain"] is True
    assert s["reasons"] == ["no hand-off report for 2026-01-06"]


def test_unconfigured_handoff_dir_is_never_stale(tmp_path):
    """A project not using hand-off mode at all must not be penalized by the
    date check — the check applies only once brain_handoff_dir is configured."""
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, _ = load(proj)
    s = handoff_status(config)
    assert s == {"latest": None, "decision": None, "stale_brain": False, "reasons": []}


def test_schedule_sets_path_and_log_redirection(tmp_path, monkeypatch):
    """Review fix round 2, Important #2: cron/launchd run with a minimal PATH
    (claude/uv/pandoc/soffice/npx are typically not on it) and nothing
    captures stdout/stderr, so a scheduled run can fail with no trace. The
    generated command/plist must carry the caller's own PATH and redirect
    output to a log file under <scribe_project>/logs/."""
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin:/bin")
    scribe_dir = tmp_path / "scribe"

    line = schedule.render(scribe_dir, None, time="02:30", kind="cron")
    assert 'PATH="/opt/homebrew/bin:/usr/bin:/bin"' in line
    assert f"{scribe_dir}/logs/schedule-" in line and ".log" in line
    assert ">>" in line  # append, not truncate, across runs

    plist = schedule.render(scribe_dir, None, kind="launchd")
    data = plistlib.loads(plist.encode("utf-8"))
    assert data["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"
    log_path = str(scribe_dir / "logs" / "schedule.log")
    assert data["StandardOutPath"] == log_path
    assert data["StandardErrorPath"] == log_path
