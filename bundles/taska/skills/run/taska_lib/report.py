"""The weekly report, generic: what goes in is decided from the tree (`select`), the writer phrases only what has no
stored sentence yet (`apply` keeps the fixed content and remembers the writer's text), and `build` checks the prose
against the cited facts and writes the docx. Settings in [report] of taska.toml: title, client_label (the heading
"Waiting on <label>" and the default owner of a client ask), file_url (a template with {path} for the workbench
snapshot link), footer. Moved from a project's scripts (Oct 31) with their tests; nothing here names a client."""
import datetime as dt
import hashlib
import json
import re
import urllib.parse
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import ledger as L

MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
M = MON
CITE_END = re.compile(r"\[([A-Z]\d{3}(?:,\s*[A-Z]\d{3})*)\]\s*\.?\s*$")


def md(d):
    d = dt.date.fromisoformat(str(d)[:10])
    return f"{MON[d.month - 1]} {d.day}"


CLOSED = ("done", "cancelled", "merged", "deleted")
MAX = {"done": 6, "decision": 4, "risk": 3, "waiting": 3, "overdue": 3}


def key(it):
    k = f"{it['id']}|{it['kind']}|{it.get('state', '')}|{it.get('due', '')}"
    return k + f"|w:{it['waiting']}" if it.get("waiting") else k   # a sentence about a wait dies with the wait


_MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_SAID = re.compile(r"\b(" + "|".join(_MON) + r")[a-z]*\.? (\d{1,2})\b")


def still_true(sentence, *its):
    """A cached sentence may be reused only if every date it names is a date of the item(s) it was written for: a
    meeting re-dated from the calendar must not keep saying the old day. Sentences or items without dates pass."""
    dates = {f"{_MON[int(d[5:7]) - 1]} {int(d[8:10])}" for it in its for d in [str(it.get("date") or "")[:10]] if len(d) == 10}
    said = {f"{m} {int(n)}" for m, n in _SAID.findall(sentence or "")}
    if re.search(r"\bwait", sentence or "", re.I) and not any(it.get("waiting") or it.get("kind") == "waiting" for it in its):
        return False   # a sentence about a wait, for an item that no longer waits (Oct 26: T117's cleared wait came back)
    return not (dates and said and not said <= dates)


def _cache_path(cfg):
    return Path(cfg.store) / "out" / "phrase-cache.json"


def load_cache(cfg):
    try:
        return json.loads(_cache_path(cfg).read_text())
    except (OSError, ValueError):
        return {}


def client_label(cfg):
    return cfg.report.get("client_label") or "the client"


def carry_ref(N, ref, listed):
    """The item a carried row stands for now: a merged item's row moves to the item that survived the merge, and is
    dropped when that item already has a row (a row for a merged id would show the same work twice)."""
    seen = set()
    while ref in N and N[ref].get("state") == "merged" and N[ref].get("merged_into") and ref not in seen:
        seen.add(ref)
        ref = N[ref]["merged_into"]
    return None if ref in listed else ref


def closed_after(n, asof):
    """True when n is closed now but was closed after `asof` (by hand: when you clicked, `at`; by a meeting: its date).
    The report speaks from Friday, so such an item was still open then (Oct 26: Part 2 held and closed on Monday)."""
    if n["state"] not in CLOSED:
        return False
    for h in reversed(n.get("history", [])):
        if h.get("by") == "human" and (h.get("set") or {}).get("state") in CLOSED:
            return (h.get("at") or h.get("date") or "")[:10] > asof.isoformat()
        if h.get("op") == "status" and h.get("applied") and h.get("effect") in ("done", "held", "cancelled"):
            return (h.get("date") or "") > asof.isoformat()
    return False


