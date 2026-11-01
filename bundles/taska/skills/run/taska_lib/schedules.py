"""Schedules: a project's [[schedules]] (a preview to the owner, a gated send to the reader, or an update of the tree
only), and the decisions a scheduled run makes. Every decision is a pure function of what it is given.

- `week_for(today)`: a run on Fri–Sun reports on the current Mon–Fri week, a run on Mon–Thu on the previous one.
- `ready(...)`: every project event of the week that has ended needs a transcript in the store; from the deadline
  (the one scheduled Friday run) the preview goes anyway and names what is missing.
- `again(...)`: a preview that already went goes again only when its run failed (at most 3 times, and not when only
  the owner's choices were missing) or when a meeting arrived after it.
- `next_run(...)`: when a schedule fires next, in the owner's local time."""
import datetime as dt
import json
import os
import re
import subprocess
import sys
from xml.sax.saxutils import escape
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
    review: bool = False


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
        if s.get("review") and s["kind"] != "send":
            raise ValueError(f"schedule {name}: review applies to a send schedule (the owner looks first, then sends by command)")
        out.append(Schedule(name, s["kind"], days, s["time"], list(s.get("to") or []), list(s.get("cc") or []), inc,
                            bool(s.get("on", True)), bool(s.get("review", False))))
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
        if s.kind == "send":
            lines.append(f"review = {str(bool(s.review)).lower()}")
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


def with_owner(text, owner):
    """`text` with `owner = "…"` as the first key of [report]: the existing line replaced, else inserted after the
    header, else a [report] table appended. Every other byte stays."""
    if not ADDRESS.match(owner or ""):
        raise ValueError(f"owner must be one address: {owner!r}")
    line = f"owner = {_toml_str(owner)}"
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == "[report]"), None)
    if start is None:
        return text.rstrip("\n") + ("\n\n" if text.strip() else "") + f"[report]\n{line}\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    for i in range(start + 1, end):
        if re.match(r"\s*owner\s*=", lines[i]):
            lines[i] = line + "\n"
            return "".join(lines)
    lines.insert(start + 1, line + "\n")
    return "".join(lines)


def save(project_dir, schedules, owner=None):
    """Rewrite <project>/taska.toml with these schedules (and, when given, [report].owner): every other byte stays,
    the old file is kept as taska.toml.bak, and the swap is atomic (temp file + os.replace)."""
    path = Path(project_dir) / "taska.toml"
    before = path.read_text()
    kept = strip_blocks(before)
    if owner is not None:
        kept = with_owner(kept, owner)
    kept = kept.rstrip("\n")
    text = kept + ("\n\n" if kept else "") + to_toml(schedules)
    (path.parent / "taska.toml.bak").write_text(before)
    tmp = path.with_name("taska.toml.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return path


# ---------- the registrar: one OS job per schedule (launchd on a Mac, Task Scheduler on Windows) ----------

DAY_NAMES = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday", 7: "Sunday"}
FRESH_DAYS = 7   # a runner lock younger than this belongs to a live machine


class RunnerBusy(ValueError):
    """Another machine registered this project's schedules recently."""


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def label(project_name, s):
    return f"com.taska.{_slug(project_name)}.{_slug(s.name)}"


def task_name(project_name, s):
    return "\\taska\\" + _slug(project_name) + "\\" + _slug(s.name)


def plist(lbl, command, s, log):
    """A launchd agent: `/bin/zsh -lic <command>` at HH:MM on each of the schedule's days; never at load."""
    hh, mm = (int(x) for x in s.time.split(":"))
    cal = "".join(f"\t\t<dict>\n\t\t\t<key>Hour</key>\n\t\t\t<integer>{hh}</integer>\n\t\t\t<key>Minute</key>\n\t\t\t<integer>{mm}</integer>\n"
                  f"\t\t\t<key>Weekday</key>\n\t\t\t<integer>{d % 7}</integer>\n\t\t</dict>\n" for d in s.days)   # ISO 7 -> launchd 0
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            f"\t<key>Label</key>\n\t<string>{escape(lbl)}</string>\n"
            "\t<key>ProgramArguments</key>\n\t<array>\n\t\t<string>/bin/zsh</string>\n\t\t<string>-lic</string>\n"
            f"\t\t<string>{escape(command)}</string>\n\t</array>\n"
            "\t<key>RunAtLoad</key>\n\t<false/>\n"
            f"\t<key>StandardErrorPath</key>\n\t<string>{escape(str(log))}</string>\n"
            f"\t<key>StandardOutPath</key>\n\t<string>{escape(str(log))}</string>\n"
            f"\t<key>StartCalendarInterval</key>\n\t<array>\n{cal}\t</array>\n</dict>\n</plist>\n")


