"""Tests that need a system tool skip WITH A WARNING, never silently.

`require_tool` skips with a reason in one fixed format; conftest's
`pytest_terminal_summary` collects those reasons and prints a MISSING SYSTEM
TOOLS section at the end of every run, so "not verified on this machine" is
visible without -rs.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PREFIX = "MISSING TOOL: "
INSTALL = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg",
    "whisper-cli": "brew install whisper-cpp + a model (brain_doctor.py whisper-models)",
    "soffice": "brew install --cask libreoffice",
}


def skip_reason(tool: str) -> str:
    return f"{PREFIX}{tool} (install: {INSTALL.get(tool, 'see brain_doctor.py')}; see brain_doctor.py)"


def require_tool(*tools: str):
    missing = [t for t in tools if not shutil.which(t)]
    return pytest.mark.skipif(bool(missing), reason=skip_reason(missing[0]) if missing else "")


def missing_tools_report(reasons: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for r in reasons:
        i = r.find(PREFIX)
        if i < 0:
            continue
        tool = r[i + len(PREFIX):].split(" ", 1)[0]
        counts[tool] = counts.get(tool, 0) + 1
    if not counts:
        return []
    n = sum(counts.values())
    lines = [f"WARNING: {n} test{'s' if n != 1 else ''} skipped — "
             "these system-tool paths were NOT verified on this machine"]
    for tool, c in sorted(counts.items()):
        lines.append(f"  {tool:<12} {c} test{'s' if c != 1 else ' '}   install: "
                     f"{INSTALL.get(tool, 'see brain_doctor.py')}")
    return lines


def make_synthetic_video(path: Path, audio: bool = False) -> Path:
    """40 s, 640x360, 10 fps: bars | solid blue | noise | grey slide (10 s each)."""
    src = [
        "smptebars=s=640x360:d=10:r=10",
        "color=c=0x2050a0:s=640x360:d=10:r=10",
        "nullsrc=s=640x360:d=10:r=10,geq=random(1)*255:128:128",
        "color=c=0xe0e0e0:s=640x360:d=10:r=10,"
        "drawbox=x=40:y=40:w=560:h=60:color=black:t=fill,"
        "drawbox=x=40:y=140:w=300:h=20:color=black:t=fill",
    ]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for s in src:
        cmd += ["-f", "lavfi", "-i", s]
    graph = "[0][1][2][3]concat=n=4:v=1:a=0,format=yuv420p[v]"
    maps = ["-map", "[v]"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=40"]
        maps += ["-map", "4:a", "-c:a", "aac"]
    cmd += ["-filter_complex", graph, *maps, str(path)]
    subprocess.run(cmd, check=True)
    return path
