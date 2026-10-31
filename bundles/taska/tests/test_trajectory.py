"""The trajectory reader turns a headless run's session file (and its subagents) into facts a person can check:
who read which transcript and how much of it, what was proposed, what `apply` and `done` answered, and whether a
refusal was followed by a re-read or only by an acknowledgement."""
import json
import sys
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import trajectory as T  # noqa: E402

EV = "2026-03-02_meetings_ab12cd34"
TASKA = "/proj/taska"


def _assistant(agent, uid, name, inp, ts, model="claude-sonnet-5-5"):
    rec = {"type": "assistant", "timestamp": ts, "uuid": uid + "-a",
           "message": {"model": model, "role": "assistant", "stop_reason": "tool_use",
                       "content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}],
                       "usage": {"input_tokens": 1, "output_tokens": 10, "cache_read_input_tokens": 100}}}
    if agent:
        rec["agentId"] = agent
    return rec


def _lines(a, b):
    return "\n".join(f"{i}\tline {i}" for i in range(a, b + 1))


def _result(agent, uid, output, ts, is_error=False):
    block = {"type": "tool_result", "tool_use_id": uid, "content": output}
    if is_error:
        block["is_error"] = True
    rec = {"type": "user", "timestamp": ts, "uuid": uid + "-u", "message": {"role": "user", "content": [block]}}
    if agent:
        rec["agentId"] = agent
    return rec


