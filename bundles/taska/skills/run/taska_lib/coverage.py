"""Coverage of one meeting by the extraction: how many quoted events fall in each third of its text.

Thirds are cut by character offset in the normalised full text (S.quote_offset's coordinates), never by timestamp:
one long turn can hold a third of a meeting. Measured on Oct 24: recall in the last third of a meeting was 41%
against 53% in the first, the "stopped early" pattern this check exists to catch."""
from . import store as S
from .text import norm

MIN_CHARS = 6000    # shorter meetings may honestly have nothing in a third
LONG_CHARS = 60000  # ~10,000 words: a third with fewer than THIN events was read shallowly (Oct 31: 13, 1, 3 on a 20,000-word readout)
THIN = 2
MIN_QUOTE_WORDS = 8  # a quote that proves a part was read: one word, or the title, proves nothing


def _part(off, n, parts):
    return min(parts, off * parts // n + 1) if n else 0


def _text_end(item):
    """(length of the normalised text, where the title full_text appends begins) so a quote of the title finds nothing."""
    full = norm(S.full_text(item))
    title = norm(item.get("title") or "")
    end = len(full) - len(title) - 1 if title and full.endswith(title) else len(full)
    return len(full), end


def too_short(quote):
    return len(norm(quote).split()) < MIN_QUOTE_WORDS


def part_of(item, quote, parts=3):
    """1-based part the quote falls in, 0 when it is not in the meeting's text (same coordinates as coverage)."""
    n, end = _text_end(item)
    off = S.quote_offset(item, quote)
    return _part(off, n, parts) if 0 <= off < end else 0


def quoted_spans(item, events):
    """(offset, end) in the normalised text of every agent event quote on this item."""
    spans = []
    for e in events:
        if e.get("evidence") != item["id"] or e.get("by") != "agent" or not e.get("quote"):
            continue
        if e.get("type") not in ("create", "agent", "note"):
            continue
        off = S.quote_offset(item, e["quote"])
        if off >= 0:
            spans.append((off, off + len(norm(e["quote"]))))
    return spans


def part_starts(item, parts=3):
    """Where each part begins: the turn (ts, speaker) holding its first character."""
    n, _ = _text_end(item)
    turns = []
    pos = 0
    for t in item.get("turns") or []:
        piece = norm(t.get("text", ""))
        if piece:
            turns.append((pos, t.get("ts", ""), t.get("speaker", "")))
            pos += len(piece) + 1
    out = []
    for p in range(parts):
        off = n * p // parts
        at = [t for t in turns if t[0] <= off]
        out.append({"part": p + 1, "ts": at[-1][1] if at else "", "speaker": at[-1][2] if at else ""})
    return out


def coverage(item, events, parts=3):
    n, _ = _text_end(item)
    counts = [0] * parts
    for off, _end in quoted_spans(item, events):
        counts[_part(off, n, parts) - 1] += 1
    return {"chars": n, "parts": [{"part": i + 1, "events": c} for i, c in enumerate(counts)],
            "starts": part_starts(item, parts),
            "empty_parts": [i + 1 for i, c in enumerate(counts) if c == 0] if n >= MIN_CHARS else [],
            "thin_parts": [i + 1 for i, c in enumerate(counts) if c < THIN] if n >= LONG_CHARS else []}
