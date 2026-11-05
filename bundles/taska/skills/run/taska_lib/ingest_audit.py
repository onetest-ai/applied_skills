"""ingest_audit: flag meetings that are missing, misdated, or truncated.

audit_ingest(ev, expected, calendar, tz) -> dict with:
  A1_missing        - expected meetings absent from the taska store
  A2_date_mismatch  - taska meetings whose date differs from the best calendar match
  A2_guessed        - ids whose date_source == "file_mtime"
  A6_truncated      - ids whose transcript ends before 80% of the meeting duration
  A6_unnamed_share  - {id: float} for meetings with any "?" speaker turns
  A7_room_audio     - meetings where one speaker tag holds >= 80% of >= 1500 words: a room recording, so the
                      speaker tags (and owners read from them) cannot be trusted
"""
import datetime as dt
import re
from zoneinfo import ZoneInfo

from .similar import STOPWORDS
from .text import norm, MONTHS

_MONTH_SET = frozenset(MONTHS)  # "jan", "feb", …, "dec"


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_dt(s, tz_name):
    """Parse an ISO-8601 string (with or without UTC offset / Z) to a naive datetime
    in the project timezone.  A naive input is returned as-is."""
    if not s:
        raise ValueError(f"empty datetime string")
    # Python 3.11+ accepts "Z"; normalise for 3.9/3.10
    s2 = s.replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s2)
    except ValueError:
        # Truncate to seconds and retry (e.g. fractional seconds without fromisoformat support)
        d = dt.datetime.fromisoformat(s2[:19])
    if d.tzinfo is not None:
        d = d.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
    return d


def _title_tokens(title):
    """Normalised token set for a title, filtered for meaningful words.

    Removes:
    - stopwords (same list as similar.py)
    - tokens shorter than 2 characters
    - pure-digit tokens (dates like "10", "23", "2026")
    - month abbreviations and full month-name prefixes ("oct", "october", …)
    """
    return {
        t for t in norm(title or "").split()
        if len(t) > 1
        and t not in STOPWORDS
        and not re.match(r'^\d+$', t)
        and t not in _MONTH_SET          # bare abbreviation: "oct"
        and t[:3] not in _MONTH_SET      # full name: "october" → "oct"
    }


def _ts_seconds(ts):
    """Convert "hh:mm:ss" or "mm:ss" timestamp to total seconds; None on failure."""
    if not ts:
        return None
    parts = ts.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except (ValueError, IndexError):
        pass
    return None


def _shared_tokens(a, b):
    return _title_tokens(a) & _title_tokens(b)


def _has_guessed_time(m):
    """True when the meeting's recorded time is not the real start time.

    Two cases:
    - date_source == "file_mtime": the whole at is the file-arrival timestamp
    - date_source == "filename": the date came from the file name but the time
      was defaulted to 12:00:00 (taska's placeholder when no real time is known)
    """
    if m.get("date_source") == "file_mtime":
        return True
    if m.get("date_source") == "filename" and (m.get("at") or "")[11:19] == "12:00:00":
        return True
    return False


def _a1_matches(m, m_at, e_at, e_title):
    """True when taska meeting m satisfies expected item with time e_at and title e_title.

    When the meeting's time is a guess (file mtime or filename-date default), match on
    same calendar date + ≥ 2 shared title tokens.  When the time is real, require
    ±20 min + ≥ 2 shared title tokens.
    """
    if len(_shared_tokens(e_title, m["title"])) < 2:
        return False
    if _has_guessed_time(m):
        return e_at.date() == m_at.date()
    return abs((e_at - m_at).total_seconds()) <= 20 * 60


# ── main entry point ─────────────────────────────────────────────────────────

ROOM_SHARE, ROOM_MIN_WORDS = 0.8, 1500   # measured: room recordings 0.89-1.00, every normal call <= 0.62


def room_audio(ev: dict) -> list:
    """Meetings whose words mostly sit under one speaker tag (a room microphone), largest share first."""
    out = []
    for m in ev.values():
        if m.get("kind") != "meeting" or m.get("duplicate_of") or not m.get("turns"):
            continue
        words = {}
        for t in m["turns"]:
            words[t.get("speaker") or "?"] = words.get(t.get("speaker") or "?", 0) + len((t.get("text") or "").split())
        total = sum(words.values())
        top = max(words, key=words.get)
        if total >= ROOM_MIN_WORDS and words[top] / total >= ROOM_SHARE:
            out.append({"id": m["id"], "title": m.get("title"), "top_speaker": top,
                        "share": round(words[top] / total, 2), "words": total})
    return sorted(out, key=lambda r: -r["share"])


