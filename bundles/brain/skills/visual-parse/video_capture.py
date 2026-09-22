#!/usr/bin/env python3
"""Meeting recordings → the visual lane: key frames + transcript → ONE parsed doc.

A second producer of the visual-lane artifact layout (like html_capture.py):
  probe       ffprobe + pick the transcript source (same-stem .vtt/.srt sidecar wins;
              whisper.cpp only when none exists)                 -> <work>/<slug>/probe.json
  transcribe  whisper.cpp -> <work>/<slug>/transcript.vtt          (only when probe says asr)
  frames      stable-span detection + dedup -> <assets>/<slug>/pNN.png, pNN.txt, pages.json
  (vision_prep.py -> vision subagents -> result_<k>.json, exactly as for decks; the VLM
   answers "<!-- no-content -->" for people-only frames)
  assemble    transcript turns + kept frames, time-ordered -> <parsed>/<doc>.md + manifest
  forget      remove a deleted recording's parsed doc, manifest entries and assets

Deterministic, no LLM. System tools: ffmpeg/ffprobe, whisper-cli. No environment
variables: the whisper model comes from --model or brain.toml [video].whisper_model.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_pages import doc_slug  # noqa: E402  (same skill directory)
from vision_assemble import demote, load_cache, load_results, title_of  # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
SIDECAR_EXT = (".vtt", ".srt")
NO_CONTENT = "<!-- no-content -->"
LOW_W, LOW_H = 96, 54
WHISPER_BINS = ("whisper-cli", "whisper-cpp")
DOCTOR_HINT = "run knowledge-pipeline/brain_doctor.py for install steps"

# ---- transcript cues --------------------------------------------------------

_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def ts_seconds(s: str) -> float:
    m = _TS.search(s)
    if not m:
        raise ValueError(f"bad cue timestamp: {s!r}")
    h, mi, se, ms = m.groups()
    total_ms = int(h or 0) * 3600000 + int(mi) * 60000 + int(se) * 1000 + int(ms.ljust(3, "0"))
    return total_ms / 1000


def fmt_hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


# Copied verbatim from parse_corpus._NON_SPEAKER_PREFIXES (pinned by test).
NON_SPEAKER_PREFIXES = frozenset({
    "note", "notes", "today", "tomorrow", "yesterday", "ok", "okay", "yes", "no",
    "so", "well", "actually", "right", "first", "second", "third", "next", "then",
    "step", "warning", "error", "caution", "important", "update", "summary",
    "question", "answer", "action", "agenda", "topic", "example", "tip", "re",
    "subject", "from", "to", "date", "time", "edit", "ps", "aside", "recap",
})


def speaker_and_text(text: str) -> tuple[str, str]:
    """Extract WebVTT voice tags and conservative ``Name: text`` prefixes.

    The ``Name:`` fallback (used by SRT and by VTT cues without <v> tags) only
    fires for name-shaped prefixes: one to three capitalised words, letters and
    name punctuation only, and whose lead word is not a common sentence-opening
    word (see ``NON_SPEAKER_PREFIXES``).
    """
    voice = re.match(r"\s*<v(?:\.[^ >]+)*\s+([^>]+)>\s*(.*)", text, flags=re.I | re.S)
    if voice:
        return voice.group(1).strip(), re.sub(r"</?v[^>]*>", "", voice.group(2)).strip()
    labelled = re.match(
        r"\s*([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,2}):\s+(.+)", text, flags=re.S)
    if labelled:
        name = labelled.group(1).strip()
        lead = name.split()[0].lower().strip(".'’-")
        if lead not in NON_SPEAKER_PREFIXES:
            return name, labelled.group(2).strip()
    return "", re.sub(r"</?v[^>]*>", "", text).strip()

def read_cues(path) -> list[dict]:
    """WebVTT or SRT -> [{start, end, speaker, text}] with times in SECONDS."""
    text = Path(path).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    text = re.sub(r"^﻿?WEBVTT[^\n]*\n", "", text)
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        rows = [r.strip() for r in block.splitlines() if r.strip()]
        i = next((k for k, r in enumerate(rows) if "-->" in r), None)
        if i is None:
            continue  # NOTE / STYLE / REGION blocks
        start_s, end_s = rows[i].split("-->", 1)
        body = " ".join(rows[i + 1:]).strip()
        if not body:
            continue
        speaker, body = speaker_and_text(body)
        body = re.sub(r"<[^>]+>", "", body).strip()
        if body:
            cues.append({"start": ts_seconds(start_s), "end": ts_seconds(end_s.split()[0]),
                         "speaker": speaker, "text": body})
    return cues


def merge_turns(cues: list[dict], max_cues: int = 10) -> list[dict]:
    """Join consecutive same-speaker cues (≤ max_cues each) — parse_corpus --merge-cues semantics."""
    turns: list[dict] = []
    for c in cues:
        t = turns[-1] if turns else None
        if t and t["speaker"] == c["speaker"] and t["n"] < max_cues:
            t["text"] += " " + c["text"]; t["end"] = c["end"]; t["n"] += 1
        else:
            turns.append({**c, "n": 1})
    return turns


# ---- frame selection (pure; frames = one 2-D float array in [0,1] per second) ----


def mad(a, b) -> float:
    import numpy as np
    return float(np.abs(a - b).mean())


def dhash(frame) -> int:
    """64-bit difference hash over an 8x9 grid of block means."""
    import numpy as np
    rows = [r.mean(axis=0) for r in np.array_split(frame, 8, axis=0)]
    grid = np.array([[c.mean() for c in np.array_split(row, 9)] for row in rows])
    bits = (grid[:, 1:] > grid[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def find_spans(frames, diff=0.08, still=0.015, min_hold=3) -> list[dict]:
    """Cut where a frame differs from the span's ANCHOR (first) frame by > diff, so slow
    scrolls/fades accumulate into a cut. Keep spans ≥ min_hold s whose median
    frame-to-frame MAD is < still (a slide is ~0; camera noise is not)."""
    import numpy as np
    spans: list[dict] = []

    def close(s, e):
        if e - s < min_hold:
            return
        steps = [mad(frames[i], frames[i - 1]) for i in range(s + 1, e)]
        if steps and float(np.median(steps)) >= still:
            return
        spans.append({"t_start": s, "t_end": e, "key": e - 1})

    start = 0
    for i in range(1, len(frames)):
        if mad(frames[i], frames[start]) > diff:
            close(start, i)
            start = i
    if len(frames):
        close(start, len(frames))
    return spans


def collapse_builds(spans, frames, build_hamming=10, build_mad=0.16, gap=1) -> list[dict]:
    """Adjacent spans that look like one slide being built up keep only the later frame.
    Both gates are needed: low-texture frames (solid colours, SMPTE bars) have near-empty
    dHashes, so Hamming alone would merge unrelated slides; a real build changes little area."""
    out: list[dict] = []
    for s in spans:
        p = out[-1] if out else None
        if p and s["t_start"] - p["t_end"] <= gap and \
                hamming(dhash(frames[p["key"]]), dhash(frames[s["key"]])) <= build_hamming and \
                mad(frames[p["key"]], frames[s["key"]]) <= build_mad:
            out[-1] = {"t_start": p["t_start"], "t_end": s["t_end"], "key": s["key"]}
        else:
            out.append(dict(s))
    return out


def dedup(spans, frames, max_hamming=6, same_mad=0.02) -> list[dict]:
    """A frame shown again later is stored once; every showing lands in shown_at."""
    kept: list[dict] = []
    for s in spans:
        k = frames[s["key"]]
        h = dhash(k)
        for q in kept:
            if hamming(q["hash"], h) <= max_hamming and mad(frames[q["key"]], k) <= same_mad:
                q["shown_at"].append([s["t_start"], s["t_end"]])
                break
        else:
            kept.append({**s, "hash": h, "shown_at": [[s["t_start"], s["t_end"]]]})
    return kept


def _hold(q) -> int:
    return sum(e - s for s, e in q["shown_at"])


def cap(kept, max_per_min=6, max_frames=300):
    by_min: dict[int, list] = {}
    for q in kept:
        by_min.setdefault(q["t_start"] // 60, []).append(q)
    out = []
    for group in by_min.values():
        out += sorted(group, key=_hold, reverse=True)[:max_per_min]
    capped = len(out) > max_frames
    if capped:
        out = sorted(out, key=_hold, reverse=True)[:max_frames]
    out.sort(key=lambda q: q["t_start"])
    return out, len(kept) - len(out), capped


def select_frames(frames, diff=0.08, still=0.015, min_hold=3, max_per_min=6, max_frames=300):
    spans = collapse_builds(find_spans(frames, diff, still, min_hold), frames)
    kept, _dropped, capped = cap(dedup(spans, frames), max_per_min, max_frames)
    return kept, capped
