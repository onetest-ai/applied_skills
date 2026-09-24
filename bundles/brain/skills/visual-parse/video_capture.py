#!/usr/bin/env python3
"""Meeting recordings → the visual lane: key frames + transcript → ONE parsed doc.

The third producer of the visual-lane artifact layout (after render_pages.py and
html_capture.py):
  probe       ffprobe + pick the transcript source (--transcript-file, else a same-stem
              .vtt/.srt/.docx, else a Teams .docx titled with the recording name wins;
              whisper.cpp only when none exists)                 -> <work>/<slug>/probe.json
  transcribe  whisper.cpp -> <work>/<slug>/transcript.vtt          (only when probe says asr)
  frames      stable-span detection + dedup -> <assets>/<slug>/pNN.png, pNN.txt, pages.json
  (vision_prep.py -> vision subagents -> result_<k>.json, exactly as for decks; the VLM
   answers "<!-- no-content -->" for people-only frames)
  review-prep frames the text dedup would drop + frames that refer to another frame -> one blind reader per item
  assemble    transcript turns + kept frames, time-ordered -> <parsed>/<doc>.md + manifest
  forget      remove a deleted recording's parsed doc, manifest entries and assets

<slug> is video_slug(): render_pages.doc_slug of the source-relative path plus
`--<ext>` (m/standup.mp4 -> m__standup--mp4), so a recording never shares an asset dir
with a same-stem deck (m/standup.pptx -> m__standup). probe and frames print it.

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
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_pages import doc_slug  # noqa: E402  (same skill directory)
from vision_assemble import load_cache, persist_page_render, title_of  # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
SIDECAR_EXT = (".vtt", ".srt", ".docx")  # priority order for the same-stem rule
NO_CONTENT = "<!-- no-content -->"
_NO_CONTENT_RE = re.compile(r"<!--\s*no-content\s*-->", re.I)
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

# ---- Teams .docx transcripts (stdlib only: zipfile + ElementTree) ----------------

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_TS = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_DOCX_TURN = re.compile(r"^(.+?)\s{2,}(\d{1,2}:\d{2}(?::\d{2})?)(.*)$", re.S)
_DOCX_EVENTS = ("started transcription", "stopped transcription")
_DOCX_DURATION = re.compile(r"^\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?\s*$")


def _docx_paragraphs(path) -> list[list[str]]:
    """word/document.xml body paragraphs -> per paragraph, the text of each run that
    has <w:t> children. Only <w:t> directly inside a <w:r> is read, so the avatar
    <w:drawing> (its <wp:posOffset> digits etc.) never enters. <w:br/>/<w:tab/> -> " "."""
    import xml.etree.ElementTree as ET
    import zipfile
    import zlib
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, EOFError, zlib.error) as e:
        # FileNotFoundError is NOT caught: a missing file is a different finding.
        raise ValueError(f"cannot read transcript {path}: not a readable Word file "
                         "(truncated download?)") from e
    paras = []
    for p in root.iter(_W_NS + "p"):
        runs = []
        for r in p.findall(_W_NS + "r"):
            if r.find(_W_NS + "t") is None:
                continue
            parts = []
            for child in r:
                if child.tag == _W_NS + "t":
                    parts.append(child.text or "")
                elif child.tag in (_W_NS + "br", _W_NS + "tab"):
                    parts.append(" ")
            runs.append("".join(parts))
        paras.append(runs)
    return paras


def _clock_seconds(ts: str) -> float:
    parts = [int(x) for x in ts.split(":")]
    h, m, s = ([0] + parts)[-3:]
    return float(h * 3600 + m * 60 + s)


def _duration_seconds(line: str) -> float | None:
    m = _DOCX_DURATION.match(line)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g or 0) for g in m.groups())
    return float(h * 3600 + mi * 60 + s)


def read_teams_docx(path) -> list[dict]:
    """A Teams transcript .docx -> [{start, end, speaker, text}] (seconds).

    Body: title, date, duration, then "<Name> started transcription" events and one
    paragraph per turn: run[0] speaker, run[1] "M:SS"/"H:MM:SS", runs[2:] text. Teams
    gives start times only: a turn ends where the next begins; the last ends at the
    header duration (else at its own start). Raises ValueError on an unreadable file."""
    paras = [runs for runs in _docx_paragraphs(path) if "".join(runs).strip()]
    duration = None
    if len(paras) >= 3:
        duration = _duration_seconds("".join(paras[2]))
        if duration is not None:
            paras = paras[3:]  # title, date, duration
    turns = []
    for runs in paras:
        joined = "".join(runs)
        # The timestamp is normally run[1]; Word may split the speaker over several runs,
        # so take the first run (index >= 1) that is exactly a timestamp.
        i = next((k for k in range(1, len(runs)) if _DOCX_TS.match(runs[k].strip())), None)
        if joined.strip().lower().endswith(_DOCX_EVENTS) and i is None:
            continue
        if i is not None:
            speaker, ts = "".join(runs[:i]).strip(), runs[i].strip()
            text = " ".join(t.strip() for t in runs[i + 1:] if t.strip())
        else:
            m = _DOCX_TURN.match(joined.strip())
            if not m:
                continue
            speaker, ts, text = m.group(1).strip(), m.group(2), m.group(3).strip()
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            turns.append({"start": _clock_seconds(ts), "end": None, "speaker": speaker, "text": text})
    for a, b in zip(turns, turns[1:]):
        a["end"] = b["start"]
    if turns:
        last = turns[-1]
        last["end"] = duration if duration is not None and duration >= last["start"] else last["start"]
    return turns


def docx_title(path) -> str | None:
    """The first non-empty paragraph (run text only), stripped; None when unreadable."""
    try:
        paras = _docx_paragraphs(path)
    except (ValueError, OSError):
        return None
    for runs in paras:
        t = "".join(runs).strip()
        if t:
            return t
    return None


def read_cues(path) -> list[dict]:
    """WebVTT, SRT or a Teams .docx -> [{start, end, speaker, text}] with times in SECONDS."""
    if str(path).lower().endswith(".docx"):
        return read_teams_docx(path)
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


# ---- IO helpers ---------------------------------------------------------------


def video_slug(rel: str) -> str:
    """Asset/work slug for a recording: doc_slug(rel) + "--" + the lowercased extension.
    doc_slug drops the extension, so m/standup.mp4 and m/standup.pptx would share
    assets/m__standup/ and `frames`/`forget` would destroy the deck's render."""
    ext = os.path.splitext(rel)[1].lstrip(".").lower()
    return doc_slug(rel) + (f"--{ext}" if ext else "")


def is_no_content(reply: str) -> bool:
    """The VLM's people-only verdict, tolerant of wrapping: after stripping whitespace
    and surrounding backticks/quotes, the reply is exactly `<!-- no-content -->`
    (case-insensitive, inner spaces allowed). Anything else is content."""
    t = reply.strip()
    while True:
        u = t.strip().strip("`'\"").strip()
        if u == t:
            break
        t = u
    return bool(_NO_CONTENT_RE.fullmatch(t))


