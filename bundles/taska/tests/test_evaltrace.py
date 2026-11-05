"""eval-trace/1 (evaltrace.py): one model built from the session, small views over it. Invented data."""
import copy
import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent.parent / "evals"
RUN = Path(__file__).resolve().parent.parent / "skills" / "run"
for p in (RUN, EVALS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import bank as B  # noqa: E402
import evaltrace as TR  # noqa: E402
import trajectory as TJ  # noqa: E402
from taska_lib import store as S  # noqa: E402

EV = "2026-03-02_meetings_ab12cd34"
TASKA = "/proj/taska"
TEXT = "We will send the budget plan on Friday. Nobody owns the venue yet. Maybe a party."
Q1, Q2 = "send the budget plan on Friday", "Maybe a party"
ITEM = {"id": EV, "kind": "meeting", "text": TEXT, "title": "Sync"}
PROPOSALS = f"/proj/.taska/proposals/{EV}.json"


def _a(agent, uid, name, inp, ts):
    rec = {"type": "assistant", "timestamp": ts, "message": {"model": "m1", "content": [
        {"type": "tool_use", "id": uid, "name": name, "input": inp}],
        "usage": {"input_tokens": 1, "output_tokens": 10, "cache_read_input_tokens": 100}}}
    return rec


def _r(uid, out, ts, err=False):
    b = {"type": "tool_result", "tool_use_id": uid, "content": out}
    if err:
        b["is_error"] = True
    return {"type": "user", "timestamp": ts, "message": {"content": [b]}}


def _w(path, recs):
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def _session(tmp_path, ack=True):
    s = tmp_path / "s1.jsonl"
    _w(s, [_a("", "m1", "Agent", {"description": "Extraction batch 1"}, "2026-03-02T07:00:00Z"),
           _r("m1", "ok", "2026-03-02T07:05:00Z"),
           {"type": "cost-state", "totalCostUSD": 1.5}])
    sub = tmp_path / "s1" / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    done = f"{TASKA} done --evidence {EV}"
    acked = f' --ack "empty tail" --quote "{Q2}"' if ack else ""
    ops = [{"op": "create", "kind": "task", "title": "Send plan", "quote": Q1},
           {"op": "create", "kind": "task", "title": "Party", "quote": Q2}]
    _w(sub / "agent-a1.jsonl", [
        _a("a1", "r1", "Read", {"file_path": f"/proj/.taska/evidence/{EV}.md", "offset": 1, "limit": 3}, "2026-03-02T07:01:00Z"),
        _r("r1", "1\tx\n2\ty\n3\tz", "2026-03-02T07:01:01Z"),
        _a("a1", "w1", "Write", {"file_path": PROPOSALS, "content": json.dumps(ops)}, "2026-03-02T07:01:02Z"),
        _r("w1", "ok", "2026-03-02T07:01:03Z"),
        _a("a1", "b1", "Bash", {"command": f"{TASKA} apply --evidence {EV} --proposals {PROPOSALS}"}, "2026-03-02T07:01:04Z"),
        _r("b1", json.dumps({"events": 1, "rejected": [{"i": 1, "why": "quote too short"}], "status": "ok"}), "2026-03-02T07:01:05Z"),
        _a("a1", "b2", "Bash", {"command": done}, "2026-03-02T07:01:06Z"),
        _r("b2", "Exit code 1\n" + json.dumps({"status": "coverage", "parts": 3, "starts": ["00:00", "00:10"], "empty_parts": [3]}),
           "2026-03-02T07:01:07Z", err=True),
        _a("a1", "b3", "Bash", {"command": f"{TASKA} leftovers --evidence {EV}"}, "2026-03-02T07:01:08Z"),
        _r("b3", json.dumps({"count": 2, "token": "tok9", "status": "ok"}), "2026-03-02T07:01:09Z"),
        _a("a1", "b4", "Bash", {"command": f"{TASKA} done --evidence {EV} --leftovers tok9{acked}"}, "2026-03-02T07:01:10Z"),
        _r("b4", json.dumps({"status": "ok"}), "2026-03-02T07:01:11Z"),
        _a("a1", "x1", "WebSearch", {"query": "nothing"}, "2026-03-02T07:01:12Z"),
        _r("x1", "", "2026-03-02T07:01:13Z"),
    ])
    (sub / "agent-a1.meta.json").write_text('{"description": "Extraction batch 1"}')
    return s


def _model(tmp_path, with_ev=True):
    return TR.from_run(TJ.load_run(_session(tmp_path)), {EV: ITEM} if with_ev else None)


def test_the_model_has_spans_per_call_and_taska_events_in_time_order(tmp_path):
    t = _model(tmp_path)
    assert t["schema"] == "eval-trace/1" and t["session"] == "s1"
    assert [e["type"] for e in t["events"]] == ["read", "propose", "propose", "apply", "apply", "gate", "leftovers", "done"]
    assert len(t["spans"]) == 8
    assert all({"id", "agent", "tool", "start", "end", "error", "step"} <= set(s) for s in t["spans"])
    assert all(e["evidence"] == EV and e["span"] in {s["id"] for s in t["spans"]} for e in t["events"])
    assert t["events"][0]["lines"] == [1, 3]
    gate = next(e for e in t["events"] if e["type"] == "gate")
    assert gate["status"] == "coverage" and gate["parts"] == 3 and gate["starts"] == ["00:00", "00:10"] and gate["quote"] == []
    assert next(e for e in t["events"] if e["type"] == "done")["ack"]
    assert next(e for e in t["events"] if e["type"] == "leftovers")["count"] == 2


def test_a_proposed_quote_carries_its_position_in_the_meeting(tmp_path):
    t = _model(tmp_path)
    p = [e for e in t["events"] if e["type"] == "propose"]
    start = S.quote_offset(ITEM, Q1)
    assert p[0]["quote"] == Q1 and p[0]["qspan"] == [start, start + len(Q1.lower())] and p[0]["op"] == "create"
    assert all(e["qspan"] is None for e in _model(tmp_path, with_ev=False)["events"] if e["type"] == "propose")


def test_an_apply_event_says_applied_or_rejected_and_why(tmp_path):
    a = [e for e in _model(tmp_path)["events"] if e["type"] == "apply"]
    assert [(e["result"], e.get("why")) for e in a] == [("applied", None), ("rejected", "quote too short")]
    assert [e["index"] for e in a] == [0, 1]


def test_unknown_tools_stay_spans_and_missing_fields_never_raise(tmp_path):
    t = _model(tmp_path)
    assert any(s["tool"] == "WebSearch" for s in t["spans"])
    s = tmp_path / "bad.jsonl"
    _w(s, [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "taska apply --evidence x"}}]}},
           {"type": "assistant", "timestamp": "2026-03-02T07:00:00Z", "message": {"content": [
               {"type": "tool_use", "id": "q", "name": "Write", "input": {"file_path": f"/p/{EV}.json", "content": "not json"}}]}},
           {"type": "bogus"}])
    t = TR.from_run(TJ.load_run(s), None)
    assert t["schema"] == "eval-trace/1" and len(t["spans"]) == 2 and [e["result"] for e in t["events"]] == ["failed"]
    ws = [x for x in _model(tmp_path)["spans"] if x["tool"] == "WebSearch"]
    assert len(ws) == 1 and not any(e["span"] == ws[0]["id"] for e in _model(tmp_path)["events"])


