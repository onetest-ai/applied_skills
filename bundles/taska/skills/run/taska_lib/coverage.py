"""Coverage of one meeting by the extraction: how many quoted events fall in each third of its text.

Thirds are cut by character offset in the normalised full text (S.quote_offset's coordinates), never by timestamp:
one long turn can hold a third of a meeting. Measured on Oct 24: recall in the last third of a meeting was 41%
against 53% in the first, the "stopped early" pattern this check exists to catch."""
from . import store as S
from .text import norm

MIN_CHARS = 6000    # shorter meetings may honestly have nothing in a third
LONG_CHARS = 60000  # ~10,000 words: a third with fewer than THIN events was read shallowly (Oct 31: 13, 1, 3 on a 20,000-word readout)
THIN = 2


def part_of(item, quote, parts=3):
    """1-based part the quote falls in, 0 when it is not in the meeting's text (same coordinates as coverage)."""
    n = len(norm(S.full_text(item)))
    off = S.quote_offset(item, quote)
    return min(parts, off * parts // n + 1) if off >= 0 and n else 0


def coverage(item, events, parts=3):
    text = norm(S.full_text(item))
    n = len(text)
    counts = [0] * parts
    for e in events:
        if e.get("evidence") != item["id"] or e.get("by") != "agent" or not e.get("quote"):
            continue
        if e.get("type") not in ("create", "agent", "note"):
            continue
        off = S.quote_offset(item, e["quote"])
        if off >= 0 and n:
            counts[min(parts - 1, off * parts // n)] += 1
    return {"chars": n, "parts": [{"part": i + 1, "events": c} for i, c in enumerate(counts)],
            "empty_parts": [i + 1 for i, c in enumerate(counts) if c == 0] if n >= MIN_CHARS else [],
            "thin_parts": [i + 1 for i, c in enumerate(counts) if c < THIN] if n >= LONG_CHARS else []}
