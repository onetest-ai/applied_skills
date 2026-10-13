"""A folder of meetings: transcripts (.vtt .srt .docx .txt) and recordings (video or audio), e.g. a synced
SharePoint/OneDrive "Call Transcripts" folder or a Teams recordings folder.

One meeting = one evidence item. A recording is paired with its transcript by stem, or by a Teams .docx whose
first paragraph is the recording's name; a recording with no transcript is transcribed with whisper.cpp.
"""
import datetime as dt
import fnmatch
import os
import re
from pathlib import Path

from .. import media, store as S, transcripts as TR
from ..text import MONTHS

DEFAULT_INCLUDE = ["*.vtt", "*.srt", "*.docx", "*.txt"] + [f"*{e}" for e in media.MEDIA_EXT]


def _walk(root, include, exclude):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for f in fns:
            if f.startswith((".", "~$")):
                continue
            rel = os.path.relpath(os.path.join(dp, f), root)
            if any(fnmatch.fnmatch(f.lower(), p.lower()) or fnmatch.fnmatch(rel, p) for p in include) and \
                    not any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(f, p) for p in exclude):
                yield rel


def _tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or "UTC")
    except Exception:  # unknown zone name or no tz database: fall back to the machine's clock
        return None


def meeting_time(rel, abspath, docx_header=None, creation_time=None, tz=None):
    """Best clock for when the meeting happened, in the project's timezone: the recording's creation time (UTC in
    the container, the most reliable clock), a Teams .docx header, a date in the file name, else the file mtime."""
    z = _tz(tz) if tz else None
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(abspath), z).replace(tzinfo=None)
    if creation_time:
        try:
            return dt.datetime.fromisoformat(creation_time.replace("Z", "+00:00")).astimezone(z).replace(tzinfo=None).isoformat(timespec="seconds")
        except ValueError:
            pass
    if docx_header:
        m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4}),? (\d{1,2}:\d{2}\s*[AP]M)", docx_header)
        if m:
            try:
                return dt.datetime.strptime(f"{m.group(1)} {m.group(2).replace(' ', '')}", "%B %d, %Y %I:%M%p").isoformat()
            except ValueError:
                pass
    name = Path(rel).name
    m = re.search(r"(20\d\d)[-_]?(\d\d)[-_]?(\d\d)", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{mtime.strftime('%H:%M:%S')}"
    m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s*(\d{1,2})\b", name, re.I) or \
        re.search(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")[a-z]*\b", name, re.I)
    if m:
        a, b = m.groups()
        mon, day = (a, b) if a[0].isalpha() else (b, a)
        y = mtime.year
        try:
            d = dt.date(y, MONTHS.index(mon[:3].lower()) + 1, int(day))
            if d > mtime.date() + dt.timedelta(days=30):
                d = d.replace(year=y - 1)
            return f"{d.isoformat()}T{mtime.strftime('%H:%M:%S') if d == mtime.date() else '12:00:00'}"
        except ValueError:
            pass
    return mtime.isoformat(timespec="seconds")


def _docx_header(path):
    try:
        paras = ["".join(r).strip() for r in TR._docx_paragraphs(path)]
    except (ValueError, OSError):
        return None
    paras = [p for p in paras if p]
    return paras[1] if len(paras) > 1 else None


def _pair(rels, root):
    """[(transcript_rel|None, media_rel|None)] — each meeting once."""
    trans = [r for r in rels if r.lower().endswith(TR.TRANSCRIPT_EXT)]
    meds = [r for r in rels if r.lower().endswith(media.MEDIA_EXT)]
    stem = lambda r: os.path.splitext(r)[0].lower()
    used, pairs = set(), []
    for m in meds:
        t = next((t for t in trans if stem(t) == stem(m) and t not in used), None)
        if not t:  # Teams names the .docx after the meeting; its first paragraph is the recording's name
            t = next((t for t in trans if t.lower().endswith(".docx") and t not in used
                      and os.path.dirname(t) == os.path.dirname(m)
                      and (TR.docx_title(os.path.join(root, t)) or "").lower() == Path(m).stem.lower()), None)
        if t:
            used.add(t)
        pairs.append((t, m))
    pairs += [(t, None) for t in trans if t not in used]
    return pairs


def _graph(cfg, scfg):
    """The msgraph source to ask for Teams transcripts: `teams_lookup` on this source (default: on when the project
    has any msgraph source), using that source's CLI settings."""
    if scfg.get("teams_lookup") is False:
        return None
    g = next((s for s in cfg.sources.values() if s.get("adapter", "").startswith("msgraph")), None)
    return g if (g or scfg.get("teams_lookup")) else None