def pick_priorities(N, week, asof, k=10, pins=()):
    """Next week's priorities: open tasks and stories due within 10 days of `asof`, or discussed in a meeting this week
    (most work has no date: a date-only rule left the whole future-state stream out). Overdue items are not priorities
    (the summary lists them to confirm). Every stream (epic) with such work gets its best row; the rest fill by rank:
    dated first by due date, then the most recently discussed."""
    a, soon = asof.isoformat(), (asof + dt.timedelta(days=10)).isoformat()

    def news(n):
        # a meeting's own entries, plus the meeting a confirmed card came from; never a hand edit's filing date
        ds = [h["date"] for h in n.get("history", []) if h.get("evidence") and h.get("date")
              and (h.get("by") != "human" or h.get("from_proposal"))]
        return max(ds) if ds else ""

    def epic(n):
        seen = set()
        while n and n.get("kind") != "epic" and n.get("parent") in N and n["id"] not in seen:
            seen.add(n["id"])
            n = N[n["parent"]]
        return n["id"] if n else None

    cand, late = [], {i for i, n in N.items() if closed_after(n, asof)}
    for n in N.values():
        if n["kind"] not in ("task", "story") or (n["state"] in CLOSED and n["id"] not in late):
            continue
        due, nw = n.get("due") or "", news(n)
        if due and due < a:
            continue
        if (due and due <= soon) or (week <= nw <= a):
            cand.append(((0, due) if due else (1, ""), "".join(chr(255 - ord(c)) for c in nw), n["id"], n))
    cand.sort(key=lambda c: (c[0], c[1], c[2]))
    due_soon = [c for c in cand if c[0][0] == 0 and c[0][1] <= soon]   # deadlines in the next 10 days: always shown
    best, seen_epics = [], {epic(c[3]) for c in due_soon}
    for c in cand:                      # then one row per stream not yet represented
        e = epic(c[3])
        if c not in due_soon and e not in seen_epics:
            seen_epics.add(e)
            best.append(c)
    rest = [c for c in cand if c not in due_soon and c not in best]
    chosen = due_soon + best + rest[:max(0, k - len(due_soon) - len(best))]   # deadlines and streams are never cut
    rows = [n for _, _, _, n in chosen]
    for i in pins:   # rows you pinned for this week: added to the ranked ones, never displacing one
        n = N.get(i)
        if n and n["kind"] in ("task", "story") and (n["state"] not in CLOSED or i in late) and n not in rows:
            rows.append(n)
    # shown latest discussed first (your choice, Oct 26); same day: nearest due date first, undated after
    rows.sort(key=lambda n: ("".join(chr(255 - ord(c)) for c in news(n)), n.get("due") or "9999", n["id"]))
    return [{"id": n["id"], "kind": "priority", "title": n["title"], "due": n.get("due"), "state": "open" if n["id"] in late else n["state"],
             "waiting": (n.get("waiting_for") or {}).get("who")} for n in rows]


def as_of(week, today):
    """The day the report speaks from: today for the current week, the week's Friday for a past week (Friday's data
    built on the following Monday must not count Saturday-to-Monday as that week, nor plan from Monday)."""
    return min(today, dt.date.fromisoformat(week) + dt.timedelta(days=4))


def select(cfg, week, reports, today=None):
    N, *_ = L.fold(cfg.store)
    today = as_of(week, today or dt.date.today())
    w0, w1 = week, today.isoformat()
    inw = lambda d: bool(d) and w0 <= str(d)[:10] <= w1
    real = lambda h: (h.get("applied") and h.get("by") != "human" and h.get("evidence") and inw(h.get("date"))
                      and h.get("op") == "status" and h.get("effect") in ("done", "held"))
    items = []
    done = []
    for n in N.values():
        if n["kind"] in ("task", "story") and n["state"] == "done":
            h = next((h for h in reversed(n["history"]) if real(h)), None)
            if h:
                done.append((h["date"], n["id"], n))
    for d, i, n in sorted(done)[:MAX["done"]]:
        items.append({"id": i, "kind": "done", "title": n["title"], "date": d, "state": n["state"]})
    for kind, k in (("decision", "decision"), ("risk", "risk")):
        xs = sorted((str(n.get("created") or ""), n["id"], n) for n in N.values()
                    if n["kind"] == k and n["state"] not in CLOSED and inw(n.get("created")))
        for d, i, n in xs[:MAX[kind]]:
            items.append({"id": i, "kind": kind, "title": n["title"], "date": d[:10], "state": n["state"]})
    wait = []
    for n in N.values():
        wf = n.get("waiting_for") or {}
        if n["kind"] in ("task", "story") and n["state"] not in CLOSED and (n.get("client_owed") or (wf.get("who") and cfg.side(wf["who"]) == "client")):
            wait.append((str(wf.get("since") or n.get("created") or ""), n["id"], n, wf))
    for d, i, n, wf in sorted(wait, key=lambda x: (x[0], x[1]), reverse=True)[:MAX["waiting"]]:  # most recent first
        items.append({"id": i, "kind": "waiting", "title": n["title"], "who": wf.get("who") or n.get("client_owner") or client_label(cfg),
                      "since": d[:10], "state": n["state"]})
    recent = (today - dt.timedelta(days=14)).isoformat()
    over = sorted(((n["due"], n["id"], n) for n in N.values()
                   if n["kind"] in ("task", "story") and n["state"] not in CLOSED and n.get("due") and recent <= n["due"] < w1),
                  key=lambda x: (x[0], x[1]), reverse=True)   # fell due in the last 14 days, most recent first
    for d, i, n in over[:MAX["overdue"]]:
        if not any(x["id"] == i for x in items):
            items.append({"id": i, "kind": "overdue", "title": n["title"], "due": d, "state": n["state"]})
    ch = Path(reports) / week / "presend-choices.json"   # the owner's choices for this week (pre-send page)
    pins = (json.loads(ch.read_text()).get("pin_priorities") or []) if ch.exists() else []
    priorities = pick_priorities(N, week, today, pins=pins)
    cache = load_cache(cfg)
    for it in items + priorities:
        s = cache.get(key(it))
        it["sentence"] = s if still_true(s, it) else None
    lead_key = "lead|" + "|".join(key(x) for x in items[:3])
    if not still_true(cache.get(lead_key), *items[:3]):
        cache.pop(lead_key, None)
    tables, slots = fixed_tables(N, cfg, week, today, cache, [x["id"] for x in items if x["kind"] == "decision"], reports)
    return {"week": week, "items": items, "priorities": priorities, "lead_key": lead_key, "lead": cache.get(lead_key),
            **tables, "slots": slots}




