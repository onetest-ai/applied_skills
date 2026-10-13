"""Microsoft 365 through the user's `msgraph` skill (a zero-dependency Node CLI that owns sign-in and tokens).

Taska never handles credentials: it shells out to `node <msgraph.js> ...` and reads its output. The CLI path is
`[sources.<key>].cli`, else `$MSGRAPH_CLI`, else `~/.claude/skills/msgraph/scripts/msgraph.js`.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile

from .. import store as S, transcripts as TR

DEFAULT_CLI = "~/.claude/skills/msgraph/scripts/msgraph.js"
WINDOW_DAYS = 7  # the CLI lists at most 50 calendar events per call


class GraphError(RuntimeError):
    pass


def cli_path(scfg):
    return os.path.expanduser(scfg.get("cli") or os.environ.get("MSGRAPH_CLI") or DEFAULT_CLI)


def run(scfg, *args, timeout=120):
    cli = cli_path(scfg)
    if not shutil.which("node"):
        raise GraphError("node is not on PATH (the msgraph skill needs Node.js >= 18)")
    if not os.path.isfile(cli):
        raise GraphError(f"msgraph CLI not found at {cli}; install the msgraph skill or set [sources.<key>].cli")
    r = subprocess.run(["node", cli, *args], capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if "NOT_LOGGED_IN" in out or "TOKEN_EXPIRED" in out:
        raise GraphError(f"msgraph is not signed in. Run: ! node {cli} login")
    if r.returncode:
        raise GraphError(out.strip()[-400:])
    return r.stdout


def status(scfg):
    out = run(scfg, "status")
    m = re.search(r"Logged in as:\s*(\S+)", out)
    return m.group(1) if m else None


def _windows(since, until):
    d = dt.date.fromisoformat(since)
    end = dt.date.fromisoformat(until)
    while d <= end:
        e = min(d + dt.timedelta(days=WINDOW_DAYS - 1), end)
        yield d.isoformat(), e.isoformat()
        d = e + dt.timedelta(days=1)


def parse_listing(text):
    """`transcripts --start --end --subject` text -> [{meeting, subject, transcript, created}] (deduplicated)."""
    out, subject, meeting = {}, None, None
    for line in text.splitlines():
        if line.startswith("Meeting:"):
            subject, meeting = line.split(":", 1)[1].strip(), None
        elif line.startswith("Meeting ID:"):
            meeting = line.split(":", 1)[1].strip()
        else:
            m = re.match(r"Transcript ID:\s*(\S+)\s+Created:\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", line.strip())
            if m and meeting:
                out[m.group(1)] = {"meeting": meeting, "subject": subject, "transcript": m.group(1),
                                   "created": m.group(2).replace(" ", "T") + ":00"}
    return list(out.values())


def sync_transcripts(cfg, key, scfg, store, cursor):
    """New Teams transcripts of meetings whose subject contains `subject`, downloaded as VTT."""
    from . import meeting_kind
    cur = dict(cursor or {"seen": []})
    seen = set(cur.get("seen") or [])
    since = scfg.get("since") or (dt.date.today() - dt.timedelta(days=14)).isoformat()
    today = dt.date.today().isoformat()
    try:
        found = {}
        for a, b in _windows(cur.get("last_window") or since, today):
            args = ["transcripts", "--start", a, "--end", b]
            if scfg.get("subject"):
                args += ["--subject", scfg["subject"]]
            for t in parse_listing(run(scfg, *args)):
                found[t["transcript"]] = t
    except GraphError as e:
        return cur, {"source": key, "status": "unavailable", "reason": str(e)}
    new, pending, dup = [], [], []
    for t in sorted(found.values(), key=lambda x: x["created"]):
        if t["transcript"] in seen or t["created"][:10] < since:
            continue
        with tempfile.TemporaryDirectory() as td:
            vtt = os.path.join(td, "t.vtt")
            try:
                run(scfg, "transcripts", "--meeting", t["meeting"], "--transcript", t["transcript"], "--vtt",
                    "--output", vtt, timeout=300)
                turns = TR.stitch(TR.read_cues(vtt))
            except (GraphError, ValueError, OSError) as e:
                pending.append({"transcript": t["transcript"][-12:], "subject": t["subject"], "reason": str(e)[:200]})
                continue
        seen.add(t["transcript"])
        if not turns:
            continue
        for x in turns:
            x["speaker"] = cfg.canonical(x["speaker"]) or x["speaker"]
        eid = S.make_id(t["created"], key, t["transcript"])
        item = {"id": eid, "source": key, "kind": "meeting", "meeting_kind": meeting_kind(t["subject"], scfg),
                "at": t["created"], "title": t["subject"], "turns": turns,
                "meta": {"transcript": "teams", "speakers": sorted({x["speaker"] for x in turns})}}
        if S.write_evidence(store, item):
            new.append(eid)
        elif S.duplicate_of(store, item):
            dup.append({"subject": t["subject"], "date": t["created"][:10], "same_as": S.duplicate_of(store, item)})
    # re-list the last window next time: a transcript can appear hours after its meeting
    cur.update(seen=sorted(seen), last_window=(dt.date.today() - dt.timedelta(days=WINDOW_DAYS)).isoformat())
    return cur, {"source": key, "status": "ok", "new": new, "pending": pending, "duplicates": dup}


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or "UTC")
    except Exception:
        return None


def _local(when, z):
    """Graph {dateTime, timeZone} (UTC unless a Prefer header said otherwise) -> naive ISO in the project zone."""
    s = (when or {}).get("dateTime", "")[:19]
    if not s or not z or (when or {}).get("timeZone", "UTC") not in ("UTC", "Coordinated Universal Time"):
        return s
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).astimezone(z).replace(tzinfo=None).isoformat()


def _tokens(s):
    s = re.sub(r"-?\d{8}_\d{6}(utc)?.*$", "", re.sub(r"[\u200b-\u200f\ufeff]", "", (s or "").lower()))  # Teams recording suffix: -20260921_165844UTC-Meeting Recording
    return {t for t in re.findall(r"[a-z0-9]+", s) if len(t) > 1 and t not in {"meeting", "recording", "the", "and"}}


_listing_cache = {}


def find_teams_transcript(scfg, title, at_utc, out_vtt, window_min=90):
    """The Teams transcript of the meeting a recording came from: same day, best title overlap, start within
    `window_min`. Downloads it to `out_vtt` and returns its subject, else None. Speakers come for free."""
    day = at_utc[:10]
    want = _tokens(title)
    months = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
    words = [w for w in re.findall(r"[A-Za-z]+", re.sub(r"[\u200b-\u200f\ufeff]", "", title))
             if len(w) > 2 and w.lower() not in months and w.lower() not in {"meeting", "recording", "the", "and"}]
    cands = []
    for word in words[:3]:  # msgraph lists meeting ids only with --subject: a coarse filter, scored below
        key = (day, word.lower())
        if key not in _listing_cache:
            _listing_cache[key] = parse_listing(run(scfg, "transcripts", "--start", day, "--end", day, "--subject", word))
        cands = _listing_cache[key]
        if cands:
            break
    t0 = dt.datetime.fromisoformat(at_utc[:19])
    best = None
    for t in cands:
        got = _tokens(t["subject"])
        overlap = len(want & got) / max(1, len(want | got))
        gap = abs((dt.datetime.fromisoformat(t["created"]) - t0).total_seconds()) / 60
        if overlap >= 0.5 and gap <= window_min and (not best or (overlap, -gap) > best[0]):
            best = ((overlap, -gap), t)
    if not best:
        return None
    t = best[1]
    run(scfg, "transcripts", "--meeting", t["meeting"], "--transcript", t["transcript"], "--vtt", "--output", out_vtt,
        timeout=300)
    return t["subject"]


def sync_calendar(cfg, key, scfg, store, cursor):
    """Calendar events in a past+future window, as evidence (subject, time, organizer, attendees, cancelled).

    Needs `msgraph calendar --start --end --json`; an msgraph without past-range listing is reported, not guessed
    around.
    """
    cur = dict(cursor or {"events": {}})
    seen = cur.get("events") or {}
    back, ahead = int(scfg.get("days_back", 14)), int(scfg.get("days_ahead", 21))
    a = (dt.date.today() - dt.timedelta(days=back)).isoformat()
    if scfg.get("since"):
        a = max(a, scfg["since"]) if seen else scfg["since"]
    b = (dt.date.today() + dt.timedelta(days=ahead)).isoformat()
    try:
        out = run(scfg, "calendar", "--start", a + "T00:00:00", "--end", b + "T23:59:59", "--limit", "500", "--json")
        events = json.loads(out)
    except GraphError as e:
        return cur, {"source": key, "status": "unavailable", "reason": str(e)}
    except json.JSONDecodeError:
        return cur, {"source": key, "status": "unsupported", "reason": "msgraph `calendar --json` returned no JSON"}
    if events and min(e.get("start", {}).get("dateTime", "9") for e in events) > dt.date.today().isoformat() and back:
        return cur, {"source": key, "status": "unsupported",
                     "reason": "this msgraph `calendar` lists only upcoming events; it needs --start/--end support "
                               "for a past range (calendarView with the given window)"}
    z = _zone(cfg.timezone)
    pats = [p.lower() for p in scfg.get("subjects") or []]
    new, changed = [], []
    for e in events:
        subj = e.get("subject") or ""
        if pats and not any(p in subj.lower() for p in pats):
            continue
        if any(x.lower() in subj.lower() for x in scfg.get("exclude_subjects") or []):
            continue  # e.g. the recurring daily call, whose transcript already says everything its event would
        start, end = _local(e.get("start"), z), _local(e.get("end"), z)
        sig = f"{start}|{end}|{e.get('isCancelled')}|{subj}"
        if seen.get(e["id"]) == sig:
            continue
        att = [cfg.canonical((x.get("emailAddress") or {}).get("name") or (x.get("emailAddress") or {}).get("address"))
               for x in e.get("attendees") or []]
        org = cfg.canonical(((e.get("organizer") or {}).get("emailAddress") or {}).get("name"))
        prev = seen.get(e["id"])
        now = dt.datetime.now(z).replace(tzinfo=None).isoformat(timespec="seconds") if z else \
            dt.datetime.now().isoformat(timespec="seconds")
        what = "cancelled" if e.get("isCancelled") else ("moved" if prev and prev.split("|")[0] != start else
                                                          "updated" if prev else
                                                          "ended" if end and end < now else "scheduled")
        text = (f"Calendar event “{subj}” {what}: {start.replace('T', ' ')} to {end[11:16]}"
                f"{' (cancelled)' if e.get('isCancelled') else ''}. Organizer: {org}. Attendees: {', '.join(a for a in att if a)}.")
        if prev and what == "moved":
            text += f" Previously {prev.split('|')[0].replace('T', ' ')}."
        # an event is evidence from the moment it is known: a past one when it ended, a future one now
        at = min(end or start, now)
        eid = S.make_id(at, key, f"{e['id']}|{sig}")
        S.write_evidence(store, {"id": eid, "source": key, "kind": "event", "at": at, "title": subj,
                                 "text": text, "meta": {"what": what, "start": start, "end": end,
                                                        "organizer": org, "cancelled": bool(e.get("isCancelled")),
                                                        "online": bool(e.get("isOnlineMeeting"))}})
        (changed if prev else new).append(eid)
        seen[e["id"]] = sig
    cur["events"] = seen
    return cur, {"source": key, "status": "ok", "new": new, "changed": changed}
