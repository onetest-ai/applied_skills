"""schedule: print a cron line or a launchd plist for unattended Scribe (spec A1).

This module only renders text. It never touches crontab, launchctl, or any system
scheduler state — installing what it prints is the operator's decision, made outside
this tool (`crontab -e`, or `launchctl load ~/Library/LaunchAgents/...plist`).

The chained command (A1, a PoC finding): headless runs use
`claude -p "<slash command>" --permission-mode bypassPermissions`. When a Brain project
is given, its hand-off classification runs first and scribe's own run second — scribe's
stale-Brain preflight (`doctor.handoff_status`) is what lets the scribe half still make
progress against the last good Brain when the brain half aborted.
"""
from __future__ import annotations

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


def render(
    scribe_project: Path,
    brain_project: Path | None,
    time: str = "02:00",
    kind: str = "cron",
) -> str:
    """A ready-to-install crontab line (`kind="cron"`) or launchd plist
    (`kind="launchd"`) for the chained hand-off + run command at `time` (`HH:MM`,
    local time). `brain_project` is `None` when this project has no Brain to hand off
    (its half of the chain is omitted entirely — never printed empty)."""
    command = _chained_command(scribe_project, brain_project)
    if kind == "cron":
        hour, minute = _cron_time_fields(time)
        return f"{minute} {hour} * * * {command}\n"
    if kind == "launchd":
        hh, _, mm = time.partition(":")
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