def closed_on(n):
    """The date n was closed, when a meeting or file said so (an applied, evidence-backed status change). A hand close
    has no such date: the edit carries the date of whatever evidence it was filed under, not when the work happened,
    so the milestone's Actual stays blank rather than borrowing a date nobody said."""
    for h in reversed(n["history"]):
        if h.get("op") == "status" and h.get("effect") in ("done", "held") and h.get("applied") and h.get("by") != "human":
            return h.get("date")
    return None


def news(n, w0, w1):
    """n's latest evidence-backed agent entry dated this week, or None: only then is a row's comment rewritten."""
    hs = [h for h in n["history"] if h.get("evidence") and h.get("by") != "human" and h.get("applied", True)
          and w0 <= str(h.get("date") or "")[:10] <= w1 and h.get("type") != "create"]
    return hs[-1] if hs else None


def new_tag(x):
    return re.sub(r"\s*\(new\)$", "", str(x))


def fixed_tables(N, cfg, week, today, cache, decisions, reports):
    w0, w1 = week, today.isoformat()
    wend = (dt.date.fromisoformat(week) + dt.timedelta(days=4)).isoformat()
    prev_path = Path(reports) / (dt.date.fromisoformat(week) - dt.timedelta(days=7)).isoformat() / "prose.json"
    prev = json.loads(prev_path.read_text()) if prev_path.exists() else {"slide3": {"risks": []}, "slide5": []}
    slots = []

    def slot(kind, ref, row_key, ctx):
        n = N.get(ref)
        h = news(n, w0, w1) if n and n["state"] not in CLOSED else None
        if not h:
            return None
        k = f"{kind}|{row_key}|{ref}|{h['mid']}"
        if k in cache:
            return cache[k]
        slots.append({"key": k, "ref": ref, "what": ctx, "latest": {"date": h.get("date"), "quote": h.get("quote")}})
        return {"slot": k}

    # milestones: client sessions due this week, in due order
    ms = []
    for n in sorted((n for n in N.values() if n["kind"] == "task" and n.get("type") == "session"
                     and n["state"] not in ("cancelled", "merged", "deleted") and w0 <= (n.get("due") or "") <= wend),
                    key=lambda n: (n["due"], n["id"])):
        c = closed_on(n) if n["state"] == "done" else None
        st = "Complete" if n["state"] == "done" else ("Planned" if n["due"] >= w1 else "Not yet confirmed")
        ms.append({"status": st, "activity": n["title"], "planned": md(n["due"]), "actual": md(c) if c else "", "ref": n["id"]})

    # risks: last week's rows (no longer "new"), status from the tree; then risks raised this week
    risks, seen = [], set()
    for r in prev["slide3"].get("risks", []):
        r = dict(r, id=new_tag(r["id"]))
        if r.get("ref"):
            ref = carry_ref(N, r["ref"], seen)
            if ref is None:
                continue
            r["ref"] = ref
        n = N.get(r.get("ref"))
        if n and n["kind"] == "risk" and n["state"] in CLOSED:
            r["status"] = "Closed"
        s = slot("mitigation", r.get("ref"), r["id"], f"risk {r['id']}: {r['desc']} (mitigation, one sentence)")
        if s is not None:
            r["mitigation"] = s
        risks.append(r); seen.add(r.get("ref"))
    nums = [int(m.group(1)) for m in (re.match(r"R-(\d+)", r["id"]) for r in risks) if m]
    k = max(nums, default=0)
    for n in sorted((n for n in N.values() if n["kind"] == "risk" and n["state"] not in CLOSED
                     and w0 <= str(n.get("created") or "")[:10] <= w1 and n["id"] not in seen),
                    key=lambda n: (str(n["created"]), n["id"])):
        k += 1
        risks.append({"id": f"R-{k:02d} (new)", "identified": dt.date.fromisoformat(str(n["created"])[:10]).strftime("%-m/%-d/%y"),
                      "desc": n["title"], "status": "Open", "impact": "", "prob": "", "mitigation": "", "ref": n["id"]})

    # client asks: last week's rows verbatim, status Done when the tree says so; then asks raised this week
    asks, listed = [], set()
    for r in prev.get("slide5", []):
        r = dict(r, sr=new_tag(r["sr"]))
        if r.get("ref"):
            ref = carry_ref(N, r["ref"], listed)
            if ref is None:
                continue
            r["ref"] = ref
        n = N.get(r.get("ref") or "")
        if n and n["state"] == "done":
            r["status"], r["comment"] = "Done", ""
        elif n and n["state"] == "cancelled":
            r["status"], r["comment"] = "Cancelled", ""
        else:
            s = slot("comment", r.get("ref"), r["sr"], f"client ask {r['sr']}: {r['decision']} (status comment, one line)")
            r["comment"] = s if s is not None else ""
        asks.append(r); listed.add(r.get("ref"))
    nums = [int(m.group(1)) for m in (re.match(r"(\d+)", str(r["sr"])) for r in asks) if m]
    k = max(nums, default=0)
    for n in sorted(N.values(), key=lambda n: n["id"]):
        wf = n.get("waiting_for") or {}
        client = n.get("client_owed") or (wf.get("who") and cfg.side(wf["who"]) == "client")
        since = str(wf.get("since") or n.get("created") or "")[:10]
        if n["kind"] in ("task", "story") and n["state"] not in CLOSED and client and n["id"] not in listed and w0 <= since <= w1:
            k += 1
            who = wf.get("who") or n.get("client_owner") or client_label(cfg)
            asks.append({"sr": f"{k} (new)", "decision": n["title"], "status": "Open", "owner": who,
                         "target": md(n["due"]) if n.get("due") else "", "requested": md(since), "ref": n["id"],
                         "comment": f"Waiting since {md(since)} on {who} [{n['id']}]."})

    label = f"{md(week)}–{dt.date.fromisoformat(wend).day}"
    unset = [r["id"].replace(" (new)", "") for r in risks if r["status"] != "Closed" and not (r.get("impact") and r.get("prob"))]
    open_ = ["Status colours on slide 3 are the manager's call."]
    if unset:
        open_.append(f"Impact and probability for {', '.join(unset)} are for a person to set.")
    return ({"milestones": ms, "decisions": decisions, "risks": risks, "slide5": asks, "status_title": f"Slide 3: Overall project status, {label}",
             "open": open_}, slots)