def is_video_render_dir(d: str) -> bool:
    """True when `d` has no pages.json or its pages.json is a video render (medium video)."""
    pj = os.path.join(d, "pages.json")
    if not os.path.exists(pj):
        return True
    try:
        return json.loads(Path(pj).read_text()).get("medium") == "video"
    except (json.JSONDecodeError, OSError, AttributeError):
        return False

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def run_quiet(cmd: list, name: str, subject: str) -> bytes:
    """Run a system tool with stdout/stderr captured, so its chatter (whisper-cli prints
    ggml/Metal init and echoes the whole transcript) never floods the agent's terminal.
    Returns stdout (bytes). A non-zero exit dies with the tool name, what it was working
    on and the last 20 lines of its stderr — never a traceback."""
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        tail = "\n".join(r.stderr.decode("utf-8", "replace").strip().splitlines()[-20:])
        die(f"{name} failed (exit {r.returncode}) on {subject}" + (f":\n{tail}" if tail else ""))
    return r.stdout


def need_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        die(f"{name} not found — {DOCTOR_HINT}", 3)
    return path


def sha_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic_write(path, text: str) -> None:
    path = str(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def source_rel(video: str, rel_to: str | None) -> str:
    if rel_to:
        rp = os.path.relpath(os.path.abspath(video), os.path.abspath(rel_to))
        if not rp.startswith(".."):
            return rp.replace(os.sep, "/")
    return os.path.basename(video)


def _is_teams_transcript(path) -> bool:
    """A readable .docx that yields at least one transcript turn."""
    try:
        return bool(read_teams_docx(path))
    except (ValueError, OSError):
        return False


def rel_under(path: str, root: str) -> str | None:
    """`path` relative to `root` (``/``-separated) when it lies under it, else None —
    never source_rel's basename fallback."""
    try:
        rp = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:  # different drives (Windows)
        return None
    if rp == os.curdir or rp == os.pardir or rp.startswith(os.pardir + os.sep) or os.path.isabs(rp):
        return None
    return rp.replace(os.sep, "/")


def find_sidecar(video: str) -> str | None:
    """The recording's transcript, by the first rule that matches:
    1. same stem, extension case-insensitive, priority .vtt, .srt, .docx (real spelling);
       a same-stem .docx counts only if it reads as a Teams transcript (>= 1 turn) — a
       same-name agenda or an unreadable file is skipped (probe warns about the latter);
    2. a .docx in the same directory whose first paragraph (docx_title) is exactly the
       video's stem — Teams names the transcript after the meeting, not the recording.
    More than one .docx claiming the video by title -> ValueError (no guessing)."""
    d, base = os.path.split(os.path.abspath(video))
    stem = os.path.splitext(base)[0]
    names = sorted(os.listdir(d))
    for ext in SIDECAR_EXT:
        for n in names:
            if os.path.splitext(n)[0] == stem and os.path.splitext(n)[1].lower() == ext:
                if ext == ".docx" and not _is_teams_transcript(os.path.join(d, n)):
                    continue  # same-name notes/agenda, or unreadable: not a transcript
                return os.path.join(d, n)
    claims = [n for n in names if os.path.splitext(n)[1].lower() == ".docx"
              and not n.startswith((".", "~$")) and docx_title(os.path.join(d, n)) == stem]
    if len(claims) > 1:
        raise ValueError(f"{len(claims)} .docx files claim {base} as their recording by title: "
                         f"{', '.join(claims)} — pick one with --transcript-file")
    return os.path.join(d, claims[0]) if claims else None


def unreadable_docx(d: str) -> list[str]:
    """Names of .docx files in `d` that cannot be read as Word files."""
    out = []
    for n in sorted(os.listdir(d)):
        if os.path.splitext(n)[1].lower() == ".docx" and not n.startswith((".", "~$")):
            try:
                _docx_paragraphs(os.path.join(d, n))
            except (ValueError, OSError):
                out.append(n)
    return out


def choose_transcript(has_audio: bool, sidecar: str | None, override: str) -> str:
    if override == "sidecar" and not sidecar:
        raise ValueError("--transcript sidecar but no transcript sidecar exists (same-stem "
                         ".vtt/.srt/.docx, or a Teams .docx titled with the recording name)")
    if override == "asr" and not has_audio:
        raise ValueError("--transcript asr but the video has no audio stream")
    if override != "auto":
        return override
    return "sidecar" if sidecar else ("asr" if has_audio else "none")


def ffprobe(video: str) -> tuple[float, bool]:
    exe = need_tool("ffprobe")
    r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                        "-of", "json", video], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffprobe cannot read {video}: {r.stderr.strip()[:300]}")
    d = json.loads(r.stdout)
    return float(d["format"]["duration"]), any(s.get("codec_type") == "audio" for s in d.get("streams", []))


def read_low_frames(video: str):
    import numpy as np
    exe = need_tool("ffmpeg")
    raw = run_quiet([exe, "-v", "error", "-i", video, "-vf",
                     f"fps=1,scale={LOW_W}:{LOW_H},format=gray", "-f", "rawvideo", "-"], "ffmpeg", video)
    return np.frombuffer(raw, np.uint8).reshape(-1, LOW_H, LOW_W).astype(np.float32) / 255.0


def extract_frame(video: str, t: float, png: str) -> None:
    run_quiet([need_tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video,
               "-frames:v", "1", "-vf", "scale='min(1920,iw)':-2", png], "ffmpeg", video)


def swap_dir(new: str, target: str) -> None:
    """Replace directory `target` with `new` by rename; the old one is removed only
    after the new one is in place (restored if that rename fails)."""
    if not os.path.exists(target):
        os.rename(new, target)
        return
    parent = os.path.dirname(os.path.abspath(target))
    old = tempfile.mkdtemp(dir=parent, prefix=f".{os.path.basename(target)}.old-")
    os.rmdir(old)
    os.rename(target, old)
    try:
        os.rename(new, target)
    except OSError:
        os.rename(old, target)
        raise
    shutil.rmtree(old, ignore_errors=True)


def upsert_manifest(path, entries: list[dict]) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8")) if os.path.exists(path) else []
    srcs = {e["source"] for e in entries}
    data = [e for e in data if e.get("source") not in srcs] + entries
    atomic_write(path, json.dumps(data, indent=2) + "\n")


# ---- probe / frames -----------------------------------------------------------


