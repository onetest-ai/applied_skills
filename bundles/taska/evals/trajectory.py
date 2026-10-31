#!/usr/bin/env python3
"""What a headless taska run actually did, read from its Claude Code session file and the subagent files beside it.

  trajectory.py SESSION.jsonl [--evidence-dir DIR] [--out trajectory.json] [--html trajectory.html]

The run's final message is a summary the model wrote; the session file is the record of every tool call and answer.
Per agent: model, turns, tools, errors, tokens. Per evidence item (with --evidence-dir): which agent read its
transcript and how many of its lines, how many proposals were written, what `apply` answered, how often `done`
refused and whether an `--ack` followed a re-read or only the refusal. The session file format is internal to
Claude Code and changes between versions: unknown record types are ignored and a missing field never raises.
"""
import argparse
import html
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ToolCall:
    agent_id: str
    tool_use_id: str
    name: str
    ts: str
    input: dict[str, str]
    output: str = ""
    is_error: bool = False


@dataclass
class AgentTrace:
    agent_id: str
    model: str = ""
    turns: int = 0
    first_ts: str = ""
    last_ts: str = ""
    tool_counts: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class Run:
    session_id: str
    started: str = ""
    ended: str = ""
    cost_usd: float = 0.0
    complete: bool = False
    agents: list[AgentTrace] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)


@dataclass
class EvidenceTrace:
    evidence: str
    reader: str = ""
    reads: list[tuple[int, int]] = field(default_factory=list)
    lines_total: int = 0
    lines_seen: int = 0
    ops_written: int = 0
    applied: int = 0
    rejected: int = 0
    rejections: list[str] = field(default_factory=list)
    refusals: int = 0
    acks: list[str] = field(default_factory=list)
    reread_before_ack: bool = True
    leftovers_calls: int = 0
    applied_after_leftovers: bool = True


@dataclass
class Summary:
    session_id: str
    complete: bool
    cost_usd: float
    duration_s: int
    agents: int
    turns: int
    tool_calls: int
    tool_errors: int
    evidence: int
    evidence_fully_read: int
    refusals: int
    acks_without_reread: int
    leftovers_undecided: int


def _records(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


def _agent_file(path: Path, agent_id: str, run: Run) -> None:
    trace = AgentTrace(agent_id=agent_id)
    pending: dict[str, ToolCall] = {}
    for rec in _records(path):
        ts = str(rec.get("ts") or rec.get("timestamp") or "")
        if ts:
            run.started = min(run.started, ts) if run.started else ts
            run.ended = max(run.ended, ts)
        kind = rec.get("type")
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        if kind == "cost-state":
            run.complete = True
            run.cost_usd = float(rec.get("totalCostUSD") or 0.0)
        elif kind == "assistant":
            trace.turns += 1
            trace.model = trace.model or str(msg.get("model") or "")
            trace.first_ts = trace.first_ts or ts
            trace.last_ts = ts or trace.last_ts
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            trace.output_tokens += int(usage.get("output_tokens") or 0)
            trace.cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    raw = block.get("input") if isinstance(block.get("input"), dict) else {}
                    call = ToolCall(agent_id, str(block.get("id") or ""), name, ts,
                                    {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in raw.items()})
                    trace.tool_counts[name] = trace.tool_counts.get(name, 0) + 1
                    pending[call.tool_use_id] = call
                    run.calls.append(call)
        elif kind == "user":
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    call = pending.pop(str(block.get("tool_use_id") or ""), None)
                    if call is None:
                        continue
                    call.output = _text(block.get("content"))
                    call.is_error = bool(block.get("is_error"))
                    trace.errors += int(call.is_error)
    run.agents.append(trace)


def load_run(session: Path) -> Run:
    session = Path(session)
    run = Run(session_id=session.stem)
    _agent_file(session, "main", run)
    for sub in sorted((session.parent / session.stem / "subagents").glob("agent-*.jsonl")):
        _agent_file(sub, sub.stem[len("agent-"):], run)
    run.calls.sort(key=lambda c: c.ts)
    return run


def _json_answer(output: str) -> dict:
    start = output.find("{")
    if start < 0:
        return {}
    try:
        answer, _ = json.JSONDecoder().raw_decode(output[start:])
    except json.JSONDecodeError:
        return {}
    return answer if isinstance(answer, dict) else {}


LINE_PREFIX = re.compile(r"^\s*(\d+)\t", re.M)


def _read_range(call: ToolCall, total: int) -> tuple[int, int]:
    """Lines the Read actually returned (from the N<tab> prefixes); the request's range only when the output has none."""
    seen = [int(x) for x in LINE_PREFIX.findall(call.output)]
    if seen:
        return (min(seen), min(max(seen), total))
    offset = int(call.input.get("offset") or 1)
    limit = call.input.get("limit")
    return (offset, min(offset + int(limit) - 1 if limit else total, total))


def _ack_of(command: str) -> str:
    m = re.search(r'--ack\s+(?:"((?:[^"\\]|\\.)*)"|\'([^\']*)\'|(\S+))', command)
    if not m:
        return ""
    return next(g for g in m.groups() if g is not None)


def _ops_in(content: str) -> int:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict) and isinstance(data.get("ops"), list):
        return len(data["ops"])
    return 0


