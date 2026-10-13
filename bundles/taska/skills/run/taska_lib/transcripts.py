"""Meeting transcripts -> speaker turns. WebVTT, SRT, Teams .docx and plain "Name: text" files; stdlib only.

The cue readers follow the same rules as brain's visual-parse/video_capture.py (Teams voice tags, conservative
"Name:" prefixes, Teams .docx run layout) so a transcript reads identically in both bundles.
"""
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

BACKCHANNEL_WORDS = 3
UNLABELLED_PARAGRAPH_S = 60
TRANSCRIPT_EXT = (".vtt", ".srt", ".docx", ".txt")
_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?")
NON_SPEAKER_PREFIXES = {"note", "today", "action", "decision", "question", "answer", "summary", "agenda",
                        "update", "next", "re", "fw", "subject", "q", "a", "step", "item", "ok", "yes", "no"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_TS = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_DOCX_TURN = re.compile(r"^(.+?)\s{2,}(\d{1,2}:\d{2}(?::\d{2})?)(.*)$", re.S)
_DOCX_EVENTS = ("started transcription", "stopped transcription")


def seconds(s):
    m = _TS.search(s or "")
    if not m:
        raise ValueError(f"bad timestamp: {s!r}")
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int((ms or "0").ljust(3, "0")) / 1000


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def speaker_and_text(text):
    voice = re.match(r"\s*<v(?:\.[^ >]+)*\s+([^>]+)>\s*(.*)", text, flags=re.I | re.S)
    if voice:
        return voice.group(1).strip(), re.sub(r"</?v[^>]*>", "", voice.group(2)).strip()
    lab = re.match(r"\s*([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,2}\d?):\s+(.+)", text, flags=re.S)
    if lab and lab.group(1).split()[0].lower().strip(".'’-") not in NON_SPEAKER_PREFIXES:
        return lab.group(1).strip(), lab.group(2).strip()
    return "", re.sub(r"</?v[^>]*>", "", text).strip()


def _cues_vtt_srt(text):
    text = re.sub(r"^﻿?WEBVTT[^\n]*\n", "", text.replace("\r\n", "\n"))
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        rows = [r.strip() for r in block.splitlines() if r.strip()]
        i = next((k for k, r in enumerate(rows) if "-->" in r), None)
        if i is None:
            continue
        body = " ".join(rows[i + 1:]).strip()
        if not body:
            continue
        sp, body = speaker_and_text(body)
        body = re.sub(r"<[^>]+>", "", body).strip()
        if body:
            out.append({"start": seconds(rows[i].split("-->")[0]), "speaker": sp, "text": body})
    return out


def _docx_paragraphs(path):
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as e:
        raise ValueError(f"unreadable docx: {e}") from e
    return [["".join(t.text or "" for t in run.iter(_W + "t")) for run in p.iter(_W + "r")]
            for p in root.iter(_W + "p")]


def docx_title(path):
    try:
        for runs in _docx_paragraphs(path):
            if "".join(runs).strip():
                return "".join(runs).strip()
    except (ValueError, OSError):
        return None
    return None


def _cues_teams_docx(path):
    """Teams .docx: title, date, duration, then one paragraph per turn: speaker run(s), "M:SS" run, text runs."""
    paras = [r for r in _docx_paragraphs(path) if "".join(r).strip()]
    if len(paras) >= 3 and re.match(r"^\s*(\d+h)?\s*(\d+m)?\s*(\d+s)?\s*$", "".join(paras[2])) and "".join(paras[2]).strip():
        paras = paras[3:]
    out = []
    for runs in paras:
        joined = "".join(runs)
        i = next((k for k in range(1, len(runs)) if _DOCX_TS.match(runs[k].strip())), None)
        if i is None and joined.strip().lower().endswith(_DOCX_EVENTS):
            continue
        if i is not None:
            sp, ts, text = "".join(runs[:i]).strip(), runs[i].strip(), " ".join(t.strip() for t in runs[i + 1:])
        else:
            m = _DOCX_TURN.match(joined.strip())
            if not m:
                continue
            sp, ts, text = m.group(1).strip(), m.group(2), m.group(3)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append({"start": seconds(ts), "speaker": sp, "text": text})
    return out


def _cues_txt(text):
    """Plain transcripts (e.g. Graph's text export): "[hh:mm:ss] Name: text" or "Name: text" per line."""
    out, t0 = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\]?\s*(.*)$", line)
        start = seconds(m.group(1)) if m else t0
        rest = m.group(2) if m else line
        sp, body = speaker_and_text(rest)
        if body:
            out.append({"start": start, "speaker": sp, "text": body})
            t0 = start
    return out


def read_cues(path):
    p = str(path).lower()
    if p.endswith(".docx"):
        return _cues_teams_docx(path)
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return _cues_txt(text) if p.endswith(".txt") else _cues_vtt_srt(text)


def repetition_loop(cues, share=0.4, min_cues=20):
    """True when one line makes up more than `share` of the cues: a speech recogniser stuck in a loop, not a
    transcript. Such output must never become evidence."""
    if len(cues) < min_cues:
        return False
    from collections import Counter
    top = Counter(re.sub(r"\W+", " ", c["text"].lower()).strip() for c in cues).most_common(1)[0][1]
    return top / len(cues) > share


# Phrases whisper invents over silence or music (subtitle credits from its training data). Dropped only from
# unlabelled speech-recognition output, never from a real transcript.
HALLUCINATIONS = re.compile(r"^\W*(thanks? (you )?(so much )?for watching|subtitles? by|subscribe to|amara\.org|"
                            r"\[(music|applause|silence|blank_audio)\]|you\.?|bye\.?)\W*$", re.I)


def stitch(cues):
    """Cues -> turns. One speaker's consecutive cues join; a short interjection (<= 3 words) between two parts
    of the same speaker's sentence is marked `bc` (backchannel) and the sentence is rejoined."""
    out, t0 = [], 0
    for c in cues:
        t = {"ts": hms(c["start"]), "speaker": c["speaker"] or "?", "text": c["text"]}
        if t["speaker"] == "?" and HALLUCINATIONS.match(t["text"]):
            continue
        if t["speaker"] == "?":  # no diarisation (ASR): keep ~one-minute paragraphs, each with its own time
            if out and out[-1]["speaker"] == "?" and c["start"] - t0 < UNLABELLED_PARAGRAPH_S:
                out[-1]["text"] += " " + t["text"]
            else:
                out.append(dict(t, bc=False))
                t0 = c["start"]
            continue
        bc = len(t["text"].split()) <= BACKCHANNEL_WORDS
        if out and out[-1]["speaker"] == t["speaker"]:
            out[-1]["text"] += " " + t["text"]
            out[-1]["bc"] = len(out[-1]["text"].split()) <= BACKCHANNEL_WORDS  # "Okay." + a real sentence is not filler
            continue
        if bc and len(out) >= 2 and out[-2]["speaker"] == t["speaker"] and out[-1].get("bc"):
            out[-2]["text"] += " " + t["text"]
            continue
        out.append(dict(t, bc=bc))
    return out


def is_transcript(path):
    if str(path).lower().endswith(".docx"):
        try:
            return bool(_cues_teams_docx(path))
        except (ValueError, OSError):
            return False
    return str(path).lower().endswith(TRANSCRIPT_EXT)
