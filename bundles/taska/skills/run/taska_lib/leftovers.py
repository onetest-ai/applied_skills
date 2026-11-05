"""Sentences of a meeting that sound like work and that no applied event quotes.

A deterministic second pass for the extractor: `leftovers` lists, per third of the meeting, every sentence with a
commitment marker (will, needs to, by Friday, agreed, risk, …) whose span no agent event on this item quotes. The
token hashes that list and the number of applied quotes, so it changes whenever an event is applied; `done` on a
long meeting requires the token issued for its current state. Measured on Oct 31: a 20,000-word readout holding
21 key items produced 12 events and passed the coverage gate, which only counts events per third. `decisions`
accounts for every third of every meeting: a decision recorded in it, or an 8+ word quote from it."""
import hashlib
import re
from dataclasses import dataclass

from . import store as S
from .coverage import LONG_CHARS, _part, _text_end, part_counts, part_of, part_starts, parts_for, quoted_spans, too_short
from .ledger import fold
from .text import norm

MARKERS = re.compile(   # matched on the normalised sentence: lower case, straight apostrophes, no other punctuation
    r"\b(will|we'll|i'll|you'll|they'll|she'll|he'll|needs? to|need(ed)? you to|have to|has to|must|"
    r"let's|let us|make sure|next steps?|action items?|deadline|deliverables?|assigned to|eta|blocked|waiting (on|for)|"
    r"by (monday|tuesday|wednesday|thursday|friday|tomorrow|next|end of|eod|early|mid|late|the end|the \d+(st|nd|rd|th)|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)|"
    r"decid\w*|decisions?|agreed?|risks?|owes?|follow(ing)? up|get back|come back|reach out|circle back|"
    r"take (that|this|it) (on|away|offline)|take ownership|can you (?!hear|see)|could you|please (send|share|draft|look|check))\b")
# decisions and risks are said as judgements, not commitments ("that's the rationale to deprioritize", "it's locked
# in", "my concern is"). Measured Nov 2 on the 12 key meetings of the Oct 31 run: 101 sentences (median 4.5 per meeting, 27 at
# most), 6 of the 8 missed key decisions/risks listed; "not going to" and "instead of" were dropped: 42 sentences, no miss
DECISION_CUES = re.compile(
    r"\b(decision|decisions|decid\w*|deprioriti[sz]\w*|go with|going with|locked? in|rather than|"
    r"the plan is|rationale|we're aligned|aligned on|sign(ed)? off|approv\w*|risks?|risky|concern\w*|worried|worry|"
    r"bugs?|blockers?|dependenc\w*|danger\w*|disruption)\b")
MIN_WORDS = 6
TEXT_CHARS = 240
SPLIT = re.compile(r"(?<=[.?!])\s+")


@dataclass
class Sentence:
    ts: str
    speaker: str
    text: str
    offset: int
    length: int
    part: int


def sentences(item, parts=3, pattern=MARKERS) -> list[Sentence]:
    """Sentences of at least MIN_WORDS words that `pattern` matches, in reading order, in coverage's coordinates."""
    full = norm(S.full_text(item))
    n = len(full)
    blocks = [(t.get("ts", ""), t.get("speaker", ""), t.get("text", "")) for t in item.get("turns") or []]
    if item.get("text"):
        blocks.append(("", "", item["text"]))
    cursor = 0
    out = []
    for ts, speaker, text in blocks:
        for s in SPLIT.split(text):
            s = s.strip()
            q = norm(s)
            if len(q.split()) < MIN_WORDS or not pattern.search(q):
                continue
            off = full.find(q, cursor)
            if off < 0:
                off = full.find(q)
            if off < 0 or not n:
                continue
            cursor = off + len(q)
            out.append(Sentence(ts, speaker, s[:TEXT_CHARS], off, len(q), _part(off, n, parts)))
    return out


def _uncovered(item, events, pattern, parts):
    """(rows, spans): the sentences `pattern` matches that no applied quote covers, and those quotes' spans."""
    spans = quoted_spans(item, events)
    rows = [{"ts": s.ts, "speaker": s.speaker, "text": s.text, "offset": s.offset, "part": s.part}
            for s in sentences(item, parts, pattern) if not any(a < s.offset + s.length and s.offset < b for a, b in spans)]
    return rows, spans


