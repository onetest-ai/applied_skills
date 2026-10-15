"""Fixtures for the taska bundle. Tests live at the bundle root (Agent Skills: a skill ships only SKILL.md and its
runtime resources). Every name below is invented."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKA_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = TASKA_ROOT / "skills" / "run"
if str(RUN_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_DIR))

VTT_DAY1 = """WEBVTT

00:00:05.000 --> 00:00:09.000
<v Nora Quill>Morning everyone, let's start with the onboarding sessions for the logistics team.</v>

00:00:10.000 --> 00:00:20.000
<v Nora Quill>The warehouse walkthrough session with the client is booked for next Tuesday at the Dunmore site.</v>

00:00:21.000 --> 00:00:22.000
<v Ravi Tamsin>Okay.</v>

00:00:23.000 --> 00:00:33.000
<v Ravi Tamsin>I still can't log into the inventory system, we are waiting for Opal to reset the passwords before we can pull the stock reports.</v>

00:00:34.000 --> 00:00:44.000
<v Opal Brennick1>I will reset the passwords today and send the new credentials to each of you by email.</v>
"""

VTT_DAY2 = """WEBVTT

00:00:03.000 --> 00:00:12.000
<v Nora Quill>Quick update, the warehouse walkthrough session happened yesterday and the team took a lot of photos of the picking process.</v>

00:00:13.000 --> 00:00:22.000
<v Ravi Tamsin>I successfully logged in with the new credentials this morning and pulled the first stock report.</v>
"""


def run_cli(project, *args, check=True):
    r = subprocess.run([sys.executable, str(RUN_DIR / "taska.py"), "--project", str(project), *args],
                       capture_output=True, text=True)
    out = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"raw": r.stdout, "err": r.stderr}
    if check:
        assert r.returncode == 0, (r.stdout, r.stderr)
    return out


@pytest.fixture
def project(tmp_path):
    """A project with a plan, two people and a meetings folder holding two daily transcripts."""
    calls = tmp_path / "calls"
    calls.mkdir()
    (calls / "2026-03-02 Daily sync.vtt").write_text(VTT_DAY1)
    (calls / "2026-03-03 Daily sync.vtt").write_text(VTT_DAY2)
    (tmp_path / "epics.csv").write_text("name,start,end\nOnboarding,2026-03-01,2026-03-20\nDiscovery,2026-03-09,2026-04-03\n")
    (tmp_path / "taska.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "Harbor Logistics"
        [plan]
        file = "epics.csv"
        [[people]]
        name = "Nora Quill"
        side = "internal"
        [[people]]
        name = "Ravi Tamsin"
        side = "internal"
        [[people]]
        name = "Opal Brennick"
        side = "client"
        aliases = ["opal@harbor.example"]
        [sources.daily]
        adapter = "meetings_folder"
        path = "{calls}"
        client_patterns = ["weekly status"]
    """))
    return tmp_path
