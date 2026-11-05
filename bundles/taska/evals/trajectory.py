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
    end: str = ""   # when its result came back: the call's duration is end - ts


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
    input_tokens: int = 0
    cache_write_tokens: int = 0
    description: str = ""   # what the dispatch called this subagent ("Extraction batch 1"), from its meta.json


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
            if agent_id == "main":   # a subagent's own cost-state never replaces the session's total
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
            trace.input_tokens += int(usage.get("input_tokens") or 0)
            trace.cache_write_tokens += int(usage.get("cache_creation_input_tokens") or 0)
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
                    call.end = ts
                    trace.errors += int(call.is_error)
    run.agents.append(trace)


def load_run(session: Path) -> Run:
    session = Path(session)
    run = Run(session_id=session.stem)
    _agent_file(session, "main", run)
    for sub in sorted((session.parent / session.stem / "subagents").glob("agent-*.jsonl")):
        _agent_file(sub, sub.stem[len("agent-"):], run)
        meta = sub.with_suffix(".meta.json")
        try:
            run.agents[-1].description = str(json.loads(meta.read_text(encoding="utf-8")).get("description") or "")
        except (OSError, ValueError, AttributeError):
            pass   # no meta file (older Claude Code): the agent keeps its id as its only name
    run.calls.sort(key=lambda c: c.ts)
    return run


