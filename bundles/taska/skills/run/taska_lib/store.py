"""The project store: append-only events, normalised evidence items, per-source cursors.

Layout under `[project].store` (default `.taska/`):
  events.jsonl            every ledger event (create, agent, human, verdict, note, processed)
  evidence/<id>.json      one evidence item: a meeting, calendar event, file or PR, normalised
  evidence/<id>.md        the same item rendered for the agent to read
  cursors.json            what each source has already delivered
  plan.json               epics as loaded at init
  proposals/ verify/ out/ working files
"""
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from .text import WD

KINDS = ("meeting", "event", "file", "pr", "issue", "message")


def P(store, *a):
    return Path(store, *a)


def ensure(store):
    for d in ("evidence", "proposals", "verify", "out"):
        P(store, d).mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def locked(store):
    """Serialise read-modify-append sections (id allocation in `apply` and in the workbench's human events): two
    writers that both count `create` events would otherwise hand out the same S007. Advisory flock, per open file
    description, so it also holds between threads; a platform without fcntl runs unlocked as before."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    P(store).mkdir(parents=True, exist_ok=True)
    fd = os.open(P(store, ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def events(store):
    p = P(store, "events.jsonl")
    return [json.loads(l) for l in p.open()] if p.exists() else []


def append(store, recs):
    if not recs:
        return
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs).encode()
    fd = os.open(P(store, "events.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:  # one O_APPEND write per call (as brain's decisions.jsonl): concurrent appenders never interleave lines
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)


def evidence(store):
    d = P(store, "evidence")
    if not d.exists():
        return {}
    return {x["id"]: x for x in (json.loads(f.read_text()) for f in sorted(d.glob("*.json")))}  # *.json.tmp is skipped


def order(ev):
    """Evidence ids in time order; a client call sorts after an internal one on the same timestamp. A copy of a
    meeting already in the store (`duplicate_of`, see repair.py) is left out: nothing it produced counts twice."""
    return sorted((i for i in ev if not ev[i].get("duplicate_of")),
                  key=lambda i: (ev[i]["at"], ev[i].get("meeting_kind") == "client", i))


def make_id(at, source, key):
    return f"{at[:10]}_{source}_{hashlib.sha256(key.encode()).hexdigest()[:8]}"


def fingerprint(item):
    """Same meeting from two sources (a synced .vtt and the Teams copy): same day, same opening words."""
    from .text import norm
    words = norm(" ".join(t["text"] for t in item.get("turns") or [])).split()
    return f"{item['at'][:10]}:{' '.join(words[:40])}" if len(words) >= 20 else None


SAME_TEXT = 0.9      # share of the shorter transcript's 5-word phrases also in the other: one meeting, two files
SAME_TEXT_DAYS = 31  # how far apart two files of one meeting can be dated (a guessed date is the day it arrived)


def shingles(item, k=5):
    from .text import norm
    w = norm(" ".join(t["text"] for t in item.get("turns") or [])).split()
    return {" ".join(w[i:i + k]) for i in range(len(w) - k + 1)} if len(w) >= 40 else set()


def is_teams(x):
    """A transcript taken from Teams (msgraph_transcripts): dated by when the meeting happened."""
    return (x.get("meta") or {}).get("transcript") == "teams"


def same_text(a, b, sa=None, sb=None):
    """Two meeting items hold one transcript dated two ways: the same text within SAME_TEXT_DAYS, where at least one
    date was only guessed from the file's arrival ("file_mtime"), or where one is the Teams copy and the other a file
    named after the day it was saved (C-09). Two files that each carry their own date are two meetings, however alike."""
    if "file_mtime" not in (a.get("date_source"), b.get("date_source")) and is_teams(a) == is_teams(b):
        return False
    sa, sb = sa if sa is not None else shingles(a), sb if sb is not None else shingles(b)
    if not sa or not sb:
        return False
    if abs((dt.date.fromisoformat(a["at"][:10]) - dt.date.fromisoformat(b["at"][:10])).days) > SAME_TEXT_DAYS:
        return False
    return len(sa & sb) / min(len(sa), len(sb)) >= SAME_TEXT


def duplicate_of(store, item):
    """The id of the meeting item this one repeats, else None: same day and opening words (two sources of one
    call), or the same transcript under another date (a re-synced file dated by the day it arrived)."""
    if item["kind"] != "meeting":
        return None
    fp, sh = fingerprint(item), shingles(item)
    for x in evidence(store).values():
        if x["id"] == item["id"] or x["kind"] != "meeting" or x.get("duplicate_of"):
            continue
        if (fp and fingerprint(x) == fp) or same_text(item, x, sh):
            return x["id"]
    return None


def write_evidence(store, item):
    """Write one evidence item (+ its .md). Returns False when it is already there, identical or as another
    source's copy of the same meeting."""
    assert item["kind"] in KINDS, item["kind"]
    item.setdefault("date", item["at"][:10])
    if item["kind"] == "meeting":
        o = duplicate_of(store, item)
        if o:
            if is_teams(item):
                take_teams_time(store, o, item)
            return False
    p = P(store, "evidence", item["id"] + ".json")
    body = json.dumps(item, ensure_ascii=False, indent=0)
    if p.exists() and p.read_text() == body:
        return False
    md = render_md(item)  # may raise on a malformed item: before anything is written
    P(store, "evidence", item["id"] + ".md").write_text(md)
    tmp = p.with_suffix(".json.tmp")  # the .json is the index: a half-written one would break every later command
    tmp.write_text(body)
    os.replace(tmp, p)
    return True