def _seen(reads: list[tuple[int, int]], total: int) -> int:
    lines: set[int] = set()
    for a, b in reads:
        lines.update(range(max(1, a), min(total, b) + 1))
    return len(lines)


def evidence_traces(run: Run, evidence_dir: Path) -> list[EvidenceTrace]:
    evidence_dir = Path(evidence_dir)
    out = []
    for md in sorted(evidence_dir.glob("*.md")):
        eid = md.stem
        total = sum(1 for _ in open(md, encoding="utf-8", errors="ignore"))
        tr = EvidenceTrace(evidence=eid, lines_total=total)
        refused_at: dict[str, int] = {}
        first_reader = applier = ""
        last_leftovers = -1
        for i, c in enumerate(run.calls):
            if c.name == "Read" and c.input.get("file_path", "").endswith(f"/evidence/{eid}.md"):
                if c.is_error:
                    continue
                tr.reads.append(_read_range(c, total))
                first_reader = first_reader or c.agent_id
            elif c.name == "Write" and Path(c.input.get("file_path", "")).name.startswith(eid) \
                    and c.input.get("file_path", "").endswith(".json"):
                tr.ops_written += _ops_in(c.input.get("content", ""))
            elif c.name == "Bash":
                cmd = c.input.get("command", "")
                if f"--evidence {eid}" not in cmd:
                    continue
                if " leftovers " in cmd:
                    tr.leftovers_calls += 1
                    last_leftovers = i
                elif " apply " in cmd:
                    applier = applier or c.agent_id
                    if last_leftovers >= 0:
                        tr.applied_after_leftovers = True
                    answer = _json_answer(c.output)
                    tr.applied += int(answer.get("events") or 0)
                    rejected = answer.get("rejected") if isinstance(answer.get("rejected"), list) else []
                    tr.rejected += len(rejected)
                    tr.rejections += [str(r.get("why") or "") for r in rejected if isinstance(r, dict)]
                elif " done " in cmd:
                    answer = _json_answer(c.output)
                    if answer.get("status") not in ("ok", None):
                        tr.refusals += 1
                        refused_at[c.agent_id] = i
                    ack = _ack_of(cmd)
                    if ack:
                        tr.acks.append(ack)
                        since = refused_at.get(c.agent_id)
                        reread = since is not None and any(
                            x.name == "Read" and x.agent_id == c.agent_id
                            and x.input.get("file_path", "").endswith(f"/evidence/{eid}.md")
                            for x in run.calls[since + 1:i])
                        tr.reread_before_ack = tr.reread_before_ack and reread
        tr.reader = applier or first_reader
        tr.lines_seen = _seen(tr.reads, total)
        if tr.leftovers_calls and not any(x.name == "Bash" and " apply " in x.input.get("command", "")
                                          and f"--evidence {eid}" in x.input.get("command", "")
                                          for x in run.calls[last_leftovers + 1:]):
            tr.applied_after_leftovers = False
        out.append(tr)
    return out