def _utc(creation_time):
    return dt.datetime.fromisoformat(creation_time.replace("Z", "+00:00")).astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _sig(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


def sync(cfg, key, scfg, store, cursor):
    from . import meeting_kind
    root = scfg.get("path")
    if not root or not os.path.isdir(root):
        return cursor, {"source": key, "status": "unavailable", "reason": f"folder not found: {root}"}
    rels = sorted(_walk(root, scfg.get("include") or DEFAULT_INCLUDE, scfg.get("exclude") or []))
    since = scfg.get("since")
    cur = dict(cursor or {})
    new, changed, pending, skipped = [], [], [], []
    for t, m in _pair(rels, root):
        rel = t or m
        sig = "|".join(_sig(os.path.join(root, x)) for x in (t, m) if x)
        if cur.get(rel) == sig:
            continue
        tpath = os.path.join(root, t) if t else None
        mpath = os.path.join(root, m) if m else None
        if tpath and tpath.lower().endswith(".docx"):
            try:
                ok = bool(TR._cues_teams_docx(tpath))
            except ValueError as e:  # truncated or still syncing: retried next sync, cursor not advanced
                pending.append({"file": t, "reason": f"{e} (corrupt, or the sync client has not finished it)"})
                continue
            if not ok:
                skipped.append({"file": t, "reason": "a .docx that is not a Teams transcript"})
                if not m:
                    cur[rel] = sig
                    continue
                tpath = None  # its recording may still be transcribed
        info = {}
        if mpath:
            try:
                info = media.probe(mpath)
            except media.MediaError as e:
                info = {"error": str(e)}
        at = meeting_time(rel, tpath or mpath, _docx_header(tpath) if tpath and tpath.endswith(".docx") else None,
                          info.get("creation_time"), cfg.timezone)
        if since and at[:10] < since:
            cur[rel] = sig
            continue
        eid = S.make_id(at, key, rel)
        how = None
        teams_vtt = str(S.P(store, "media", eid + ".teams.vtt"))
        if not tpath and mpath and _graph(cfg, scfg) and not os.path.exists(teams_vtt) and info.get("creation_time"):
            # a Teams recording usually has a Teams transcript (real speakers, no speech recognition): fetch it first
            try:
                from . import msgraph
                S.P(store, "media").mkdir(parents=True, exist_ok=True)
                msgraph.find_teams_transcript(_graph(cfg, scfg), Path(m).stem, _utc(info["creation_time"]), teams_vtt)
            except Exception as e:  # Graph offline or not signed in: fall back to local transcription
                info["teams_error"] = str(e)[:200]
        if not tpath and os.path.exists(teams_vtt):
            tpath, how = teams_vtt, "teams"
        if not tpath:  # a recording on its own: transcribe it (slow; only within `transcribe`'s budget)
            out_vtt = str(S.P(store, "media", eid + ".vtt"))
            if not os.path.exists(out_vtt):
                if getattr(cfg, "asr_budget", 0) <= 0:
                    pending.append({"file": m, "reason": "needs transcription: run `taska.py transcribe`",
                                    "minutes": round(info.get("duration", 0) / 60) or None})
                    continue
                cfg.asr_budget -= 1
                try:
                    media.transcribe(mpath, out_vtt, media.whisper_model(cfg.media), cfg.media.get("language", "auto"))
                except media.MediaError as e:
                    pending.append({"file": m, "reason": str(e)})
                    continue
            tpath, how = out_vtt, "asr"
        elif how != "teams":
            how = "transcript"
        try:
            cues = TR.read_cues(tpath)
        except (ValueError, OSError) as e:
            pending.append({"file": rel, "reason": f"unreadable transcript: {e}"})
            continue
        if TR.repetition_loop(cues):
            if how == "asr":
                os.remove(tpath)  # retried by the next `transcribe`
            pending.append({"file": rel, "reason": "the transcript is one line repeated (speech recogniser loop); "
                            + ("it will be re-transcribed" if how == "asr" else "not used")})
            continue
        turns = TR.stitch(cues)
        if not turns:
            skipped.append({"file": rel, "reason": "no speech turns"})
            cur[rel] = sig
            continue
        title = Path(m or t).stem
        item = {"id": eid, "source": key, "kind": "meeting", "meeting_kind": meeting_kind(title, scfg), "at": at,
                "title": title, "turns": turns,
                "meta": {"transcript": how, "file": rel, "recording": m, "duration_min":
                         round(info["duration"] / 60) if info.get("duration") else None,
                         "speakers": sorted({cfg.canonical(x["speaker"]) for x in turns if x["speaker"] != "?"})}}
        for x in item["turns"]:
            x["speaker"] = cfg.canonical(x["speaker"]) or x["speaker"]
        if S.write_evidence(store, item):
            (changed if rel in cur else new).append(eid)
        elif S.duplicate_of(store, item):
            skipped.append({"file": rel, "reason": f"same meeting as {S.duplicate_of(store, item)}"})
        cur[rel] = sig
    return cur, {"source": key, "status": "ok", "new": new, "changed": changed, "pending": pending,
                 "skipped": skipped}
