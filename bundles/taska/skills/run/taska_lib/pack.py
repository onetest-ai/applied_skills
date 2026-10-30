"""The status-meeting pack: computed from the tree, every line cited. The status colour stays the manager's call."""
import datetime as dt
import re
from collections import defaultdict

from . import ledger as L, staleness as ST
from .deliverables import deliverables
from .text import fmt_date

_CITE_RE = re.compile(r'\s*\[[A-Z]\d{3}(?:[,\s]+[A-Z]\d{3})*\]')


def _pmo_bullet(text):
    """Strip taska citation brackets (e.g. [T113, D006]) for client-facing PMO output."""
    return _CITE_RE.sub('', text or '').strip()


def _fmt(d):
    return fmt_date(d) if d else ""


def _applied_in(n, start, pred):
    return [h for h in n["history"] if h.get("applied") and h.get("by") == "agent" and (h.get("date") or "") >= start
            and pred(h)]


def build(store, cfg, days=7):
    folded = L.fold(store)
    N, notes, order, ev = folded
    if not order:
        return "# Status pack\n\n_No evidence processed yet._\n"
    rows, _ = ST.classify(store, cfg, folded=folded)
    today = ev[order[-1]]["date"]
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=days - 1)).isoformat()
    ahead = (dt.date.fromisoformat(today) + dt.timedelta(days=7)).isoformat()
    live = [n for n in N.values() if n["state"] not in ("merged", "deleted")]
    in_window = [c for c in order if ev[c]["date"] >= start]
    by_src = defaultdict(int)
    for c in in_window:
        by_src[ev[c]["kind"]] += 1
    L_ = [f"# {cfg.name}: status pack, {fmt_date(start, today)} to {fmt_date(today, today)}",
          f"_{', '.join(f'{v} {k}s' for k, v in by_src.items()) or 'no new evidence'} · "
          f"{sum(n['kind'] == 'story' for n in live)} stories · {sum(n['kind'] == 'task' for n in live)} tasks · "
          "status colour: **yours to set**_", ""]

    L_.append("## Schedule & scope")
    sig = []
    for n in live:
        for h in _applied_in(n, start, lambda h: h.get("effect") == "delayed"):
            sig.append(f"- **Slipped:** {n['title']}: “{h['quote']}” {ST.cite(h, today)}")
        for h in _applied_in(n, start, lambda h: h.get("effect") == "due" and h.get("due")):
            if len([d for d in n["due_history"] if d["due"]]) > 1:
                sig.append(f"- **Date moved:** {n['title']} → {fmt_date(h['due'], today)} {ST.cite(h, today)}")
    for n in live:
        if n["kind"] == "epic" and n.get("plan") == "Off-plan":
            grew = sum(1 for m in live if m["created"] and m["created"] >= start and _epic(N, m["id"]) == n["id"])
            if grew:
                sig.append(f"- **Off-plan:** {n['title']} gained {grew} items")
    for r in rows:
        if "slipping" in r["flags"] and not r["shadow"]:
            sig.append(f"- **Keeps slipping:** {r['node']['title']} "
                       f"({' → '.join(fmt_date(d['due'], today) if d['due'] else '?' for d in r['node']['due_history'])})")
    L_ += sig or ["_No schedule or scope signals in this window._"]
    L_.append("")

    L_.append("## Stories that moved")
    moved = []
    for n in live:
        if n["kind"] != "story":
            continue
        met = _applied_in(n, start, lambda h: h.get("op") == "ac_met")
        kids = [N[c] for c in n["children"]]
        k_done = [k for k in kids for h in _applied_in(k, start, lambda h: h.get("effect") in ("done", "held"))]
        if met or k_done:
            ac = f"{sum(x['met'] for x in n['ac'])}/{len(n['ac'])} criteria"
            moved.append(f"- {n['title']} ({ac}, {L.path(N, n['id']).split(' › ')[0]})"
                         + (f": done: {', '.join(sorted({k['title'] for k in k_done}))}" if k_done else ""))
    L_ += moved or ["_No story moved in this window._"]
    L_.append("")

    L_.append("## Sessions")
    held = [(n, h) for n in live for h in _applied_in(n, start, lambda h: h.get("effect") == "held")]
    if held:
        L_.append("Held: " + "; ".join(f"{n['title']} {ST.cite(h, today)}" for n, h in sorted(held, key=lambda x: x[1]["date"])))
    nxt = [n for n in live if n.get("type") == "session" and n["state"] not in L.CLOSED and n["due"]
           and today <= n["due"] <= ahead]
    if nxt:
        L_.append("Coming up: " + "; ".join(f"{n['title']} ({fmt_date(n['due'], today)})" for n in sorted(nxt, key=lambda n: n["due"])))
    if not held and not nxt:
        L_.append("_None recorded._")
    L_.append("")

    L_.append("## Waiting on the client")
    owed = [n for n in live if n["state"] not in L.CLOSED and (n["client_owed"] or
            (n.get("waiting_for") and cfg.side(n["waiting_for"].get("who")) == "client"))]
    owed.sort(key=lambda n: (-len(n["blocks"]), (n.get("waiting_for") or {}).get("since") or n["created"] or ""))
    more = len(owed) - 12
    for n in owed[:12]:
        w = n.get("waiting_for")
        who = n.get("client_owner") or (w or {}).get("who") or "client"
        L_.append(f"- {n['title']}: {who}" + (f" ({w['what']}, since {fmt_date(w['since'], today)})" if w else "")
                  + (f"; holds up {len(n['blocks'])}" if n["blocks"] else ""))
    if more > 0:
        L_.append(f"_+{more} more in the workbench._")
    if not owed:
        L_.append("_Nothing recorded._")
    L_.append("")

    dl = deliverables(N, ev)
    if dl:
        L_.append(f"## Deliverables ({len(dl)})")
        for d in dl:
            owner = f", {d['internal_owner']}" if d["internal_owner"] else ""
            due = f", due {fmt_date(d['due'], today)}" if d["due"] else ""
            landed = f" · file: {d['landed']['title']} (synced {fmt_date(d['landed']['date'], today)})" if d["landed"] else ""
            L_.append(f"- {d['title']} — {d['ac_met']}/{d['ac_total']} AC, {d['state']}{owner}{due}{landed}")
        L_.append("")

    top = [r for r in rows if r["top"]]
    L_.append(f"## Asks ({len(top)})")
    by = defaultdict(list)
    for r in top:
        by[r["ask"]].append(r)
    for who, rs in sorted(by.items(), key=lambda kv: -max(r["score"] for r in kv[1])):
        L_.append(f"**{who}**")
        L_ += [f"- {ST.question(r, N)}" for r in rs]
    if not top:
        L_.append("_Nothing to chase._")
    L_.append("")

    dec = [n for n in live if n["kind"] == "decision" and (n["created"] or "") >= start]
    if dec:
        L_.append("## Decisions")
        L_ += [f"- {n['title']} {ST.cite(n['history'][0])}" for n in dec]
        L_.append("")
    risks = [n for n in live if n["kind"] == "risk" and (n["created"] or "") >= start]
    if risks:
        L_.append(f"## Risks heard ({len(risks)})")
        L_ += [f"- {n['title']} {ST.cite(n['history'][0])}" for n in risks[:6]]
        L_.append("")
    close = [r for r in rows if r["tier"] == "close" and not r["shadow"]]
    if close:
        L_.append(f"## Confirm or close ({len(close)})")
        L_.append("Quiet for over a week; usually finished without anyone saying so: "
                  + "; ".join(f"{r['node']['title']} ({r['ask']})" for r in close[:12]))
    return "\n".join(L_) + "\n"