def _split_command(command):
    """'"C:\\x\\pythonw.exe" "C:\\t\\taska.py" --project ...' -> (program, arguments) for a Task Scheduler <Exec>."""
    m = re.match(r'\s*("([^"]+)"|(\S+))\s*(.*)$', command, re.S)
    return (m.group(2) or m.group(3)), m.group(4).strip()


def task_xml(name, command, s):
    """A Task Scheduler task: weekly on the schedule's days at HH:MM, hidden, as the logged-on user, run once at
    wake when the time was missed (StartWhenAvailable)."""
    prog, args = _split_command(command)
    days = "".join(f"          <{DAY_NAMES[d]} />\n" for d in sorted(set(s.days)))
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            f"  <RegistrationInfo><Description>taska schedule {escape(s.name)} ({escape(name)})</Description></RegistrationInfo>\n"
            "  <Triggers>\n    <CalendarTrigger>\n"
            f"      <StartBoundary>2026-01-05T{escape(s.time)}:00</StartBoundary>\n      <Enabled>true</Enabled>\n"
            f"      <ScheduleByWeek>\n        <DaysOfWeek>\n{days}        </DaysOfWeek>\n        <WeeksInterval>1</WeeksInterval>\n      </ScheduleByWeek>\n"
            "    </CalendarTrigger>\n  </Triggers>\n"
            "  <Principals><Principal id=\"Author\"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n"
            "  <Settings>\n    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
            "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n    <StartWhenAvailable>true</StartWhenAvailable>\n"
            "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n    <Hidden>true</Hidden>\n    <Enabled>true</Enabled>\n"
            "    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>\n  </Settings>\n"
            f"  <Actions Context=\"Author\"><Exec><Command>{escape(prog)}</Command><Arguments>{escape(args, {chr(34): '&quot;'})}</Arguments></Exec></Actions>\n"
            "</Task>\n")


SEND_FLAG = "--send"


def default_command(cfg, s, send=False):
    """`taska run-schedule <name>` with this interpreter (pythonw on Windows: no console window); `send` adds --send."""
    py = sys.executable
    if sys.platform == "win32":
        py = re.sub(r"python(\.exe)?$", r"pythonw\1", py, flags=re.I)
    taska = Path(__file__).resolve().parent.parent / "taska.py"
    return f'"{py}" "{taska}" --project "{cfg.project_dir}" run-schedule "{s.name}"' + (f" {SEND_FLAG}" if send else "")


def command_for(cfg, s, send=False):
    """The project's [run].scheduled template ({kind}, {name}, {project}, {flags}) when set, else the default command.
    {flags} is --send when the owner sends a reviewed report now, else empty."""
    tpl = (cfg.run or {}).get("scheduled")
    if not tpl:
        return default_command(cfg, s, send)
    return tpl.format(kind=s.kind, name=s.name, project=str(cfg.project_dir), flags=SEND_FLAG if send else "").rstrip()


def _launchctl(verb, path):
    subprocess.run(["launchctl", verb, path], capture_output=True, text=True)


def _schtasks(verb, name, xml=None):
    if verb == "create":
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", name, "/XML", str(xml)], capture_output=True, text=True)
    elif verb == "delete":
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", name], capture_output=True, text=True)
    else:
        r = subprocess.run(["schtasks", "/Query", "/FO", "CSV", "/NH"], capture_output=True, text=True)
    return r.stdout


def _lock_path(cfg):
    return Path(cfg.store) / "runner.json"


def runner_lock(cfg):
    """{machine, at} of the machine that registered this project's schedules, or None."""
    try:
        return json.loads(_lock_path(cfg).read_text())
    except (OSError, ValueError):
        return None


