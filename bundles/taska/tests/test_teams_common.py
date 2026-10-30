"""Shared Teams processing: fetch via the msgraph CLI, one evidence item per message, candidates, judge, pending
proposals, rejected log, cursor. Stubbed CLI and judge; invented data only."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib import ledger as L, store as S
from taska_lib.sources import teams_common as TC


class Cfg:
    working_days = [1, 2, 3, 4, 5]

    def canonical(self, x):
        return x


def graph(mid, text, ts, sender="Sam Lee", files=(), reply_to=None, replies=()):
    return {"id": mid, "replyToId": reply_to, "createdDateTime": ts, "messageType": "message",
            "from": {"user": {"displayName": sender}}, "body": {"contentType": "html", "content": f"<p>{text}</p>"},
            "attachments": [{"id": f"a{i}", "name": f, "contentUrl": f"https://files.example/{f}"} for i, f in enumerate(files)],
            "webUrl": f"https://teams.example/{mid}", "replies": list(replies)}


def store_with_tree():
    d = tempfile.mkdtemp()
    S.ensure(d)
    S.append(d, [
        {"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-09-01",
         "fields": {"title": "Discovery", "ac": [], "depends_on": []}},
        {"type": "create", "mid": "c2", "id": "S001", "kind": "story", "by": "plan", "evidence": None, "date": "2026-09-01",
         "fields": {"title": "Stakeholder contact list compiled", "parent": "E01", "ac": ["List shared with the client"],
                    "depends_on": []}},
        {"type": "create", "mid": "c3", "id": "S002", "kind": "story", "by": "plan", "evidence": None, "date": "2026-09-01",
         "fields": {"title": "Billing process mapped", "parent": "E01", "ac": ["Map reviewed"], "depends_on": []}},
    ])
    return d


def fake_judge(scores):
    """scores: {(text_prefix, node): {change: (yes, partial)}}; everything else is a clear no."""
    def j(msg, task, changes, url=None, timeout=None):
        out = {}
        for c in changes:
            y, p = next((v[c] for (pre, n), v in scores.items() if msg["text"].startswith(pre) and n == task["id"] and c in v), (0.02, 0.03))
            out[c] = {"yes": y, "partial": p}
        return out
    return j


CARD = {"model": "jaredpalmer/kev-4b", "revision": "r1", "propose_at": 0.75, "auto_apply_allowed": False}


# ---- fetch ----
def test_channel_fetch_uses_real_cli_words(monkeypatch):
    calls = []
    monkeypatch.setattr(TC.MG, "run", lambda scfg, *a: calls.append(a) or json.dumps([]))
    TC.fetch_channel({}, "team-1", "chan-1")
    assert calls[0] == ("channels", "--team-id", "team-1", "--channel-id", "chan-1", "--messages", "--limit", "50",
                        "--expand-replies", "--json")


def test_chat_fetch_uses_real_cli_words(monkeypatch):
    calls = []
    monkeypatch.setattr(TC.MG, "run", lambda scfg, *a: calls.append(a) or json.dumps([]))
    TC.fetch_chat({}, "chat-1", max_messages=300)
    assert calls[0] == ("teams", "--messages", "chat-1", "--max", "300", "--json")


def test_channel_replies_are_flattened_with_their_parent(monkeypatch):
    raw = [graph("p1", "Who has the stakeholder list?", "2026-10-20T09:00:00Z",
                 replies=[graph("r1", "Final stakeholder list is in the folder now", "2026-10-20T10:00:00Z")])]
    monkeypatch.setattr(TC.MG, "run", lambda scfg, *a: json.dumps(raw))
    msgs = TC.fetch_channel({}, "t", "c")
    r = [m for m in msgs if m["id"] == "r1"][0]
    assert r["reply_to"] == "p1" and "Who has the stakeholder list" in r["ctx"]


def test_system_events_are_dropped(monkeypatch):
    raw = [dict(graph("s1", "", "2026-10-20T09:00:00Z"), messageType="unknownFutureValue")]
    monkeypatch.setattr(TC.MG, "run", lambda scfg, *a: json.dumps(raw))
    assert TC.fetch_chat({}, "c") == []


def test_flat_chat_context_is_preceding_same_day(monkeypatch):
    raw = [graph("m3", "Done.", "2026-10-20T11:00:00Z"), graph("m2", "Can someone share the list?", "2026-10-20T10:00:00Z"),
           graph("m1", "Old message from the day before", "2026-10-19T10:00:00Z")]
    monkeypatch.setattr(TC.MG, "run", lambda scfg, *a: json.dumps(raw))
    m3 = [m for m in TC.fetch_chat({}, "c") if m["id"] == "m3"][0]
    assert "share the list" in m3["ctx"] and "day before" not in m3["ctx"]


# ---- evidence ----
def test_evidence_item_shape():
    m = TC.to_msg(graph("m1", "Final stakeholder list is in the folder", "2026-10-21T16:42:00Z", files=["list.xlsx"]), "chat-1")
    m["ctx"] = "earlier text"
    ev = TC.evidence_item(m)
    assert ev["kind"] == "message" and ev["source"] == "teams" and ev["at"] == "2026-10-21T16:42:00"
    assert ev["turns"] == [{"speaker": "Sam Lee", "ts": "16:42", "text": "Final stakeholder list is in the folder"}]
    assert ev["meta"]["context"] == "earlier text" and ev["url"] == "https://teams.example/m1"
    assert "earlier text" not in S.full_text(ev)


def test_evidence_id_ignores_the_display_name():
    a = TC.evidence_item(TC.to_msg(graph("m1", "x y z w v", "2026-10-21T16:42:00Z"), "chat-1"))
    b = TC.evidence_item(dict(TC.to_msg(graph("m1", "x y z w v", "2026-10-21T16:42:00Z"), "chat-1"), container_name="Renamed"))
    assert a["id"] == b["id"]


# ---- process ----
def run_process(d, msgs, scores, card=CARD, key="teams_chat:c1"):
    return TC.process(d, Cfg(), {}, msgs, cursor_key=key, judge_fn=fake_judge(scores), card=card)


def msg(mid, text, ts="2026-10-21T16:42:00Z", files=()):
    return TC.to_msg(graph(mid, text, ts, files=files), "c1")


def test_strong_completion_becomes_a_pending_proposal():
    d = store_with_tree()
    r = run_process(d, [msg("m1", "Final stakeholder contact list is shared with the client")],
                    {("Final stakeholder", "S001"): {"done": (0.80, 0.10)}})
    assert r["proposed"] == 1
    N, *_ = L.fold(d)
    s = N["S001"]["suggestions"]
    assert N["S001"]["state"] == "open" and s and s[0]["status"] == "pending" and s[0]["effect"] == "done"
    assert s[0]["link"] == "https://teams.example/m1" and s[0]["gate"]["model"] == "jaredpalmer/kev-4b"
    assert s[0]["why"].startswith("Sam Lee") and "open → done" in s[0]["suggested"]


def test_weak_signal_goes_to_rejected_log_with_scores():
    d = store_with_tree()
    r = run_process(d, [msg("m1", "Thinking about the stakeholder contact list again later today")],
                    {("Thinking", "S001"): {"done": (0.20, 0.30)}})
    assert r["proposed"] == 0 and r["rejected"] == 1
    rec = json.loads(open(os.path.join(d, "chat_rejected.jsonl")).read().splitlines()[0])
    assert rec["reason"].startswith("below_threshold") and rec["best_node"] == "S001" and rec["best_score"] == 0.5


def test_too_short_is_logged_not_dropped():
    d = store_with_tree()
    r = run_process(d, [msg("m1", "ok thx")], {})
    assert r["too_short"] == 1
    rec = json.loads(open(os.path.join(d, "chat_rejected.jsonl")).read().splitlines()[0])
    assert rec["reason"] == "too_short"


def test_finding_record_is_written():
    d = store_with_tree()
    run_process(d, [msg("m1", "Final stakeholder contact list is shared with the client")],
                {("Final stakeholder", "S001"): {"done": (0.85, 0.05)}})
    f = [json.loads(l) for l in open(os.path.join(d, "out", "teams-findings.jsonl"))]
    assert f[0]["node"] == "S001" and f[0]["change"] == "done" and f[0]["severity"] == "Blocker"
    assert f[0]["quote"] in "Final stakeholder contact list is shared with the client"
    assert f[0]["gate"]["score"] == 0.9 and f[0]["gate"]["threshold"] == 0.75


def test_evidence_is_marked_processed_so_extraction_skips_it():
    d = store_with_tree()
    run_process(d, [msg("m1", "Final stakeholder contact list is shared with the client")], {})
    evid = [e for e in S.evidence(d)][0]
    assert evid in S.processed(S.events(d))


def test_cursor_advances_and_rerun_is_idempotent():
    d = store_with_tree()
    ms = [msg("m1", "Final stakeholder contact list is shared with the client")]
    sc = {("Final stakeholder", "S001"): {"done": (0.80, 0.10)}}
    run_process(d, ms, sc)
    assert S.cursors(d)["teams_chat:c1"]["last_at"] == "2026-10-21T16:42:00Z"
    r2 = run_process(d, ms, sc)
    assert r2["new"] == 0
    assert len([e for e in S.events(d) if e.get("op") == "status"]) == 1


def test_judge_failure_stops_without_advancing_the_cursor():
    d = store_with_tree()

    def broken(*a, **k):
        raise TC.J.JudgeUnavailable("decision model not reachable at http://127.0.0.1:8009")
    with pytest.raises(TC.J.JudgeUnavailable):
        TC.process(d, Cfg(), {}, [msg("m1", "Final stakeholder contact list is shared with the client")],
                   cursor_key="k", judge_fn=broken, card=CARD)
    assert "k" not in S.cursors(d)


def test_task_already_in_that_state_is_verified_not_proposed():
    d = store_with_tree()
    L.human_event(d, {"op": "edit", "node": "S001", "set": {"state": "done"}})
    r = run_process(d, [msg("m1", "Final stakeholder contact list is shared with the client")],
                    {("Final stakeholder", "S001"): {"done": (0.80, 0.10)}})
    assert r["proposed"] == 0 and r["verified"] == 1


# ---- adapters (ADAPTERS signature) ----
def _card_file(d):
    p = os.path.join(d, "card.json")
    json.dump(CARD, open(p, "w"))
    return p


def test_registered_adapters():
    from taska_lib.sources import ADAPTERS
    assert ADAPTERS["teams_chat"] is TC.chat_adapter and ADAPTERS["teams_channel"] is TC.channel_adapter


def test_chat_adapter_runs_and_reports(monkeypatch):
    d = store_with_tree()
    raw = [graph("m1", "Final stakeholder contact list is shared with the client", "2026-10-21T16:42:00Z")]
    monkeypatch.setattr(TC.MG, "run", lambda scfg, *a: json.dumps(raw))
    monkeypatch.setattr(TC.J, "judge", fake_judge({("Final stakeholder", "S001"): {"done": (0.8, 0.1)}}))
    cur, rep = TC.chat_adapter(Cfg(), "daily", {"chat_id": "c1", "chat_name": "Daily", "model_card": _card_file(d)}, d, {})
    assert rep["status"] == "ok" and rep["proposed"] == 1 and rep["source"] == "daily"


def test_adapter_missing_config_is_an_exact_error():
    d = store_with_tree()
    _, rep = TC.chat_adapter(Cfg(), "daily", {"model_card": "x"}, d, {})
    assert rep["status"] == "error" and "chat_id" in rep["error"]
    _, rep = TC.channel_adapter(Cfg(), "gen", {"team_id": "t"}, d, {})
    assert rep["status"] == "error" and "channel_id" in rep["error"]


def test_adapter_reports_judge_unavailable(monkeypatch):
    d = store_with_tree()
    monkeypatch.setattr(TC.MG, "run", lambda scfg, *a: json.dumps([graph("m1", "Final stakeholder contact list is shared now with the team", "2026-10-21T16:42:00Z")]))

    def broken(*a, **k):
        raise TC.J.JudgeUnavailable("decision model not reachable at http://127.0.0.1:8009/v1/systemone")
    monkeypatch.setattr(TC.J, "judge", broken)
    _, rep = TC.chat_adapter(Cfg(), "daily", {"chat_id": "c1", "model_card": _card_file(d)}, d, {})
    assert rep["status"] == "error" and "8009" in rep["error"]


# ---- the measured rule comes from the model card ----
def test_card_rule_limits_changes_candidates_and_length():
    d = store_with_tree()
    asked = []

    def j(msg, task, changes, url=None, timeout=None):
        asked.append((task["id"], tuple(changes)))
        return {c: {"yes": 0.0, "partial": 0.0} for c in changes}
    card = dict(CARD, changes=["done", "progress"], k=1, min_words_to_judge=8)
    TC.process(d, Cfg(), {}, [msg("m1", "stakeholder contact list shared"),                    # 4 words: too short
                              msg("m2", "the stakeholder contact list is now shared with everyone", ts="2026-10-21T17:00:00Z"),
                              msg("m3", "billing map draft is up", ts="2026-10-21T18:00:00Z")],  # 5 words: stored, not judged
               cursor_key="k", judge_fn=j, card=card)
    assert asked == [("S001", ("done", "progress"))]
    rec = [json.loads(l)["reason"] for l in open(os.path.join(d, "chat_rejected.jsonl"))]
    assert rec == ["too_short", "below_threshold:0.75", "not_judged:short"]


def test_default_rule_without_card_keys():
    d = store_with_tree()
    asked = []

    def j(msg, task, changes, url=None, timeout=None):
        asked.append(tuple(changes))
        return {c: {"yes": 0.0, "partial": 0.0} for c in changes}
    TC.process(d, Cfg(), {}, [msg("m1", "Final stakeholder contact list is shared with the client")], cursor_key="k",
               judge_fn=j, card=CARD)
    assert asked and all("cancelled" not in a for a in asked)