def apply(cfg, prose_path, sel_path):
    """Write the fixed content into prose.json; the writer's text is used only where the selection left a slot."""
    P, S = json.loads(Path(prose_path).read_text()), json.loads(Path(sel_path).read_text())
    cache = load_cache(cfg)
    fills = P.pop("fills", None) or {}
    summary = (P.get("slide3") or {}).get("summary") or []
    out = []
    for i, it in enumerate(S["items"]):
        s = it.get("sentence") or (summary[i] if i < len(summary) and it["id"] in summary[i] else None)
        if s:
            cache[key(it)] = s
            out.append(s)
    why = {r.get("ref"): r for r in P.get("priorities") or []}
    pri = []
    for it in S["priorities"]:
        w = it.get("sentence") or (why.get(it["id"]) or {}).get("why")
        if w:
            cache[key(it)] = w
        pri.append({**{k: v for k, v in (why.get(it["id"]) or {}).items() if k in ("label", "owner")}, "ref": it["id"], "why": w or ""})
    lead = S.get("lead") or P.get("lead") or ""
    if lead:
        cache[S["lead_key"]] = lead
    missing = []

    def fill(v):
        if isinstance(v, dict) and "slot" in v:
            t = fills.get(v["slot"])
            if t:
                cache[v["slot"]] = t
                return t
            missing.append(v["slot"])
            return ""
        return v
    risks = [{k: fill(v) for k, v in r.items()} for r in S["risks"]]
    asks = [{k: fill(v) for k, v in r.items()} for r in S["slide5"]]
    P.update({"lead": lead, "priorities": pri, "slide5": asks, "open": S["open"]})
    P.setdefault("titles", {})["status"] = S["status_title"]
    P["slide3"] = {"summary": out, "milestones": S["milestones"], "decisions": S["decisions"], "risks": risks}
    Path(prose_path).write_text(json.dumps(P, indent=1, ensure_ascii=False))
    _cache_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    _cache_path(cfg).write_text(json.dumps(cache, indent=1, ensure_ascii=False))
    return {"status": "ok", "cached": len(cache), "summary": len(out), "slots_unfilled": missing}


