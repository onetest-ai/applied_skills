"""Coverage of one meeting by the extraction: how many quoted events fall in each third of its text.

Thirds are cut by character offset in the normalised full text (S.quote_offset's coordinates), never by timestamp:
one long turn can hold a third of a meeting. Measured on Oct 24: recall in the last third of a meeting was 41%
against 53% in the first, the "stopped early" pattern this check exists to catch."""
import re

from . import store as S
from .text import norm

MIN_CHARS = 6000    # shorter meetings may honestly have nothing in a third
THIN_FROM = 30000   # characters from which a third with fewer than THIN events counts as thin
LONG_CHARS = 60000  # ~10,000 words: a third with fewer than THIN events was read shallowly (Oct 31: 13, 1, 3 on a 20,000-word readout)
THIN = 2
SECTION_WORDS = 3000  # one fresh reader per section of about this many words
SECTIONS_FROM = THIN_FROM  # a meeting of this many characters or fewer is one section, read by one reader
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


def _walk(item) -> list[dict]:
    """Every turn with text, in order, with its offset in norm(full_text), at the coordinates part_of uses."""
    out = []
    pos = 0
    for t in item.get("turns") or []:
        piece = norm(t.get("text", ""))
        if piece:
            out.append({"pos": pos, "ts": t.get("ts", ""), "speaker": t.get("speaker", ""), "text": t.get("text", "")})
            pos += len(piece) + 1
    return out


def part_starts(item, parts=3):
    """Where each part begins: the turn (ts, speaker) holding its first character."""
    n, _ = _text_end(item)
    turns = _walk(item)
    out = []
    for p in range(parts):
        off = n * p // parts
        at = [t for t in turns if t["pos"] <= off]
        out.append({"part": p + 1, "ts": at[-1]["ts"] if at else "", "speaker": at[-1]["speaker"] if at else ""})
    return out


def parts_for(item) -> int:
    """Thirds from MIN_CHARS on; a shorter meeting is one part."""
    return 3 if _text_end(item)[0] >= MIN_CHARS else 1


def part_counts(item, events, parts=3, text_only=False) -> list[int]:
    """Agent event quotes per part; text_only leaves out a quote found only in the title full_text appends."""
    n, end = _text_end(item)
    counts = [0] * parts
    for off, _end in quoted_spans(item, events):
        if not (text_only and off >= end):
            counts[_part(off, n, parts) - 1] += 1
    return counts


def coverage(item, events, parts=3):
    n, _ = _text_end(item)
    counts = part_counts(item, events, parts)
    return {"chars": n, "parts": [{"part": i + 1, "events": c} for i, c in enumerate(counts)],
            "starts": part_starts(item, parts),
            "empty_parts": [i + 1 for i, c in enumerate(counts) if c == 0] if n >= MIN_CHARS else [],
            "thin_parts": [i + 1 for i, c in enumerate(counts) if c < THIN] if n >= THIN_FROM else []}


def _sentences(text, pos, limit=None):
    """(start, words) of each sentence of `text`, at offsets of norm(full_text) from `pos`; a sentence of more than
    `limit` words is cut into chunks of at most `limit` words."""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        piece = norm(sentence)
        if not piece:
            continue
        tokens = piece.split()
        if limit and len(tokens) > limit:
            at = pos
            for i in range(0, len(tokens), limit):
                chunk = tokens[i:i + limit]
                out.append((at, len(chunk)))
                at += len(" ".join(chunk)) + 1
        else:
            out.append((pos, len(tokens)))
        pos += len(piece) + 1
    return out


def _units(item, words=None):
    """(start, words, ts, speaker) of each turn (each sentence of a turn longer than `words`, keeping its ts and speaker),
    then each sentence of a bare text, at the offsets of norm(full_text)."""
    walk = _walk(item)
    units = []
    for t in walk:
        count = len(t["text"].split())
        if words and count > words:
            units += [(start, n, t["ts"], t["speaker"]) for start, n in _sentences(t["text"], t["pos"], words)]
        else:
            units.append((t["pos"], count, t["ts"], t["speaker"]))
    pos = walk[-1]["pos"] + len(norm(walk[-1]["text"])) + 1 if walk else 0
    return units + [(start, n, "", "") for start, n in _sentences(item.get("text") or "", pos, words)]


