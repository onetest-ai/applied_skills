"""Sentences of a meeting that sound like work and that no applied event quotes.

A deterministic second pass for the extractor: `leftovers` lists, per third of the meeting, every sentence with a
commitment marker (will, needs to, by Friday, agreed, risk, …) whose span no agent event on this item quotes. The
token hashes that list and the number of applied quotes, so it changes whenever an event is applied; `done` on a
long meeting requires the token issued for its current state. Measured on Oct 31: a 20,000-word readout holding
21 key items produced 12 events and passed the coverage gate, which only counts events per third."""
import hashlib
import re
from dataclasses import dataclass

from . import store as S
from .coverage import LONG_CHARS, _part, quoted_spans
from .text import norm

MARKERS = re.compile(   # matched on the normalised sentence: lower case, straight apostrophes, no other punctuation
    r"\b(will|we'll|i'll|you'll|they'll|she'll|he'll|needs? to|need(ed)? you to|have to|has to|must|"
    r"let's|let us|make sure|next steps?|action items?|deadline|deliverables?|assigned to|eta|blocked|waiting (on|for)|"
    r"by (monday|tuesday|wednesday|thursday|friday|tomorrow|next|end of|eod|early|mid|late|the end|the \d+(st|nd|rd|th)|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)|"
    r"decid(e|ed|es|ing|sion)|agreed?|risks?|owes?|follow(ing)? up|get back|come back|reach out|circle back|"
    r"take (that|this|it) (on|away|offline)|take ownership|can you (?!hear|see)|could you|please (send|share|draft|look|check))\b")
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


def sentences(item, parts=3) -> list[Sentence]:
    """Marker-bearing sentences of at least MIN_WORDS words, in reading order, located in coverage's coordinates."""
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
            if len(q.split()) < MIN_WORDS or not MARKERS.search(q):
                continue
            off = full.find(q, cursor)
            if off < 0:
                off = full.find(q)
            if off < 0 or not n:
                continue
            cursor = off + len(q)
            out.append(Sentence(ts, speaker, s[:TEXT_CHARS], off, len(q), _part(off, n, parts)))
    return out


def leftovers(item, events, parts=3) -> dict:
    spans = quoted_spans(item, events)
    by_part: dict[int, list[dict]] = {p: [] for p in range(1, parts + 1)}
    for s in sentences(item, parts):
        if any(a < s.offset + s.length and s.offset < b for a, b in spans):
            continue
        by_part[s.part].append({"ts": s.ts, "speaker": s.speaker, "text": s.text, "offset": s.offset})
    offsets = [x["offset"] for rows in by_part.values() for x in rows]
    token = hashlib.sha256(f"{item['id']}|{len(spans)}|{','.join(map(str, offsets))}".encode()).hexdigest()[:12]
    chars = len(norm(S.full_text(item)))
    return {"evidence": item["id"], "chars": chars, "long": chars >= LONG_CHARS,
            "parts": [{"part": p, "leftovers": rows} for p, rows in by_part.items()],
            "count": len(offsets), "token": token}
