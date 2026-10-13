"""The project store: append-only events, normalised evidence items, per-source cursors.

Layout under `[project].store` (default `.taska/`):
  events.jsonl            every ledger event (create, agent, human, verdict, note, processed)
  evidence/<id>.json      one evidence item: a meeting, calendar event, file or PR, normalised
  evidence/<id>.md        the same item rendered for the agent to read
  cursors.json            what each source has already delivered
  plan.json               epics as loaded at init
  proposals/ verify/ out/ working files
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from .text import WD

KINDS = ("meeting", "event", "file", "pr", "issue")


def P(store, *a):
    return Path(store, *a)


def ensure(store):
    for d in ("evidence", "proposals", "verify", "out"):
        P(store, d).mkdir(parents=True, exist_ok=True)


def events(store):
    p = P(store, "events.jsonl")
    return [json.loads(l) for l in p.open()] if p.exists() else []


def append(store, recs):
    if not recs:
        return
    with P(store, "events.jsonl").open("a") as f:  # one write per call: concurrent appenders never interleave lines
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))


def evidence(store):
    d = P(store, "evidence")
    if not d.exists():
        return {}
    return {x["id"]: x for x in (json.loads(f.read_text()) for f in sorted(d.glob("*.json")))}


def order(ev):
    """Evidence ids in time order; a client call sorts after an internal one on the same timestamp."""
    return sorted(ev, key=lambda i: (ev[i]["at"], ev[i].get("meeting_kind") == "client", i))


def make_id(at, source, key):
    return f"{at[:10]}_{source}_{hashlib.sha256(key.encode()).hexdigest()[:8]}"


def fingerprint(item):
    """Same meeting from two sources (a synced .vtt and the Teams copy): same day, same opening words."""
    from .text import norm
    words = norm(" ".join(t["text"] for t in item.get("turns") or [])).split()
    return f"{item['at'][:10]}:{' '.join(words[:40])}" if len(words) >= 20 else None


def duplicate_of(store, item):
    fp = fingerprint(item)
    if not fp:
        return None
    for x in evidence(store).values():
        if x["id"] != item["id"] and x["kind"] == "meeting" and fingerprint(x) == fp:
            return x["id"]
    return None


def write_evidence(store, item):
    """Write one evidence item (+ its .md). Returns False when it is already there, identical or as another
    source's copy of the same meeting."""
    assert item["kind"] in KINDS, item["kind"]
    item.setdefault("date", item["at"][:10])
    if item["kind"] == "meeting" and duplicate_of(store, item):
        return False
    p = P(store, "evidence", item["id"] + ".json")
    body = json.dumps(item, ensure_ascii=False, indent=0)
    if p.exists() and p.read_text() == body:
        return False
    p.write_text(body)
    P(store, "evidence", item["id"] + ".md").write_text(render_md(item))
    return True


def render_md(x):
    wd = WD[dt.date.fromisoformat(x["date"]).weekday()].title()
    head = [f"# {x['title']}", f"evidence: {x['id']} · {x['date']} ({wd}) · {x['kind']}"
            + (f" · {x['meeting_kind']}" if x.get("meeting_kind") else "") + f" · source {x['source']}"]
    if x.get("url"):
        head.append(f"link: {x['url']}")
    for k, v in (x.get("meta") or {}).items():
        if v not in (None, "", []):
            head.append(f"{k}: {v if not isinstance(v, list) else ', '.join(map(str, v))}")
    body = []
    for t in x.get("turns") or []:
        if not t.get("bc"):
            body.append(f"[{t['ts']}] {t['speaker']}: {t['text']}")
    if x.get("text"):
        body.append(x["text"])
    return "\n".join(head) + "\n\n" + "\n\n".join(body) + "\n"


def full_text(x, drop_bc=False):
    parts = [t["text"] for t in x.get("turns") or [] if not (drop_bc and t.get("bc"))]
    if x.get("text"):
        parts.append(x["text"])
    parts.append(x.get("title") or "")
    return " ".join(parts)


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