def _task():
    s1 = S.quote_offset(ITEM, Q1)
    s2 = S.quote_offset(ITEM, Q2)
    return {"task": "t1", "evidence": EV,
            "must_create": [{"id": "m1", "span": [s1, s1 + len(Q1)], "quote": Q1}],
            "must_not_create": [{"id": "n1", "span": [s2, s2 + len(Q2)], "quote": Q2, "why": "brainstorm"}]}


def _grade():
    row = lambda i, t: {"id": i, "type": t, "result": "fail"}
    return {"task": "t1", "pass": False, "checks": [row("m1", "must_create"), row("m1:dup", "no_duplicate"),
                                                    row("n1", "must_not_create"), row("processed", "outcome")]}


def test_link_gives_each_check_the_events_that_touched_its_span(tmp_path):
    t = _model(tmp_path)
    g = _grade()
    before = copy.deepcopy(g)
    out = TR.link(g, _task(), t)
    assert g == before and out is not g
    by = {c["id"]: c["events"] for c in out["checks"]}
    types = lambda ids: [next(e["type"] for e in t["events"] if e["id"] == i) for i in ids]
    first = [e["id"] for e in t["events"] if e["type"] == "propose"][0]
    second = [e["id"] for e in t["events"] if e["type"] == "propose"][1]
    assert first in by["m1"] and second not in by["m1"]
    assert types(by["m1"]) == ["read", "propose", "apply", "gate", "leftovers", "done"]
    assert second in by["n1"] and first not in by["n1"]
    assert types(by["processed"]) == ["gate", "done"]
    assert by["m1:dup"] == by["m1"]


