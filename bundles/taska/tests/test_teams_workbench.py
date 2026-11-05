"""Teams messages in the workbench timeline: only those that produced a proposal become a step. Invented data."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))
sys.path.insert(0, os.path.dirname(__file__))

from taska_lib import workbench as W
from taska_lib.sources import teams_common as TC
import test_teams_common as T


class Cfg(T.Cfg):
    name = "Demo"
    timezone = None


def test_only_messages_with_a_proposal_are_timeline_steps():
    d = T.store_with_tree()
    msgs = [T.msg("m1", "Final stakeholder contact list is shared with the client"),
            T.msg("m2", "Lunch plans for the offsite next week are not settled yet", ts="2026-10-21T17:00:00Z")]
    TC.process(d, Cfg(), {}, msgs, cursor_key="k", judge_fn=T.fake_judge({("Final stakeholder", "S001"): {"done": (0.8, 0.1)}}),
               card=T.CARD)
    calls = W.data(d, Cfg())["calls"]
    assert len(calls) == 1 and calls[0]["evidence_kind"] == "message"
