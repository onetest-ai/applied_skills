"""What a machine needs for taska, and how to get it: `doctor` reports which tools are on PATH, names the exact
install command for each missing one on this platform, and which OS scheduler registers the schedules. Nothing is
installed from here."""
import shutil
import sys

INSTALL = {
    "darwin": {"ffmpeg": "brew install ffmpeg", "whisper": "brew install whisper-cpp", "node": "brew install node",
               "python": "brew install python@3.12", "claude": "npm install -g @anthropic-ai/claude-code"},
    "win32": {"ffmpeg": "winget install Gyan.FFmpeg", "whisper": "download whisper.cpp's Windows release (whisper-cli.exe) and put it on PATH",
              "node": "winget install OpenJS.NodeJS.LTS", "python": "winget install Python.Python.3.12",
              "claude": "npm install -g @anthropic-ai/claude-code"},
}
SCHEDULERS = {"darwin": "launchd", "win32": "Task Scheduler"}


def tools():
    """Where each tool is, or None (the media tools come from media.tools)."""
    from . import media
    t = dict(media.tools())
    t.update(node=shutil.which("node"), python=sys.executable, claude=shutil.which("claude"))
    return t


def install_hints(found, platform=None):
    """{tool: command} for every tool in `found` that is missing, on `platform` (default: this one)."""
    cmds = INSTALL.get(platform or sys.platform, {})
    return {k: cmds[k] for k, v in found.items() if not v and k in cmds}


def scheduler(platform=None):
    return SCHEDULERS.get(platform or sys.platform)