def sections(item, words=SECTION_WORDS) -> list[dict]:
    """Consecutive stretches of about `words` words, cut at turn boundaries (sentence ends inside a turn longer than `words`, and when there are no turns);
    start/end are offsets in norm(full_text), the coordinates part_of uses, and together cover the text before the title.
    A meeting of SECTIONS_FROM characters or fewer is one section."""
    units = _units(item, words)
    n, text_end = _text_end(item)
    whole = n <= SECTIONS_FROM
    cuts: list[list[tuple[int, int, str, str]]] = []
    for u in units:
        if cuts and (whole or sum(x[1] for x in cuts[-1]) + u[1] <= words):
            cuts[-1].append(u)
        else:
            cuts.append([u])
    out = []
    for i, group in enumerate(cuts):
        end = cuts[i + 1][0][0] if i + 1 < len(cuts) else text_end
        out.append({"n": i + 1, "start": 0 if i == 0 else group[0][0], "end": end,
                    "start_ts": group[0][2], "words": sum(x[1] for x in group)})
    return out


def section_of(item, quote, secs=None) -> int:
    """1-based section the quote's first character falls in, 0 when it is not in the meeting's text."""
    _, end = _text_end(item)
    off = S.quote_offset(item, quote)
    if not 0 <= off < end:
        return 0
    return next((s["n"] for s in secs or sections(item) if s["start"] <= off < s["end"]), 0)


def _rendered(item) -> list[tuple[str, int, int]]:
    """(line, start, end) of every body line S.render_md writes, offsets in norm(full_text); a blank separator has start == end == -1."""
    lines: list[tuple[str, int, int]] = []

    def block(text, prefix, pos):
        for line in S._lines(text, prefix):
            piece = norm(line[len(prefix):])
            lines.append((line, pos, pos + len(piece)))
            pos += len(piece) + 1 if piece else 0

    pos = 0
    for t in item.get("turns") or []:
        piece = norm(t.get("text", ""))
        if not t.get("bc"):
            if lines:
                lines.append(("", -1, -1))
            block(t["text"], f"[{t['ts']}] {t['speaker']}: ", pos)
        pos += len(piece) + 1 if piece else 0
    if item.get("text"):
        if lines:
            lines.append(("", -1, -1))
        block(item["text"], "", pos)
    return lines


def _layout(md_lines, item) -> tuple[int, list[tuple[str, int, int]]] | None:
    """(index of the first body line, the rendered body) when md_lines has S.render_md's layout for item, else None."""
    lines = list(md_lines)
    while lines and not lines[-1]:
        lines.pop()
    body = lines.index("") + 1 if "" in lines else len(lines)
    want = _rendered(item)
    return (body, want) if [x[0] for x in want] == lines[body:] else None


def section_lines(md_lines, item, secs) -> list[dict]:
    """Each section with the 1-based first_line and last_line it occupies in the rendered .md, derived from how
    S.render_md lays the body out. None for both when md_lines does not have that layout (a stale .md); never a guess."""
    fit = _layout(md_lines, item)
    body, want = fit or (0, [])
    firsts: list[int | None] = []
    for s in secs:
        at = next((i for i, (_, _start, end) in enumerate(want) if end > s["start"]), None) if fit else None
        firsts.append(None if at is None else body + at + 1)
    out = []
    for i, (s, f) in enumerate(zip(secs, firsts)):
        nxt = firsts[i + 1] if i + 1 < len(firsts) else body + len(want) + 1
        last = None if f is None or nxt is None else nxt - 1
        if last is not None and i + 1 < len(secs) and want[nxt - body - 1][1] < secs[i + 1]["start"]:
            last = nxt
        out.append({**s, "first_line": f, "last_line": last})
    return out


def part_lines(md_lines, item, part, parts=3) -> tuple[int | None, int | None]:
    """1-based (first_line, last_line) of the rendered .md lines that overlap the part's offsets [n*(part-1)/parts,
    n*part/parts) in norm(full_text); (None, None) when md_lines does not have S.render_md's layout (a stale .md)."""
    fit = _layout(md_lines, item)
    if not fit:
        return None, None
    body, want = fit
    n, _ = _text_end(item)
    lo, hi = -(-n * (part - 1) // parts), -(-n * part // parts)
    hits = [i for i, (_, start, end) in enumerate(want) if 0 <= start < hi and end > lo]
    return (body + hits[0] + 1, body + hits[-1] + 1) if hits else (None, None)
