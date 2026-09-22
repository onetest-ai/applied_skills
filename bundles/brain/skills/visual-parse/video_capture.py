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
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_pages import doc_slug  # noqa: E402  (same skill directory)
from vision_assemble import demote, load_cache, load_results, persist_page_render, title_of  # noqa: E402

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


# ---- IO helpers ---------------------------------------------------------------

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


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


def find_sidecar(video: str) -> str | None:
    d, base = os.path.split(os.path.abspath(video))
    stem = os.path.splitext(base)[0]
    for ext in SIDECAR_EXT:
        for n in sorted(os.listdir(d)):
            if os.path.splitext(n)[0] == stem and os.path.splitext(n)[1].lower() == ext:
                return os.path.join(d, n)
    return None


def choose_transcript(has_audio: bool, sidecar: str | None, override: str) -> str:
    if override == "sidecar" and not sidecar:
        raise ValueError("--transcript sidecar but no same-stem .vtt/.srt exists")
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
    raw = subprocess.run([exe, "-v", "error", "-i", video, "-vf",
                          f"fps=1,scale={LOW_W}:{LOW_H},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, LOW_H, LOW_W).astype(np.float32) / 255.0


def extract_frame(video: str, t: float, png: str) -> None:
    subprocess.run([need_tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video,
                    "-frames:v", "1", "-vf", "scale='min(1920,iw)':-2", png], check=True)


def upsert_manifest(path, entries: list[dict]) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8")) if os.path.exists(path) else []
    srcs = {e["source"] for e in entries}
    data = [e for e in data if e.get("source") not in srcs] + entries
    atomic_write(path, json.dumps(data, indent=2) + "\n")


# ---- probe / frames -----------------------------------------------------------


def cmd_probe(a) -> int:
    video = os.path.abspath(a.video)
    rel = source_rel(a.video, a.rel_to)
    slug = doc_slug(rel)
    try:
        duration, has_audio = ffprobe(video)
    except RuntimeError as e:
        if a.manifest:
            upsert_manifest(a.manifest, [{"source": rel, "error": str(e)}])
        die(str(e))
    sidecar = find_sidecar(video)
    try:
        transcript = choose_transcript(has_audio, sidecar, a.transcript)
    except ValueError as e:
        die(str(e))
    warnings = []
    if transcript == "sidecar":
        cues = read_cues(sidecar)
        if not cues:
            die(f"sidecar-empty: {sidecar} has no cues — fix it, or override with "
                "--transcript asr|none")
        if cues[-1]["end"] > duration + 5:
            warnings.append(f"sidecar is longer than the video ({fmt_hms(cues[-1]['end'])} > "
                            f"{fmt_hms(duration)}) — likely a wrong pairing")
    probe = {"source": rel, "video": video, "slug": slug, "duration": duration, "has_audio": has_audio,
             "sidecar": sidecar if transcript == "sidecar" else None,
             "sidecar_source": source_rel(sidecar, a.rel_to) if (sidecar and transcript == "sidecar") else None,
             "transcript": transcript, "warnings": warnings}
    out = os.path.join(a.work, slug, "probe.json")
    atomic_write(out, json.dumps(probe, indent=2) + "\n")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"{rel}: {fmt_hms(duration)}, transcript={transcript} -> {out}")
    return 0


def frames_key(video: str, params: dict) -> str:
    return hashlib.sha256((sha_file(video) + json.dumps(params, sort_keys=True)).encode()).hexdigest()


def cmd_frames(a) -> int:
    video = os.path.abspath(a.video)
    rel = source_rel(a.video, a.rel_to)
    slug = doc_slug(rel)
    outdir = os.path.join(a.assets_root, slug)
    os.makedirs(outdir, exist_ok=True)
    params = {"diff": a.diff, "still": a.still, "min_hold": a.min_hold,
              "max_per_min": a.max_per_min, "max_frames": a.max_frames}
    key = frames_key(video, params)
    pj = os.path.join(outdir, "pages.json")
    if os.path.exists(pj) and json.loads(Path(pj).read_text()).get("frames_key") == key:
        print(f"{rel}: frames unchanged (cache hit) -> {outdir}")
        return 0
    for old in glob.glob(os.path.join(outdir, "p*.png")) + glob.glob(os.path.join(outdir, "p*.txt")):
        os.remove(old)
    duration, _ = ffprobe(video)
    kept, capped = select_frames(read_low_frames(video), **params)
    pages = []
    for n, q in enumerate(kept, 1):
        png = os.path.join(outdir, f"p{n:02d}.png")
        extract_frame(video, min(q["key"] + 0.5, max(duration - 0.1, 0.0)), png)
        open(os.path.join(outdir, f"p{n:02d}.txt"), "w").close()
        pages.append({"page": n, "image": f"{slug}/p{n:02d}.png", "img_sha": sha_file(png),
                      "text_len": 0, "n_drawings": 0, "n_tables": 0, "img_cover": 0.0,
                      "flagged": True, "why": "video-frame",
                      "t_start": q["t_start"], "t_end": q["t_end"], "shown_at": q["shown_at"]})
    doc = {"doc": os.path.basename(video), "slug": slug, "dpi": None, "medium": "video",
           "duration": duration, "frames_key": key, "frames_capped": capped, "pages": pages}
    atomic_write(pj, json.dumps(doc, indent=2) + "\n")
    print(f"{rel}: {len(pages)} key frame(s) -> {outdir}" + ("  [capped at --max-frames]" if capped else ""))
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
                       f"{demote(md.strip())}\n")
    return "\n".join(out)


