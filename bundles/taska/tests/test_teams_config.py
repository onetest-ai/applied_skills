"""A taska.toml with Teams sources loads, and config accepts exactly the registered adapters. Invented data."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib import config as C
from taska_lib.sources import ADAPTERS

TOML = """[project]
name = "Demo"
store = ".taska"

[sources.chat]
adapter = "teams_chat"
chat_id = "19:meeting_x@thread.v2"
model_card = "card.json"

[sources.general]
adapter = "teams_channel"
team_id = "t"
channel_id = "19:c@thread.tacv2"
model_card = "card.json"
"""


def test_teams_sources_load(tmp_path):
    (tmp_path / "taska.toml").write_text(TOML)
    cfg = C.load(str(tmp_path))
    assert cfg.sources["chat"]["adapter"] == "teams_chat" and cfg.sources["general"]["adapter"] == "teams_channel"


def test_config_accepts_exactly_the_registered_adapters():
    assert set(C.adapters()) == set(ADAPTERS)
