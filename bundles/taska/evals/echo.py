"""An independent note-taker's calls as evidence for a meeting (Echo: ~/Echo/calls/<call>/).

A call folder holds meta.json (started_at, title) and transcript.md; once Echo has processed it, also actions.json
(task, owner, due, quote, t, status) and notes.md (its "## Decisions" bullets end with [mm:ss] references). A call is
matched to a taska meeting by what was said (3-word overlap of the two transcripts, on or within a day of the meeting's
date): titles differ between Echo and Teams, and meeting clocks are often guessed. Reads only."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DECISIONS = re.compile(r"^##\s+decisions\s*$", re.I)
BULLET = re.compile(r"^\s*[-*]\s+(.+)")
REFS = re.compile(r"\s*\[(\d+:\d+(?:,\s*\d+:\d+)*)\]\s*(\.?)\s*$")
MATCH = 0.3   # measured on a real project: own call 0.58-0.79 of shared wording, every other call 0.14 or less
DATE = re.compile(r"\d{4}-\d\d-\d\d")


@dataclass
class EchoCall:
    id: str
    started_at: str
    title: str
    processed: bool
    actions: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    transcript: str = ""


def _mmss(t: int | float | str | None) -> str:
    try:
        s = int(float(t))
    except (TypeError, ValueError):
        return ""
    return f"{s // 60:02d}:{s % 60:02d}"


def _seconds(mmss: str) -> int:
    m, s = mmss.split(":")
    return int(m) * 60 + int(s)


def _decisions(notes: Path) -> list[dict]:
    out, inside = [], False
    for line in notes.read_text(encoding="utf-8", errors="replace").splitlines():
        if DECISIONS.match(line):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        m = BULLET.match(line) if inside else None
        if m:
            refs = REFS.search(m.group(1))
            what = (REFS.sub(r"\2", m.group(1)) if refs else m.group(1)).strip()
            at = refs.group(1) if refs else ""
            out.append({"what": what, "at": at, "t": _seconds(at.split(",")[0]) if at else None})
    return out


def load_calls(root: Path) -> list[EchoCall]:
    calls = []
    for d in sorted(Path(root).iterdir()):
        if d.is_symlink() or not (d / "meta.json").is_file():
            continue
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        actions = json.loads((d / "actions.json").read_text(encoding="utf-8")) if (d / "actions.json").is_file() else []
        calls.append(EchoCall(
            id=d.name, started_at=meta.get("started_at") or "", title=meta.get("title") or "",
            processed=(d / "notes.md").is_file() and (d / "actions.json").is_file(),
            actions=[dict(a, at=_mmss(a.get("t"))) for a in (actions if isinstance(actions, list) else [])],
            decisions=_decisions(d / "notes.md") if (d / "notes.md").is_file() else [],
            transcript=(d / "transcript.md").read_text(encoding="utf-8", errors="replace") if (d / "transcript.md").is_file() else ""))
    return calls


def _shingles(text: str) -> set[str]:
    words = re.sub(r"[^\w']+", " ", text.lower()).split()
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def _days(item_id: str, meeting: dict) -> set[dt.date]:
    out = set()
    for s in (item_id, meeting.get("at") or ""):
        m = DATE.search(s)
        if m:
            d = dt.date.fromisoformat(m.group(0))
            out |= {d - dt.timedelta(days=1), d, d + dt.timedelta(days=1)}
    return out


def match_calls(meetings: dict[str, dict], calls: list[EchoCall]) -> dict[str, EchoCall | None]:
    """Each meeting's own call (the most shared wording, at least MATCH), or None: {evidence id: call}. `meetings` maps an
    evidence id to its item, whose text is read with the same rule as store.full_text (turns, then text)."""
    words = {c.id: _shingles(c.transcript) for c in calls}
    out = {}
    for evid, m in meetings.items():
        said = _shingles(" ".join([t.get("text", "") for t in m.get("turns") or []] + [m.get("text") or ""]))
        days, best = _days(evid, m), (0.0, None)
        for c in calls:
            if not DATE.match(c.id) or dt.date.fromisoformat(c.id[:10]) not in days or not words[c.id] or not said:
                continue
            share = len(said & words[c.id]) / min(len(said), len(words[c.id]))
            if share > best[0]:
                best = (share, c)
        out[evid] = best[1] if best[0] >= MATCH else None
    return out
