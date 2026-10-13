"""Text normalisation for quote checks and deterministic date resolution."""
import datetime as dt
import re
import unicodedata

WD = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def norm(s):
    """Lower-case, punctuation-free, single-spaced: the form quotes are compared in."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[‘’“”]", "'", s)
    s = re.sub(r"[^\w']+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _next_working(d, working_days):
    while d.isoweekday() not in working_days:
        d += dt.timedelta(days=1)
    return d


def resolve_due(text, ref_date, working_days=(1, 2, 3, 4, 5)):
    """Spoken date words -> ISO date, resolved against the date they were said. None when not a date.

    Handles ISO dates, "Oct 14"/"14th of October", today/eod, tomorrow (next working day), "the 12th"
    (this month if still ahead, else next), "middle of next week" (that week's Wednesday), end of week,
    "next week" (its Friday) and weekday names with or without "next".
    """
    if not text:
        return None
    t = text.lower().strip()
    d = dt.date.fromisoformat(ref_date)
    m = re.search(r"(\d{4}-\d{2}-\d{2})", t)
    if m:
        return m.group(1)
    m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})\b", t) or \
        re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(MONTHS) + r")", t)
    if m:
        g = m.groups()
        mon, day = (g[0], g[1]) if g[0].isalpha() else (g[1], g[0])
        try:
            x = dt.date(d.year, MONTHS.index(mon[:3]) + 1, int(day))
        except ValueError:
            return None
        if x < d - dt.timedelta(days=180):  # "Jan 5" said in December
            x = x.replace(year=x.year + 1)
        return x.isoformat()
    if "today" in t or "eod" in t or "end of day" in t:
        return d.isoformat()
    if "tomorrow" in t:
        return _next_working(d + dt.timedelta(days=1), working_days).isoformat()
    m = re.search(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", t)
    if m:
        day = int(m.group(1))
        y, mo = d.year, d.month
        if day < d.day:
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        try:
            return dt.date(y, mo, day).isoformat()
        except ValueError:
            return None
    nxt = re.search(r"\bnext\b", t) is not None
    if re.search(r"\b(middle|mid)\b", t) and "week" in t:
        wed = d + dt.timedelta(days=(2 - d.weekday()) % 7)
        if nxt and wed.isocalendar()[1] == d.isocalendar()[1]:
            wed += dt.timedelta(days=7)
        return wed.isoformat()
    if "end of week" in t or "end of the week" in t or "this week" in t or "eow" in t:
        return (d + dt.timedelta(days=(4 - d.weekday()) % 7)).isoformat()
    if nxt and "week" in t and not any(w in t for w in WD):
        return (d + dt.timedelta(days=(4 - d.weekday()) % 7 + 7)).isoformat()
    for i, w in enumerate(WD):
        if w in t or re.search(r"\b" + w[:3] + r"\b", t):
            delta = (i - d.weekday()) % 7 or 7
            if nxt and delta < 7 and (d + dt.timedelta(days=delta)).isocalendar()[1] == d.isocalendar()[1]:
                delta += 7
            return (d + dt.timedelta(days=delta)).isoformat()
    return None


def fmt_date(d):
    x = dt.date.fromisoformat(d[:10])
    return f"{x.strftime('%b')} {x.day}"
