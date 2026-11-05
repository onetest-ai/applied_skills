"""bank.trial_metrics / keep_transcript: what one trial cost, read from its session file and subagents. Invented data."""
import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent.parent / "evals"
sys.path.insert(0, str(EVALS))
import bank as B  # noqa: E402


def _asst(ts, inp, out, cr, cw, uid=None, tool="Bash"):
    content = [{"type": "tool_use", "id": uid, "name": tool, "input": {"command": "ls"}}] if uid else []
    return {"type": "assistant", "timestamp": ts, "message": {"model": "m", "content": content, "usage": {
        "input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}


def _res(ts, uid, text, err=False):
    b = {"type": "tool_result", "tool_use_id": uid, "content": text}
    if err:
        b["is_error"] = True
    return {"type": "user", "timestamp": ts, "message": {"content": [b]}}


def _write(path, recs):
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def _session(tmp_path):
    s = tmp_path / "sess1.jsonl"
    _write(s, [_asst("2026-03-02T07:00:00Z", 10, 20, 100, 5, uid="t1"),
               _res("2026-03-02T07:00:30Z", "t1", "boom", err=True),
               _asst("2026-03-02T07:01:00Z", 1, 2, 3, 4),
               {"type": "cost-state", "totalCostUSD": 0.74}])
    sub = tmp_path / "sess1" / "subagents"
    sub.mkdir(parents=True)
    _write(sub / "agent-a1.jsonl", [_asst("2026-03-02T07:06:52Z", 100, 200, 1000, 50, uid="s1", tool="Read"),
                                  _res("2026-03-02T07:06:52Z", "s1", "File content exceeds maximum allowed tokens", err=True)])
    (sub / "agent-a1.meta.json").write_text('{"description": "batch 1"}')
    return s


def test_metrics_sum_the_session_and_its_subagents(tmp_path):
    m = B.trial_metrics(_session(tmp_path))
    assert m == {"latency_s": 412, "tokens": {"input": 111, "output": 222, "cache_read": 1103, "cache_write": 59},
                 "cost_usd": 0.74, "errors": {"tool": 2, "refusals": 0, "blocked": 0, "acks_without_reread": 0},
                 "error_counts": {"improvised": 1, "gate_refusals": 0, "slips": 1}}


def test_a_blocked_command_is_counted(tmp_path):
    s = tmp_path / "b.jsonl"
    _write(s, [_asst("2026-03-02T07:00:00Z", 1, 1, 0, 0, uid="t1"), _res("2026-03-02T07:00:01Z", "t1", "This requires approval", err=True)])
    e = B.trial_metrics(s)["errors"]
    assert e["tool"] == 1 and e["blocked"] == 1


def test_a_missing_or_empty_session_gives_zeros(tmp_path):
    s = tmp_path / "e.jsonl"
    s.write_text("not json\n")
    m = B.trial_metrics(s)
    assert m["latency_s"] == 0 and m["cost_usd"] == 0.0 and m["tokens"] == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def test_keep_transcript_copies_the_file_and_its_subagents(tmp_path):
    s = _session(tmp_path)
    out = tmp_path / "trial"
    dest = B.keep_transcript(s, out)
    assert dest == out / "transcript"
    assert (dest / "sess1.jsonl").read_text() == s.read_text()
    assert (dest / "sess1" / "subagents" / "agent-a1.jsonl").exists()
    assert (dest / "sess1" / "subagents" / "agent-a1.meta.json").exists()


def test_keep_transcript_without_subagents(tmp_path):
    s = tmp_path / "solo.jsonl"
    s.write_text("{}\n")
    assert (B.keep_transcript(s, tmp_path / "o") / "solo.jsonl").exists()


def test_a_subagent_cost_state_does_not_replace_the_session_total(tmp_path):
    s = _session(tmp_path)
    sub = tmp_path / "sess1" / "subagents" / "agent-a1.jsonl"
    sub.write_text(sub.read_text() + json.dumps({"type": "cost-state", "totalCostUSD": 0.05}) + "\n")
    assert B.trial_metrics(s)["cost_usd"] == 0.74


def test_a_refused_done_without_is_error_is_counted(tmp_path):
    s = tmp_path / "r.jsonl"
    _write(s, [_asst("2026-03-02T07:00:00Z", 1, 1, 0, 0, uid="t1"),
               _res("2026-03-02T07:00:01Z", "t1", '{"status": "coverage"}')])
    s.write_text(s.read_text().replace('"command": "ls"', '"command": "taska done --evidence x"'))
    e = B.trial_metrics(s)["errors"]
    assert e["refusals"] == 1 and e["tool"] == 0
