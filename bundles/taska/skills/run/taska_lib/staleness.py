"""What went stale and what to ask, computed (never judged): flags, scores, tiers, questions."""
import datetime as dt
from collections import defaultdict

from .ledger import CLOSED, fold, needs_verdict
from .text import fmt_date

SILENT_MEETINGS = 3      # meetings without a word before something counts as quiet...
QUIET_DAYS = 2           # ...and at least this many days: four sessions in one afternoon are not silence
MAX_ASKS = 15            # the prep list
PER_STORY = 2            # at most this many asks per story in the prep list
CLOSE_AFTER_DAYS = 7     # quiet longer than this and not owed by the client: confirm or close, don't ask


def classify(store, cfg=None, upto=None, folded=None):
    N, notes, order, ev = folded or fold(store, upto)
    if not order:
        return [], N
    meetings = [c for c in order if ev[c]["kind"] == "meeting"] or order
    mpos = {c: i for i, c in enumerate(meetings)}
    today = ev[order[-1]]["date"]

    def last_meeting_index(hs):
        idx = [mpos[h["evidence"]] for h in hs if h.get("evidence") in mpos]
        return max(idx) if idx else None

    rows = []
    for n in N.values():
        if n["kind"] not in ("story", "task") or n["state"] in CLOSED:
            continue
        hs = [h for h in n["history"] if h.get("by") == "agent" and h.get("evidence")]
        said = [h for h in hs if h.get("kind") in ("internal", "client") and h.get("speaker")]
        last = said[-1] if said else (hs[-1] if hs else None)  # the last thing a person said beats a file listing
        li = last_meeting_index(hs)
        if n["kind"] == "story":  # a story is quiet only if its tasks are quiet too
            kid = [h for c in n["children"] for h in N[c]["history"] if h.get("by") == "agent"]
            k = last_meeting_index(kid)
            li = max(x for x in (li, k, -1) if x is not None)
        if li is None:
            li = mpos.get(n["created_in"], len(meetings) - 1)
        silent = len(meetings) - 1 - li
        dh, due = n["due_history"], n["due"]
        undated_slip = bool(dh) and dh[-1]["due"] is None
        unconf = [h for h in hs if needs_verdict(h) and h.get("verdict") in ("weak", "wrong")]
        flags = []
        claimed_done = bool(unconf) and unconf[-1] is hs[-1] and unconf[-1].get("effect") in ("done", "held")
        if claimed_done:
            flags.append("confirm_done")
        elif due and due < today and not undated_slip:
            flags.append("presumed_happened" if n.get("type") == "session" else "overdue")
        if len([h for h in dh if h["due"]]) >= 3 or undated_slip:
            flags.append("slipping")
        if n["state"] == "blocked":
            flags.append("blocked")
        stale_dep = [d for d in n["waiting_on"] if N[d]["due"] and N[d]["due"] < today]
        if stale_dep:
            flags.append("at_risk")
        wf = n.get("waiting_for")
        if wf and wf.get("since") and wf["since"] < today and silent >= 1:
            flags.append("waiting")
        client_owed = n["client_owed"] or (n.get("type") == "dependency" and n.get("client_owner"))
        last_date = (last or {}).get("date") or n["created"] or today
        quiet = silent >= SILENT_MEETINGS and \
            (dt.date.fromisoformat(today) - dt.date.fromisoformat(last_date)).days >= QUIET_DAYS
        if quiet and not flags and not (due and due >= today):
            flags.append("chase_client" if client_owed else "dropped")
        if unconf and unconf[-1] is hs[-1] and not claimed_done:
            flags.append("unconfirmed")
        if not flags:
            continue
        side = cfg.side(wf.get("who")) if (cfg and wf) else None
        score = (6 if client_owed or side == "client" else 3 if n.get("client_owner") else 0) \
            + 2 * len(n["blocks"]) + 2 * max(0, len(dh) - 1) + min(silent, 3) \
            + (3 if "blocked" in flags else 0) + (2 if "slipping" in flags else 0) \
            + (2 if "waiting" in flags else 0) - (4 if "confirm_done" in flags else 0)
        parent = N.get(n["parent"]) or {}
        any_last = (hs[-1] if hs else {}).get("date") or n["created"] or today
        days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(any_last)).days
        rows.append({"node": n, "flags": flags, "silent": silent, "days": days, "since": any_last,
                     "score": score, "stale_dep": stale_dep,
                     "ask": n.get("internal_owner") or parent.get("internal_owner") or n.get("creator") or "team",
                     "anchor": last or {"quote": "", "date": n["created"] or today, "speaker": n.get("creator")},
                     "unconf": unconf, "client_owed": bool(client_owed)})
    flagged = {r["node"]["id"] for r in rows}
    week_ago = (dt.date.fromisoformat(today) - dt.timedelta(days=CLOSE_AFTER_DAYS)).isoformat()
    for r in rows:
        n = r["node"]
        # asked once, at the root: downstream "at risk", a quiet story whose tasks are already asked about
        r["shadow"] = (r["flags"] == ["at_risk"] and all(d in flagged for d in r["stale_dep"])) or \
            (n["kind"] == "story" and set(r["flags"]) <= {"dropped", "unconfirmed"}
             and any(c in flagged for c in n["children"])) or r["flags"] == ["unconfirmed"]
        old = r["anchor"]["date"] < week_ago and (not n["due"] or n["due"] < week_ago)
        r["tier"] = "close" if (set(r["flags"]) <= {"dropped", "overdue", "presumed_happened", "unconfirmed"}
                                and old and r["silent"] >= SILENT_MEETINGS and not r["client_owed"]
                                and not n["blocks"]) else "ask"
    rows.sort(key=lambda r: -r["score"])
    per, top = defaultdict(int), 0
    for r in rows:
        r["top"] = False
        if r["shadow"] or r["tier"] != "ask":
            continue
        k = r["node"]["parent"] if r["node"]["kind"] == "task" else r["node"]["id"]
        if per[k] < PER_STORY and top < MAX_ASKS:
            r["top"], per[k], top = True, per[k] + 1, top + 1
    return rows, N


