"""The reads a processed item gets after its extraction, by name and version. Raising a version or adding a read is a
code change; nothing else lists reads. A processed item missing a current read has a gap: pending lists it when it was
extracted under the current code (`extracted`), when a person asked (`backfill` events or [run].decisions_since:
`requested`), or, unless [run].decisions_since is set, by itself inside a window back from the newest processed item, at most `backfill_max` per run
(`backfill`). Gaps outside the window are only counted."""
import datetime as dt
from dataclasses import dataclass

from . import store as S
from .ledger import reads_of

BACKFILL_DAYS = 14
BACKFILL_MAX = 5


@dataclass(frozen=True)
class Read:
    name: str
    version: int
    kinds: frozenset[str]


READS: dict[str, Read] = {"decisions": Read("decisions", 1, frozenset({"meeting"}))}

Event = dict[str, object]
Item = dict[str, object]


def anchor(ev: dict[str, Item], evs: list[Event]) -> str | None:
    """The date of the newest processed item in time order: "today" for the window, as staleness takes it."""
    done = S.processed(evs)
    last = [c for c in S.order(ev) if c in done]
    return str(ev[last[-1]]["date"]) if last else None


def window_start(today: str | None, days: int) -> str | None:
    return (dt.date.fromisoformat(today) - dt.timedelta(days=days)).isoformat() if today else None


def _waiting(evs: list[Event]) -> set[str]:
    """Items extracted with their decision pass still to come, since each one's latest reprocess."""
    w: set[str] = set()
    for e in evs:
        if e["type"] == "reprocess":
            w.discard(str(e["evidence"]))
        elif e["type"] == "processed" and e.get("decisions") == "pending":
            w.add(str(e["evidence"]))
    return w


def requested(evs: list[Event], read: Read) -> set[str]:
    """Items a person asked to have this read at its current version (`taska backfill`). The ids sit under
    `evidence_ids`, not `evidence`: every other reader of events takes `evidence` to be one id."""
    return {str(c) for e in evs if e["type"] == "backfill" and e.get("read") == read.name
            and e.get("version") == read.version for c in e.get("evidence_ids") or []}


def gaps(ev: dict[str, Item], evs: list[Event], read: Read) -> list[str]:
    """Processed items of the read's kinds, in time order, without the read at its current version."""
    done, had = S.processed(evs), reads_of(evs)
    return [c for c in S.order(ev) if c in done and ev[c]["kind"] in read.kinds
            and had.get(c, {}).get(read.name, 0) < read.version]


def plan(ev: dict[str, Item], evs: list[Event], run: dict[str, object]) -> tuple[dict[str, tuple[str, str]], dict[str, dict[str, object]]]:
    """({item id: (read name, why)} for the rows pending lists, {read name: gap counts})."""
    days = int(run.get("backfill_days", BACKFILL_DAYS))
    cap = int(run.get("backfill_max", BACKFILL_MAX))
    since = run.get("decisions_since")
    start = window_start(anchor(ev, evs), days)
    waiting = _waiting(evs)
    rows: dict[str, tuple[str, str]] = {}
    report: dict[str, dict[str, object]] = {}
    for name, read in READS.items():
        asked = requested(evs, read)
        alias = name == "decisions" and bool(since)   # deprecated: while set, it alone decides (window backfill is off)
        in_window, outside, listed = 0, 0, 0
        oldest = None
        for c in gaps(ev, evs, read):
            date = str(ev[c]["date"])
            if name == "decisions" and c in waiting:
                rows.setdefault(c, (name, "extracted"))
                continue
            if c in asked or (alias and date >= str(since)):
                rows.setdefault(c, (name, "requested"))
                continue
            oldest = oldest or date
            if start is not None and date >= start:
                in_window += 1
                if not alias and listed < cap and c not in rows:
                    rows[c] = (name, "backfill")
                    listed += 1
            else:
                outside += 1
        report[name] = {"in_window": in_window, "waiting": in_window - listed, "outside_window": outside, "oldest": oldest}
    return rows, report