def _seconds(a: str, b: str) -> int:
    import datetime as dt
    try:
        return int((dt.datetime.fromisoformat(b.replace("Z", "+00:00"))
                    - dt.datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds())
    except ValueError:
        return 0


def summary(run: Run, traces: list[EvidenceTrace]) -> Summary:
    return Summary(
        session_id=run.session_id, complete=run.complete, cost_usd=round(run.cost_usd, 2),
        duration_s=_seconds(run.started, run.ended) if run.started and run.ended else 0,
        agents=len(run.agents), turns=sum(a.turns for a in run.agents), tool_calls=len(run.calls),
        tool_errors=sum(a.errors for a in run.agents), evidence=len(traces),
        evidence_fully_read=sum(1 for t in traces if t.lines_total and t.lines_seen >= t.lines_total - 1),
        refusals=sum(t.refusals for t in traces),
        acks_without_reread=sum(1 for t in traces if t.acks and not t.reread_before_ack),
        leftovers_undecided=sum(1 for t in traces if t.leftovers_calls and not t.applied_after_leftovers))


def to_json(run: Run, traces: list[EvidenceTrace]) -> str:
    return json.dumps({"summary": asdict(summary(run, traces)), "agents": [asdict(a) for a in run.agents],
                       "evidence": [asdict(t) for t in traces]}, indent=1, ensure_ascii=False)


def render_html(run: Run, traces: list[EvidenceTrace]) -> str:
    s = summary(run, traces)
    e = html.escape
    rows = ["<tr><th>agent</th><th>model</th><th>turns</th><th>tools</th><th>errors</th><th>output tokens</th></tr>"]
    for a in run.agents:
        tools = ", ".join(f"{k} {v}" for k, v in sorted(a.tool_counts.items()))
        rows.append(f"<tr><td>{e(a.agent_id)}</td><td>{e(a.model)}</td><td>{a.turns}</td><td>{e(tools)}</td>"
                    f"<td>{a.errors}</td><td>{a.output_tokens}</td></tr>")
    ev = ["<tr><th>evidence</th><th>reader</th><th>lines read</th><th>proposed</th><th>applied</th><th>rejected</th>"
          "<th>done refused</th><th>acks</th><th>re-read before ack</th><th>leftovers → apply</th></tr>"]
    for t in traces:
        if not (t.reads or t.applied or t.refusals or t.acks):
            continue
        ev.append(f"<tr><td>{e(t.evidence)}</td><td>{e(t.reader)}</td><td>{t.lines_seen}/{t.lines_total}</td>"
                  f"<td>{t.ops_written}</td><td>{t.applied}</td><td>{t.rejected}</td><td>{t.refusals}</td>"
                  f"<td>{e('; '.join(t.acks))}</td><td>{'yes' if t.reread_before_ack else 'no'}</td>"
                  f"<td>{'—' if not t.leftovers_calls else ('yes' if t.applied_after_leftovers else 'no')}</td></tr>")
    return ("<!doctype html><meta charset='utf-8'><title>Trajectory</title>"
            "<style>body{font:14px system-ui;margin:24px}table{border-collapse:collapse;margin:12px 0}"
            "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}</style>"
            f"<h1>Run {e(run.session_id)}</h1><p>{'complete' if s.complete else 'incomplete'} · {s.duration_s}s · "
            f"${s.cost_usd} · {s.agents} agents · {s.turns} turns · {s.tool_calls} tool calls · {s.tool_errors} errors · "
            f"{s.evidence_fully_read}/{s.evidence} transcripts read in full · {s.refusals} done refusals · "
            f"{s.acks_without_reread} acknowledged without a re-read · {s.leftovers_undecided} leftovers lists with no apply after</p>"
            f"<table>{''.join(rows)}</table><table>{''.join(ev)}</table>")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session")
    ap.add_argument("--evidence-dir")
    ap.add_argument("--out")
    ap.add_argument("--html")
    a = ap.parse_args(argv)
    run = load_run(Path(a.session))
    traces = evidence_traces(run, Path(a.evidence_dir)) if a.evidence_dir else []
    text = to_json(run, traces)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    if a.html:
        Path(a.html).write_text(render_html(run, traces), encoding="utf-8")
    return 0 if run.complete else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
