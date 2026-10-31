"""Schedules: a project's [[schedules]] (a preview to the owner, a gated send to the reader, or an update of the tree
only), and the decisions a scheduled run makes. Every decision is a pure function of what it is given.

- `week_for(today)`: a run on Fri–Sun reports on the current Mon–Fri week, a run on Mon–Thu on the previous one.
- `ready(...)`: every project event of the week that has ended needs a transcript in the store; from the deadline
  (the one scheduled Friday run) the preview goes anyway and names what is missing.
- `again(...)`: a preview that already went goes again only when its run failed (at most 3 times, and not when only
  the owner's choices were missing) or when a meeting arrived after it.
- `next_run(...)`: when a schedule fires next, in the owner's local time."""
import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

KINDS = ("preview", "send", "update")
TRANSCRIPT_ADAPTERS = ("meetings_folder", "msgraph_transcripts")
DEADLINE = dt.time(20, 0)
INCLUDE = {"body": True, "docx": True, "link": True, "pptx": False, "workbench": False}
# words every project title shares: two titles match on the words that tell meetings apart
COMMON = {"meeting", "sync", "weekly", "with", "from", "part", "internal", "client", "call", "session", "status", "report"}
MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Schedule:
    name: str
    kind: str
    days: tuple
    time: str
    to: list = field(default_factory=list)
    cc: list = field(default_factory=list)
    include: dict = field(default_factory=lambda: dict(INCLUDE))
    on: bool = True


def _all_words(s):
    return set(re.findall(r"[a-z]{2,}", (s or "").lower()))


def _words(s, common):
    return {w for w in _all_words(s) if len(w) >= 4 and w not in COMMON and w not in common}


def same_meeting(event_subject, title, common=()):
    """Two titles name the same meeting when they share 2 distinctive words (fewer when the event has fewer). The
    project's own name words (`common`) never count. An event with no distinctive word is matched on all its words."""
    common = {w.lower() for w in common}
    want = _words(event_subject, common)
    if want:
        return len(want & _words(title, common)) >= min(2, len(want))
    allw = _all_words(event_subject)
    return bool(allw) and allw <= _all_words(title)


def sync_sources(sources):
    """The sources to sync before readiness is judged: every transcript source, then the calendar. Files and events
    only: no extraction, no agent."""
    return [k for k, s in sources.items() if s.get("adapter") in TRANSCRIPT_ADAPTERS] + \
           [k for k, s in sources.items() if s.get("adapter") == "msgraph_calendar"]


def preview_week(today):
    """The week a Friday-evening (or weekend) preview is for: the Monday of that week."""
    return (today - dt.timedelta(days=today.weekday())).isoformat()


def send_week(today):
    """The week a Monday send is for: the previous week."""
    return (today - dt.timedelta(days=today.weekday() + 7)).isoformat()


def week_for(today):
    """The week a scheduled run reports on: the current week from Friday, the previous week until Thursday."""
    return preview_week(today) if today.weekday() >= 4 else send_week(today)


def ready(events, meetings, now, week, tz, deadline=DEADLINE, common=()):
    """(go, missing). Every project event of the week that has ended needs a transcript: a meeting in the store on the
    same local day sharing >= 2 distinctive title words. go is True when none is missing, or from the deadline on the
    week's Friday regardless; missing names the meetings still without one."""
    monday = dt.date.fromisoformat(week)
    z = ZoneInfo(tz)
    missing = []
    for e in events:
        subj = e.get("subject") or ""
        if subj.lower().startswith(("canceled", "cancelled")) or not e.get("end"):
            continue
        end = dt.datetime.fromisoformat(e["end"])
        day = dt.datetime.fromisoformat(e["start"]).astimezone(z).date()
        if not (monday <= day < monday + dt.timedelta(days=7)) or end > now:
            continue
        if not any(m.get("date") == day.isoformat() and same_meeting(subj, m.get("title"), common) for m in meetings):
            missing.append(f"{subj} ({MON[day.month - 1]} {day.day})")
    cutoff = dt.datetime.combine(monday + dt.timedelta(days=4), deadline, tzinfo=now.tzinfo)
    return (not missing or now >= cutoff), missing


