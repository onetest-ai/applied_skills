"""`taska doctor` names the exact install command for what is missing, per platform, and which OS scheduler
registers the schedules (taska_lib/setup.py). Nothing is installed."""
from taska_lib import setup as SU


def test_windows_hints_use_winget_and_name_whisper_release():
    h = SU.install_hints({"ffmpeg": None, "whisper": None, "node": None, "python": "C:\\Python\\python.exe"}, "win32")
    assert h["ffmpeg"] == "winget install Gyan.FFmpeg" and h["node"] == "winget install OpenJS.NodeJS.LTS"
    assert "whisper.cpp" in h["whisper"] and "python" not in h


def test_mac_hints_use_brew():
    h = SU.install_hints({"ffmpeg": None, "whisper": None, "node": "/opt/homebrew/bin/node", "python": "/usr/bin/python3"}, "darwin")
    assert h == {"ffmpeg": "brew install ffmpeg", "whisper": "brew install whisper-cpp"}


def test_scheduler_per_platform():
    assert SU.scheduler("darwin") == "launchd" and SU.scheduler("win32") == "Task Scheduler" and SU.scheduler("linux") is None


def test_doctor_reports_platform_scheduler_and_install(tmp_path):
    import json
    from conftest import run_cli
    (tmp_path / "taska.toml").write_text('[project]\nname = "Harbor Logistics"\n')
    r = run_cli(tmp_path, "doctor")
    assert r["platform"] in ("darwin", "win32", "linux") and "scheduler" in r and isinstance(r["install"], dict)
    assert set(r["tools"]) >= {"ffmpeg", "whisper", "node", "python", "claude"}