def test_why_returns_only_the_linked_events_and_story_the_meeting_in_words(tmp_path):
    t = _model(tmp_path)
    g = TR.link(_grade(), _task(), t)
    w = TR.why(t, g, "n1")
    assert [e["type"] for e in w] == ["read", "propose", "apply", "gate", "leftovers", "done"]
    assert w[1]["quote"] == Q2 and w[2]["result"] == "rejected" and w[2]["why"] == "quote too short"
    assert TR.why(t, g, "nope") == []
    st = TR.story(t, EV)
    assert [r["step"] for r in st][:3] == ["read lines 1-3", "propose create", "propose create"]
    assert all({"at", "agent", "step", "detail"} <= set(r) for r in st)
    assert any("quote too short" in r["detail"] for r in st) and any("coverage" in r["detail"] for r in st)
    assert TR.story(t, "other") == []


def test_metrics_from_the_model_equal_trial_metrics(tmp_path):
    s = _session(tmp_path)
    t = TR.from_run(TJ.load_run(s), None)
    m = TR.metrics(t)
    assert m == B.trial_metrics(s)
    assert m["errors"]["refusals"] == 1 and m["cost_usd"] == 1.5
    assert m["errors"]["acks_without_reread"] == 1   # the ack followed a refusal with no re-read


def test_perfetto_export_matches_the_old_call_timeline(tmp_path):
    run = TJ.load_run(_session(tmp_path))
    old_bars = [{"name": TJ.step_words(c, with_meeting=False), "ts": TJ.micros(c.ts), "meeting": TJ.meeting_of(c)}
                for c in run.calls if c.ts]
    p = TR.perfetto(TR.from_run(run, None))
    assert p == TJ.to_trace(run)
    bars = [e for e in p["traceEvents"] if e["ph"] == "X"]
    assert [{"name": b["name"], "ts": b["ts"], "meeting": b["args"]["meeting"]} for b in bars] == old_bars
    assert {e["args"]["name"] for e in p["traceEvents"] if e["ph"] == "M"} == {"main", "Extraction batch 1"}