def audit_ingest(ev: dict, expected: list, calendar: list, tz: str) -> dict:
    """Audit the taska store for missing, misdated and truncated meetings.

    Parameters
    ----------
    ev        : store.evidence(store) — {id: item}
    expected  : [{"at": iso, "title": str, "source": str}]
    calendar  : [{"start": iso, "subject": str, "end": iso (optional)}]
    tz        : project timezone name (e.g. "Europe/Amsterdam")

    Returns
    -------
    {"A1_missing", "A2_date_mismatch", "A2_guessed", "A6_truncated", "A6_unnamed_share"}
    """
    # Active (non-duplicate) meetings only
    meetings = [x for x in ev.values()
                if x.get("kind") == "meeting" and not x.get("duplicate_of")]

    # Pre-parse taska meeting datetimes (stored as naive local)
    m_dt = {m["id"]: dt.datetime.fromisoformat(m["at"][:19]) for m in meetings}

    # Pre-parse calendar
    cal = []
    for c in calendar:
        try:
            start = _parse_dt(c["start"], tz)
            end = _parse_dt(c["end"], tz) if c.get("end") else None
            cal.append({"start": start, "end": end, "subject": c.get("subject") or ""})
        except (ValueError, KeyError):
            continue

    # Pre-parse expected
    exp = []
    for e in expected:
        try:
            exp.append({"at": _parse_dt(e["at"], tz), "title": e.get("title") or "",
                        "source": e.get("source") or ""})
        except (ValueError, KeyError):
            continue

    # ── A1: expected meetings missing from the taska store ───────────────────
    A1_missing = []
    for e in exp:
        found = any(
            _a1_matches(m, m_dt[m["id"]], e["at"], e["title"])
            for m in meetings
        )
        if not found:
            A1_missing.append({"at": e["at"].isoformat(), "title": e["title"],
                                "source": e["source"]})

    # ── A2: date mismatch and guessed dates ──────────────────────────────────
    A2_date_mismatch = []
    A2_guessed = [m["id"] for m in meetings if m.get("date_source") == "file_mtime"]

    def _best_cal_match(m, max_days=7):
        """Return the calendar event closest in time within max_days sharing ≥ 2 tokens."""
        mat = m_dt[m["id"]]
        tok = _title_tokens(m["title"])
        candidates = []
        for c in cal:
            if len(tok & _title_tokens(c["subject"])) < 2:
                continue
            if abs((c["start"].date() - mat.date()).days) > max_days:
                continue
            candidates.append((abs((c["start"] - mat).total_seconds()), c))
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[0])[1]

    for m in meetings:
        best = _best_cal_match(m)
        if best is None:
            continue
        if best["start"].date() != m_dt[m["id"]].date():
            A2_date_mismatch.append({
                "id": m["id"],
                "taska": m_dt[m["id"]].date().isoformat(),
                "calendar": best["start"].date().isoformat(),
            })

    # ── A6: truncated transcripts and unnamed speaker share ──────────────────
    A6_truncated = []
    A6_unnamed_share = {}

    for m in meetings:
        turns = m.get("turns") or []
        mat = m_dt[m["id"]]

        # Duration resolution order (first that gives a value wins):
        #  1. Best token-based calendar match (A2 pick) that has an end time.
        #  2. ±20-min same-day calendar event with an end time, regardless of token
        #     overlap — covers meetings with short or unusual titles that don't
        #     produce 2 shared tokens but are unambiguously the same slot.
        #  3. meta.duration_min from the evidence item itself.
        duration_min = None
        best = _best_cal_match(m)
        if best is not None and best.get("end"):
            duration_min = (best["end"] - best["start"]).total_seconds() / 60
        if duration_min is None:
            for c in cal:
                if c["start"].date() == mat.date() and c.get("end"):
                    if abs((c["start"] - mat).total_seconds()) <= 20 * 60:
                        duration_min = (c["end"] - c["start"]).total_seconds() / 60
                        break
        if duration_min is None:
            meta = m.get("meta") or {}
            raw = meta.get("duration_min")
            if raw is not None:
                try:
                    duration_min = float(raw)
                except (ValueError, TypeError):
                    pass

        # Truncation check
        if turns and duration_min:
            last_ts = (turns[-1].get("ts") or "")
            last_secs = _ts_seconds(last_ts)
            if last_secs is not None and last_secs < 0.8 * duration_min * 60:
                A6_truncated.append(m["id"])

        # Unnamed share
        if turns:
            unnamed = sum(1 for t in turns if not t.get("speaker") or t["speaker"] == "?")
            share = round(unnamed / len(turns), 3)
            if share > 0:
                A6_unnamed_share[m["id"]] = share

    return {
        "A7_room_audio": room_audio(ev),
        "A1_missing": A1_missing,
        "A2_date_mismatch": A2_date_mismatch,
        "A2_guessed": A2_guessed,
        "A6_truncated": A6_truncated,
        "A6_unnamed_share": A6_unnamed_share,
    }
