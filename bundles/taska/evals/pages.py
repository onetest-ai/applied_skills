"""Paginated transcript views for pilot roles, and proof of what each role was actually given.

  pages.py write --source MEETING.md --out DIR [--page-chars N]   one view per transcript: DIR/<evidence>/pNN.md + manifest.json
  pages.py delivery --transcript ROLE.jsonl --manifest DIR/<evidence>/manifest.json [...]

A page is an exact slice source[start:end] of the transcript as written; pages tile it with no gap or overlap. The page
file shows that slice with some single spaces turned into newlines so no line is longer than the Read tool shows whole
(LINE_CHARS); a newline replaces exactly one space, so every page-file character still sits at its source offset and
`wraps` restores the slice byte for byte. Delivery is read from a role's own tool results: a page is `available` when
listed, `requested` when a call names its file, `whole` only when its every line came back unshortened."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS.parent / "skills" / "run"))
from taska_lib.text import norm  # noqa: E402

PAGE_CHARS = 40_000   # with LINE_CHARS lines a page stays under the Read tool's 25k-token answer, accented text included
LINE_CHARS = 600      # taska_lib.store.MAX_LINE: Read shows about 2,000 characters of a line, so a longer one is cut
TURN = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\] ([^:\n]{1,80}): ", re.M)
SENTENCE = re.compile(r"[.?!][\"')\]]? ")
LONG_TURN = 20_000
ONE_TAG_SHARE = 0.9


@dataclass(frozen=True)
class Page:
    n: int
    start: int
    end: int
    text: str


def turns(source: str) -> list[dict]:
    """Every `[ts] speaker:` turn with its id (t0001, ...) and its source offsets."""
    hits = list(TURN.finditer(source))
    return [{"id": f"t{k + 1:04d}", "ts": m.group(1), "speaker": m.group(2).strip(), "start": m.start(),
             "end": hits[k + 1].start() if k + 1 < len(hits) else len(source)} for k, m in enumerate(hits)]


def _cut(source: str, pos: int, limit: int, starts: list[int]) -> int:
    """Where a page that begins at `pos` ends: the last turn start in its second half, else the last sentence end,
    else the last space, else the limit itself."""
    floor = pos + (limit - pos) // 2
    t = [s for s in starts if floor < s <= limit]
    if t:
        return t[-1]
    window = source[floor:limit]
    s = [m.end() for m in SENTENCE.finditer(window)]
    if s:
        return floor + s[-1]
    sp = window.rfind(" ")
    return floor + sp + 1 if sp >= 0 else limit


def paginate(source: str, page_chars: int = PAGE_CHARS) -> list[Page]:
    if page_chars < 2 * LINE_CHARS:
        raise ValueError(f"page_chars {page_chars} is below two lines ({2 * LINE_CHARS})")
    starts = [t["start"] for t in turns(source)]
    out, pos = [], 0
    while pos < len(source) or not out:
        end = len(source) if len(source) - pos <= page_chars else _cut(source, pos, pos + page_chars, starts)
        out.append(Page(len(out) + 1, pos, end, source[pos:end]))
        pos = end
    return out


def render(text: str) -> tuple[str, list[int], int]:
    """The page file's text: each line longer than LINE_CHARS broken at its last space within the limit (that space
    becomes the newline). Returns the text, the offsets turned into newlines, and how many lines no space could break."""
    chars, wraps, unbreakable = list(text), [], 0
    line_start = 0
    i = 0
    while i < len(chars):
        if chars[i] == "\n":
            line_start = i + 1
        elif i - line_start >= LINE_CHARS:
            sp = text.rfind(" ", line_start, i)
            if sp > line_start:
                chars[sp] = "\n"
                wraps.append(sp)
                line_start = sp + 1
            else:
                unbreakable += 1
                nxt = [k for k in (text.find(" ", i), text.find("\n", i)) if k >= 0]
                if not nxt:
                    break
                i = min(nxt)
                if chars[i] == " ":
                    chars[i] = "\n"
                    wraps.append(i)
                line_start = i + 1
                i += 1
                continue
        i += 1
    return "".join(chars), wraps, unbreakable


def unrender(rendered: str, wraps: list[int]) -> str:
    chars = list(rendered)
    for w in wraps:
        if chars[w] != "\n":
            raise ValueError(f"offset {w} is not a wrap")
        chars[w] = " "
    return "".join(chars)


def speaker_quality(source: str) -> list[str]:
    """Warnings a reader must carry: one speaker tag holding nearly all of a long transcript, or one enormous turn.
    Either means the speaker field cannot identify who said what."""
    ts = turns(source)
    if not ts:
        return ["no `[ts] speaker:` turns: speakers are unknown"]
    chars: dict[str, int] = {}
    for t in ts:
        chars[t["speaker"]] = chars.get(t["speaker"], 0) + t["end"] - t["start"]
    total = sum(chars.values())
    top, n = max(chars.items(), key=lambda kv: kv[1])
    out = []
    if total >= LONG_TURN and n / total >= ONE_TAG_SHARE:
        out.append(f"one speaker tag ({top}) holds {round(100 * n / total)}% of {total} characters: speaker attribution is unreliable")
    long = [t for t in ts if t["end"] - t["start"] > LONG_TURN]
    if long:
        out.append(f"{len(long)} turn(s) over {LONG_TURN} characters (longest {max(t['end'] - t['start'] for t in long)}): turns are not speaker changes")
    return out


def _text(path: Path) -> str:
    """A file's text exactly as stored (no newline translation): the form every offset refers to."""
    return Path(path).read_bytes().decode("utf-8")