def test_the_why_cli_prints_the_linked_events(tmp_path, capsys):
    t = _model(tmp_path)
    g = TR.link(_grade(), _task(), t)
    (tmp_path / "trace.json").write_text(json.dumps(t))
    (tmp_path / "grade.json").write_text(json.dumps(g))
    assert B.main(["why", "--trial", str(tmp_path), "--check", "m1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [e["type"] for e in out] == ["read", "propose", "apply", "gate", "leftovers", "done"]


def _items():
    """Two meetings and a re-synced copy of the first."""
    other = "2026-03-03_meetings_ee00ee00"
    copy = "2026-03-02_meetings_cc11cc11"
    return {EV: ITEM, copy: dict(ITEM, id=copy, duplicate_of=EV),
            other: dict(ITEM, id=other, text="Zed will write the invoice today. Nothing else.")}, copy, other


def _two_calls(tmp_path, evid, quote):
    s = tmp_path / "d.jsonl"
    path = f"/proj/.taska/proposals/{evid}.json"
    _w(s, [_a("", "w1", "Write", {"file_path": path, "content": json.dumps([{"op": "create", "quote": quote}])}, "2026-03-02T07:01:02Z"),
           _r("w1", "ok", "2026-03-02T07:01:03Z"),
           _a("", "b1", "Bash", {"command": f"{TASKA} apply --evidence {evid} --proposals {path}"}, "2026-03-02T07:01:04Z"),
           _r("b1", json.dumps({"events": 1, "rejected": [], "status": "ok"}), "2026-03-02T07:01:05Z")])
    return s


def test_events_on_a_resynced_copy_are_placed_at_the_original_meeting(tmp_path):
    items, copy, _ = _items()
    t = TR.from_run(TJ.load_run(_two_calls(tmp_path, copy, Q1)), items)
    assert {e["evidence"] for e in t["events"]} == {EV}
    assert all(e.get("copy") == copy for e in t["events"])
    g = TR.link(_grade(), _task(), t)
    m1 = next(c for c in g["checks"] if c["id"] == "m1")["events"]
    assert [e["type"] for e in t["events"] if e["id"] in m1] == ["propose", "apply"]
    assert [r["step"] for r in TR.story(t, EV)] == ["propose create", "apply applied"]


def test_link_keeps_the_meetings_apart(tmp_path):
    items, _, other = _items()
    t = TR.from_run(TJ.load_run(_two_calls(tmp_path, other, "write the invoice today")), items)
    g = TR.link(_grade(), _task(), t)
    assert all(c["events"] == [] for c in g["checks"])
    assert TR.story(t, EV) == [] and len(TR.story(t, other)) == 2


def _apply_session(tmp_path, answer, err=False, write=True):
    s = tmp_path / "ap.jsonl"
    path = f"/proj/.taska/proposals/{EV}.json"
    recs = []
    if write:
        recs += [_a("", "w1", "Write", {"file_path": path, "content": json.dumps([{"op": "create", "quote": Q1}])}, "2026-03-02T07:01:02Z"),
                 _r("w1", "ok", "2026-03-02T07:01:03Z")]
    recs += [_a("", "b1", "Bash", {"command": f"{TASKA} apply --evidence {EV} --proposals {path}"}, "2026-03-02T07:01:04Z"),
             _r("b1", answer, "2026-03-02T07:01:05Z", err=err)]
    _w(s, recs)
    return s


def test_a_failed_or_unparseable_apply_is_one_failed_event_with_its_reason(tmp_path):
    for answer, err, why in [('Exit code 1\n{"status": "error", "error": "proposals: no such file"}', True, "proposals: no such file"),
                             ("Traceback boom", True, "Traceback boom"),
                             ('{"status": "ok"}', False, None)]:
        ev = [e for e in TR.from_run(TJ.load_run(_apply_session(tmp_path, answer, err)), None)["events"] if e["type"] == "apply"]
        assert [(e["result"], e["index"]) for e in ev] == [("failed", 0)]
        assert why is None or why in ev[0]["why"]


def test_an_apply_without_a_seen_ops_file_is_counted_from_its_answer(tmp_path):
    answer = json.dumps({"events": 2, "rejected": [{"i": 1, "why": "bad op"}], "status": "ok"})
    ev = [e for e in TR.from_run(TJ.load_run(_apply_session(tmp_path, answer, write=False)), None)["events"] if e["type"] == "apply"]
    assert [(e["result"], e["why"], e["quote"]) for e in ev] == [("applied", None, None), ("rejected", "bad op", None), ("applied", None, None)]   # 2 applied + 1 rejected = 3 ops


def test_an_errored_read_is_a_span_and_no_event(tmp_path):
    s = tmp_path / "er.jsonl"
    _w(s, [_a("", "r1", "Read", {"file_path": f"/proj/.taska/evidence/{EV}.md"}, "2026-03-02T07:01:00Z"),
           _r("r1", "File does not exist", "2026-03-02T07:01:01Z", err=True)])
    t = TR.from_run(TJ.load_run(s), None)
    assert t["events"] == [] and t["spans"][0]["error"] is True


def test_acks_without_reread_has_one_derivation(tmp_path):
    evd = tmp_path / "evidence"
    evd.mkdir()
    (evd / f"{EV}.md").write_text("a\nb\nc\n")
    for ack, want in ((True, 1), (False, 0)):
        d = tmp_path / str(ack)
        d.mkdir()
        run = TJ.load_run(_session(d, ack=ack))
        assert TJ.summary(run, TJ.evidence_traces(run, evd)).acks_without_reread == want
        assert TR.metrics(TR.from_run(run, None))["errors"]["acks_without_reread"] == want


def test_a_refused_done_without_evidence_is_still_a_refusal(tmp_path):
    s = tmp_path / "nr.jsonl"
    _w(s, [_a("", "b1", "Bash", {"command": f"{TASKA} done --note x"}, "2026-03-02T07:01:00Z"),
           _r("b1", '{"status": "coverage"}', "2026-03-02T07:01:01Z"),
           _a("", "b2", "Bash", {"command": "x"}, "2026-03-02T07:01:02Z"),
           _r("b2", "This requires approval", "2026-03-02T07:01:03Z", err=True)])
    run = TJ.load_run(s)
    e = TR.metrics(TR.from_run(run, None))["errors"]
    assert e["refusals"] == len(TJ.refused_dones(run)) == 1 and e["blocked"] == TJ.blocked_commands(run) == 1


def test_a_check_is_dated_by_the_earliest_applied_event_over_its_span_else_the_first_linked_event(tmp_path):
    t = _model(tmp_path)
    out = TR.link(_grade(), _task(), t)
    at = {c["id"]: c.get("at") for c in out["checks"]}
    applied = next(e for e in t["events"] if e["type"] == "apply" and e["result"] == "applied")
    first = lambda cid: next(e for e in t["events"] if e["id"] == next(c for c in out["checks"] if c["id"] == cid)["events"][0])
    assert at["m1"] == applied["at"] == "2026-03-02T07:01:04Z" and at["m1:dup"] == at["m1"]
    assert at["n1"] == first("n1")["at"] == "2026-03-02T07:01:00Z"   # only a rejected apply touched it
    assert at["processed"] == first("processed")["at"]


def test_event_words_are_one_view_shared_by_story_and_the_transcript_page(tmp_path):
    import bankview as BV
    t = _model(tmp_path)
    page = BV.transcript_page(t, "x")
    import html
    for r in TR.story(t, EV):
        assert html.escape(r["step"], quote=True) in page and html.escape(r["detail"], quote=True) in page
    for e in t["events"]:
        assert {"at", "agent", "step", "detail"} == set(TR.event_words(e, {}))


def test_a_string_quote_stays_whole_in_the_words():
    w = TR.event_words({"type": "gate", "status": "coverage", "quote": "we ship friday", "agent": "a0"}, {"a0": "main"})
    assert "we ship friday" in w["detail"] and "w; e" not in w["detail"] and w["agent"] == "main"
    w = TR.event_words({"type": "gate", "status": "coverage", "quote": ["one", "two"]}, {})
    assert "one; two" in w["detail"]


def _mixed(tmp_path):
    """Every kind of errored or refused call, once or twice, with invented commands."""
    t = "2026-03-02T07:%02d:00Z"
    rows = [
        ("Bash", {"command": f"{TASKA} done --evidence {EV}"}, 'Exit code 1\n{"status": "coverage", "empty_parts": [3]}', True),
        ("Bash", {"command": f"{TASKA} done --evidence {EV}"}, '{"status": "ack_unquoted"}', False),
        ("Bash", {"command": f"{TASKA} done --evidence {EV}"}, '{"status": "ok"}', False),
        ("Bash", {"command": f"{TASKA} apply --evidence {EV}"}, 'Exit code 1\n{"status": "error", "error": "bad file"}', True),
        ("Bash", {"command": f"{TASKA} context --node t1"}, 'Exit code 1\n{"status": "needs_verdict"}', True),
        ("Bash", {"command": f"{TASKA} context"}, "Exit code 1\nTraceback (most recent call last)", True),
        ("Bash", {"command": "cd /p && ls | head"}, "This Bash command contains multiple operations", True),
        ("Bash", {"command": "rm x"}, "This command requires approval", True),
        ("Bash", {"command": "python3 /p/taska.py context"}, 'Exit code 1\n{"status": "needs_verdict"}', True),
        ("Bash", {"command": f"{TASKA} context --node t2"}, 'Exit code 1\n{"status": "ok"}', True),
        ("Bash", {"command": "grep -E 'a{999}' f"}, "grep: maximum repetition exceeds 255", True),
        ("Bash", {"command": "ls"}, "fine", False),
        ("Read", {"file_path": "/proj/big.md"}, "File content exceeds maximum allowed tokens", True),
        ("Edit", {"file_path": "/proj/a.md"}, "No changes to make", True),
        ("Write", {"file_path": "/proj/b.md"}, "InputValidationError", True),
        ("Read", {"file_path": "/proj/ok.md"}, "1\tx", False),
    ]
    recs = []
    for i, (name, inp, out, err) in enumerate(rows):
        recs += [_a("", f"u{i}", name, inp, t % (i * 2)), _r(f"u{i}", out, t % (i * 2 + 1), err=err)]
    s = tmp_path / "mixed.jsonl"
    _w(s, recs)
    return TJ.load_run(s)


def test_every_errored_or_refused_call_lands_in_exactly_one_count(tmp_path):
    run = _mixed(tmp_path)
    trace = TR.from_run(run, None)
    e = TR.metrics(trace)["error_counts"]
    assert (e["gate_refusals"], e["improvised"], e["slips"]) == (4, 5, 4)
    assert trace["error_counts"] == {"gate_refusals": 4, "improvised": 5, "slips": 4}
    taska_refused_ok = sum(1 for c in run.calls if not c.is_error and TJ.json_answer(c.output).get("status") not in (None, "ok", "error")
                           and TR.is_taska(c))
    assert sum(e.values()) == sum(c.is_error for c in run.calls) + taska_refused_ok


def test_the_old_error_keys_are_unchanged_by_the_new_counts(tmp_path):
    m = TR.metrics(TR.from_run(_mixed(tmp_path), None))
    e = m["errors"]
    assert set(e) == {"tool", "refusals", "blocked", "acks_without_reread"} and "error_counts" in m
    assert {"tool", "refusals", "blocked", "acks_without_reread"} <= set(e)
    assert e["blocked"] == 2 and e["refusals"] == 2
