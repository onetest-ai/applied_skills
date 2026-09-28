import json
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