# ---------- check and docx ----------
CITE = re.compile(r"\[([A-Z]\d{3}(?:,\s*[A-Z]\d{3})*)\]")


def facts_text(N, ids):
    out = []
    for i in ids:
        n = N[i]
        out.append(n["title"])
        for k in ("due", "created"):
            if n.get(k):
                out.append(md(n[k]))
        for h in n["history"]:
            if h.get("date"):
                out.append(md(h["date"]))
            if h.get("quote"):
                out.append(h["quote"])
        wf = n.get("waiting_for") or {}
        if wf.get("since"):
            out.append(md(wf["since"]))
    return " ".join(out)


def check(text, N, allowed, people):
    errs = []
    ids = [x.strip() for m in CITE.findall(text) for x in m.split(",")]
    if not ids:
        errs.append("no citation")
    for i in ids:
        if i not in N:
            errs.append(f"unknown id {i}")
        elif allowed is not None and i not in allowed:
            errs.append(f"{i} not allowed in this slot")
    ok_ids = [i for i in ids if i in N]
    facts = facts_text(N, ok_ids)
    for d in re.findall(r"\b(?:%s) \d{1,2}\b" % "|".join(MON), text):
        if d not in facts:
            errs.append(f"date '{d}' not in cited facts")
    plain = CITE.sub("", text)
    for num in re.findall(r"\b\d+(?:,\d{3})*\b", re.sub(r"\b(?:%s) \d{1,2}\b" % "|".join(MON), "", plain)):
        if num not in facts:
            errs.append(f"number '{num}' not in cited facts")
    for w in ("green", "amber", "red", "on track"):
        if re.search(rf"\b{w}\b", plain, re.I):
            errs.append(f"status word '{w}'")
    for name in re.findall(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", plain):
        if name in people and False:
            pass
    return errs, re.sub(r"\s+\[[A-Z]\d{3}(?:,\s*[A-Z]\d{3})*\]", "", text).strip()


def summary_allowed(N, P, sel, W0, W1):
    """Ids the summary may cite: items that moved in the week, the priorities, and every item the deterministic
    selection chose (highlights.json: done, decisions, risks, waiting, overdue)."""
    moved = {i for i, n in N.items() if any(W0 <= (h.get("date") or "") <= W1 for h in n.get("history", []))}
    return moved | {r["ref"] for r in P.get("priorities") or []} | {x["id"] for x in (sel or {}).get("items") or []}


def p(text, style=None, bold=False, italic=False):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr = "<w:rPr>" + ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + "</w:rPr>" if (bold or italic) else ""
    return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


REFS = []          # every Ref cited in a table, in first-seen order: the References section lists them
LINKS = []         # external hyperlink targets (relationship ids rId100+)
ID_RE = re.compile(r"^[A-Z]\d{3}$")




def hist_date(h):
    """When a history entry happened: a hand edit by when you made it (its `date` is only the evidence it was filed
    under); everything else by its evidence date."""
    return (h.get("at") or "")[:10] or h.get("date") if h.get("by") == "human" else h.get("date")


def what(h):
    """One history entry as the workbench names it."""
    if h.get("by") == "human":
        return "edited by hand: " + ", ".join(f"{k} → {v}" for k, v in (h.get("set") or {}).items()) if h.get("set") else f"{h.get('op')} by hand"
    if h.get("op") == "status":
        return f"status: {h.get('effect')}"
    return {"link": "linked to related work", "ac_met": "criterion met", "waiting": "waiting"}.get(h.get("op"), str(h.get("op")))


def short(q, n=30):
    w = q.split()
    return " ".join(w[:n]) + (" …" if len(w) > n else "")


def link_run(text, anchor=None, rid=None):
    attr = f'w:anchor="{anchor}"' if anchor else f'r:id="{rid}"'
    return (f'<w:hyperlink {attr}><w:r><w:rPr><w:color w:val="2F54D4"/><w:u w:val="single"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:hyperlink>')


def ext_link(url):
    LINKS.append(url)
    return f"rId{99 + len(LINKS)}"


def table(header, rows, widths=None, ref_col=None):
    ref_col = header.index("Ref") if ref_col is None and "Ref" in header else ref_col
    def cell(t, b=False, w=None, ref=False):
        tcpr = f'<w:tcPr><w:tcW w:w="{w}" w:type="dxa"/></w:tcPr>' if w else ""
        if ref and ID_RE.match(str(t or "")):
            if t not in REFS:
                REFS.append(t)
            return f"<w:tc>{tcpr}<w:p>{link_run(t, anchor='ref_' + t)}</w:p></w:tc>"
        return f"<w:tc>{tcpr}{p(t or '', bold=b)}</w:tc>"
    border = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                     for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    x = f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{border}</w:tblBorders></w:tblPr>'
    x += "<w:tr>" + "".join(cell(h, True, (widths or [None] * len(header))[i]) for i, h in enumerate(header)) + "</w:tr>"
    for r in rows:
        x += "<w:tr>" + "".join(cell(str(c), False, (widths or [None] * len(r))[i], i == ref_col) for i, c in enumerate(r)) + "</w:tr>"
    return x + "</w:tbl>" + p("")


def write_docx(path, body, version="", built=None, title="weekly report"):
    W = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>{body}'
           '<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
           '<w:pgMar w:top="1000" w:right="1000" w:bottom="1000" w:left="1000"/></w:sectPr></w:body></w:document>')
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}>'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="20"/></w:rPr></w:rPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="80"/></w:pPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
              '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/></w:style></w:styles>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                   '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
        # Word's File > Info shows these: when it was built and which version it is
        t = (built or dt.datetime.now().astimezone()).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
                   'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
                   'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                   f'<dc:title>{escape(title)}</dc:title><dc:creator>taska</dc:creator><cp:version>{escape(version)}</cp:version>'
                   f'<dcterms:created xsi:type="dcterms:W3CDTF">{t}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{t}</dcterms:modified>'
                   '</cp:coreProperties>')
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   + "".join(f'<Relationship Id="rId{100 + k}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                             f'Target="{escape(u, {chr(34): "&quot;"})}" TargetMode="External"/>' for k, u in enumerate(LINKS))
                   + '</Relationships>')
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)