def _epic(N, i):
    while i in N and N[i]["parent"]:
        i = N[i]["parent"]
    return i


def _rag_of(flags):
    if "slipping" in flags or "overdue" in flags or "blocked" in flags:
        return "Red"
    if "at_risk" in flags or "waiting" in flags:
        return "Amber"
    return "Green"


def _state_label(state):
    """Map ledger state to PMO English for client-facing output."""
    return {"done": "Complete", "cancelled": "Closed", "in_progress": "In-Progress",
            "blocked": "At-Risk"}.get(state or "", "On-Track")


def build_client(store, cfg, days=7):
    """Return a ClientWeeklyData dict: citation-stripped, PMO-English, ready for slide generation.

    Column headers follow the standard PMO weekly template:
    - slide5: Sr. | Decision | Status | Owner | Target | Requested | Comments
    - slide6: Sr. | Requests | Status | Planned First Draft | Planned Final | Actual Delivery | Approver
    """
    folded = L.fold(store)
    N, notes, order, ev = folded
    empty = {
        "week": dt.date.today().isoformat(), "project_name": cfg.name,
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "slide3": {"headline": "", "week_label": "", "rag": {"overall": "Green", "delivery": "Green",
                   "schedule": "Green", "scope": "Amber", "quality": "Green"},
                   "milestones": [], "activities": [], "actions": [], "decisions": [], "narrative": ""},
        "slide4": {"completed": [], "priorities": []},
        "slide5": [], "slide6": [],
    }
    if not order:
        return empty

    rows, _ = ST.classify(store, cfg, folded=folded)
    today = ev[order[-1]]["date"]
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=days - 1)).isoformat()
    # week_label e.g. "Oct 19-23, 2026"
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(today)
    week_label = (f"{start_d.strftime('%b')} {start_d.day}-{end_d.day}, {end_d.year}"
                  if start_d.month == end_d.month
                  else f"{start_d.strftime('%b')} {start_d.day} – {end_d.strftime('%b')} {end_d.day}, {end_d.year}")

    live = [n for n in N.values() if n["state"] not in ("merged", "deleted")]
    stale_map = {r["node"]["id"]: r for r in rows}

    # RAG summary (slide 3 top row)
    all_flags = [f for r in rows for f in (r.get("flags") or [])]
    rag_schedule = _rag_of(all_flags)
    dl_all = [d for d in deliverables(N, ev) if d["state"] not in ("merged", "deleted")]
    dl_rags = [d["rag"] for d in dl_all]
    rag_delivery = "Red" if "Red" in dl_rags else "Amber" if "Amber" in dl_rags else "Green"
    rag_overall = "Red" if "Red" in (rag_schedule, rag_delivery) else \
                  "Amber" if "Amber" in (rag_schedule, rag_delivery) else "Green"
    rag = {"overall": rag_overall, "delivery": rag_delivery, "schedule": rag_schedule,
           "scope": "Amber", "quality": "Green"}

    # slide 3 — milestones (one row per story)
    milestones = []
    for n in [n for n in live if n["kind"] == "story"]:
        r = stale_map.get(n["id"])
        flags = r.get("flags") or [] if r else []
        if n["state"] in L.CLOSED:
            story_rag = "Green"
        elif r and r.get("top"):
            story_rag = _rag_of(flags) if flags else "Red"
        elif r and "slipping" in flags:
            story_rag = "Amber"
        else:
            story_rag = "Green"
        note = _pmo_bullet(ST.question(r, N)) if r and r.get("top") else ""
        milestones.append({"title": _pmo_bullet(n["title"]), "status": story_rag, "note": note})

    dec = [n for n in live if n["kind"] == "decision" and (n["created"] or "") >= start]
    decisions = [{"title": _pmo_bullet(n["title"])} for n in dec]

    # Activity table (slide 3): tasks/stories that completed or were planned this week
    activities = []
    act_seen: set = set()
    ai = 1
    for n in live:
        for h in _applied_in(n, start, lambda h: h.get("effect") in ("done", "held")):
            t = _pmo_bullet(n["title"])
            if t not in act_seen:
                activities.append({"sr": str(ai), "status": "Complete", "activity": t,
                                    "planned": _fmt(n.get("due")), "actual": fmt_date(h["date"])})
                act_seen.add(t); ai += 1

    # Actions table (slide 3 lower): internal actions/decisions
    actions = []
    for i, n in enumerate([n for n in live if n["kind"] == "decision"
                            and (n["created"] or "") >= start], 1):
        actions.append({"sr": str(i), "status": "Open", "activity": _pmo_bullet(n["title"]),
                        "planned": _fmt(n.get("due")), "actual": ""})

    # slide 4 — completed / priorities
    seen_c: set = set()
    completed = []
    for n in live:
        for h in _applied_in(n, start, lambda h: h.get("effect") in ("done", "held")):
            t = _pmo_bullet(n["title"])
            if t and t not in seen_c:
                completed.append(t); seen_c.add(t)
    top = [r for r in rows if r["top"]]
    priorities = [_pmo_bullet(r["node"]["title"]) for r in top[:8] if r["node"].get("title")]

    # slide 5 — Decisions & Actions Needed from the Client
    # Columns: Sr. | Decision | Status | Owner | Target | Requested | Comments
    owed = [n for n in live if n["state"] not in L.CLOSED and (n["client_owed"] or
            (n.get("waiting_for") and cfg.side((n.get("waiting_for") or {}).get("who")) == "client"))]
    owed.sort(key=lambda n: (-len(n["blocks"]), (n.get("waiting_for") or {}).get("since") or n["created"] or ""))
    slide5 = []
    for i, n in enumerate(owed[:12], 1):
        w = n.get("waiting_for")
        owner = n.get("client_owner") or (w or {}).get("who") or "client"
        target = _fmt((w or {}).get("due") or n.get("due"))
        requested = _fmt((w or {}).get("since") or n.get("created"))
        if n["state"] in L.CLOSED:
            st_val = "Closed"
        elif n["state"] == "in_progress":
            st_val = "In-Progress"
        else:
            st_val = "Open"
        slide5.append({
            "sr": str(i), "decision": _pmo_bullet(n["title"]),
            "status": st_val, "owner": owner, "target": target,
            "requested": requested, "comments": "",
        })

    # slide 6 — Deliverable Tracker
    # Columns: Sr. | Requests | Status | Planned First Draft | Planned Final | Actual Delivery | Approver
    slide6 = [
        {
            "sr": str(i),
            "requests": _pmo_bullet(d["title"] or ""),
            "status": _state_label(d["state"]),
            "rag": d["rag"],
            "planned_first_draft": _fmt(d.get("first_draft_due")),
            "planned_final": _fmt(d["due"]),
            "actual_delivery": _fmt((d["landed"] or {}).get("date")) if d.get("landed") else "",
            "approver": d["internal_owner"] or "",
        }
        for i, d in enumerate(dl_all, 1)
    ]

    return {
        "week": today, "project_name": cfg.name,
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "slide3": {"headline": "", "week_label": week_label, "rag": rag,
                   "milestones": milestones, "activities": activities,
                   "actions": actions, "decisions": decisions, "narrative": ""},
        "slide4": {"completed": completed, "priorities": priorities},
        "slide5": slide5, "slide6": slide6,
    }