def cmd_assemble(a) -> int:
    probe = json.loads(Path(a.probe).read_text())
    work_dir = os.path.dirname(os.path.abspath(a.probe))
    pj = os.path.join(a.render_dir, "pages.json")
    pages_doc = json.loads(Path(pj).read_text())
    if pages_doc.get("medium") != "video":
        die(f"{pj} is not a video render dir (medium != video)")
    results = load_results(a.results)
    vlm = {**load_cache(a.db), **results}
    live = [p for p in pages_doc["pages"] if not p.get("dropped")]
    missing = [p["img_sha"] for p in live if p["img_sha"] not in vlm]
    if missing:
        die(f"refusing to assemble: {len(missing)} frame(s) have no VLM result "
            f"(run vision_prep + vision subagents first): {', '.join(missing[:10])}")
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
        md = vlm[p["img_sha"]].strip()
        if md == NO_CONTENT:
            png = os.path.join(a.render_dir, f"p{p['page']:02d}.png")
            if os.path.exists(png):
                os.remove(png)  # people-only frames are not retained
            p["dropped"] = "no-content"
            continue
        kept.append((p, md))
    atomic_write(pj, json.dumps(pages_doc, indent=2) + "\n")
    dropped = sum(1 for p in pages_doc["pages"] if p.get("dropped") == "no-content")
    rel = probe["source"]
    text = render_doc(rel, method, pages_doc["slug"],
                      merge_turns(cues, a.merge_cues), kept)
    atomic_write(os.path.join(a.parsed, doc_name(rel)), text)
    inputs = [rel] + ([probe["sidecar_source"]] if probe.get("sidecar_source") else [])
    entries = [{"source": rel, "md": doc_name(rel), "method": "video-lane", "transcript": probe["transcript"],
                "inputs": inputs, "frames_kept": len(kept), "frames_dropped_no_content": dropped,
                "frames_capped": bool(pages_doc.get("frames_capped")), "warnings": probe.get("warnings", [])}]
    if probe.get("sidecar_source"):
        entries.append({"source": probe["sidecar_source"], "skipped": True,
                        "method": "consumed-by-video", "consumed_by": rel})
    upsert_manifest(a.manifest or os.path.join(a.parsed, "manifest.json"), entries)
    print(f"{rel}: {len(cues)} cue(s), {len(kept)} frame(s) kept, {dropped} dropped -> "
          f"{os.path.join(a.parsed, doc_name(rel))}")
    return 0


def cmd_forget(a) -> int:
    manifest = a.manifest or os.path.join(a.parsed, "manifest.json")
    data = json.loads(Path(manifest).read_text()) if os.path.exists(manifest) else []
    keep = [e for e in data if e.get("source") != a.source and e.get("consumed_by") != a.source]
    atomic_write(manifest, json.dumps(keep, indent=2) + "\n")
    md = os.path.join(a.parsed, doc_name(a.source))
    if os.path.exists(md):
        os.remove(md)
    slug = doc_slug(a.source)
    for root in (a.assets_root, a.work):
        if root and os.path.isdir(os.path.join(root, slug)):
            shutil.rmtree(os.path.join(root, slug))
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
        subprocess.run([need_tool("ffmpeg"), "-v", "error", "-y", "-i", probe["video"], "-vn",
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav], check=True)
        out = os.path.join(td, "out")
        subprocess.run([exe, "-m", model, "-f", wav, "-l", language, "-ovtt", "-of", out, "-np"], check=True)
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
    s.set_defaults(func=cmd_assemble)
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