# ---------- build ----------
def build(cfg, week, prose, out, snapshot=None, upto=None):
    """The docx for `week` from `prose` (checked against the tree) at `out`, plus out + '.check.json'. Returns the check
    report {week, slots, errors, version, built}. `snapshot`: the workbench file name published next to it."""
    REFS.clear(); LINKS.clear()
    a = type("A", (), {"week": week, "prose": str(prose), "out": str(out), "snapshot": snapshot, "upto": upto})
    N, _, order, ev = L.fold(cfg.store, upto=a.upto)
    start = dt.date.fromisoformat(a.week)
    end = start + dt.timedelta(days=4)
    P = json.load(open(a.prose))
    people = {pp.name for pp in cfg.people}
    report = {"week": a.week, "slots": [], "errors": []}

    # Only priorities (and items that moved in the window) may be cited in the summary (spec §4.2).
    W0, W1 = start.isoformat(), end.isoformat()
    sel_p = Path(a.prose).with_name("highlights.json")
    sel = json.loads(sel_p.read_text()) if sel_p.exists() else None
    summary_ok = summary_allowed(N, P, sel, W0, W1)

    def chk(slot, text, allowed=None):
        errs, clean = check(text, N, allowed, people)
        report["slots"].append({"slot": slot, "text": text, "errors": errs})
        if errs:
            report["errors"].append({"slot": slot, "errors": errs})
            return f"(confirm) {clean}"
        return clean

    def state(i):
        n = N[i]
        return {"done": "Done", "cancelled": "Cancelled", "in_progress": "In progress", "blocked": "Blocked"}.get(n["state"], "Open")

    stamp_at = len(b := p(P["title"], "Heading1"))   # the stamp line goes here once the content is known
    b += p(chk("lead", P["lead"], summary_ok))

    # 1. priorities (computed fields: owner, target, status come from the tree; why-now is prose)
    b += p("Priorities for next week", "Heading2")
    rows = []
    for k, r in enumerate(P["priorities"], 1):
        if r.get("ref") not in N:
            report["errors"].append({"slot": f"priority.{k}", "errors": [f"unknown ref {r.get('ref')}"]})
            continue
        n = N[r["ref"]]
        owner = n.get("internal_owner") or r.get("owner") or "—"
        due = md(n["due"]) if n.get("due") else "—"
        rows.append([k, n["title"] if not r.get("label") else r["label"], owner, due, chk(f"why_now.{k}", r["why"], None), r["ref"]])
    b += table(["#", "Priority", "Owner", "Target", "Why now", "Ref"], rows, [400, 3600, 1800, 900, 6000, 700])

    # 2. waiting on client (computed)
    b += p(f"Waiting on {client_label(cfg)}", "Heading2")
    wrows = []
    for n in sorted(N.values(), key=lambda n: n["id"]):
        if n["kind"] != "task" or n["state"] in ("done", "cancelled", "merged", "deleted"):
            continue
        wf = n.get("waiting_for") or {}
        if not (n.get("client_owed") or (wf.get("who") and cfg.side(wf["who"]) == "client")):
            continue
        since = wf.get("since") or n.get("created")
        wrows.append([n["title"], wf.get("who") or n.get("client_owner") or client_label(cfg), md(since) if since else "—",
                      "; ".join(N[x]["title"] for x in n["blocks"][:2]) or "—", n["id"]])
    b += table(["What we need", "From", "Since", "Holds up", "Ref"], wrows, [4200, 1600, 900, 5600, 700])

    # 3. slide 3
    s3 = P["slide3"]
    b += p(P["titles"]["status"], "Heading2")
    b += p("Executive summary", bold=True)
    for k, t in enumerate(s3["summary"], 1):
        b += p("• " + chk(f"summary.{k}", t, summary_ok))
    b += p("Key milestones", bold=True)
    b += table(["#", "Status", "Activity", "Planned", "Actual", "Ref"],
               [[k, m["status"], m["activity"], m["planned"], m.get("actual", ""), m["ref"]] for k, m in enumerate(s3["milestones"], 1)],
               [400, 1400, 6800, 1200, 1200, 700])
    b += p("Key decisions and alignments", bold=True)
    b += table(["#", "Decision", "Date", "Ref"],
               [[k, N[d]["title"], md(N[d]["created"]) if N[d].get("created") else "", d] for k, d in enumerate([x for x in s3["decisions"] if x in N], 1)], [400, 9800, 1200, 700])
    b += p("Risks and issues", bold=True)
    b += table(["ID", "Identified", "Description", "Status", "Impact", "Probability", "Mitigation", "Ref"],
               [[r["id"], r["identified"], r["desc"], r["status"], r.get("impact", ""), r.get("prob", ""),
                 chk(f"mitigation.{r['id']}", r["mitigation"], None) if "[" in r["mitigation"] else r["mitigation"], r["ref"]]
                for r in s3["risks"]], [600, 900, 3800, 700, 700, 900, 3800, 700])

    # 4. slide 5 (carried rows + comments; status computed from the tree when linked)
    b += p(P["titles"]["client_asks"], "Heading2")
    crow = []
    for r in P["slide5"]:
        ref = r.get("ref") or "—"
        st = r["status"]
        if ref in N and N[ref]["state"] == "done" and st != "Done":
            st = "Done (tree)"
        cm = chk(f"comment.{r['sr']}", r["comment"], None) if r.get("comment") else ""
        crow.append([r["sr"], r["decision"], st, r["owner"], r["target"], r["requested"], cm, ref])
    b += table(["Sr.", "Decision", "Status", "Owner", "Target", "Requested", "Comments", "Ref"], crow,
               [500, 4200, 1000, 1500, 800, 900, 3600, 700])

    # 5. references: what each cited item is and where it came from (the report's Ref links jump here)
    b += p("References", "Heading2")
    b += p("What each Ref means: the item, where it was raised and the words that created it."
           + " Each item's dated history is the workbench view of it.", italic=True)
    if a.snapshot and cfg.report.get("file_url"):
        # SharePoint downloads .html instead of showing it and drops any #anchor, so a per-item deep link into the
        # snapshot can never open in Word web or desktop; one absolute link to the file is the honest offer
        url = cfg.report["file_url"].format(path=urllib.parse.quote(f"{a.week}/{a.snapshot}"))
        b += f'<w:p><w:r><w:t xml:space="preserve">Interactive workbench (downloads, opens in a browser): </w:t></w:r>{link_run(a.snapshot, rid=ext_link(url))}</w:p>'
    for m in CITE.findall(json.dumps(P)):
        for x in m.split(","):
            x = x.strip()
            if x in N and x not in REFS:
                REFS.append(x)
    bid = 0
    for i in REFS:
        if i not in N:
            continue
        n, bid = N[i], bid + 1
        par = N.get(n.get("parent") or "")
        first = next((h for h in n["history"] if h.get("evidence")), None)
        if first and first.get("evidence") in ev:
            e = ev[first["evidence"]]
            src = f"{e['title']} · {md(first['date'])}" + (f" at {first['ts']}" if first.get("ts") else "") + (f" · {first['speaker']}" if first.get("speaker") else "")
        else:
            src = "added by hand in the workbench"
        now = state(i) + (f" · owner {n['internal_owner']}" if n.get("internal_owner") else "") + (f" · due {md(n['due'])}" if n.get("due") else "")
        head = (f'<w:p><w:bookmarkStart w:id="{bid}" w:name="ref_{i}"/><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{escape(i)} · {escape(n["title"])}</w:t></w:r>'
                f'<w:bookmarkEnd w:id="{bid}"/>'
                + "</w:p>")
        b += head
        b += p(f"{n['kind'].title()}{' in ' + par['id'] + ' ' + par['title'] if par else ''} · now: {now}", italic=True)
        b += p(f"From: {src}")
        if first and first.get("quote"):
            b += p(f"“{first['quote']}”", italic=True)
        for h in sorted((h for h in n["history"] if h is not first and (h.get("applied") or h.get("type") == "create")),
                        key=lambda h: str(h.get("date") or "")):
            b += p(f"{md(hist_date(h))} · {what(h)}" + (f" · {ev[h['evidence']]['title']}" if h.get("evidence") in ev and h.get("by") != "human" else "")
                   + (f": “{short(h['quote'])}”" if h.get("quote") and h.get("by") != "human" else ""))

    b += p("Open before sending", "Heading2")
    for t in P["open"]:
        b += p("☐ " + t)
    if report["errors"]:
        b += p(f"Check: {len(report['errors'])} slot(s) failed and are marked (confirm).", italic=True)
    b += p(cfg.report.get("footer") or "Generated by taska from internal meetings. Internal only: do not forward to the client. "
           "Ref = taska item id (drop the column when pasting into slides).", italic=True)
    # version = fingerprint of everything a reader sees except the stamp itself: same tree -> same version on every
    # rebuild; SharePoint's own metadata never touches word/document.xml, so the version survives the upload
    version = hashlib.sha1((b + "\n".join(LINKS)).encode()).hexdigest()[:6]
    now = dt.datetime.now().astimezone()
    stamp = (f"Week of {md(W0)}–{md(W1)}, {start.year} · built {now.strftime('%b %-d, %H:%M %Z')} · version {version}"
             f" · from internal meetings up to {md(ev[order[-1]]['date'])} · internal only")
    b = b[:stamp_at] + p(stamp, italic=True) + b[stamp_at:]
    write_docx(a.out, b, version=version, built=now, title=f"{cfg.report.get('title') or cfg.name} weekly report")
    report.update(version=version, built=now.isoformat(timespec="seconds"))
    Path(a.out + ".check.json").write_text(json.dumps(report, indent=1))
    return report