def _session(tmp_path, with_cost=True, reread=True):
    sess = tmp_path / "s1.jsonl"
    sub = tmp_path / "s1" / "subagents"
    sub.mkdir(parents=True)
    ev_md = f"/proj/.taska/evidence/{EV}.md"
    main = [
        {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-03-02T07:00:00Z", "sessionId": "s1"},
        {"type": "attachment", "timestamp": "2026-03-02T07:00:01Z", "attachment": {"type": "environment"}},
        _assistant("", "t1", "Agent", {"prompt": "Follow EXTRACT.md", "model": "sonnet"}, "2026-03-02T07:00:02Z",
                   model="claude-fable-5-1"),
        _result("", "t1", [{"type": "text", "text": "done"}], "2026-03-02T07:05:00Z"),
    ]
    if with_cost:
        main.append({"type": "cost-state", "sessionId": "s1", "totalCostUSD": 1.25, "totalDuration": 300000})
    sess.write_text("\n".join(json.dumps(r) for r in main) + "\n")
    a = "a1"
    reader = [
        _assistant(a, "r1", "Read", {"file_path": ev_md, "limit": 60}, "2026-03-02T07:01:00Z"),
        _result(a, "r1", _lines(1, 60), "2026-03-02T07:01:01Z"),
        _assistant(a, "r2", "Read", {"file_path": ev_md, "offset": 61, "limit": 60}, "2026-03-02T07:01:02Z"),
        _result(a, "r2", _lines(61, 120), "2026-03-02T07:01:03Z"),
        _assistant(a, "w1", "Write", {"file_path": f"/proj/.taska/proposals/{EV}.json",
                                      "content": json.dumps([{"op": "note"}, {"op": "status"}, {"op": "create"}])},
                   "2026-03-02T07:01:04Z"),
        _result(a, "w1", "ok", "2026-03-02T07:01:05Z"),
        _assistant(a, "b1", "Bash", {"command": f"{TASKA} apply --evidence {EV} --proposals /proj/.taska/proposals/{EV}.json"},
                   "2026-03-02T07:01:06Z"),
        _result(a, "b1", json.dumps({"events": 2, "rejected": [{"i": 2, "why": "quote not verbatim"}], "status": "ok"}),
                "2026-03-02T07:01:07Z"),
        _assistant(a, "b2", "Bash", {"command": f"{TASKA} done --evidence {EV}"}, "2026-03-02T07:01:08Z"),
        _result(a, "b2", "Exit code 1\n" + json.dumps({"status": "coverage", "empty_parts": [3]}), "2026-03-02T07:01:09Z",
                is_error=True),
    ]
    if reread:
        reader += [_assistant(a, "r3", "Read", {"file_path": ev_md, "offset": 100, "limit": 40}, "2026-03-02T07:01:10Z"),
                   _result(a, "r3", _lines(100, 139), "2026-03-02T07:01:11Z")]
    reader += [
        _assistant(a, "b3", "Bash", {"command": f'{TASKA} done --evidence {EV} --note "n" --ack "part 3 is goodbyes"'},
                   "2026-03-02T07:01:12Z"),
        _result(a, "b3", json.dumps({"status": "ok", "processed": EV}), "2026-03-02T07:01:13Z"),
    ]
    (sub / f"agent-{a}.jsonl").write_text("\n".join(json.dumps(r) for r in reader) + "\n")
    later = [_assistant("a0", "d1", "Read", {"file_path": ev_md}, "2026-03-02T07:02:00Z"),
             _result("a0", "d1", _lines(1, 140), "2026-03-02T07:02:01Z")]
    (sub / "agent-a0.jsonl").write_text("\n".join(json.dumps(r) for r in later) + "\n")
    evd = tmp_path / "evidence"
    evd.mkdir()
    (evd / f"{EV}.md").write_text("\n".join(f"[00:{i:02d}:00] Nora Quill: line {i}" for i in range(140)) + "\n")
    return sess, evd


def test_reads_agents_and_tool_calls_from_main_and_subagent_files(tmp_path):
    sess, _ = _session(tmp_path)
    run = T.load_run(sess)
    assert run.session_id == "s1" and run.complete and run.cost_usd == 1.25
    assert [a.agent_id for a in run.agents] == ["main", "a0", "a1"]
    main, _, a1 = run.agents
    assert main.model == "claude-fable-5-1" and main.tool_counts == {"Agent": 1}
    assert a1.model == "claude-sonnet-5-5" and a1.turns == 7 and a1.tool_counts["Read"] == 3 and a1.errors == 1
    assert a1.output_tokens == 70 and a1.cache_read_tokens == 700
    assert run.started == "2026-03-02T07:00:00Z" and run.ended == "2026-03-02T07:05:00Z"


def test_tool_result_blocks_as_list_are_joined(tmp_path):
    sess, _ = _session(tmp_path)
    run = T.load_run(sess)
    agent_call = next(c for c in run.calls if c.name == "Agent")
    assert agent_call.output == "done" and agent_call.input["model"] == "sonnet"


def test_a_run_without_cost_state_is_incomplete(tmp_path):
    sess, _ = _session(tmp_path, with_cost=False)
    run = T.load_run(sess)
    assert run.complete is False and run.cost_usd == 0.0


def test_evidence_trace_counts_reads_ops_apply_and_refusals(tmp_path):
    sess, evd = _session(tmp_path)
    run = T.load_run(sess)
    tr = T.evidence_traces(run, evd)
    assert [t.evidence for t in tr] == [EV]
    t = tr[0]
    assert t.reader == "a1" and t.reads == [(1, 60), (61, 120), (100, 139), (1, 140)]
    assert t.lines_total == 140 and t.lines_seen == 140
    assert t.ops_written == 3 and t.applied == 2 and t.rejected == 1 and t.rejections == ["quote not verbatim"]
    assert t.refusals == 1 and t.acks == ["part 3 is goodbyes"] and t.reread_before_ack is True


def test_calls_are_ordered_by_time_across_files(tmp_path):
    sess, _ = _session(tmp_path)
    run = T.load_run(sess)
    assert [c.ts for c in run.calls] == sorted(c.ts for c in run.calls)
    assert run.calls[-1].agent_id == "a0"


def test_an_ack_without_a_reread_is_flagged(tmp_path):
    sess, evd = _session(tmp_path, reread=False)
    t = T.evidence_traces(T.load_run(sess), evd)[0]
    assert t.lines_seen == 140 and t.reads[-1] == (1, 140) and t.reread_before_ack is False
    s = T.summary(T.load_run(sess), [t])
    assert s.acks_without_reread == 1 and s.evidence == 1 and s.evidence_fully_read == 1


def test_html_lists_every_evidence(tmp_path):
    sess, evd = _session(tmp_path)
    run = T.load_run(sess)
    page = T.render_html(run, T.evidence_traces(run, evd))
    assert EV in page and "claude-sonnet-5-5" in page and "part 3 is goodbyes" in page


def test_errored_or_capped_reads_are_not_counted_as_read(tmp_path):
    # review: a refused Read ("File content exceeds maximum allowed tokens") and a whole-file Read cut at the harness
    # cap both counted as the whole file
    sess, evd = _session(tmp_path)
    sub = tmp_path / "s1" / "subagents" / "agent-a9.jsonl"
    ev_md = f"/proj/.taska/evidence/{EV}.md"
    recs = [_assistant("a9", "x1", "Read", {"file_path": ev_md}, "2026-03-02T07:03:00Z"),
            _result("a9", "x1", "File content exceeds maximum allowed tokens", "2026-03-02T07:03:01Z", is_error=True),
            _assistant("a9", "x2", "Read", {"file_path": ev_md}, "2026-03-02T07:03:02Z"),
            _result("a9", "x2", _lines(1, 30), "2026-03-02T07:03:03Z")]
    sub.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    t = T.evidence_traces(T.load_run(sess), evd)[0]
    assert t.reads[-1] == (1, 30) and len(t.reads) == 5


def test_leftovers_then_done_without_an_apply_is_flagged(tmp_path):
    sess, evd = _session(tmp_path)
    sub = tmp_path / "s1" / "subagents" / "agent-a1.jsonl"
    recs = [_assistant("a1", "l1", "Bash", {"command": f"{TASKA} leftovers --evidence {EV}"}, "2026-03-02T07:01:14Z"),
            _result("a1", "l1", json.dumps({"count": 4, "token": "abc123abc123", "status": "ok"}), "2026-03-02T07:01:15Z"),
            _assistant("a1", "b4", "Bash", {"command": f"{TASKA} done --evidence {EV} --leftovers abc123abc123"}, "2026-03-02T07:01:16Z"),
            _result("a1", "b4", json.dumps({"status": "ok"}), "2026-03-02T07:01:17Z")]
    with open(sub, "a") as f:
        f.write("\n".join(json.dumps(r) for r in recs) + "\n")
    run = T.load_run(sess)
    t = T.evidence_traces(run, evd)[0]
    assert t.leftovers_calls == 1 and t.applied_after_leftovers is False
    assert T.summary(run, [t]).leftovers_undecided == 1


def test_json_answer_tolerates_text_after_the_json():
    assert T._json_answer('{"status": "coverage"}\n\nExit code 1')["status"] == "coverage"
