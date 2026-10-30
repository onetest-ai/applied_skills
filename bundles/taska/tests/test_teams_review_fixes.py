"""Fixes from the whole-branch review of the Teams proposals work. Invented data only."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))
sys.path.insert(0, os.path.dirname(__file__))

from taska_lib import ledger as L, store as S, teams_report as R
from taska_lib.sources import teams_common as TC
import test_teams_common as T
import test_teams_pending as TP


class Cfg(T.Cfg):
    name = "Demo"
    timezone = None
    people = []


def test_context_with_a_pending_teams_proposal_does_not_crash():   # C1
    d = TP._store()
    ctx = L.context(d, Cfg())
    s001 = json.dumps(ctx)
    assert "done@" in s001 and "awaiting_confirm" in s001


def test_pending_teams_op_is_not_an_unverified_claim():   # I2
    d = TP._store()
    assert "unverified_claims" not in json.dumps(L.context(d, Cfg()))


def test_teams_message_never_reaches_extraction_pending(tmp_path):   # C2
    taska = os.path.join(os.path.dirname(__file__), "../skills/run")
    sys.path.insert(0, taska)
    import taska as TK
    d = T.store_with_tree()
    S.write_evidence(d, TC.evidence_item(T.msg("m9", "Final stakeholder contact list is shared with the client")))
    assert all(p["kind"] != "message" for p in TK._pending(d))


def test_judge_answer_without_a_change_is_judge_unavailable(monkeypatch):   # C2
    from taska_lib import judge as J

    class R_:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"answers": {}}).encode()
    monkeypatch.setattr(J.urllib.request, "urlopen", lambda *a, **k: R_())
    with pytest.raises(J.JudgeUnavailable):
        J.judge({"text": "x y z"}, {"id": "S001", "title": "t"}, ["done"], url="http://127.0.0.1:1/x")


def test_confirm_on_a_pinned_state_is_refused_clearly():   # I1
    d = TP._store()
    L.human_event(d, {"op": "edit", "node": "S001", "set": {"state": "in_progress"}})
    with pytest.raises(ValueError) as e:
        L.human_event(d, {"op": "accept", "target": "p1"})
    assert "set by you" in str(e.value)


def test_findings_only_for_proposed_or_verified_and_deduped():   # I3
    d = T.store_with_tree()
    sc = {("Final stakeholder", "S001"): {"done": (0.8, 0.1)}}
    m = T.msg("m1", "Final stakeholder contact list is shared with the client")
    TC.process(d, Cfg(), {}, [m], cursor_key="k", judge_fn=T.fake_judge(sc), card=T.CARD)
    S.update_cursor(d, "k", {"last_at": ""})     # a crash before the cursor moved: the message is processed again
    TC.process(d, Cfg(), {}, [m], cursor_key="k", judge_fn=T.fake_judge(sc), card=T.CARD)
    html = R.render(d)
    assert html.count("<div class='f ") == 1 and "<b>1</b>proposals" in html


def test_bad_cli_output_is_a_source_error_not_a_crash(monkeypatch):   # I4
    d = T.store_with_tree()
    monkeypatch.setattr(TC.MG, "run", lambda scfg, *a: "<html>not json</html>")
    _, rep = TC.chat_adapter(Cfg(), "chat", {"chat_id": "c1", "model_card": T._card_file(d)}, d, {})
    assert rep["status"] == "error" and rep["error"]


def test_missing_model_card_is_missing_config():   # I6
    d = T.store_with_tree()
    _, rep = TC.channel_adapter(Cfg(), "gen", {"team_id": "t", "channel_id": "c"}, d, {})
    assert rep["status"] == "error" and rep["error"].startswith("MISSING_CONFIG") and "model_card" in rep["error"]


def test_ready_reports_each_missing_piece(monkeypatch, tmp_path):   # doctor
    monkeypatch.setattr(TC.MG, "status", lambda scfg: "someone@example.com")
    ok, why = TC.ready({"model_card": str(tmp_path / "none.json")})
    assert not ok and "model card" in why
    card = T._card_file(str(tmp_path))
    ok, why = TC.ready({"model_card": card, "judge_url": "http://127.0.0.1:9/v1/systemone"})
    assert not ok and "decision model" in why
    monkeypatch.setattr(TC.MG, "status", lambda scfg: "")
    ok, why = TC.ready({"model_card": card})
    assert not ok and "msgraph" in why
