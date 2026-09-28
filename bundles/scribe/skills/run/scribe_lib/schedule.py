"""schedule: print a cron line or a launchd plist for unattended Scribe (spec A1).

This module only renders text. It never touches crontab, launchctl, or any system
scheduler state — installing what it prints is the operator's decision, made outside
this tool (`crontab -e`, or `launchctl load ~/Library/LaunchAgents/...plist`).

The chained command (A1, a PoC finding): headless runs use
`claude -p "<slash command>" --permission-mode bypassPermissions`. When a Brain project
is given, its hand-off classification runs first and scribe's own run second — scribe's
stale-Brain preflight (`doctor.handoff_status`) is what lets the scribe half still make
progress against the last good Brain when the brain half aborted.

Review fix round 2, Important #2: cron and launchd both run with a minimal PATH (no
user shell profile is sourced), so `claude`/`uv`/`pandoc`/`soffice`/`npx` are typically
NOT on it and the scheduled run fails — silently, since nothing is watching stdout/stderr
either. `render()` therefore captures `os.environ["PATH"]` from the process running
`scribe.py schedule` (the interactive shell the operator already has `claude` etc.
working in) and bakes it into the generated command/plist, and points stdout+stderr at a
log file under `<scribe project>/logs/` so a silent failure leaves evidence.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

_LABEL = "ai.applied.scribe"


def _brain_command(brain_project: Path) -> str:
    return f'cd {shlex.quote(str(brain_project))} && claude -p "/brain:brain-maintenance handoff" --permission-mode bypassPermissions'


def _scribe_command(scribe_project: Path) -> str:
    return f'cd {shlex.quote(str(scribe_project))} && claude -p "/scribe:run --due" --permission-mode bypassPermissions'


def _chained_command(scribe_project: Path, brain_project: Path | None) -> str:
    parts = []
    if brain_project is not None:
        parts.append(_brain_command(brain_project))
    parts.append(_scribe_command(scribe_project))
    return "; ".join(parts)


def _cron_time_fields(time: str) -> tuple[str, str]:
    hh, _, mm = time.partition(":")
    hour = str(int(hh))
    minute = str(int(mm))
    return hour, minute


def _log_dir(scribe_project: Path) -> Path:
    return scribe_project / "logs"


def render(
    scribe_project: Path,
    brain_project: Path | None,
    time: str = "02:00",
    kind: str = "cron",
) -> str:
    """A ready-to-install crontab line (`kind="cron"`) or launchd plist
    (`kind="launchd"`) for the chained hand-off + run command at `time` (`HH:MM`,
    local time). `brain_project` is `None` when this project has no Brain to hand off
    (its half of the chain is omitted entirely — never printed empty). Both forms carry
    the current process's `PATH` and redirect stdout/stderr to a log file under
    `<scribe_project>/logs/` (see the module docstring, review fix round 2 #2)."""
    command = _chained_command(scribe_project, brain_project)
    path_value = os.environ.get("PATH", "")
    log_dir = _log_dir(scribe_project)
    if kind == "cron":
        hour, minute = _cron_time_fields(time)
        # crontab has no per-line PATH-of-the-caller inheritance and no shell profile
        # is sourced, so PATH is set explicitly for the whole chained command (`export`,
        # not a bare `PATH=... cmd` prefix, which would apply only to the first simple
        # command before the first `&&`/`;`). The log file name is date-stamped via a
        # `date` command substitution — crontab requires a literal `%` to be
        # backslash-escaped (an unescaped `%` means "newline" to cron), hence `\%`.
        log_path = f'"{log_dir}/schedule-$(date +\\%Y-\\%m-\\%d).log"'
        return f'{minute} {hour} * * * export PATH="{path_value}"; {command} >> {log_path} 2>&1\n'
    if kind == "launchd":
        hh, _, mm = time.partition(":")
        # A plist path string is never shell-expanded, so it cannot itself carry a
        # `date`-substituted name the way the cron line does; launchd (over)writes this
        # single file on every run instead of rotating it per day.
        log_path = str(log_dir / "schedule.log")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "    <key>Label</key>\n"
            f"    <string>{_LABEL}</string>\n"
            "    <key>ProgramArguments</key>\n"
            "    <array>\n"
            "        <string>/bin/sh</string>\n"
            "        <string>-c</string>\n"
            f"        <string>{xml_escape(command)}</string>\n"
            "    </array>\n"
            "    <key>EnvironmentVariables</key>\n"
            "    <dict>\n"
            "        <key>PATH</key>\n"
            f"        <string>{xml_escape(path_value)}</string>\n"
            "    </dict>\n"
            "    <key>StandardOutPath</key>\n"
            f"    <string>{xml_escape(log_path)}</string>\n"
            "    <key>StandardErrorPath</key>\n"
            f"    <string>{xml_escape(log_path)}</string>\n"
            "    <key>StartCalendarInterval</key>\n"
            "    <dict>\n"
            "        <key>Hour</key>\n"
            f"        <integer>{int(hh)}</integer>\n"
            "        <key>Minute</key>\n"
            f"        <integer>{int(mm)}</integer>\n"
            "    </dict>\n"
            "</dict>\n"
            "</plist>\n"
        )
    raise ValueError(f"unknown schedule kind: {kind!r}")
