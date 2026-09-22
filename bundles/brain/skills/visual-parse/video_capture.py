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