def decisions(item, events) -> dict:
    """Per third (one part under coverage.MIN_CHARS), the decision creates whose quote falls in it, and its
    decision/risk cue sentences that no applied quote covers, as hints only: decisions are said without cue words
    (Nov 2: a cue-word gate missed every decision of a blind re-read). A risk accounts for no part (Nov 2 paid run:
    two risks accounted for all three thirds of a meeting holding six unrecorded decisions). `done` needs each part
    with none recorded accounted for by a --no-decisions quote from it."""
    parts = parts_for(item)
    nodes = fold(None, evs=events, ev={item["id"]: item})[0]
    live = {i for i, x in nodes.items() if x["state"] not in ("merged", "deleted")}
    made = [e for e in events if e.get("type") == "create" and e.get("kind") == "decision" and e.get("id") in live]
    recorded = part_counts(item, made, parts, text_only=True)   # undone, merged or deleted ones, or the title, count for none
    rows, _spans = _uncovered(item, events, DECISION_CUES, parts)
    items = _items(item, events, live, nodes, parts)
    return {"parts": [{"part": p, "recorded": recorded[p - 1], "items": items[p - 1],
                       "hints": [{k: r[k] for k in ("ts", "speaker", "text", "offset")} for r in rows if r["part"] == p]}
                      for p in range(1, parts + 1)],
            "starts": part_starts(item, parts), "count_unrecorded": recorded.count(0)}


def _items(item, events, live: set[str], nodes: dict[str, dict], parts: int) -> list[list[dict[str, str]]]:
    """Per part, every live node created from this meeting by an agent (or confirmed from an agent's proposal) and
    every card proposed from it that is pending or rejected (`card`; an accepted one is its node), in reading order,
    located as the decision gate locates a quote: a quote found only in the title lands in no part."""
    n, end = _text_end(item)
    undone = {e.get("target") for e in events if e.get("type") == "human" and e.get("op") == "undo"}
    decided = {e.get("target"): e["op"] for e in events
               if e.get("type") == "human" and e.get("op") in ("accept", "reject") and e.get("mid") not in undone}
    found: list[tuple[int, dict[str, str]]] = []
    for e in events:
        if e.get("type") == "agent" and e.get("op") == "propose" and e.get("evidence") == item["id"] \
                and e.get("mid") not in undone and decided.get(e.get("mid")) != "accept" and e.get("quote"):
            off = S.quote_offset(item, e["quote"])
            if 0 <= off < end:
                found.append((off, {"id": e["mid"], "kind": e["proposal"]["kind"], "title": e["proposal"]["title"],
                                    "card": "rejected" if decided.get(e["mid"]) == "reject" else "pending"}))
            continue
        if e.get("type") != "create" or e.get("evidence") != item["id"] or e.get("id") not in live or not e.get("quote"):
            continue
        if e.get("by") != "agent" and not e.get("from_proposal"):
            continue
        off = S.quote_offset(item, e["quote"])
        if 0 <= off < end:
            found.append((off, {"id": e["id"], "kind": e["kind"], "title": nodes[e["id"]]["title"]}))
    out: list[list[dict[str, str]]] = [[] for _ in range(parts)]
    for off, x in sorted(found, key=lambda t: t[0]):
        out[_part(off, n, parts) - 1].append(x)
    return out


def decision_acks(item, d, quotes: list[str]) -> tuple[dict[str, str], list[int], list[str]]:
    """(decisions_read, parts still missing, quotes too short or not in the text). A part with a decision
    recorded reads "recorded" and needs no quote; any other part reads the first quote located in it."""
    parts = len(d["parts"])
    bad: list[str] = []
    located: dict[int, str] = {}
    for q in quotes:
        p = 0 if too_short(q) else part_of(item, q, parts)
        if p:
            located.setdefault(p, q)
        else:
            bad.append(q)
    read: dict[str, str] = {}
    missing: list[int] = []
    for row in d["parts"]:
        if row["recorded"]:
            read[str(row["part"])] = "recorded"
        elif row["part"] in located:
            read[str(row["part"])] = located[row["part"]]
        else:
            missing.append(row["part"])
    return read, missing, bad


def leftovers(item, events, parts=3) -> dict:
    rows, spans = _uncovered(item, events, MARKERS, parts)
    by_part: dict[int, list[dict]] = {p: [] for p in range(1, parts + 1)}
    for r in rows:
        by_part[r["part"]].append({k: r[k] for k in ("ts", "speaker", "text", "offset")})
    offsets = [x["offset"] for x in rows]
    token = hashlib.sha256(f"{item['id']}|{len(spans)}|{','.join(map(str, offsets))}".encode()).hexdigest()[:12]
    chars = len(norm(S.full_text(item)))
    return {"evidence": item["id"], "chars": chars, "long": chars >= LONG_CHARS,
            "parts": [{"part": p, "leftovers": rows} for p, rows in by_part.items()],
            "count": len(offsets), "token": token}
