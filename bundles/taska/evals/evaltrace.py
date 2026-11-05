"""eval-trace/1: what one headless run did, as one model with small views over it.

The model is built once from `trajectory.load_run(session)` (the only code that reads Claude Code's records); the
views below depend on the model, never on the session format: `link` ties each graded check to the events that
touched its span, `why` and `story` read it for an agent and for a person, `metrics` gives the numbers and `perfetto`
the old call timeline. A field the session lacks is left out or null; nothing here raises on it.

  {"schema": "eval-trace/1", "session", "started", "ended", "cost_usd", "complete", "refusals", "blocked", "acks_without_reread",
   "agents": [{id, name, model, turns, errors, tokens}], "spans": [{id, agent, tool, model, start, end, error,
   step, evidence}], "events": [{id, span, agent, at, evidence, type: read|propose|apply|gate|done|leftovers, ...}]}
"""
import json
import re
import shlex
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
for _p in (RUN_DIR, EVALS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import trajectory as TJ  # noqa: E402
from taska_lib import store as S  # noqa: E402
from taska_lib.text import norm  # noqa: E402

SCHEMA = "eval-trace/1"
TASKA_STEP = ("read", "gate", "leftovers", "done")   # the meeting's own steps every check is linked to


def _qspan(ev: dict | None, evidence: str, quote: str) -> list[int] | None:
    item = (ev or {}).get(evidence)
    if not item or not quote:
        return None
    at = S.quote_offset(item, quote)
    return None if at < 0 else [at, at + len(norm(quote))]


def _words(command: str, flag: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return [parts[i + 1] for i, p in enumerate(parts[:-1]) if p == flag]


def is_taska(c: TJ.ToolCall) -> bool:
    return c.name == "Bash" and ("/taska " in c.input.get("command", "") or "taska.py" in c.input.get("command", ""))


def error_counts(run: TJ.Run) -> dict[str, int]:
    """Every errored or refused call in exactly one bucket. gate_refusals: a taska answer whose status is not ok, error or
    absent (whatever the tool flagged); slips: a failed call that is not a Bash command, or a taska `error` answer;
    improvised: a failed Bash call with no taska answer (a blocked command, a bad grep). A taska call whose answer says
    `ok` but which the tool flagged as an error counts as improvised too: nothing taska refused, the call itself failed."""
    out = {"improvised": 0, "gate_refusals": 0, "slips": 0}
    for c in run.calls:
        status = TJ.json_answer(c.output).get("status") if is_taska(c) else None
        if status not in (None, "ok", "error"):
            out["gate_refusals"] += 1
        elif status == "error" or (c.is_error and c.name != "Bash"):
            out["slips"] += 1
        elif c.is_error:
            out["improvised"] += 1
    return out


def from_run(run: TJ.Run, ev: dict | None = None) -> dict:
    """The eval-trace/1 model of a run. `ev` (store.evidence) gives each proposed quote its position in the meeting and
    places events on a re-synced copy (`duplicate_of`) at the original meeting, as every consumer of events must."""
    names = {a.agent_id: a.description or a.agent_id for a in run.agents}
    models = {a.agent_id: a.model for a in run.agents}
    spans, events = [], []
    proposals: dict[str, list[dict]] = {}

    def original(evid: str) -> str:
        seen = set()
        while (ev or {}).get(evid, {}).get("duplicate_of") and evid not in seen:
            seen.add(evid)
            evid = ev[evid]["duplicate_of"]
        return evid

    def add(span: str, c: TJ.ToolCall, evidence: str, typ: str, **fields) -> dict:
        e = {"id": f"e{len(events) + 1}", "span": span, "agent": c.agent_id, "at": c.ts, "evidence": original(evidence),
             "type": typ} | fields
        if original(evidence) != evidence:
            e["copy"] = evidence
        events.append(e)
        return e

    for i, c in enumerate(run.calls):
        sid = f"s{i + 1}"
        evid = TJ.meeting_of(c)
        spans.append({"id": sid, "agent": c.agent_id, "tool": c.name, "model": models.get(c.agent_id, ""), "start": c.ts,
                      "end": c.end, "error": c.is_error, "step": TJ.step_words(c, with_meeting=False), "evidence": evid})
        path = c.input.get("file_path", "")
        cmd = c.input.get("command", "")
        if c.name == "Read" and evid and f"/evidence/{evid}.md" in path and not c.is_error:
            add(sid, c, evid, "read", lines=list(TJ.read_range(c)))
        elif c.name == "Write" and evid and path.endswith(".json"):
            made = []
            for k, op in enumerate(TJ.ops_in(c.input.get("content", ""))):
                if not isinstance(op, dict):
                    continue
                quote = str(op.get("quote") or "")
                made.append(add(sid, c, evid, "propose", index=k, file=path, op=op.get("op") or op.get("type"),
                                kind=op.get("kind"), title=op.get("title"), quote=quote, qspan=_qspan(ev, original(evid), quote)))
            proposals[path] = made
        elif c.name == "Bash" and evid and re.search(r"\sapply\s", cmd):
            answer = TJ.json_answer(c.output)
            made = proposals.get((_words(cmd, "--proposals") or [""])[0])
            if c.is_error or answer.get("status") != "ok" or "events" not in answer:   # nothing is known to be applied
                reason = answer.get("error") or answer.get("status") or c.output.strip()[:120] or "no answer"
                add(sid, c, evid, "apply", result="failed", why=str(reason), index=0, quote=None, qspan=None)
                continue
            rejected = {int(r["i"]): r for r in answer.get("rejected") or [] if isinstance(r, dict) and isinstance(r.get("i"), int)}
            count = len(made) if made is not None else int(answer.get("events") or 0) + len(rejected)
            for k in range(count):
                p = next((x for x in made or [] if x["index"] == k), {})
                row = {"index": k, "quote": p.get("quote"), "qspan": p.get("qspan")}
                if k in rejected:
                    add(sid, c, evid, "apply", result="rejected", why=rejected[k].get("why"), **row)
                else:
                    add(sid, c, evid, "apply", result="applied", why=None, **row)
        elif c.name == "Bash" and evid and re.search(r"\sleftovers\s", cmd):
            answer = TJ.json_answer(c.output)
            add(sid, c, evid, "leftovers", count=answer.get("count"), token=answer.get("token"), status=answer.get("status"))
        elif c.name == "Bash" and re.search(r"\sdone\s", cmd):
            answer = TJ.json_answer(c.output)
            ack = TJ.ack_of(cmd)
            if TJ.done_refused(c):
                add(sid, c, evid, "gate", status=answer.get("status"), parts=answer.get("parts"), starts=answer.get("starts"),
                    empty_parts=answer.get("empty_parts"), thin_parts=answer.get("thin_parts"), quote=_words(cmd, "--quote"), ack=ack or None)
            elif evid and (answer or not c.is_error):
                add(sid, c, evid, "done", status=answer.get("status"), ack=ack or None)
    agents = [{"id": a.agent_id, "name": names[a.agent_id], "model": a.model, "turns": a.turns, "errors": a.errors,
               "first": a.first_ts, "last": a.last_ts,
               "tokens": {"input": a.input_tokens, "output": a.output_tokens, "cache_read": a.cache_read_tokens,
                          "cache_write": a.cache_write_tokens}} for a in run.agents]
    return {"schema": SCHEMA, "session": run.session_id, "started": run.started, "ended": run.ended,
            "cost_usd": run.cost_usd, "complete": run.complete,
            "refusals": len(TJ.refused_dones(run)), "blocked": TJ.blocked_commands(run),
            "acks_without_reread": len(TJ.unread_acks(run)), "error_counts": error_counts(run),
            "agents": agents, "spans": spans, "events": events}


def _overlap(a: list[int], b: list[int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def link(grade: dict, task: dict, trace: dict) -> dict:
    """A copy of the grade whose checks each carry `events`: the meeting's events that touched the check's span, plus
    the meeting's reads, gates, leftovers and done calls, in time order. Outcome checks get the gates and done calls."""
    spans = {}
    for kind in ("must_create", "must_not_create"):
        for entry in task.get(kind, []):
            spans[entry["id"]] = entry.get("span")
    mine = [e for e in trace.get("events", []) if e.get("evidence") == task.get("evidence")]
    out = dict(grade)
    out["checks"] = []
    for check in grade.get("checks", []):
        span = spans.get(str(check.get("id", "")).removesuffix(":dup")) if check.get("type") != "outcome" else None
        if span:
            keep = [e for e in mine if e["type"] in TASKA_STEP or (e.get("qspan") and _overlap(e["qspan"], span) > 0)]
        else:
            keep = [e for e in mine if e["type"] in ("gate", "done")]
        row = dict(check) | {"events": [e["id"] for e in keep]}
        applied = [e for e in keep if e["type"] == "apply" and e.get("result") == "applied" and span and e.get("qspan")
                   and _overlap(e["qspan"], span) > 0]
        when = min((e["at"] for e in applied if e.get("at")), default=None) or next((e["at"] for e in keep if e.get("at")), None)
        if when:
            row["at"] = when
        out["checks"].append(row)
    return out


def why(trace: dict, grade: dict, check_id: str) -> list[dict]:
    """Only the events linked to one check, in time order: what an analysis agent needs to name the cause of a miss."""
    check = next((c for c in grade.get("checks", []) if c.get("id") == check_id), None)
    want = set(check.get("events") or []) if check else set()
    return [e for e in trace.get("events", []) if e["id"] in want]


def event_words(e: dict, names: dict | None = None) -> dict:
    """One taska event in words: {at, agent, step, detail}. The only place events are put into words."""
    t = e.get("type")
    if t == "read":
        a, b = e.get("lines") or [None, None]
        step, detail = f"read lines {a}-{b if b is not None else 'end'}", ""
    elif t == "propose":
        step, detail = f"propose {e.get('op') or '?'}", f"{e.get('title') or ''} {e.get('quote') or ''}".strip()
    elif t == "apply":
        step = f"apply {e.get('result')}"
        detail = f"{e.get('quote') or ''}" + (f": {e['why']}" if e.get("why") else "")
    elif t == "gate":
        step = "done refused"
        q = e.get("quote")
        offered_quotes = [q] if isinstance(q, str) and q else [str(x) for x in q] if isinstance(q, (list, tuple)) else []
        offered = f", quote offered: {'; '.join(offered_quotes)}" if offered_quotes else ""
        detail = f"{e.get('status')}" + (f", parts {e['parts']}" if e.get("parts") else "") + offered
    elif t == "leftovers":
        step, detail = "leftovers", f"{e.get('count')} sentences, token {e.get('token')}"
    else:
        step, detail = "done", str(e.get("status") or "")
    agent = e.get("agent") or ""
    return {"at": e.get("at") or "", "agent": (names or {}).get(agent, agent), "step": step, "detail": detail}


def story(trace: dict, evidence: str) -> list[dict]:
    """The meeting's steps in words, in order: {at, agent, step, detail}."""
    names = {a["id"]: a.get("name") or a["id"] for a in trace.get("agents", [])}
    return [event_words(e, names) for e in trace.get("events", []) if e.get("evidence") == evidence]


def metrics(trace: dict) -> dict:
    """What the trial cost, in the shape bank.trial_metrics has always returned."""
    tok = lambda k: sum(a["tokens"][k] for a in trace.get("agents", []))
    started, ended = trace.get("started"), trace.get("ended")
    return {"latency_s": TJ.seconds(started, ended) if started and ended else 0,
            "tokens": {"input": tok("input"), "output": tok("output"), "cache_read": tok("cache_read"),
                       "cache_write": tok("cache_write")},
            "cost_usd": round(trace.get("cost_usd") or 0.0, 4),
            "errors": {"tool": sum(a["errors"] for a in trace.get("agents", [])),
                       "refusals": trace.get("refusals", 0), "blocked": trace.get("blocked", 0),
                       "acks_without_reread": trace.get("acks_without_reread", 0)},
            "error_counts": {k: (trace.get("error_counts") or {}).get(k, 0) for k in ("improvised", "gate_refusals", "slips")}}


def perfetto(trace: dict) -> dict:
    """The call timeline in Chrome's Trace Event Format: one process per agent, one bar per tool call."""
    out: list[dict] = []
    pids = {a["id"]: i for i, a in enumerate(trace.get("agents", []))}
    for a in trace.get("agents", []):
        out.append({"name": "process_name", "ph": "M", "pid": pids[a["id"]], "args": {"name": a.get("name") or a["id"]}})
    for s in trace.get("spans", []):
        if not s.get("start"):
            continue
        start = TJ.micros(s["start"])
        dur = max(1000, TJ.micros(s["end"]) - start) if s.get("end") else 1000
        out.append({"name": s["step"], "ph": "X", "pid": pids.get(s["agent"], 0), "tid": 0, "ts": start, "dur": dur,
                    "args": {"tool": s["tool"], "meeting": s.get("evidence", ""), "error": s["error"]}})
    return {"traceEvents": out, "displayTimeUnit": "ms"}