def save_evidence(store, x):
    """Rewrite an evidence item: the .md first (it may raise on a malformed item), then the .json, the index,
    swapped in whole so a crash never leaves it half-written."""
    P(store, "evidence", x["id"] + ".md").write_text(render_md(x))
    p = P(store, "evidence", x["id"] + ".json")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(x, ensure_ascii=False, indent=0))
    os.replace(tmp, p)


def rerender(store):
    """Rewrite each evidence .md that differs from render_md of its .json (the .md is derived; the .json, the index, is
    never touched). Returns how many were rewritten."""
    n = 0
    for x in evidence(store).values():
        p = P(store, "evidence", x["id"] + ".md")
        md = render_md(x)
        if not p.exists() or p.read_text() != md:
            p.write_text(md)
            n += 1
    return n


def take_teams_time(store, eid, teams):
    """The folder copy `eid` of the meeting `teams` holds takes the Teams time: same id, so its events (which the fold
    dates by their evidence item) and every mid stay valid. Done only when the day differs."""
    p = P(store, "evidence", eid + ".json")
    x = json.loads(p.read_text())
    if is_teams(x) or x["date"] == teams["at"][:10]:
        return False
    x.update(at=teams["at"], date=teams["at"][:10], date_source="teams", redated_from=x["date"], redated_by=teams["id"])
    save_evidence(store, x)
    return True


MAX_LINE = 1500   # Claude Code's Read shows ~2000 characters of a line: a longer one hides the rest from the agent


def _lines(text, prefix=""):
    """`text` in lines of at most MAX_LINE characters, each starting with `prefix`: split after a sentence end when one
    is close enough, otherwise between words. Every word is kept, in order."""
    room = MAX_LINE - len(prefix)
    out, rest = [], " ".join(text.split())
    while len(rest) > room:
        cut = max(rest.rfind(". ", 0, room), rest.rfind("? ", 0, room), rest.rfind("! ", 0, room))
        cut = cut + 1 if cut > room // 2 else rest.rfind(" ", 0, room)
        if cut <= 0:
            cut = room
        out.append(prefix + rest[:cut].strip())
        rest = rest[cut:].strip()
    return out + [prefix + rest]


def render_md(x):
    wd = WD[dt.date.fromisoformat(x["date"]).weekday()].title()
    head = [f"# {x['title']}", f"evidence: {x['id']} · {x['date']} ({wd}) · {x['kind']}"
            + (f" · {x['meeting_kind']}" if x.get("meeting_kind") else "") + f" · source {x['source']}"]
    if x.get("url"):
        head.append(f"link: {x['url']}")
    for k, v in (x.get("meta") or {}).items():
        if v not in (None, "", []):
            head += _lines(str(v) if not isinstance(v, list) else ", ".join(map(str, v)), f"{k}: ")
    body = []
    for t in x.get("turns") or []:
        if not t.get("bc"):
            body.append("\n".join(_lines(t["text"], f"[{t['ts']}] {t['speaker']}: ")))
    if x.get("text"):
        body.append("\n".join(_lines(x["text"])))
    return "\n".join(head) + "\n\n" + "\n\n".join(body) + "\n"


def full_text(x, drop_bc=False):
    parts = [t["text"] for t in x.get("turns") or [] if not (drop_bc and t.get("bc"))]
    if x.get("text"):
        parts.append(x["text"])
    parts.append(x.get("title") or "")
    return " ".join(parts)


def quote_offset(item, quote):
    """Index of norm(quote) in norm(full_text(item)), or -1 if quote is absent or empty."""
    from .text import norm
    if not quote:
        return -1
    full = norm(full_text(item))
    q = norm(quote)
    return full.find(q)


def cursors(store):
    p = P(store, "cursors.json")
    return json.loads(p.read_text()) if p.exists() else {}


def save_cursors(store, c):
    p = P(store, "cursors.json")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=1, ensure_ascii=False))
    os.replace(tmp, p)


def update_cursor(store, key, value):
    """Replace one source's cursor, re-reading the file first: a long `transcribe` and a `sync` running at the same
    time never overwrite each other's progress."""
    c = cursors(store)
    c[key] = value
    save_cursors(store, c)


def processed(evs):
    """Evidence ids processed and not re-queued since (a `reprocess` event, written when an item changes after it
    was processed or by `taska.py redo`, puts it back in the queue)."""
    done = set()
    for e in evs:
        if e["type"] == "processed":
            done.add(e["evidence"])
        elif e["type"] == "reprocess":
            done.discard(e["evidence"])
    return done


def requeue(store, ids, why):
    import datetime as _dt
    done = processed(events(store))
    recs = [{"type": "reprocess", "evidence": i, "why": why, "at": _dt.datetime.now().isoformat(timespec="seconds")}
            for i in ids if i in done]
    append(store, recs)
    return [r["evidence"] for r in recs]