def again(sent, meetings):
    """(go, why) for a week whose preview already ran: once more when that run failed (at most 3 runs), or when the
    week has meetings that preview did not include. An older marker without the list of meetings never re-sends."""
    waits_for_you = sent.get("failed") == ["presend_gate"]   # only the owner's choices are open: a retry changes nothing
    if sent.get("run_exit", 0) != 0 and sent.get("attempts", 1) < 3 and not waits_for_you:
        return True, "the last run failed"
    new = [m for m in meetings if "meetings" in sent and m not in sent["meetings"]]
    if new:
        return True, "new meetings since the last preview: " + ", ".join(new)
    return False, "already sent; it waits for your choices on the pre-send page" if waits_for_you else "already sent"


def lock_busy(lock):
    """True while a live process holds the run lock (a lock left by a killed run is not busy)."""
    lock = Path(lock)
    try:
        pid = int((lock / "pid").read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def parse(raw):
    """[[schedules]] entries -> [Schedule]; raises ValueError naming the bad field."""
    out, names = [], set()
    for s in raw or []:
        name = s.get("name")
        if not name or name in names:
            raise ValueError(f"[[schedules]].name must be present and unique: {name!r}")
        names.add(name)
        if s.get("kind") not in KINDS:
            raise ValueError(f"schedule {name}: kind must be one of {KINDS}")
        days = tuple(s.get("days") or ())
        if not days or any(d not in range(1, 8) for d in days):
            raise ValueError(f"schedule {name}: days must be ISO weekdays (1-7)")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(s.get("time") or "")):
            raise ValueError(f"schedule {name}: time must be HH:MM")
        for k in ("to", "cc"):
            bad = [a for a in s.get(k) or [] if not ADDRESS.match(str(a))]
            if bad:
                raise ValueError(f"schedule {name}: {k} has a malformed address: {bad}")
        inc = dict(INCLUDE)
        unknown = set(s.get("include") or {}) - set(INCLUDE)
        if unknown:
            raise ValueError(f"schedule {name}: include has unknown items {sorted(unknown)}; known: {sorted(INCLUDE)}")
        inc.update({k: bool(v) for k, v in (s.get("include") or {}).items()})
        out.append(Schedule(name, s["kind"], days, s["time"], list(s.get("to") or []), list(s.get("cc") or []), inc,
                            bool(s.get("on", True))))
    return out


def next_run(s, now):
    """The next time schedule `s` fires after `now` (an aware datetime), or None when it is off."""
    if not s.on:
        return None
    hh, mm = (int(x) for x in s.time.split(":"))
    for d in range(0, 8):
        t = (now + dt.timedelta(days=d)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t.isoweekday() in s.days and t > now:
            return t
    return None


# ---------- the writer: [[schedules]] blocks are the only part of taska.toml it rewrites ----------

def _toml_str(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_toml(schedules):
    """[Schedule] -> the [[schedules]] blocks as TOML text (every field written, so a reader sees the defaults)."""
    blocks = []
    for s in schedules:
        lines = ["[[schedules]]", f"name = {_toml_str(s.name)}", f"kind = {_toml_str(s.kind)}",
                 "days = [" + ", ".join(str(d) for d in s.days) + "]", f"time = {_toml_str(s.time)}",
                 "to = [" + ", ".join(_toml_str(a) for a in s.to) + "]",
                 "cc = [" + ", ".join(_toml_str(a) for a in s.cc) + "]",
                 "include = {" + ", ".join(f"{k} = {str(bool(s.include.get(k, v))).lower()}" for k, v in INCLUDE.items()) + "}",
                 f"on = {str(bool(s.on)).lower()}"]
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)


def strip_blocks(text):
    """`text` without its [[schedules]] blocks: a block runs from its header to the next table header."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        bare = line.strip()
        if bare.startswith("["):
            skipping = bare.startswith("[[schedules]]")
        if not skipping:
            out.append(line)
    return "".join(out)


def save(project_dir, schedules):
    """Rewrite <project>/taska.toml with these schedules: every other byte stays, the old file is kept as
    taska.toml.bak, and the swap is atomic (temp file + os.replace)."""
    path = Path(project_dir) / "taska.toml"
    before = path.read_text()
    kept = strip_blocks(before).rstrip("\n")
    text = kept + ("\n\n" if kept else "") + to_toml(schedules)
    (path.parent / "taska.toml.bak").write_text(before)
    tmp = path.with_name("taska.toml.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return path