def _sha(b: bytes | str) -> str:
    return hashlib.sha256(b.encode("utf-8") if isinstance(b, str) else b).hexdigest()


def write_views(src: Path, out: Path, page_chars: int = PAGE_CHARS) -> dict:
    """One view of a transcript: pNN.md per page and manifest.json (original hash, canonical text hash, every page's
    offsets, hash, turns and wraps, speaker-quality warnings). The text is the file decoded as UTF-8 with no newline
    translation, so offsets are positions in that text."""
    raw = Path(src).read_bytes()
    source = raw.decode("utf-8")
    ev = Path(src).stem
    d = Path(out) / ev
    d.mkdir(parents=True, exist_ok=True)
    ts = turns(source)
    rows = []
    for p in paginate(source, page_chars):
        body, wraps, unbreakable = render(p.text)
        f = d / f"p{p.n:02d}.md"
        f.write_text(body, encoding="utf-8", newline="")
        inside = [t for t in ts if t["start"] < p.end and t["end"] > p.start]
        rows.append({"n": p.n, "file": f.name, "start": p.start, "end": p.end, "chars": p.end - p.start, "sha256": _sha(p.text),
                     "file_sha256": _sha(body), "lines": body.count("\n") + 1, "wraps": wraps, "unbreakable_lines": unbreakable,
                     "turns": [inside[0]["id"], inside[-1]["id"]] if inside else None,
                     "continues_turn": bool(inside) and inside[0]["start"] < p.start})
    doc = {"evidence": ev, "source": str(src), "source_sha256": _sha(raw), "text_sha256": _sha(source), "chars": len(source),
           "page_chars": page_chars, "line_chars": LINE_CHARS, "pages": rows, "turns": len(ts),
           "speaker_quality": speaker_quality(source)}
    (d / "manifest.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def read_view(d: Path) -> str:
    """The source text rebuilt from a view: every page file unwrapped and checked against its hash."""
    m = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
    parts = []
    for p in m["pages"]:
        text = unrender(_text(Path(d) / p["file"]), p["wraps"])
        if _sha(text) != p["sha256"]:
            raise ValueError(f"{p['file']}: page text differs from its manifest")
        parts.append(text)
    text = "".join(parts)
    if _sha(text) != m["text_sha256"]:
        raise ValueError("pages do not rebuild the source")
    return text


def locate(manifest: dict, source: str, quote: str) -> dict | None:
    """Where a quote sits: its exact source offsets (compared as quotes are checked: case, punctuation and spacing
    ignored) and every page it
    touches; a quote across a page boundary names both pages."""
    words = norm(quote).split()
    if not words:
        return None
    word = lambda w: re.escape(w).replace("'", "['‘’]")
    m = re.search(r"(?<![\w'‘’])" + r"[^\w'‘’]+".join(word(w) for w in words) + r"(?![\w'‘’])", source, re.I)
    if m is None:
        return None
    pages = [p["n"] for p in manifest["pages"] if p["start"] < m.end() and p["end"] > m.start()]
    return {"start": m.start(), "end": m.end(), "pages": pages}


READ_LINE = re.compile(r"^\s*(\d+)[\t→](.*)$")
TRUNCATED = re.compile(r"truncated|exceeds maximum|too large|\[\.\.\.\]", re.I)


def _read_lines(output: str) -> dict[int, str]:
    out = {}
    for line in output.split("\n"):
        m = READ_LINE.match(line)
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def delivery(calls: list, manifest: dict, view_dir: str) -> dict:
    """Per page, from one role's tool calls (trajectory.ToolCall: name, input, output, is_error): `requested` when a
    call names the page file, `whole` when every line of the page file came back unchanged (Read's numbered lines,
    joined across calls; or the whole file in one other tool's output), `truncated` when an answer for it was cut.
    Availability is the manifest; requesting proves only that; `whole` is delivery, never comprehension."""
    rows = []
    for p in manifest["pages"]:
        path = str(Path(view_dir) / p["file"])
        body = _text(Path(view_dir) / p["file"])
        want = body.split("\n")
        got: dict[int, str] = {}
        requested, truncated, whole_once = False, False, False
        for c in calls:
            named = any(path in v or (p["file"] in v and manifest["evidence"] in v) for v in c.input.values())
            if not named:
                continue
            requested = True
            notes = [l for l in c.output.split("\n") if not READ_LINE.match(l)] if c.name == "Read" else []
            if c.is_error or any(TRUNCATED.search(l) for l in notes):
                truncated = truncated or bool(TRUNCATED.search(c.output))
                if c.is_error:
                    continue
            lines = _read_lines(c.output) if c.name == "Read" else {}
            if lines:
                got.update(lines)
            elif body.strip() and body in c.output:
                whole_once = True
        complete = whole_once or all(got.get(k + 1) == line for k, line in enumerate(want))
        rows.append({"page": p["n"], "file": p["file"], "available": True, "requested": requested,
                     "whole": requested and complete,
                     "lines_returned": len([k for k, line in enumerate(want) if got.get(k + 1) == line]), "lines": len(want),
                     "truncated": truncated})
    return {"evidence": manifest["evidence"], "pages": rows, "all_whole": all(r["whole"] for r in rows),
            "missing": [r["page"] for r in rows if not r["whole"]]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--source", required=True, action="append")
    w.add_argument("--out", required=True)
    w.add_argument("--page-chars", type=int, default=PAGE_CHARS)
    d = sub.add_parser("delivery")
    d.add_argument("--transcript", required=True, help="a role's subagent .jsonl")
    d.add_argument("--manifest", required=True, action="append")
    a = ap.parse_args(argv)
    if a.cmd == "write":
        docs = [write_views(Path(s), Path(a.out), a.page_chars) for s in a.source]
        for doc in docs:
            if read_view(Path(a.out) / doc["evidence"]) != _text(Path(doc["source"])):
                raise SystemExit(f"{doc['evidence']}: view does not rebuild its source")
        print(json.dumps([{"evidence": x["evidence"], "pages": len(x["pages"]), "speaker_quality": x["speaker_quality"]} for x in docs]))
        return 0
    import trajectory as TJ
    run = TJ.Run(session_id="role")
    TJ._agent_file(Path(a.transcript), "role", run)
    out = [delivery(run.calls, json.loads(Path(m).read_text(encoding="utf-8")), str(Path(m).parent)) for m in a.manifest]
    print(json.dumps(out, indent=1))
    return 0 if all(o["all_whole"] for o in out) else 1


if __name__ == "__main__":
    sys.exit(main())