def claim_runner(cfg, machine, now=None, fresh_days=FRESH_DAYS, force=False):
    """Name `machine` as the one runner. Refuses (RunnerBusy) while another machine's claim is younger than
    `fresh_days`, unless `force` (the owner says that machine is gone)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cur = runner_lock(cfg)
    if cur and cur.get("machine") != machine and not force:
        try:
            age = now - dt.datetime.fromisoformat(cur["at"])
        except (KeyError, ValueError):
            age = dt.timedelta(days=fresh_days)
        if age < dt.timedelta(days=fresh_days):
            raise RunnerBusy(f"{cur['machine']} registered these schedules on {cur['at'][:10]}; one machine runs a project "
                             f"(taska handover, or --force when that machine is gone)")
    lock = {"machine": machine, "at": now.isoformat(timespec="seconds")}
    _lock_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    _lock_path(cfg).write_text(json.dumps(lock))
    return lock


def _machine():
    import socket
    return socket.gethostname()


def register(cfg, agents_dir=None, launchctl=None, schtasks=None, machine=None, now=None, force=False):
    """Write, (re)load or unload one OS job per schedule. Returns one record per schedule:
    {name, label, path, action: loaded|unloaded|unsupported}. Claims the one-runner lock first."""
    labels = {}
    for s in cfg.schedules:
        lbl = label(cfg.name, s)
        if lbl in labels:
            raise ValueError(f"schedules {labels[lbl]!r} and {s.name!r} would get the same label {lbl}")
        labels[lbl] = s.name
    if sys.platform not in ("darwin", "win32"):
        return [{"name": s.name, "label": label(cfg.name, s), "path": None, "action": "unsupported"} for s in cfg.schedules]
    if cfg.schedules:
        claim_runner(cfg, machine or _machine(), now, force=force)
    out = []
    if sys.platform == "win32":
        run = schtasks or _schtasks
        xml_dir = Path(cfg.store) / "tasks"
        for s in cfg.schedules:
            name = task_name(cfg.name, s)
            if not s.on:
                run("delete", name)
                out.append({"name": s.name, "label": name, "path": None, "action": "unloaded"})
                continue
            xml_dir.mkdir(parents=True, exist_ok=True)
            path = xml_dir / f"{_slug(s.name)}.xml"
            path.write_text(task_xml(name, command_for(cfg, s), s), encoding="utf-16")
            run("create", name, path)
            out.append({"name": s.name, "label": name, "path": str(path), "action": "loaded"})
        return out
    agents = Path(agents_dir) if agents_dir else Path.home() / "Library/LaunchAgents"
    run = launchctl or _launchctl
    for s in cfg.schedules:
        lbl = label(cfg.name, s)
        cmd = command_for(cfg, s)
        path = agents / f"{lbl}.plist"
        run("unload", str(path))   # always: a job loaded under this label from an older file must not run twice
        if not s.on:
            if path.exists():
                path.unlink()
            out.append({"name": s.name, "label": lbl, "path": None, "action": "unloaded"})
            continue
        agents.mkdir(parents=True, exist_ok=True)
        log = cfg.project_dir / "logs" / f"{_slug(s.name)}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plist(lbl, cmd, s, log))
        run("load", str(path))
        out.append({"name": s.name, "label": lbl, "path": str(path), "action": "loaded"})
    return out


def unregister(cfg, agents_dir=None, launchctl=None, schtasks=None):
    """Remove every job of this project and release the runner lock (the handover from this machine)."""
    out = []
    if sys.platform == "win32":
        run = schtasks or _schtasks
        for s in cfg.schedules:
            run("delete", task_name(cfg.name, s))
            out.append({"name": s.name, "label": task_name(cfg.name, s), "path": None, "action": "unloaded"})
    elif sys.platform == "darwin":
        agents = Path(agents_dir) if agents_dir else Path.home() / "Library/LaunchAgents"
        run = launchctl or _launchctl
        for s in cfg.schedules:
            path = agents / f"{label(cfg.name, s)}.plist"
            run("unload", str(path))
            path.unlink(missing_ok=True)
            out.append({"name": s.name, "label": label(cfg.name, s), "path": None, "action": "unloaded"})
    _lock_path(cfg).unlink(missing_ok=True)
    return out


def registered(project_name, agents_dir=None, schtasks=None):
    """{label: plist path} (Mac) or {task name: next run} (Windows) of this project's jobs on this machine."""
    if sys.platform == "win32":
        prefix = task_name(project_name, Schedule("x", "update", (1,), "00:00")).rsplit("\\", 1)[0] + "\\"
        out = {}
        for line in (schtasks or _schtasks)("query", "").splitlines():
            cells = [c.strip('"') for c in line.strip().split('","')]
            if len(cells) >= 2 and cells[0].startswith(prefix):
                out[cells[0]] = cells[1]
        return out
    agents = Path(agents_dir) if agents_dir else Path.home() / "Library/LaunchAgents"
    prefix = f"com.taska.{_slug(project_name)}."
    return {p.stem: str(p) for p in sorted(agents.glob(f"{prefix}*.plist"))} if agents.is_dir() else {}