def cite(h, today=None):
    ts = h.get("ts") or ""
    ts = ts[3:8] if ts.startswith("00:") else ts[:8]
    return f"[{fmt_date(h['date'], today)}{' · ' + ts if ts else ''}{' · client' if h.get('kind') == 'client' else ''}]"


def question(r, N):
    n, a, f = r["node"], r["anchor"], r["flags"]
    today = (dt.date.fromisoformat(r["since"][:10]) + dt.timedelta(days=r["days"])).isoformat() \
        if r.get("since") and r.get("days") is not None else None
    fd = lambda d: fmt_date(d, today)
    who = a.get("speaker") or {"file": "a file", "event": "the calendar", "pr": "a pull request"}.get(a.get("kind"), "someone")
    said = f"last word: {who}, {fd(a['date'])}: “{a['quote']}”" if a.get("quote") \
        else f"raised {fd(a['date'])}"
    quiet = f"nothing since {fd(r['since'])} ({r['days']} days)"
    holds = (f" It holds up {len(n['blocks'])}: " + "; ".join(N[b]["title"] for b in n["blocks"][:3]) + ".") \
        if n["blocks"] else ""
    if "confirm_done" in f:
        h = r["unconf"][-1]
        return f"Can we close “{n['title']}”? The evidence suggests it is done: “{h['quote']}” {cite(h, today)}"
    if "presumed_happened" in f:
        return f"“{n['title']}” was set for {fd(n['due'])}. Did it happen, and what came out of it?{holds} ({said})"
    if "slipping" in f:
        hist = " → ".join(fd(h["due"]) if h["due"] else "?" for h in n["due_history"])
        return f"“{n['title']}” keeps moving ({hist}). What is the real date, and what moves it?{holds}"
    if "waiting" in f:
        w = n["waiting_for"]
        return f"Still waiting on {w.get('who') or 'someone'} for {w.get('what')} (since {fd(w['since'])})? “{n['title']}” is held until then.{holds}"
    if "overdue" in f:
        return f"“{n['title']}” was due {fd(n['due'])}. Where is it now?{holds} ({said})"
    if "blocked" in f:
        return f"“{n['title']}” is blocked. Who can unblock it, and by when?{holds} ({said})"
    if "at_risk" in f:
        d = N[r["stale_dep"][0]]
        return f"“{n['title']}” waits on “{d['title']}”, due {fd(d['due'])} and still open. Is it still on track?"
    if "chase_client" in f:
        return f"Waiting on {n.get('client_owner') or 'the client'} for “{n['title']}”: {quiet}. Chased?{holds} ({said})"
    if "unconfirmed" in f:
        h = r["unconf"][-1]
        return f"Is “{n['title']}” really {h.get('effect') or h.get('op')}? The evidence only says “{h['quote']}” {cite(h, today)}."
    return f"“{n['title']}”: {quiet}. Still happening, and who has it?{holds} ({said})"