def cmd_probe(a) -> int:
    video = os.path.abspath(a.video)
    rel = source_rel(a.video, a.rel_to)
    slug = video_slug(rel)
    try:
        duration, has_audio = ffprobe(video)
    except RuntimeError as e:
        if a.manifest:
            upsert_manifest(a.manifest, [{"source": rel, "error": str(e)}])
        die(str(e))
    warnings = []
    if a.transcript_file:
        if a.transcript in ("asr", "none"):
            die(f"--transcript-file and --transcript {a.transcript} are mutually exclusive — "
                "an explicit transcript file IS the transcript source")
        sidecar = os.path.abspath(a.transcript_file)
        if os.path.splitext(sidecar)[1].lower() not in SIDECAR_EXT:
            die(f"--transcript-file must be a .vtt, .srt or .docx transcript: {sidecar}")
        if not os.path.isfile(sidecar):
            die(f"--transcript-file not found: {sidecar}")
        transcript = "sidecar"
    else:
        try:
            sidecar = find_sidecar(video)
        except ValueError as e:
            if a.transcript not in ("asr", "none"):
                die(str(e))
            sidecar = None  # the override does not use a sidecar, so the ambiguity is moot
        try:
            transcript = choose_transcript(has_audio, sidecar, a.transcript)
        except ValueError as e:
            die(str(e))
    warnings += [f"could not read {n} (truncated download?) — it was not considered as a "
                 "transcript" for n in unreadable_docx(os.path.dirname(video))]
    sidecar_source = None
    if transcript == "sidecar":
        try:
            cues = read_cues(sidecar)
        except ValueError as e:
            die(f"{e} — fix or re-download it, or override with --transcript-file or "
                "--transcript asr|none")
        except OSError as e:
            die(f"cannot read transcript {sidecar}: {e.strerror or e}")
        # Only a transcript under the source root may be consumed (and its parsed doc
        # retired): source_rel's basename fallback would name an unrelated root file.
        root = (a.rel_to if a.transcript_file else
                (a.rel_to if a.rel_to and rel_under(video, a.rel_to) else os.path.dirname(video)))
        sidecar_source = rel_under(sidecar, root) if root else None
        if sidecar_source is None:
            warnings.append("transcript file is outside the source root — it will be used but not "
                            "retired from the index")
        if not cues:
            die(f"sidecar-empty: {sidecar} has no cues — fix it, or override with "
                "--transcript asr|none")
        if cues[-1]["end"] > duration + 5:
            warnings.append(f"sidecar is longer than the video ({fmt_hms(cues[-1]['end'])} > "
                            f"{fmt_hms(duration)}) — likely a wrong pairing")
    probe = {"source": rel, "video": video, "slug": slug, "duration": duration, "has_audio": has_audio,
             "sidecar": sidecar if transcript == "sidecar" else None,
             "sidecar_source": sidecar_source,
             "transcript": transcript, "warnings": warnings}
    out = os.path.join(a.work, slug, "probe.json")
    atomic_write(out, json.dumps(probe, indent=2) + "\n")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"{rel}: {fmt_hms(duration)}, transcript={transcript}, slug={slug} -> {out}")
    return 0


def frames_key(video: str, params: dict) -> str:
    return hashlib.sha256((sha_file(video) + json.dumps(params, sort_keys=True)).encode()).hexdigest()


def cmd_frames(a) -> int:
    video = os.path.abspath(a.video)
    rel = source_rel(a.video, a.rel_to)
    slug = video_slug(rel)
    outdir = os.path.join(a.assets_root, slug)
    if not is_video_render_dir(outdir):
        die(f"{os.path.join(outdir, 'pages.json')} is not a video render (medium != video) — "
            "refusing to overwrite another document's pages")
    params = {"diff": a.diff, "still": a.still, "min_hold": a.min_hold,
              "max_per_min": a.max_per_min, "max_frames": a.max_frames}
    key = frames_key(video, params)
    pj = os.path.join(outdir, "pages.json")
    if os.path.exists(pj) and json.loads(Path(pj).read_text()).get("frames_key") == key:
        print(f"{rel}: frames unchanged (cache hit), slug={slug} -> {outdir}")
        return 0
    # Render into a sibling temp dir and swap it in whole: pages.json and its PNGs
    # change together, and a failure part-way leaves the previous render untouched.
    parent = os.path.dirname(os.path.abspath(outdir))
    os.makedirs(parent, exist_ok=True)
    work = tempfile.mkdtemp(dir=parent, prefix=f".{slug}.tmp-")
    try:
        duration, _ = ffprobe(video)
        kept, capped = select_frames(read_low_frames(video), **params)
        pages = []
        for n, q in enumerate(kept, 1):
            png = os.path.join(work, f"p{n:02d}.png")
            extract_frame(video, min(q["key"] + 0.5, max(duration - 0.1, 0.0)), png)
            open(os.path.join(work, f"p{n:02d}.txt"), "w").close()
            pages.append({"page": n, "image": f"{slug}/p{n:02d}.png", "img_sha": sha_file(png),
                          "text_len": 0, "n_drawings": 0, "n_tables": 0, "img_cover": 0.0,
                          "flagged": True, "why": "video-frame",
                          "t_start": q["t_start"], "t_end": q["t_end"], "shown_at": q["shown_at"]})
        doc = {"doc": os.path.basename(video), "slug": slug, "dpi": None, "medium": "video",
               "duration": duration, "frames_key": key, "frames_capped": capped, "pages": pages}
        atomic_write(os.path.join(work, "pages.json"), json.dumps(doc, indent=2) + "\n")
        swap_dir(work, outdir)
    except BaseException:  # includes die()'s SystemExit
        shutil.rmtree(work, ignore_errors=True)
        raise
    print(f"{rel}: {len(pages)} key frame(s), slug={slug} -> {outdir}" + ("  [capped at --max-frames]" if capped else ""))
    return 0


# ---- assemble / forget ----------------------------------------------------------


def doc_name(rel: str) -> str:
    return rel.replace("/", "__") + ".md"


def method_for(probe: dict, work_dir: str) -> str:
    t = probe["transcript"]
    if t == "asr":
        asr = json.loads(Path(work_dir, "asr.json").read_text())
        stem = os.path.splitext(asr["model"])[0]
        return f"video-lane (transcript: asr:whisper.cpp:{stem})"
    return f"video-lane (transcript: {t})"


def flatten_headings(md: str) -> str:
    """No line of a frame may open a new chunk: `chunking.section_records` treats every
    column-0 `#` line as a heading (it is not fence-aware), which would leave the
    `(frame pNN)` chunk with only its markers, or — for a `# comment` in screen-shared code —
    open a live H1 that re-roots the breadcrumb of every later section. VLM headings become
    bold lines; `#` lines inside a ``` or ~~~ fence (or an unclosed one) are indented by one
    space, which keeps the code readable and stops them matching as headings."""
    out, fence = [], None
    for ln in md.splitlines():
        marker = ln.lstrip()[:3]
        if marker in ("```", "~~~") and (fence is None or marker == fence):
            fence = None if fence else marker
            out.append(ln)
            continue
        if re.match(r"^#{1,6}(\s|$)", ln):
            m = re.match(r"^#{1,6}\s+(.*\S)\s*$", ln)
            out.append(" " + ln if fence else (f"**{m.group(1)}**" if m else ""))
            continue
        out.append(ln)
    return "\n".join(out)


