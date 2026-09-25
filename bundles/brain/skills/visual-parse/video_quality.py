#!/usr/bin/env python3
"""Deterministic quality metrics for one assembled video-lane doc (no LLM, no network).

Measures what a reader of the parsed Markdown would trip over, so a change to frame
selection, the vision prompt or assemble can be proven before/after on the same recording:

  frame_parity        frame sections in the doc == live (non-dropped) pages in pages.json
  cue_coverage        transcript cues whose text appears in the doc (needs --sidecar)
  redundant_pairs     frame pairs whose transcriptions share >= --jaccard of their words
  redundant_confirmed_distinct  of those, pairs a blind reader kept (`adds` verdict in pages.json)
  caption_leaks       frame lines repeating >= 6 words spoken while the frame was on screen
                      (± 30 s), or describing a caption overlay
  cross_frame_refs    frame lines referring to another frame ("same as previous frame", "Unlike A")
  ui_leaks            frame lines containing a --ui-token (meeting-app chrome), case-sensitive
  marker_only_chunks  frame chunks that hold only the image/on-screen markers after chunking
  golden              exact-string checks from --golden {must_contain, must_not_contain, ui_tokens}
  retention           for frames dropped as duplicates: share of their words kept by the survivor
                      (needs --results, the VLM result dir; null when a transcription is missing)

Exit 1 when frame parity fails or any golden check fails; 0 otherwise. Thresholds for the
other metrics are the caller's call — they are reported, not gated.

Usage:
  video_quality.py --doc <parsed>/<rel>.md --pages <assets>/<slug>/pages.json
                   [--sidecar <vtt|srt|docx>] [--golden golden.json] [--results <vision dir>]
                   [--ui-token "Take control" ...] [--jaccard 0.75] --out metrics.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(os.path.dirname(HERE), "knowledge-index"))  # chunking.py, as indexed

_FRAME_H = re.compile(r"^## .*\(frame p(\d+)\)\s*$")
_CUE_H = re.compile(r"^## .*\(cue \d+\)\s*$")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
# a described caption overlay ("live-caption bar reads…", "Caption spoken: …"), not any text
# that happens to contain the word (a slide's "caption block", a CMS field)
_CAPTION = re.compile(r"\b(live|burned[- ]in|closed)[- ]captions?\b|\bcaptions?\s+(spoken|bar|overlay|text)\b"
                      r"|\bcaption(ed)?\s+line\b|\bcaption\s*:", re.I)
_ON_SCREEN = re.compile(r"<!--\s*on-screen:\s*(.*?)\s*-->")
_HMS = re.compile(r"(\d+):(\d\d):(\d\d)")
from video_capture import _XREF  # one definition: what review-prep re-reads is what this counts
_WORD = re.compile(r"[a-z0-9']+")
MIN_TOKENS = 15


def _sections(md: str) -> list[tuple[str, str]]:
    """(heading line, body) for every level-2 section; deeper headings stay in the body."""
    out, head, body = [], None, []
    for ln in md.splitlines():
        if ln.startswith("## "):
            if head is not None:
                out.append((head, "\n".join(body)))
            head, body = ln, []
        elif head is not None:
            body.append(ln)
    if head is not None:
        out.append((head, "\n".join(body)))
    return out


def _secs(hms: str) -> int:
    h, m, s = _HMS.search(hms).groups()
    return int(h) * 3600 + int(m) * 60 + int(s)


def frame_sections(md: str) -> list[dict]:
    """Frame sections with their body (markers stripped) and on-screen windows in seconds."""
    out = []
    for head, body in _sections(md):
        m = _FRAME_H.match(head)
        if m:
            on = _ON_SCREEN.search(body)
            windows = [tuple(_secs(t) for t in w.split("–")) for w in on.group(1).split(", ")] if on else []
            out.append({"page": int(m.group(1)), "heading": head, "windows": windows,
                        "body": _COMMENT.sub("", body).strip()})
    return out


def cue_entries(md: str) -> list[tuple[int, str]]:
    """(start second, text) for every transcript section."""
    return [(_secs(head), _COMMENT.sub("", body).strip()) for head, body in _sections(md) if _CUE_H.match(head)]


def cue_texts(md: str) -> list[str]:
    return [t for _, t in cue_entries(md)]


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def redundant_pairs(frames: list[dict], threshold: float = 0.75, min_tokens: int = MIN_TOKENS) -> list:
    toks = [(f["page"], tokens(f["body"])) for f in frames]
    toks = [(p, t) for p, t in toks if len(t) >= min_tokens]
    out = []
    for i, (pa, ta) in enumerate(toks):
        for pb, tb in toks[i + 1:]:
            j = jaccard(ta, tb)
            if j >= threshold:
                out.append((pa, pb, round(j, 3)))
    return out


def confirmed_distinct(pairs: list, pages_doc: dict) -> list:
    """The redundant pairs a blind reader looked at and kept (an `adds` verdict on either frame):
    similar text, but the images differ — kept on purpose, not a missed duplicate."""
    adds = {p["page"] for p in pages_doc["pages"]
            if any(isinstance(r, dict) and r.get("verdict") == "adds" for r in
                   (p.get("review") if isinstance(p.get("review"), list) else [p.get("review")]))}
    return [t for t in pairs if t[0] in adds or t[1] in adds]


def _ngrams(words: list[str], n: int) -> set[tuple]:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def caption_leaks(frames: list[dict], cues: list[tuple[int, str]], n: int = 6,
                  near: int = 30) -> list[tuple[int, str]]:
    """Frame lines that describe a caption overlay, or repeat >= n consecutive words spoken
    while the frame was on screen (± `near` s). A slide read aloud later is not a leak."""
    out = []
    for f in frames:
        said = set()
        for t, c in cues:
            if any(s - near <= t <= e + near for s, e in f.get("windows", [])):
                said |= _ngrams(_WORD.findall(c.lower()), n)
        for ln in f["body"].splitlines():
            if not ln.strip():
                continue
            if _CAPTION.search(ln) or _ngrams(_WORD.findall(ln.lower()), n) & said:
                out.append((f["page"], ln.strip()))
    return out


def cross_frame_refs(frames: list[dict]) -> list[tuple[int, str]]:
    return [(f["page"], ln.strip()) for f in frames for ln in f["body"].splitlines() if _XREF.search(ln)]


def ui_leaks(frames: list[dict], ui_tokens: list[str]) -> list[tuple[int, str]]:
    return [(f["page"], ln.strip()) for f in frames for ln in f["body"].splitlines()
            if any(t in ln for t in ui_tokens)]


def golden_check(md: str, golden: dict) -> dict:
    """Exact-string checks. An item with a `frame` key must appear in frame text, not just the
    transcript — a spoken ID must not mask a misread on screen."""
    frames_text = "\n".join(f["body"] for f in frame_sections(md))
    must = golden.get("must_contain", [])
    missing = [g["id"] for g in must if g["text"] not in (frames_text if g.get("frame") else md)]
    forbidden = [g["id"] for g in golden.get("must_not_contain", []) if g["text"] in md]
    return {"contain_hits": len(must) - len(missing), "contain_total": len(must),
            "missing": missing, "forbidden_present": forbidden}


def marker_only_chunks(md: str, max_chars: int = 1200) -> int:
    import chunking
    recs = chunking.section_records(chunking.strip_preamble(md), max_chars)
    return sum(1 for r in recs if "(frame p" in r["title"] and not _COMMENT.sub("", r["body"]).strip())


def live_pages(pages_doc: dict) -> list[int]:
    return [p["page"] for p in pages_doc["pages"] if not p.get("dropped")]


def frame_parity(md: str, pages_doc: dict) -> bool:
    return [f["page"] for f in frame_sections(md)] == live_pages(pages_doc)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def cue_coverage(md: str, cues: list[dict]) -> tuple[int, int]:
    text = _norm(" ".join(cue_texts(md)))
    return sum(1 for c in cues if _norm(c["text"]) in text), len(cues)


def retention(pages_doc: dict, vlm: dict) -> list[dict]:
    sha = {p["page"]: p.get("img_sha") for p in pages_doc["pages"]}
    out = []
    for p in pages_doc["pages"]:
        if p.get("dropped") != "duplicate":
            continue
        dup, keep = tokens(vlm.get(p.get("img_sha"), "")), tokens(vlm.get(sha.get(p.get("duplicate_of")), ""))
        out.append({"page": p["page"], "duplicate_of": p.get("duplicate_of"),
                    "containment": round(len(dup & keep) / len(dup), 3) if dup else None})
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", required=True, help="assembled video-lane Markdown")
    ap.add_argument("--pages", required=True, help="the render dir's pages.json")
    ap.add_argument("--sidecar", help="transcript file the doc was assembled from (.vtt/.srt/.docx)")
    ap.add_argument("--golden", help="JSON {must_contain:[{id,text}], must_not_contain:[{id,text}], ui_tokens:[...]}")
    ap.add_argument("--results", help="VLM result dir (result_*.json) — enables duplicate retention")
    ap.add_argument("--ui-token", action="append", default=[], help="meeting-app text that must not be transcribed")
    ap.add_argument("--jaccard", type=float, default=0.75, help="redundant-pair threshold (default 0.75)")
    ap.add_argument("--out", required=True, help="metrics JSON path")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    md = Path(a.doc).read_text(encoding="utf-8")
    pages_doc = json.loads(Path(a.pages).read_text())
    golden = json.loads(Path(a.golden).read_text()) if a.golden else {}
    frames = frame_sections(md)
    cues = cue_entries(md)
    ui = a.ui_token + golden.get("ui_tokens", [])
    m = {
        "doc": a.doc,
        "frames": len(frames),
        "live_pages": len(live_pages(pages_doc)),
        "dropped": {why: sum(1 for p in pages_doc["pages"] if p.get("dropped") == why)
                    for why in sorted({p["dropped"] for p in pages_doc["pages"] if p.get("dropped")})},
        "frame_parity": frame_parity(md, pages_doc),
        "redundant_pairs": redundant_pairs(frames, a.jaccard),
        "redundant_confirmed_distinct": confirmed_distinct(redundant_pairs(frames, a.jaccard), pages_doc),
        "caption_leaks": caption_leaks(frames, cues),
        "cross_frame_refs": cross_frame_refs(frames),
        "ui_leaks": ui_leaks(frames, ui),
        "marker_only_chunks": marker_only_chunks(md),
    }
    if a.sidecar:
        from video_capture import read_cues
        m["cue_coverage"] = list(cue_coverage(md, read_cues(a.sidecar)))
    if a.golden:
        m["golden"] = golden_check(md, golden)
    if a.results:
        from vision_assemble import load_results
        m["retention"] = retention(pages_doc, load_results(a.results))
    Path(a.out).write_text(json.dumps(m, indent=2) + "\n")
    g = m.get("golden", {})
    print(f"{a.doc}: {m['frames']} frame(s), parity={m['frame_parity']}, "
          f"redundant={len(m['redundant_pairs'])} ({len(m['redundant_confirmed_distinct'])} kept by review), captions={len(m['caption_leaks'])}, "
          f"xrefs={len(m['cross_frame_refs'])}, ui={len(m['ui_leaks'])}, "
          f"marker_only={m['marker_only_chunks']}"
          + (f", golden={g['contain_hits']}/{g['contain_total']} forbidden={len(g['forbidden_present'])}" if g else "")
          + f" -> {a.out}")
    failed = not m["frame_parity"] or bool(g.get("missing")) or bool(g.get("forbidden_present"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