def find_session(copy: Path, since: float, root: Path) -> Path | None:
    """The newest session file under root written since `since` whose records run in `copy` (their `cwd`): the session
    of one eval copy, never merely the newest file."""
    want = str(Path(copy).resolve())
    for f in sorted(Path(root).glob("*/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True):
        if f.stat().st_mtime < since:
            break
        with open(f, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    rec = json.loads(line) if line.startswith("{") else {}
                except json.JSONDecodeError:
                    continue
                cwd = rec.get("cwd") if isinstance(rec, dict) else None
                if cwd and str(Path(cwd).resolve()) == want:
                    return f
    return None


EVIDENCE_ID = re.compile(r"\d{4}-\d\d-\d\d_[a-z]+_[0-9a-f]{8}")


def meeting_of(c: ToolCall) -> str:
    """The evidence item a call was about: its --evidence, else the evidence file it read or wrote."""
    m = re.search(r"--evidence\s+(\S+)", c.input.get("command", "")) or EVIDENCE_ID.search(Path(c.input.get("file_path", "")).name)
    return m.group(1) if m and m.groups() else m.group(0) if m else ""


def step_words(c: ToolCall, with_meeting: bool = True) -> str:
    """One step in words: "taska done 2026-10-21_… refused (decisions)", "read <file>", "started Extraction batch 2"."""
    if c.name == "Bash":
        m = re.search(r"taska(?:\.py)?(?:\s+--project\s+\S+)?\s+(\S+)(?:.*?--evidence\s+(\S+))?", c.input.get("command", ""))
        if m:
            answer = json_answer(c.output)
            outcome = f" refused ({answer.get('status')})" if c.is_error and answer.get("status") else " failed" if c.is_error else ""
            return f"taska {m.group(1)} {(m.group(2) or '') if with_meeting else ''}".rstrip() + outcome
        return "shell: " + c.input.get("command", "")[:80]
    if c.name in ("Read", "Write", "Edit"):
        return f"{c.name.lower()} {Path(c.input.get('file_path', '')).name}"
    if c.name == "Agent":
        return f"started {c.input.get('description', 'a subagent')}"
    return c.name


def micros(ts: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1_000_000)


def to_trace(run: Run) -> dict:
    """The call timeline in Chrome's Trace Event Format (open in ui.perfetto.dev or chrome://tracing): one process per
    agent, named by its batch, one bar per tool call from its request to its result, the step in words as its name."""
    import evaltrace   # lazily: evaltrace imports this module
    return evaltrace.perfetto(evaltrace.from_run(run))


def json_answer(output: str) -> dict:
    start = output.find("{")
    if start < 0:
        return {}
    try:
        answer, _ = json.JSONDecoder().raw_decode(output[start:])
    except json.JSONDecodeError:
        return {}
    return answer if isinstance(answer, dict) else {}


_json_answer = json_answer   # the name other modules and tests already use


LINE_PREFIX = re.compile(r"^\s*(\d+)\t", re.M)


def read_range(call: ToolCall, total: int | None = None) -> tuple[int, int | None]:
    """Lines the Read actually returned (from the N<tab> prefixes); the request's range only when the output has none.
    `total` (the file's length) caps the range; without it an unbounded request ends at None."""
    seen = [int(x) for x in LINE_PREFIX.findall(call.output)]
    cap = lambda n: n if total is None else min(n, total)
    if seen:
        return (min(seen), cap(max(seen)))
    offset = int(call.input.get("offset") or 1)
    limit = call.input.get("limit")
    return (offset, cap(offset + int(limit) - 1) if limit else total)


def ack_of(command: str) -> str:
    m = re.search(r'--ack\s+(?:"((?:[^"\\]|\\.)*)"|\'([^\']*)\'|(\S+))', command)
    if not m:
        return ""
    return next(g for g in m.groups() if g is not None)


def ops_in(content: str) -> list:
    """The ops of a proposals file: a JSON list, or a dict's `ops`; empty for anything else."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("ops")
    return data if isinstance(data, list) else []


def _acked_parts(calls: list[ToolCall], i: int, eid: str) -> set[int]:
    """The parts an `--ack` at calls[i] acknowledges: the gap the newest coverage refusal on the meeting named (from any
    agent), else the parts a later refusal said lacked a quote or a re-read."""
    fallback: set[int] = set()
    for c in reversed(calls[:i + 1]):
        if not done_refused(c) or f"--evidence {eid}" not in c.input.get("command", ""):
            continue
        a = json_answer(c.output)
        gap = {p for k in ("empty_parts", "thin_parts") for p in _ints(a.get(k))}
        if gap:
            return gap
        fallback = fallback or {p for k in ("parts_without_reread", "parts_without_quote") for p in _ints(a.get(k))}
    return fallback


def _ints(value: object) -> list[int]:
    return [v for v in value if isinstance(v, int) and not isinstance(v, bool)] if isinstance(value, list) else []


def _line(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _reread_by_fresh_agent(calls: list[ToolCall], i: int, eid: str) -> bool:
    """Every part the ack at calls[i] acknowledges was re-read through `taska reread` after the last apply on the
    meeting: an ok answer with a line range, then a Read of its md whose window overlaps that range."""
    parts = _acked_parts(calls, i, eid)
    if not parts:
        return False
    last_apply = max((j for j, c in enumerate(calls[:i]) if c.name == "Bash" and " apply " in c.input.get("command", "")
                      and f"--evidence {eid}" in c.input.get("command", "")), default=-1)
    done: set[int] = set()
    for j in range(last_apply + 1, i):
        c = calls[j]
        if c.name != "Bash" or " reread " not in c.input.get("command", "") or f"--evidence {eid}" not in c.input["command"]:
            continue
        a = json_answer(c.output)
        part, first, last, md = a.get("part"), _line(a.get("first_line")), _line(a.get("last_line")), a.get("md")
        if a.get("status") != "ok" or not isinstance(part, int) or first is None or last is None or not isinstance(md, str):
            continue
        for r in calls[j + 1:i]:
            if r.name == "Read" and r.agent_id == c.agent_id and not r.is_error and r.input.get("file_path", "") == md:
                lo, hi = read_range(r)
                if lo <= last and (hi is None or hi >= first):
                    done.add(part)
                    break
    return parts <= done


def unread_acks(run: Run) -> set[str]:
    """Evidence ids where an accepted `--ack` was not preceded by a re-read of what it acknowledges: either the same agent's Read
    of the evidence file between its refusal and the ack (an ack on the refused call itself has none), or, in any agent,
    a `taska reread` of every acked part after the last apply followed by a Read of its lines."""
    refused_at: dict[tuple[str, str], int] = {}
    ok: dict[str, bool] = {}
    for i, c in enumerate(run.calls):
        if c.name != "Bash" or " done " not in c.input.get("command", ""):
            continue
        m = re.search(r"--evidence\s+(\S+)", c.input["command"])
        if not m:
            continue
        eid = m.group(1)
        if done_refused(c):
            refused_at[(eid, c.agent_id)] = i
        if ack_of(c.input["command"]) and not done_refused(c):   # a refused ack is the gate working, not an ack taska accepted
            since = refused_at.get((eid, c.agent_id))
            reread = since is not None and any(
                x.name == "Read" and x.agent_id == c.agent_id and x.input.get("file_path", "").endswith(f"/evidence/{eid}.md")
                for x in run.calls[since + 1:i])
            ok[eid] = ok.get(eid, True) and (reread or _reread_by_fresh_agent(run.calls, i, eid))
    return {eid for eid, v in ok.items() if not v}


def _seen(reads: list[tuple[int, int]], total: int) -> int:
    lines: set[int] = set()
    for a, b in reads:
        lines.update(range(max(1, a), min(total, b) + 1))
    return len(lines)


def evidence_traces(run: Run, evidence_dir: Path) -> list[EvidenceTrace]:
    evidence_dir = Path(evidence_dir)
    out = []
    unread = unread_acks(run)
    for md in sorted(evidence_dir.glob("*.md")):
        eid = md.stem
        total = sum(1 for _ in open(md, encoding="utf-8", errors="ignore"))
        tr = EvidenceTrace(evidence=eid, lines_total=total)
        first_reader = applier = ""
        first_leftovers = -1
        for i, c in enumerate(run.calls):
            if c.name == "Read" and c.input.get("file_path", "").endswith(f"/evidence/{eid}.md"):
                if c.is_error:
                    continue
                tr.reads.append(read_range(c, total))
                first_reader = first_reader or c.agent_id
            elif c.name == "Write" and Path(c.input.get("file_path", "")).name.startswith(eid) \
                    and c.input.get("file_path", "").endswith(".json"):
                tr.ops_written += len(ops_in(c.input.get("content", "")))
            elif c.name == "Bash":
                cmd = c.input.get("command", "")
                if f"--evidence {eid}" not in cmd:
                    continue
                if " leftovers " in cmd:
                    tr.leftovers_calls += 1
                    first_leftovers = i if first_leftovers < 0 else first_leftovers
                elif " apply " in cmd:
                    applier = applier or c.agent_id
                    if first_leftovers >= 0:
                        tr.applied_after_leftovers = True
                    answer = json_answer(c.output)
                    tr.applied += int(answer.get("events") or 0)
                    rejected = answer.get("rejected") if isinstance(answer.get("rejected"), list) else []
                    tr.rejected += len(rejected)
                    tr.rejections += [str(r.get("why") or "") for r in rejected if isinstance(r, dict)]
                elif " done " in cmd:
                    if done_refused(c):
                        tr.refusals += 1
                    ack = ack_of(cmd)
                    if ack:
                        tr.acks.append(ack)
        tr.reread_before_ack = eid not in unread
        tr.reader = applier or first_reader
        tr.lines_seen = _seen(tr.reads, total)
        if tr.leftovers_calls and not any(x.name == "Bash" and " apply " in x.input.get("command", "")
                                          and f"--evidence {eid}" in x.input.get("command", "")
                                          for x in run.calls[first_leftovers + 1:]):   # the list led to an apply; a final check after the last apply is expected
            tr.applied_after_leftovers = False
        out.append(tr)
    return out


def done_refused(c: ToolCall) -> bool:
    """A `taska done` whose answer carries a status other than ok: a gate refusal, whether or not the tool flagged an error."""
    return c.name == "Bash" and " done " in c.input.get("command", "") and json_answer(c.output).get("status") not in ("ok", None)


def refused_dones(run: Run) -> list[ToolCall]:
    return [c for c in run.calls if done_refused(c)]


BLOCKED = ("requires approval", "multiple operations")   # what Claude Code answers to a command it will not run


def blocked_commands(run: Run) -> int:
    return sum(1 for c in run.calls if c.is_error and any(b in c.output for b in BLOCKED))


def seconds(a: str, b: str) -> int:
    import datetime as dt
    try:
        return int((dt.datetime.fromisoformat(b.replace("Z", "+00:00"))
                    - dt.datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds())
    except ValueError:
        return 0


def summary(run: Run, traces: list[EvidenceTrace]) -> Summary:
    return Summary(
        session_id=run.session_id, complete=run.complete, cost_usd=round(run.cost_usd, 2),
        duration_s=seconds(run.started, run.ended) if run.started and run.ended else 0,
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