def render_doc(source: str, method: str, slug: str, turns: list[dict], frames: list[tuple]) -> str:
    items = [(t["start"], 0, t) for t in turns] + [(p["t_start"], 1, (p, md)) for p, md in frames]
    items.sort(key=lambda x: (x[0], x[1]))
    out = [f"# SOURCE: {source}\n# method: {method}\n# fidelity: full\n"]
    seq = 0
    for _t, kind, obj in items:
        if kind == 0:
            seq += 1
            sp = obj["speaker"]
            who = f" — {sp}" if sp else ""
            marker = f"<!-- speaker: {sp} -->\n\n" if sp else ""
            out.append(f"## {fmt_hms(obj['start'])}{who} (cue {seq})\n\n{marker}{obj['text']}\n")
        else:
            p, md = obj
            n = p["page"]
            shown = ", ".join(f"{fmt_hms(s)}–{fmt_hms(e)}" for s, e in p["shown_at"])
            out.append(f"## {fmt_hms(p['t_start'])} · {title_of(md, n)} (frame p{n:02d})\n\n"
                       f"<!-- image: {slug}/p{n:02d}.png -->\n<!-- on-screen: {shown} -->\n\n"
                       f"{flatten_headings(md.strip())}\n")
    return "\n".join(out)


def content_tokens(md: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", md.lower()))


def dedup_content(kept, jaccard=0.8, min_tokens=15):
    """Second dedup round, on meaning: a frame whose transcription shares >= `jaccard` of its
    words with an EARLIER kept frame is a duplicate of it (a scrolled email, a slide shown
    again with a different zoom — cases the 96x54 pixel round cannot tell from a new slide).
    Marks each duplicate's page dict `dropped: "duplicate"`, `duplicate_of: N` and returns
    (kept_without_duplicates, duplicate_pages). Transcriptions under `min_tokens` words are
    never merged; `jaccard <= 0` disables the round."""
    if jaccard <= 0:
        return list(kept), []
    live, dups, seen = [], [], []
    for p, md in kept:
        t = content_tokens(md)
        match = None
        if len(t) >= min_tokens and not p.get("keep"):  # a reviewer confirmed it adds information
            for q, u in seen:
                if len(t | u) and len(t & u) / len(t | u) >= jaccard:
                    match = q
                    break
        if match is None:
            live.append((p, md))
            if len(t) >= min_tokens:
                seen.append((p, t))
        else:
            p["dropped"], p["duplicate_of"] = "duplicate", match["page"]
            dups.append(p)
    return live, dups


# A transcription that leans on another frame: "same as the previous frame", or — from a
# duplicate reader shown images A and B — a comparison with them ("Unlike A", "not present in A",
# "shown in B", "image A"). Only comparative phrasing counts, so spreadsheet text ("in B column",
# "from A to Z") and "A/R", "Plan A:", "B2" do not.
_AB = r"(?-i:[AB])(?![\w/&:-]|\.\w)"
_XREF = re.compile(r"\b(previous|prior|earlier|preceding)\s+(frame|slide|screen)s?\b"
                   r"|\bsame\s+as\s+(the\s+)?(previous|prior|earlier|above|before)\b"
                   r"|\b(unlike|than)\s+" + _AB +
                   r"|\b(present|shown|visible|seen|absent)\s+(in|from)\s+" + _AB +
                   r"|\b(image|frame|screenshot)\s+(?-i:[AB])\b", re.I)

REVIEW_INSTRUCTIONS = """# Read meeting-recording frames — a blind check

You have not seen any earlier transcription of these frames, and you do not need one: answer
from the images only. Do not open any other file — no result files, parsed documents or
pages.json; the check only works if the answers come from the images alone. Your item is in
`item_pNN.json` (NN = your page, two digits; `review_batch.json` lists every item for the
dispatcher); answer it by the `img_sha` written there.

- kind `duplicate`: open `kept_image` (A) and `image` (B). Ignoring the cursor, live captions and
  the meeting application, does B show exactly the same information as A? Answer
  `{"same": true}`, or `{"same": false, "md": "<full transcription of B>"}` — transcribe B on its
  own, as if A did not exist: never write "A" or "B" and never compare the two.
- kind `standalone`: open `image` and transcribe it: `{"md": "<full transcription>"}`.

Rules for every `md`: start with a `# <title>` line (the slide title, or `<app> — <window or
document title>`); copy identifiers, codes, file names, numbers and names shown in the shared
content character for character — never normalise or "correct" them; write `[illegible]` for
text you cannot read with certainty; ignore the meeting application (participant tiles and
names, toolbar, taskbar, live captions) and do not mention it; never refer to other frames.

One reader per item: you are given one item (its `page`) — open only `item_pNN.json`, answer
only the item in it, and write only `review_result_pNN.json` (same NN) containing
`{img_sha: {...}}` with the `img_sha` copied from `item_pNN.json`. (A single reader answering every item may instead write
`review_result.json` with every item exactly once, but one isolated reader per item avoids
confusing one image with another.)
"""


_FRAME_ECHO = re.compile(r"\A\s*<!--\s*frame:\s*p(\d+)\s*-->[ \t]*\n?")


def load_results_strict(results_dir: str) -> dict:
    """{img_sha: md} from a vision run dir — refusing, with the file names, when any result_*.json is
    not a JSON object. A skipped file silently removes its frames from the review round."""
    md, bad = {}, []
    for rf in sorted(glob.glob(os.path.join(results_dir, "result_*.json"))):
        try:
            part = json.loads(Path(rf).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            bad.append(f"{os.path.basename(rf)} ({e})")
            continue
        if not isinstance(part, dict):
            bad.append(f"{os.path.basename(rf)} (not an object)")
            continue
        md.update(part)
    if bad:
        die("refusing: malformed vision result file(s): " + "; ".join(bad) +
            " — re-run the vision batch(es) that wrote them")
    return md


def check_frame_echo(pages_doc: dict, vlm: dict) -> dict:
    """Each video transcription starts with `<!-- frame: pNN -->` naming the batch item it
    answers. Refuse when a result is filed under another frame's hash (a vision agent that
    shifted its answers by one frame); return the transcriptions with the echo removed.
    Results without an echo (written before the rule) are accepted with a warning."""
    out, wrong, untagged = dict(vlm), [], 0
    for p in pages_doc["pages"]:
        md = vlm.get(p["img_sha"])
        if md is None:
            continue
        m = _FRAME_ECHO.match(md)
        if not m:
            untagged += 0 if is_no_content(md) else 1   # the gate demands a bare sentinel there
        elif int(m.group(1)) != p["page"]:
            wrong.append(f"p{p['page']:02d} holds the answer for p{int(m.group(1)):02d}")
        else:
            out[p["img_sha"]] = md[m.end():]
    if wrong:
        die("refusing: vision results are filed under the wrong frames (" + "; ".join(wrong) +
            ") — re-run the vision batch(es) that hold these frames")
    if untagged:
        print(f"warning: {untagged} frame transcription(s) have no frame echo (<!-- frame: pNN -->); "
              "a result filed under the wrong frame cannot be detected for them", file=sys.stderr)
    return out


def _text_sha(md: str) -> str:
    return hashlib.sha256((md or "").strip().encode()).hexdigest()[:16]


def _records(p: dict) -> list[dict]:
    """The review verdicts stored on a page (pages.json), oldest first. A page can hold one
    per kind — e.g. a rewrite from one round and a duplicate confirmation from the next."""
    recs = p.get("review")
    recs = [recs] if isinstance(recs, dict) else recs if isinstance(recs, list) else []
    # `verify` verdicts came from an identifier vote that no longer exists; they are dropped
    return [r for r in recs if isinstance(r, dict) and r.get("verdict") and r.get("kind") != "verify"]


def _record_matches(rec: dict, *texts: str) -> bool:
    """A stored verdict still applies while the frame's text is the text it judged, or the
    reviewer's own text (which the cache holds after a reviewed run)."""
    keys = {rec.get("judged_sha")} | ({_text_sha(rec["md"])} if rec.get("md") else set())
    return any(t is not None and _text_sha(t) in keys for t in texts)


def _chain(p: dict, vlm: dict) -> list[dict]:
    """The text-producing verdicts (`rewrite`/`adds`) that apply to the frame's current text, in
    the order they were recorded. A verdict applies when it judged, or produced, a text in the
    chain: the first-pass text (--results), a later round's re-read of an earlier verdict's text,
    or the reviewed text the cache holds after a reviewed run — so every entry point sees the
    same chain. Verdicts judged on a different first pass stay out."""
    t = vlm.get(p["img_sha"])
    if t is None:
        return []
    recs = [r for r in _records(p) if r["verdict"] in ("rewrite", "adds") and r.get("md")]
    seen, chain, grew = {_text_sha(t)}, [], True
    while grew:
        grew = False
        for r in recs:
            keys = {r.get("judged_sha"), _text_sha(r["md"])}
            if not any(r is c for c in chain) and keys & seen:
                chain.append(r); seen |= keys; grew = True
    return sorted(chain, key=lambda r: next(i for i, x in enumerate(recs) if x is r))


def effective_md(p: dict, vlm: dict) -> str | None:
    """The frame's transcription after review: the last verdict in its chain wins over the
    first-pass VLM text, however that text was supplied (--results or cache)."""
    chain = _chain(p, vlm)
    return chain[-1]["md"] if chain else vlm.get(p["img_sha"])


def _pinned(p: dict, vlm: dict) -> bool:
    """A blind reader said this frame differs from the one the text dedup matched it with."""
    return any(r["verdict"] == "adds" for r in _chain(p, vlm))


def _resolved(item: dict, p: dict, vlm: dict) -> bool:
    for rec in _records(p):
        if not _record_matches(rec, vlm.get(p["img_sha"]), item["_md"]):
            continue
        if item["kind"] == "duplicate":
            # only a comparison with THIS kept frame confirms the drop
            if rec["kind"] == "duplicate" and rec.get("kept_img_sha") == item["kept_img_sha"]:
                return True
        elif rec["kind"] != "duplicate":
            return True       # a blind re-read; an `adds` text that leans on A is read again on its own
    return False


def review_items(pages_doc: dict, vlm: dict, render_dir: str, jaccard: float = 0.8) -> list[dict]:
    """What the second round must still look at, on the post-review state: frames the text
    dedup would drop (with the frame that would absorb them) and kept frames whose transcription
    refers to another frame. Items already answered by a
    verdict stored in pages.json (for the same text) are left out. Each item keeps the text it
    was computed from under `_md` — for code only; review-prep never writes it out."""
    img = lambda p: os.path.abspath(os.path.join(render_dir, f"p{p['page']:02d}.png"))
    by_sha = {p["img_sha"]: p for p in pages_doc["pages"]}
    cand = []
    for p in pages_doc["pages"]:
        if p.get("dropped") not in (None, "duplicate"):
            continue
        md = effective_md(p, vlm)
        if md is None or is_no_content(md.strip()):
            continue
        keep = _pinned(p, vlm)
        cand.append(({**{k: v for k, v in p.items() if k not in ("dropped", "duplicate_of", "keep")},
                      **({"keep": True} if keep else {})}, md.strip()))
    live, dups = dedup_content(cand, jaccard)  # on copies: pages.json is not touched
    by_page = {p["page"]: (p, md) for p, md in cand}
    items = []
    for d in dups:
        kp, _kmd = by_page[d["duplicate_of"]]
        items.append({"kind": "duplicate", "img_sha": d["img_sha"], "page": d["page"], "image": img(d),
                      "kept_page": kp["page"], "kept_image": img(kp), "kept_img_sha": kp["img_sha"],
                      "_md": by_page[d["page"]][1]})
    for p, md in live:
        if _XREF.search(md):
            items.append({"kind": "standalone", "img_sha": p["img_sha"], "page": p["page"], "image": img(p), "_md": md})
    items = [i for i in items if not _resolved(i, by_sha[i["img_sha"]], vlm)]
    return sorted(items, key=lambda i: i["page"])


def _answer_ok(kind, ans) -> bool:
    if not isinstance(ans, dict):
        return False
    md_ok = isinstance(ans.get("md"), str) and ans["md"].strip() != ""
    if kind == "duplicate":
        return ans.get("same") is True or (ans.get("same") is False and md_ok)
    if kind == "standalone":
        return md_ok
    return False


def load_review(review_dir: str) -> dict:
    """{img_sha: (kind, answer, item)} from a review dir — refuses unless every item in its batch has
    a well-formed answer for its kind. An empty batch needs no answer file."""
    try:
        items = json.loads(Path(review_dir, "review_batch.json").read_text())
        if not items:
            return {}
        files = sorted(glob.glob(os.path.join(review_dir, "review_result*.json")))
        parts = [(f, json.loads(Path(f).read_text())) for f in files]
    except FileNotFoundError as e:
        die(f"refusing to assemble: {e.filename} missing (run review-prep, then one blind reader per "
            "review item)")
    except json.JSONDecodeError as e:
        die(f"refusing to assemble: malformed review file: {e}")
    if not isinstance(items, list) or not parts or not all(isinstance(a, dict) for _f, a in parts):
        die(f"refusing to assemble: no review_result*.json answers in {review_dir} (each must be an object)")
    # one isolated reader per item writes review_result_pNN.json; merge them, refusing
    # contradictions and answers to items this batch did not ask about
    listed = {it.get("img_sha") for it in items}
    page_of = {it.get("img_sha"): it.get("page") for it in items}
    answers: dict = {}
    for f, part in parts:
        own = re.fullmatch(r"review_result_p(\d+)\.json", os.path.basename(f))
        for sha, ans in part.items():
            if own and sha in page_of and page_of[sha] != int(own.group(1)):
                die(f"refusing to assemble: {os.path.basename(f)} answers the item of p{page_of[sha]:02d} "
                    "— a per-item reader must answer only its own page")
            if sha not in listed:
                die(f"refusing to assemble: {os.path.basename(f)} answers {sha[:12]}…, which this review "
                    "batch does not list — a reader answered the wrong item")
            if sha in answers and answers[sha] != ans:
                die(f"refusing to assemble: two review files answer {sha[:12]}… differently")
            answers[sha] = ans
    legacy = sorted({str(it.get("kind")) for it in items} - {"duplicate", "standalone"})
    if legacy:
        die(f"refusing to assemble: {review_dir} was written by an older review-prep (item kind "
            f"{', '.join(legacy)}, no longer used) — run review-prep again with a new --out dir and "
            "answer that batch")
    out, bad = {}, []
    for it in items:
        ans = answers.get(it.get("img_sha"))
        if not _answer_ok(it.get("kind"), ans):
            bad.append(f"p{it.get('page', 0):02d} ({it.get('kind')})")
            continue
        out[it["img_sha"]] = (it["kind"], ans, it)
    if bad:
        die(f"refusing to assemble: review answers missing or malformed for {', '.join(bad)}")
    return out


def apply_reviews(pages_doc: dict, review_dirs: list[str], vlm: dict) -> None:
    """Decide each review dir's blind answers against the current text and store the verdicts
    on their pages, in order; a later verdict of the same kind replaces the earlier one. Idempotent: a
    verdict stored by an earlier run is re-derived from the dirs given now — unless its output
    is the text now current (a rerun from the cache), which it already produced."""
    by_sha = {p["img_sha"]: p for p in pages_doc["pages"]}
    loaded = [load_review(d) for d in review_dirs]
    settled = set()
    for answers in loaded:
        for sha, (kind, _ans, _it) in answers.items():
            p, txt = by_sha.get(sha), vlm.get(sha)
            if p is None:
                continue
            if txt is not None and any(r["kind"] == kind for r in _chain(p, vlm)):
                settled.add((sha, kind))
            else:
                p["review"] = [r for r in _records(p) if r["kind"] != kind]
    for answers in loaded:
        for sha, (kind, ans, it) in answers.items():
            p = by_sha.get(sha)
            if p is None or sha not in vlm or (sha, kind) in settled:
                continue
            cur = (effective_md(p, vlm) or "").strip()
            base = {"kind": kind, "judged_sha": _text_sha(cur)}
            md = _FRAME_ECHO.sub("", ans.get("md") or "", count=1).strip()
            if kind == "duplicate":
                base["kept_img_sha"] = it.get("kept_img_sha") or next(
                    (q["img_sha"] for q in pages_doc["pages"] if q["page"] == it.get("kept_page")), None)
                rec = {**base, "verdict": "same"} if ans["same"] else {**base, "verdict": "adds", "md": md}
            else:
                rec = {**base, "verdict": "rewrite", "md": md}
            p["review"] = [r for r in _records(p) if r["kind"] != kind] + [rec]


def cmd_review_prep(a) -> int:
    pj = os.path.join(a.render_dir, "pages.json")
    pages_doc = json.loads(Path(pj).read_text())
    if pages_doc.get("medium") != "video":
        die(f"{pj} is not a video render dir (medium != video)")
    vlm = {**load_cache(a.db, "video"),
           **check_frame_echo(pages_doc, load_results_strict(a.results) if a.results else {})}
    apply_reviews(pages_doc, a.review, vlm)  # in memory only: pages.json is written by assemble
    items = review_items(pages_doc, vlm, a.render_dir, a.content_dup)
    os.makedirs(a.out, exist_ok=True)
    blind = [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]   # no first-pass text
    atomic_write(os.path.join(a.out, "review_batch.json"), json.dumps(blind, indent=1) + "\n")
    # one file per reader holding only its item: a reader shown every item's img_sha can copy
    # a neighbour's key
    for f in glob.glob(os.path.join(a.out, "item_p*.json")):
        os.remove(f)
    for it in blind:
        atomic_write(os.path.join(a.out, f"item_p{it['page']:02d}.json"), json.dumps(it, indent=1) + "\n")
    atomic_write(os.path.join(a.out, "review_instructions.md"), REVIEW_INSTRUCTIONS)
    kinds = {k: sum(1 for i in items if i["kind"] == k) for k in ("duplicate", "standalone")}
    print(f"{pages_doc.get('doc', '')}: {kinds['duplicate']} duplicate(s) to confirm, "
          f"{kinds['standalone']} frame(s) to re-read -> {a.out}"
          + ("" if items else "  (nothing to review)"))
    return 0


def cmd_assemble(a) -> int:
    probe = json.loads(Path(a.probe).read_text())
    work_dir = os.path.dirname(os.path.abspath(a.probe))
    pj = os.path.join(a.render_dir, "pages.json")
    pages_doc = json.loads(Path(pj).read_text())
    if pages_doc.get("medium") != "video":
        die(f"{pj} is not a video render dir (medium != video)")
    # new results are checked for mis-filing; the cache holds text already checked
    results = check_frame_echo(pages_doc, load_results_strict(a.results) if a.results else {})  # never glob the CWD
    vlm = {**load_cache(a.db, "video"), **results}
    # Duplicate status is recomputed on every run, so a changed or disabled --content-dup
    # restores frames. A duplicate whose current transcription is unavailable stays dropped.
    for p in pages_doc["pages"]:
        if p.get("dropped") == "duplicate" and p["img_sha"] in vlm:
            p.pop("dropped"); p.pop("duplicate_of", None)
    live = [p for p in pages_doc["pages"] if not p.get("dropped")]
    missing = [p["img_sha"] for p in live if p["img_sha"] not in vlm]
    if missing:
        die(f"refusing to assemble: {len(missing)} frame(s) have no VLM result "
            f"(run vision_prep + vision subagents first): {', '.join(missing[:10])}")
    # Review verdicts: new answers (--review) are stored on their pages; stored verdicts are
    # kept while the frame's text is unchanged. The round must be complete before anything
    # is written — a stale, empty or foreign review dir leaves items pending and refuses.
    apply_reviews(pages_doc, a.review, vlm)
    pending = review_items(pages_doc, vlm, a.render_dir, a.content_dup)
    if pending and not a.no_review:
        kinds = ", ".join(f"{sum(1 for i in pending if i['kind'] == k)} {k}"
                          for k in ("duplicate", "standalone")
                          if any(i["kind"] == k for i in pending))
        if a.review:
            die(f"refusing to assemble: the review in {', '.join(a.review)} does not answer {len(pending)} "
                f"pending item(s) ({kinds}) — it is stale, for other frames, or its rewrites created new "
                "review work; run `video_capture.py review-prep` with the same --review dir(s) and "
                "--out <new dir>, answer it, then pass every --review dir to assemble")
        die(f"refusing to assemble: {len(pending)} frame(s) need the review round ({kinds}) — "
            "run `video_capture.py review-prep`, one blind reader per review item (each writes "
            "review_result_pNN.json), then pass --review <dir>; or pass --no-review to skip it deliberately")
    for p in pages_doc["pages"]:  # "adds" pins a frame against the text dedup while it applies
        if _pinned(p, vlm):
            p["keep"] = True
        else:
            p.pop("keep", None)
    eff = {p["img_sha"]: effective_md(p, vlm) for p in pages_doc["pages"] if p["img_sha"] in vlm}
    # the reviewed text is what the cache must hold for this document's pages
    reviewed = {p["img_sha"] for p in pages_doc["pages"] if _records(p)}
    results = {sha: eff[sha] for sha in eff if sha in results or sha in reviewed}
    # Resolve everything that can fail BEFORE any write/delete, so a bad sidecar
    # path or missing/malformed asr.json leaves pages.json/PNGs/manifest untouched.
    try:
        method = method_for(probe, work_dir)
    except FileNotFoundError:
        die(f"no ASR model info at {os.path.join(work_dir, 'asr.json')} — "
            "run `video_capture.py transcribe` first")
    except (json.JSONDecodeError, KeyError) as e:
        die(f"malformed {os.path.join(work_dir, 'asr.json')}: {e}")
    if probe["transcript"] == "sidecar":
        try:
            cues = read_cues(probe["sidecar"])
        except FileNotFoundError:
            die(f"sidecar not found: {probe['sidecar']}")
    elif probe["transcript"] == "asr":
        vtt = os.path.join(work_dir, "transcript.vtt")
        if not os.path.exists(vtt):
            die(f"no ASR transcript at {vtt} — run `video_capture.py transcribe` first")
        cues = read_cues(vtt)
    else:
        cues = []
    persist_page_render(a.db, pages_doc, results)
    kept = []
    for p in pages_doc["pages"]:
        if p.get("dropped"):
            continue
        md = eff[p["img_sha"]].strip()
        if is_no_content(md):
            png = os.path.join(a.render_dir, f"p{p['page']:02d}.png")
            if os.path.exists(png):
                os.remove(png)  # people-only frames are not retained
            p["dropped"] = "no-content"
            continue
        kept.append((p, md))
    kept, _dups = dedup_content(kept, a.content_dup)
    # A survivor is rendered as on screen for its duplicates' windows too; its own
    # shown_at on disk is left as selected, which keeps the round reversible.
    extra: dict[int, list] = {}
    live_pages = {p["page"] for p, _ in kept}
    for p in pages_doc["pages"]:
        if p.get("dropped") == "duplicate":
            target = p.get("duplicate_of")
            if target not in live_pages:
                # a duplicate kept only because its transcription is unavailable, whose
                # survivor is no longer rendered: say so instead of losing it silently
                print(f"warning: p{p['page']:02d} is a duplicate of "
                      f"{'p%02d' % target if isinstance(target, int) else 'an unknown page'}, which is not "
                      f"in the doc; re-run vision_prep + subagents to re-decide it", file=sys.stderr)
                continue
            extra.setdefault(target, []).extend(p["shown_at"])
    kept = [({**p, "shown_at": sorted(p["shown_at"] + extra.get(p["page"], []))}, md) for p, md in kept]
    atomic_write(pj, json.dumps(pages_doc, indent=2) + "\n")
    dropped = sum(1 for p in pages_doc["pages"] if p.get("dropped") == "no-content")
    duplicates = sum(1 for p in pages_doc["pages"] if p.get("dropped") == "duplicate")
    rel = probe["source"]
    text = render_doc(rel, method, pages_doc["slug"],
                      merge_turns(cues, a.merge_cues), kept)
    atomic_write(os.path.join(a.parsed, doc_name(rel)), text)
    inputs = [rel] + ([probe["sidecar_source"]] if probe.get("sidecar_source") else [])
    entries = [{"source": rel, "md": doc_name(rel), "method": "video-lane", "transcript": probe["transcript"],
                "inputs": inputs, "frames_kept": len(kept), "frames_dropped_no_content": dropped,
                "frames_dropped_duplicate": duplicates,
                "frames_capped": bool(pages_doc.get("frames_capped")), "warnings": probe.get("warnings", [])}]
    if probe.get("sidecar_source"):
        entries.append({"source": probe["sidecar_source"], "skipped": True,
                        "method": "consumed-by-video", "consumed_by": rel})
    upsert_manifest(a.manifest or os.path.join(a.parsed, "manifest.json"), entries)
    if probe.get("sidecar_source"):
        # An earlier parse_corpus pass may have indexed the sidecar as its own doc; the
        # consumed entry above retires it, so its parsed doc must go too (else the
        # transcript is indexed twice, or brain_sync's strict-sources check fails).
        stale = os.path.join(a.parsed, doc_name(probe["sidecar_source"]))
        if os.path.exists(stale):
            os.remove(stale)
    print(f"{rel}: {len(cues)} cue(s), {len(kept)} frame(s) kept, {dropped} dropped, {duplicates} duplicate(s) -> "
          f"{os.path.join(a.parsed, doc_name(rel))}")
    return 0


def safe_source(src: str) -> bool:
    """A source-relative path: non-empty, not ".", not absolute, no ".." component."""
    if not src or src.strip() in ("", ".") or os.path.isabs(src) or src.startswith(("/", "\\")):
        return False
    parts = re.split(r"[\\/]+", src)
    return ".." not in parts and any(p not in ("", ".") for p in parts)


def cmd_forget(a) -> int:
    if not safe_source(a.source):
        die(f"--source must be a source-relative path (not empty, '.', absolute or with '..'): {a.source!r}")
    manifest = a.manifest or os.path.join(a.parsed, "manifest.json")
    data = json.loads(Path(manifest).read_text()) if os.path.exists(manifest) else []
    keep = [e for e in data if e.get("source") != a.source and e.get("consumed_by") != a.source]
    atomic_write(manifest, json.dumps(keep, indent=2) + "\n")
    md = os.path.join(a.parsed, doc_name(a.source))
    if os.path.exists(md):
        os.remove(md)
    slug = video_slug(a.source)
    for root in (a.assets_root, a.work):
        d = os.path.join(root, slug) if root else None
        if d and os.path.isdir(d):
            if not is_video_render_dir(d):
                print(f"warning: {d} holds a non-video render — left in place", file=sys.stderr)
                continue
            shutil.rmtree(d)
    print(f"forgot {a.source}: {len(data) - len(keep)} manifest entr(ies) removed")
    return 0


# ---- transcribe (whisper.cpp; only when probe chose asr) --------------------------


def resolve_model(model: str | None, config: str | None) -> tuple[str | None, str]:
    language = "auto"
    cfg_model = None
    if config:
        video = tomllib.loads(Path(config).read_text(encoding="utf-8")).get("video", {}) or {}
        language = str(video.get("language") or "auto")
        if video.get("whisper_model"):
            p = Path(os.path.expanduser(str(video["whisper_model"])))
            cfg_model = str(p if p.is_absolute() else Path(config).resolve().parent / p)
    return (model or cfg_model), language


def cmd_transcribe(a) -> int:
    probe = json.loads(Path(a.probe).read_text())
    if probe["transcript"] != "asr":
        print(f"{probe['source']}: transcript source is {probe['transcript']} — nothing to transcribe")
        return 0
    exe = next((shutil.which(b) for b in WHISPER_BINS if shutil.which(b)), None)
    if not exe:
        die(f"whisper-cli not found — {DOCTOR_HINT}", 3)
    model, language = resolve_model(a.model, a.config)
    language = a.language or language
    if not model:
        die("no whisper model configured — choose one with `brain_doctor.py whisper-models`, "
            "then `brain_doctor.py set-whisper-model --config brain.toml --model <path>`")
    if not os.path.isfile(model):
        die(f"whisper model not found: {model} — see `brain_doctor.py whisper-models`")
    work = os.path.dirname(os.path.abspath(a.probe))
    key = {"video_sha": sha_file(probe["video"]), "model": os.path.basename(model),
           "model_bytes": os.path.getsize(model), "language": language}
    vtt, asr_json = os.path.join(work, "transcript.vtt"), os.path.join(work, "asr.json")
    if os.path.exists(vtt) and os.path.exists(asr_json) and json.loads(Path(asr_json).read_text()) == key:
        print(f"{probe['source']}: transcript unchanged (cache hit) -> {vtt}")
        return 0
    with tempfile.TemporaryDirectory(dir=work, prefix=".asr-") as td:
        wav = os.path.join(td, "audio.wav")
        run_quiet([need_tool("ffmpeg"), "-v", "error", "-y", "-i", probe["video"], "-vn",
                   "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav], "ffmpeg", probe["video"])
        out = os.path.join(td, "out")
        run_quiet([exe, "-m", model, "-f", wav, "-l", language, "-ovtt", "-of", out, "-np"],
                  "whisper-cli", probe["video"])
        os.replace(out + ".vtt", vtt)  # only a complete transcript ever lands
    atomic_write(asr_json, json.dumps(key, indent=2) + "\n")
    print(f"{probe['source']}: {len(read_cues(vtt))} cue(s) via whisper.cpp ({key['model']}) -> {vtt}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="meeting recordings -> the visual lane")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="ffprobe + choose the transcript source")
    p.add_argument("--video", required=True); p.add_argument("--rel-to", help="source root (for the rel path/slug)")
    p.add_argument("--work", required=True, help="<project>/video")
    p.add_argument("--transcript", choices=["auto", "sidecar", "asr", "none"], default="auto")
    p.add_argument("--transcript-file", help="explicit transcript (.vtt/.srt/Teams .docx) when it "
                   "is paired by neither same stem nor .docx title; excludes --transcript asr|none")
    p.add_argument("--manifest", help="record an ffprobe failure here as an error entry")
    p.set_defaults(func=cmd_probe)
    f = sub.add_parser("frames", help="stable-span key frames -> visual-lane layout")
    f.add_argument("--video", required=True); f.add_argument("--rel-to")
    f.add_argument("--assets-root", required=True, help="<project>/assets")
    f.add_argument("--diff", type=float, default=0.08, help="cut when MAD vs the span's first frame exceeds this")
    f.add_argument("--still", type=float, default=0.015, help="keep a span only if its median step MAD is below this")
    f.add_argument("--min-hold", type=int, default=3, help="seconds a frame must stay to count")
    f.add_argument("--max-per-min", type=int, default=6)
    f.add_argument("--max-frames", type=int, default=300)
    f.set_defaults(func=cmd_frames)
    s = sub.add_parser("assemble", help="transcript + VLM-gated frames -> one parsed doc")
    s.add_argument("--probe", required=True); s.add_argument("--render-dir", required=True)
    s.add_argument("--results", help="dir with the vision subagents' result_*.json")
    s.add_argument("--parsed", required=True, help="parsed dir (doc name follows parse_corpus)")
    s.add_argument("--manifest", help="default: <parsed>/manifest.json")
    s.add_argument("--db", help="knowledge.sqlite — read/write the page_render cache")
    s.add_argument("--merge-cues", type=int, default=10)
    s.add_argument("--content-dup", type=float, default=0.8,
                   help="drop a frame whose transcription shares >= this share of its words with an "
                        "earlier kept frame (0 disables; recomputed on every run)")
    rg = s.add_mutually_exclusive_group()
    rg.add_argument("--review", action="append", default=[], help="review dir from review-prep, answered by one blind reader per item "
                                    "(review_result_pNN.json): confirms duplicates, rewrites frames that "
                                    "refer to other frames; repeat for follow-up rounds")
    s.set_defaults(func=cmd_assemble)
    rg.add_argument("--no-review", action="store_true",
                   help="assemble without the review round even when frames need it (not recommended)")
    rv = sub.add_parser("review-prep", help="list frames the text dedup would drop and frames that "
                                            "refer to another frame, for one blind reader per item")
    rv.add_argument("--render-dir", required=True)
    rv.add_argument("--results", help="dir with the vision subagents' result_*.json")
    rv.add_argument("--db", help="knowledge.sqlite — read the page_render cache")
    rv.add_argument("--out", required=True, help="review dir (review_batch.json, review_instructions.md)")
    rv.add_argument("--review", action="append", default=[],
                    help="earlier review dir(s) already answered — list only what is still pending")
    rv.add_argument("--content-dup", type=float, default=0.8, help="same threshold assemble will use")
    rv.set_defaults(func=cmd_review_prep)
    g = sub.add_parser("forget", help="remove a deleted recording's parsed doc, manifest entries, assets")
    g.add_argument("--source", required=True, help="source-relative path of the video")
    g.add_argument("--parsed", required=True); g.add_argument("--manifest")
    g.add_argument("--assets-root"); g.add_argument("--work")
    g.set_defaults(func=cmd_forget)
    t = sub.add_parser("transcribe", help="whisper.cpp transcript (only when probe chose asr)")
    t.add_argument("--probe", required=True)
    t.add_argument("--model", help="ggml model path (default: brain.toml [video].whisper_model)")
    t.add_argument("--config", help="brain.toml")
    t.add_argument("--language", help="override [video].language (default auto)")
    t.set_defaults(func=cmd_transcribe)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.func(a)
    except SystemExit as e:
        return int(e.code or 0)


if __name__ == "__main__":
    sys.exit(main())
