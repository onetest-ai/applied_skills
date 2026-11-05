"""repair_evidence: fix meeting items written before file names like "10.19.2026_<title>.vtt" were read as dates.

Such an item was dated by the day the file arrived ("date_source": "file_mtime"), so a re-synced transcript could
enter the ledger a second time under the wrong day. The repair works on evidence items only, never on events:
  - re-date an item whose only date was its file's mtime but whose file name carries the meeting date;
  - re-date an item dated only by its file's arrival to the calendar event it records (calendar=, tz=): an event that
    is not cancelled, started at most 36 hours before the file arrived, and shares >= 2 title words with it;
  - mark a later copy of the same transcript `duplicate_of` the first one (the extractor and readers skip it).
The fold dates every agent event by its evidence item, so re-dating moves its events to the meeting's day; the
item id keeps its old date prefix (ids are references and never change). Dry run by default.
"""
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

from . import store as S
from .text import date_in_name


_TITLE_STOP = {"with", "from", "part", "that", "this", "placeholder", "canceled", "cancelled"}


def _title_words(s):
    return {w for w in re.findall(r"[a-z]{4,}", (s or "").lower()) if w not in _TITLE_STOP}


def _calendar_event(x, calendar, tz):
    """The calendar event a file-dated meeting records, as (local start, subject), or None."""
    arrived = dt.datetime.fromisoformat(x["at"][:19]).replace(tzinfo=ZoneInfo(tz))
    words = _title_words(x.get("title") or (x.get("meta") or {}).get("file"))
    best = None
    for e in calendar or []:
        if (e.get("subject") or "").lower().startswith(("canceled", "cancelled")):
            continue
        start = dt.datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
        if dt.timedelta(0) <= arrived - start <= dt.timedelta(hours=36) and len(words & _title_words(e["subject"])) >= 2:
            if best is None or start > best[0]:
                best = (start, e["subject"])
    return best


def repair_evidence(store, apply=False, calendar=None, tz="UTC"):
    ev = S.evidence(store)
    redate, at, unmatched = [], {}, []
    for x in sorted(ev.values(), key=lambda x: x["id"]):
        f = (x.get("meta") or {}).get("file") or ""
        if x["kind"] != "meeting" or x.get("date_source") != "file_mtime" or not f:
            continue
        d = date_in_name(f, dt.date.fromisoformat(x["date"]))
        if d and d.isoformat() != x["date"]:
            redate.append({"id": x["id"], "date": d.isoformat(), "from": x["date"]})
        elif not d and calendar is not None:
            hit = _calendar_event(x, calendar, tz)
            if hit and hit[0].date().isoformat() != x["date"]:
                redate.append({"id": x["id"], "date": hit[0].date().isoformat(), "from": x["date"], "by": "calendar",
                               "event": hit[1]})
                at[x["id"]] = hit[0].replace(tzinfo=None).isoformat(timespec="seconds")
            elif not hit:
                unmatched.append(x["id"])
    new = {r["id"]: r["date"] for r in redate}
    view = {i: dict(x, at=at.get(i, f"{new[i]}T12:00:00"), date=new[i]) if i in new else x for i, x in ev.items()}
    meetings = sorted((x for x in view.values() if x["kind"] == "meeting"),
                      key=lambda x: (x.get("date_source") == "file_mtime" or x["id"] in new, x["at"], x["id"]))
    sh = {x["id"]: S.shingles(x) for x in meetings}
    first, duplicates = [], []
    for x in meetings:   # an original first: a file-name or recording date beats a guessed one, then the earlier
        if x.get("duplicate_of"):
            continue
        o = next((y for y in first if S.same_text(x, y, sh[x["id"]], sh[y["id"]])), None)
        if o:
            duplicates.append({"id": x["id"], "duplicate_of": o["id"]})
        else:
            first.append(x)
    if apply:
        dup = {d["id"]: d["duplicate_of"] for d in duplicates}
        for i in set(new) | set(dup):
            x = dict(ev[i])
            if i in new:
                x.update(at=at.get(i, f"{new[i]}T12:00:00"), date=new[i], date_source="calendar" if i in at else "filename",
                         redated_from=ev[i]["date"])
            if i in dup:
                x["duplicate_of"] = dup[i]
            S.save_evidence(store, x)
    r = {"redate": redate, "duplicates": duplicates, "applied": apply}
    if calendar is not None:
        r["guessed_unmatched"] = unmatched   # still dated by file arrival: no calendar event explains them
    return r
