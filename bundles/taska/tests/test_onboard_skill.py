"""The onboard skill sets a new owner up without the terminal after install: doctor names what to install, the
schedules are registered through `taska schedules --register`, Windows is covered (pinned phrases)."""
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "onboard" / "SKILL.md"


def test_the_onboard_skill_covers_setup_schedules_and_windows():
    s = SKILL.read_text()
    assert s.startswith("---\n") and "description: Use when " in s
    for phrase in ("`$TASKA ai --probe`", "`[[schedules]]`", "`$TASKA schedules --register`", "`taska run-schedule <name>`",
                   "Task Scheduler on Windows", "One machine runs a project", "`--force`", "**Run now**",
                   "**Windows.**", "`winget`", "`pythonw`", "`http://127.0.0.1:8766/`", "`[report].owner`"):
        assert phrase in s, phrase
    assert "$TASKA schedule --time" not in s   # the old cron/launchd printout is gone: schedules are registered
