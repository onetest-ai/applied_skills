"""The work tree: Epic > Story > Task, folded from append-only events.

- Agents add evidence-backed events (create, status, revise, ac_met, link, waiting, suggest_move, suggest_merge,
  note), each citing a verbatim span of one evidence item. Status changes, ac_met and link only apply once the
  verifier says `supports`.
- Humans edit through the workbench. A field a human set is pinned: agent evidence still lands in the history
  (applied: false) but never overwrites it. Every human event can be undone; agent suggestions apply only when a
  human accepts them.
"""
import datetime as dt
import hashlib
import json
from collections import defaultdict

from . import store as S
from .text import norm, resolve_due

STATUS = {"held", "done", "progress", "blocked", "cancelled", "reopen", "due", "delayed", "reassign"}
VERIFIED_OPS = {"ac_met", "link", "waiting"}
KINDS = {"epic", "story", "task", "risk", "decision"}
TASK_TYPES = {"action", "session", "dependency"}
PARENT_OK = {"story": {"epic"}, "task": {"story"}, "risk": {"epic", "story"}, "decision": {"epic", "story"},
             "epic": {None}}
PREFIX = {"epic": "E", "story": "S", "task": "T", "risk": "R", "decision": "D"}
CLOSED = {"done", "cancelled", "merged", "deleted"}
HUMAN_FIELDS = {"title", "internal_owner", "client_owner", "due", "state", "parent", "type", "client_owed",
                "waiting_for"}
AGENT_OPS = {"create", "status", "revise", "ac_met", "link", "waiting", "suggest_move", "suggest_merge", "note"}
HUMAN_OPS = {"edit", "ac_add", "ac_set", "link", "unlink", "merge", "delete", "accept", "reject", "undo", "create"}
MAX_AC = 6
HIST_KEYS = ("mid", "type", "op", "effect", "evidence", "date", "ts", "speaker", "quote", "note", "due_text", "due",
             "by", "kind", "set", "text", "met", "ac", "depends_on", "into", "title", "ac_add", "who", "what", "source")


def mid_of(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def needs_verdict(e):
    return e.get("type") == "agent" and (e["op"] in VERIFIED_OPS or (e["op"] == "status" and e.get("effect") in STATUS))


def init_epics(store, epics):
    """One epic per plan activity, written once at init (by: plan)."""
    recs = [{"type": "create", "mid": mid_of("plan", x["name"]), "id": f"E{i:02d}", "kind": "epic", "by": "plan",
             "evidence": None, "date": None,
             "fields": {"title": x["name"], "plan": x["name"], "window": [x.get("start"), x.get("end")],
                        "phase": x.get("phase")}} for i, x in enumerate(epics, 1)]
    S.append(store, recs)
    return len(recs)


# ---------- fold ----------
def _node(e):
    f = e["fields"]
    return {"id": e["id"], "kind": e["kind"], "title": f["title"], "parent": f.get("parent"),
            "type": f.get("type"), "plan": f.get("plan"), "window": f.get("window"), "phase": f.get("phase"),
            "internal_owner": f.get("internal_owner"), "client_owner": f.get("client_owner"),
            "client_owed": bool(f.get("client_owed")), "ac": [], "depends_on": [], "state": "open",
            "due_history": [], "history": [], "pinned": [], "suggestions": [], "merged_into": None,
            "waiting_for": None, "created": e.get("date"), "created_in": e.get("evidence"),
            "creator": e.get("speaker"), "by": e.get("by", "agent"), "changed_in": []}


def fold(store, upto=None, evs=None, ev=None):
    """(nodes, notes, evidence order up to `upto`, evidence). upto=None: the last processed evidence item."""
    ev = S.evidence(store) if ev is None else ev
    order = S.order(ev)
    pos = {c: i for i, c in enumerate(order)}
    evs = S.events(store) if evs is None else evs
    if upto is None:
        seen = {e.get("evidence") for e in evs if e.get("by") == "agent"} | S.processed(evs)
        lim = max([pos[c] for c in seen if c in pos] or [-1])
    else:
        lim = pos[upto]
    verdict = {e["mid"]: e["verdict"] for e in evs if e["type"] == "verdict"}
    undone = {e["target"] for e in evs if e["type"] == "human" and e["op"] == "undo"}
    decided = {e["target"]: e["op"] for e in evs if e["type"] == "human" and e["op"] in ("accept", "reject")
               and e["mid"] not in undone}
    N, notes = {}, defaultdict(list)

    def hist(n, e, applied, **extra):
        n["history"].append({k: e[k] for k in HIST_KEYS if k in e}
                            | {"applied": applied, "verdict": verdict.get(e.get("mid"))} | extra)
        if e.get("evidence"):
            n["changed_in"].append(e["evidence"])

    def setf(n, field, value, e):
        if e.get("by") == "human":
            if field not in n["pinned"]:
                n["pinned"].append(field)
        elif field in n["pinned"]:
            return False
        n[field] = value
        return True

    def resolve(i):
        while i in N and N[i]["merged_into"]:
            i = N[i]["merged_into"]
        return i

    for e in evs:
        if e.get("evidence") and pos.get(e["evidence"], 1e9) > lim:
            continue
        if e.get("mid") in undone:
            continue
        t = e["type"]
        if t == "create":
            n = _node(e)
            N[e["id"]] = n
            n["ac"] = [{"text": x, "met": False, "met_by": None, "source": e.get("by")} for x in e["fields"].get("ac") or []]
            n["depends_on"] = list(e["fields"].get("depends_on") or [])
            if e["fields"].get("due"):
                n["due_history"].append({"due": e["fields"]["due"], "date": e.get("date"), "ts": e.get("ts"),
                                         "evidence": e.get("evidence"), "text": e["fields"].get("due_text", "")})
            if e.get("by") == "human":
                n["pinned"] = ["title"]
            hist(n, e, True, op="create")
        elif t == "note":
            notes[e["node"]].append(e)
        elif t == "agent":
            n = N.get(resolve(e.get("node")))
            if not n:
                continue
            op = e["op"]
            ok = not needs_verdict(e) or verdict.get(e["mid"]) == "supports"
            if op == "status":
                if ok and n["state"] not in ("merged", "deleted"):
                    ok = _apply_status(n, e, setf)
                hist(n, e, ok)
            elif op == "revise":
                ok = setf(n, "title", e["title"], e) if e.get("title") else True
                n["ac"] += [{"text": x, "met": False, "met_by": None, "source": "agent"} for x in e.get("ac_add") or []]
                hist(n, e, ok)
            elif op == "ac_met":
                k = e["ac"] - 1
                if ok and 0 <= k < len(n["ac"]) and not n["ac"][k].get("human"):
                    n["ac"][k].update(met=True, met_by=e["mid"])
                hist(n, e, ok)
            elif op == "link":
                d = resolve(e["depends_on"])
                if ok and d in N and d not in n["depends_on"] and d != n["id"]:
                    n["depends_on"].append(d)
                hist(n, e, ok)
            elif op == "waiting":
                if ok:
                    ok = setf(n, "waiting_for", {"who": e.get("who"), "what": e.get("what"), "since": e["date"],
                                                 "evidence": e["evidence"]} if e.get("what") else None, e)
                hist(n, e, ok)
            elif op in ("suggest_move", "suggest_merge"):
                st = decided.get(e["mid"], "pending")
                to = e.get("parent") or e.get("into")
                n["suggestions"].append({"mid": e["mid"], "op": op, "to": to, "quote": e.get("quote"),
                                         "note": e.get("note"), "status": st, "evidence": e.get("evidence"),
                                         "date": e.get("date")})
                if st == "accept":
                    _structural(N, n, op[8:], to, dict(e, by="human"), setf)
                hist(n, e, st == "accept", status=st)
        elif t == "human":
            op = e["op"]
            if op in ("undo", "accept", "reject"):
                continue
            n = N.get(resolve(e.get("node")))
            if not n:
                continue
            if op == "edit":
                for k, v in (e.get("set") or {}).items():
                    if k not in HUMAN_FIELDS:
                        continue
                    if k == "due":
                        n["due_history"].append({"due": v, "date": e.get("date"), "ts": None,
                                                 "evidence": e.get("evidence"), "text": "set by you"})
                        setf(n, "due", v, e)
                    elif k == "parent":
                        _structural(N, n, "move", v, e, setf)
                    elif k == "waiting_for":
                        setf(n, k, dict(v, since=v.get("since") or e.get("date")) if v else None, e)
                    else:
                        setf(n, k, v, e)
            elif op == "ac_add":
                n["ac"].append({"text": e["text"], "met": False, "met_by": None, "source": "human"})
            elif op == "ac_set":
                k = e["ac"] - 1
                if 0 <= k < len(n["ac"]):
                    if "text" in e:
                        n["ac"][k]["text"] = e["text"]
                    if "met" in e:
                        n["ac"][k].update(met=bool(e["met"]), human=True)
                    if e.get("drop"):
                        n["ac"][k]["dropped"] = True
            elif op == "link":
                d = resolve(e["depends_on"])
                if d in N and d not in n["depends_on"] and d != n["id"]:
                    n["depends_on"].append(d)
            elif op == "unlink":
                n["depends_on"] = [x for x in n["depends_on"] if x != e["depends_on"]]
            elif op == "merge":
                _structural(N, n, "merge", e["into"], e, setf)
            elif op == "delete":
                n["state"] = "deleted"
                n["pinned"].append("state")
            hist(n, e, True)
    for n in N.values():
        n["depends_on"] = list(dict.fromkeys(resolve(d) for d in n["depends_on"] if resolve(d) != n["id"]))
        n["ac"] = [x for x in n["ac"] if not x.get("dropped")]
        n["due"] = n["due_history"][-1]["due"] if n["due_history"] else None
    _derive(N)
    return N, notes, order[:lim + 1], ev


def _apply_status(n, e, setf):
    eff = e["effect"]
    if "state" in n["pinned"] and eff not in ("due", "delayed", "reassign"):
        return False
    if eff in ("held", "done"):
        n["state"] = "done"
        n["waiting_for"] = None if "waiting_for" not in n["pinned"] else n["waiting_for"]
    elif eff == "progress" and n["state"] in ("open", "blocked"):
        n["state"] = "in_progress"
    elif eff in ("blocked", "cancelled"):
        n["state"] = eff
    elif eff == "reopen" and n["state"] in ("done", "cancelled"):
        n["state"] = "in_progress"
    elif eff == "delayed":
        if "due" in n["pinned"]:
            return False
        n["due_history"].append({"due": None, "date": e["date"], "ts": e.get("ts"), "evidence": e["evidence"],
                                 "text": e.get("due_text") or "delayed, no new date"})
    elif eff == "reassign":
        for k in ("internal_owner", "client_owner"):
            if e.get(k):
                setf(n, k, e[k], e)
    if eff != "delayed" and e.get("due"):
        if "due" in n["pinned"]:
            return eff not in ("due",)
        if not n["due_history"] or n["due_history"][-1]["due"] != e["due"]:
            n["due_history"].append({"due": e["due"], "date": e["date"], "ts": e.get("ts"),
                                     "evidence": e["evidence"], "text": e.get("due_text", "")})
    return True


def _structural(N, n, op, to, e, setf):
    if to not in N or to == n["id"]:
        return
    if op == "move":
        if N[to]["kind"] in PARENT_OK.get(n["kind"], set()):
            setf(n, "parent", to, dict(e, by="human"))
    elif op == "merge" and N[to]["kind"] == n["kind"]:
        tgt = N[to]
        n["merged_into"], n["state"] = to, "merged"
        tgt["history"] = sorted(tgt["history"] + [dict(h, merged_from=n["id"]) for h in n["history"]],
                                key=lambda h: (h.get("date") or "", h.get("ts") or ""))
        known = {x["text"] for x in tgt["ac"]}
        tgt["ac"] += [x for x in n["ac"] if x["text"] not in known]
        tgt["depends_on"] += [d for d in n["depends_on"] if d not in tgt["depends_on"] and d != to]
        for c in N.values():
            if c["parent"] == n["id"]:
                c["parent"] = to


def _derive(N):
    kids = defaultdict(list)
    for n in N.values():
        if n["state"] not in ("merged", "deleted"):
            kids[n["parent"]].append(n["id"])
    for n in N.values():
        n["children"] = kids.get(n["id"], [])
        n["derived"] = None
    for n in N.values():
        if n["kind"] != "story" or n["state"] in CLOSED or "state" in n["pinned"]:
            continue
        tasks = [N[c] for c in n["children"] if N[c]["kind"] == "task"]
        if n["ac"] and all(x["met"] for x in n["ac"]):
            n["state"], n["derived"] = "done", "all acceptance criteria met"
        elif n["state"] == "open" and any(t["state"] in ("done", "in_progress") for t in tasks):
            n["state"], n["derived"] = "in_progress", "a task moved"
    for n in N.values():
        w = [d for d in n["depends_on"] if d in N and N[d]["state"] not in CLOSED]
        if n["kind"] == "story":
            w += [c for c in n["children"] if N[c]["kind"] == "task" and N[c]["client_owed"]
                  and N[c]["state"] not in CLOSED and c not in w]
        n["waiting_on"] = w
    for n in N.values():
        n["blocks"] = sorted(m["id"] for m in N.values() if n["id"] in m["waiting_on"] and m["state"] not in CLOSED)


def path(N, i):
    out = []
    while i in N:
        out.append(N[i]["title"])
        i = N[i]["parent"]
    return " › ".join(reversed(out))


# ---------- context: what the extractor sees ----------
def context(store, cfg):
    N, notes, order, ev = fold(store)

    def view(n):
        if n["state"] in CLOSED:
            return {"id": n["id"], "title": n["title"], "state": n["state"]}
        d = {"id": n["id"], "title": n["title"], "state": n["state"]}
        if n.get("created"):
            d["since"] = n["created"]
        if n["kind"] == "story":
            d["ac"] = [f"{i}. {'[x]' if x['met'] else '[ ]'} {x['text']}" for i, x in enumerate(n["ac"], 1)]
        if n["kind"] == "task":
            d["type"] = n["type"]
        for k in ("internal_owner", "client_owner", "due"):
            if n.get(k):
                d[k] = n[k]
        if n["client_owed"]:
            d["client_owed"] = True
        if n["depends_on"]:  # spelled out with titles: a bare id list was read backwards by agents
            d["waits_for"] = [f"{x} {N[x]['title']}" for x in n["depends_on"] if x in N]
        if n["blocks"]:
            d["holds_up"] = [f"{x} {N[x]['title']}" for x in n["blocks"] if x in N]
        if n["waiting_for"]:
            d["waiting_for"] = f"{n['waiting_for'].get('who') or '?'}: {n['waiting_for'].get('what')} (since {n['waiting_for'].get('since')})"
        if n["pinned"]:
            d["pinned_by_human"] = n["pinned"]
        pend = [f"{h.get('effect') or h.get('op')}@{h['date']}" for h in n["history"]
                if h.get("type") == "agent" and needs_verdict(h) and h.get("verdict") is None]
        if pend:
            d["unverified_claims"] = pend[-3:]
        sug = [f"{s['op'][8:]} -> {s['to']}" for s in n["suggestions"] if s["status"] == "pending"]
        if sug:
            d["pending_suggestions"] = sug
        last = next((h for h in reversed(n["history"]) if h.get("evidence")), None)
        if last:
            d["last"] = last["date"]
        for k in ("story", "task"):
            xs = [view(N[c]) for c in n["children"] if N[c]["kind"] == k]
            if xs:
                d[{"story": "stories"}.get(k, k + "s")] = xs
        for k in ("risk", "decision"):  # a register: enough to recognise and not duplicate, one line each
            xs = [f"{c} {N[c]['title'][:90]}" for c in n["children"] if N[c]["kind"] == k]
            if xs:
                d[k + "s"] = xs
        return d

    epics = sorted((n for n in N.values() if n["kind"] == "epic" and n["state"] not in ("merged", "deleted")),
                   key=lambda x: x["id"])
    return {"project": cfg.name,
            "people": [{"name": p.name, "side": p.side} for p in cfg.people],
            "evidence_processed": {"count": len(order),
                                   "latest": [f"{ev[c]['date']} {ev[c]['kind']} {ev[c]['title'][:70]}" for c in order[-5:]]},
            "epics": [dict(view(e), window="→".join(x for x in (e.get("window") or []) if x)) for e in epics
                      if e["children"]],
            "epics_without_work": [f"{e['id']} {e['title']}" for e in epics if not e["children"]]}


# ---------- apply: agent proposals ----------
def apply(store, cfg, evid, proposals):
    ev = S.evidence(store)
    if evid not in ev:
        raise ValueError(f"unknown evidence {evid!r}")
    item = ev[evid]
    full, full_nobc = norm(S.full_text(item)), norm(S.full_text(item, drop_bc=True))
    evs = S.events(store)
    N, *_ = fold(store, evs=evs, ev=ev)
    counts = defaultdict(int)
    for e in evs:
        if e["type"] == "create":
            counts[e["kind"]] += 1
    seen = {e.get("mid") for e in evs}
    handles, out, rej = {}, [], []
    wd = cfg.working_days

    def locate(q):
        for t in item.get("turns") or []:
            if q[:40] in norm(t["text"]):
                return t["ts"], cfg.canonical(t["speaker"])
        return None, None

    def base(p, q, *key):
        ts, sp = locate(q)
        return {"mid": mid_of(evid, q, *key), "evidence": evid, "date": item["date"], "source": item["source"],
                "kind": item.get("meeting_kind") or item["kind"], "ts": ts, "speaker": sp, "quote": p["quote"],
                "quote_norm": q, "note": p.get("note"), "confidence": p.get("confidence"), "by": "agent"}

    def ref(x):
        return handles.get(x, x)

    def owner(x):
        return cfg.canonical(x) if x else None

    for i, p in enumerate(proposals):
        q = norm(p.get("quote", ""))
        op = p.get("op")
        why = None
        if len(q.split()) < 3:
            why = "quote too short"
        elif q not in full and q not in full_nobc:
            why = "quote not verbatim in this evidence item (one contiguous span)"
        elif op not in AGENT_OPS:
            why = f"bad op {op!r}"
        if why:
            rej.append({"i": i, "why": why, "quote": p.get("quote", "")[:80]})
            continue
        if op == "create":
            k, par = p.get("kind"), ref(p.get("parent"))
            pk = N[par]["kind"] if par in N else None
            if k not in KINDS:
                why = f"bad kind {k!r}"
            elif k == "epic":
                if p.get("plan") != "Off-plan":
                    why = "agents create epics only for off-plan work: plan must be 'Off-plan'"
            elif pk not in PARENT_OK[k]:
                why = f"a {k} needs a parent of kind {sorted(x for x in PARENT_OK[k] if x)}, got {par!r} ({pk})"
            elif k == "story" and not p.get("ac"):
                why = "a story needs acceptance criteria (ac: [...]): what must be true for it to be done"
            elif k == "story" and len(p["ac"]) > MAX_AC:
                why = f"more than {MAX_AC} acceptance criteria: split it into two stories and link them"
            elif k == "task":
                if p.get("type") not in TASK_TYPES:
                    why = f"task type must be one of {sorted(TASK_TYPES)}"
                elif not (p.get("internal_owner") or p.get("client_owner") or p.get("due_text")
                          or p.get("type") in ("session", "dependency")):
                    why = "admission bar: a task needs an owner, a date, or to be a session/dependency; else op 'note'"
            if why:
                rej.append({"i": i, "why": why, "title": p.get("title")})
                continue
            counts[k] += 1
            nid = f"{PREFIX[k]}{counts[k]:03d}" if k != "epic" else f"E{counts[k]:02d}"
            handles[p.get("ref") or f"#{i}"] = nid
            e = base(p, q, "create", nid) | {"type": "create", "id": nid, "kind": k, "fields": {
                "title": p["title"], "parent": par if k != "epic" else None, "type": p.get("type"),
                "plan": "Off-plan" if k == "epic" else None, "internal_owner": owner(p.get("internal_owner")),
                "client_owner": owner(p.get("client_owner")), "client_owed": bool(p.get("client_owed")),
                "ac": p.get("ac") or [], "due": resolve_due(p.get("due_text"), item["date"], wd),
                "due_text": p.get("due_text")}}
            out.append(e)
            N[nid] = _node(e)
            # dependencies named at creation are links like any other: verified before they count
            for j, d in enumerate(p.get("depends_on") or []):
                d = ref(d)
                if d in N and N[d]["kind"] in ("story", "task") and k in ("story", "task"):
                    out.append(base(p, q, "link", nid, d) | {"type": "agent", "op": "link", "node": nid, "depends_on": d})
            continue
        node = ref(p.get("node"))
        if op == "note":
            if node not in N:
                rej.append({"i": i, "why": f"note needs a known node, got {node!r}"})
                continue
            out.append(base(p, q, "note", node) | {"type": "note", "node": node, "text": p.get("note") or ""})
            continue
        if node not in N:
            rej.append({"i": i, "why": f"unknown node {node!r}"})
            continue
        later = [x for x in (node, ref(p.get("depends_on")), ref(p.get("into")))
                 if x in N and (N[x]["created"] or "") > item["date"]]
        if later:
            rej.append({"i": i, "why": f"{later} did not exist yet on {item['date']}"})
            continue
        e = base(p, q, op, node, p.get("effect"), p.get("depends_on"), p.get("into"), p.get("parent"), p.get("ac"),
                 p.get("what")) | {"type": "agent", "op": op, "node": node}
        if e["mid"] in seen:
            continue
        if op == "status":
            if p.get("effect") not in STATUS | {"mention"}:
                rej.append({"i": i, "why": f"bad effect {p.get('effect')!r}"})
                continue
            e |= {"effect": p["effect"], "due_text": p.get("due_text"),
                  "due": resolve_due(p.get("due_text"), item["date"], wd) if p["effect"] != "delayed" else None,
                  "internal_owner": owner(p.get("internal_owner")), "client_owner": owner(p.get("client_owner"))}
        elif op == "revise":
            if not (p.get("title") or p.get("ac_add")):
                rej.append({"i": i, "why": "revise needs a new title and/or ac_add"})
                continue
            if len(N[node]["ac"]) + len(p.get("ac_add") or []) > MAX_AC:
                rej.append({"i": i, "why": f"{node} would have more than {MAX_AC} criteria: create a second story"})
                continue
            e |= {"title": p.get("title"), "ac_add": p.get("ac_add")}
        elif op == "ac_met":
            if N[node]["kind"] != "story" or not isinstance(p.get("ac"), int):
                rej.append({"i": i, "why": "ac_met needs a story node and a 1-based ac number"})
                continue
            e |= {"ac": p["ac"]}
        elif op == "link":
            d = ref(p.get("depends_on"))
            if d not in N or N[d]["kind"] not in ("story", "task") or N[node]["kind"] not in ("story", "task"):
                rej.append({"i": i, "why": f"link joins stories/tasks; got {node} -> {d}"})
                continue
            e |= {"depends_on": d}
        elif op == "waiting":
            if N[node]["kind"] not in ("story", "task"):
                rej.append({"i": i, "why": "waiting applies to a story or task"})
                continue
            e |= {"who": owner(p.get("who")), "what": p.get("what")}
        elif op == "suggest_move":
            to = ref(p.get("parent"))
            if to not in N or N[to]["kind"] not in PARENT_OK.get(N[node]["kind"], set()):
                rej.append({"i": i, "why": f"cannot move {node} under {to}"})
                continue
            e |= {"parent": to}
        elif op == "suggest_merge":
            to = ref(p.get("into"))
            if to not in N or N[to]["kind"] != N[node]["kind"] or to == node:
                rej.append({"i": i, "why": f"merge needs two nodes of one kind; got {node} -> {to}"})
                continue
            e |= {"into": to}
        out.append(e)
        seen.add(e["mid"])
    S.append(store, out)
    by = defaultdict(int)
    for e in out:
        by["create_" + e["kind"] if e["type"] == "create" else e.get("op") or e["type"]] += 1
    return {"events": len(out), "by_op": dict(by), "rejected": rej}


def mark_processed(store, evid, note=""):
    S.append(store, [{"type": "processed", "evidence": evid, "by": "agent", "note": note,
                      "at": dt.datetime.now().isoformat(timespec="seconds")}])


# ---------- verify ----------
def verify_prep(store, weak=False):
    ev = S.evidence(store)
    evs = S.events(store)
    latest = {e["mid"]: e["verdict"] for e in evs if e["type"] == "verdict"}
    N, *_ = fold(store, evs=evs, ev=ev)
    out = []
    for e in evs:
        if not needs_verdict(e):
            continue
        v = latest.get(e["mid"])
        if v and not (weak and v == "weak"):
            continue
        item = ev.get(e["evidence"]) or {}
        turns = item.get("turns") or []
        k = next((i for i, t in enumerate(turns) if e["quote_norm"][:40] in norm(t["text"])), None)
        ctx = [f"[{t['ts']}] {t['speaker']}: {t['text']}" for t in turns[max(0, k - 2):k + 3] if not t.get("bc")] \
            if k is not None else [(item.get("text") or "")[:1200]]
        n = N.get(e["node"], {})
        if e["op"] == "ac_met":
            claim = f"acceptance criterion {e['ac']} met: " + (n["ac"][e["ac"] - 1]["text"] if 0 < e["ac"] <= len(n.get("ac", [])) else "?")
        elif e["op"] == "link":
            claim = f"cannot finish until “{N.get(e['depends_on'], {}).get('title', e['depends_on'])}” ({e['depends_on']}) does"
        elif e["op"] == "waiting":
            claim = (f"is waiting on {e.get('who') or 'someone'}: {e.get('what')}" if e.get("what")
                     else "is no longer waiting on anyone (what it waited for arrived or was dropped)")
        else:
            claim = e.get("effect")
        out.append({"mid": e["mid"], "node": e["node"], "kind": n.get("kind"), "path": path(N, e["node"]),
                    "claimed": claim, "due": e.get("due"), "quote": e["quote"],
                    "evidence": f"{e['date']} {item.get('kind')} {item.get('title')}", "context": ctx})
    return out


def verify_apply(store, verdicts):
    mids = {e["mid"] for e in S.events(store) if needs_verdict(e)}
    out, rej = [], []
    for v in verdicts:
        if v.get("mid") not in mids or v.get("verdict") not in ("supports", "weak", "wrong"):
            rej.append(v)
            continue
        out.append({"type": "verdict", "mid": v["mid"], "verdict": v["verdict"], "reason": v.get("reason"),
                    "by": "verifier"})
    S.append(store, out)
    c = defaultdict(int)
    for v in out:
        c[v["verdict"]] += 1
    return {"verdicts": dict(c), "rejected": rej}


# ---------- human events (workbench) ----------
def human_event(store, body):
    evs = S.events(store)
    N, _, order, ev = fold(store, evs=evs)
    op = body.get("op")
    if op not in HUMAN_OPS:
        raise ValueError(f"bad op {op!r}")
    last = order[-1] if order else None
    now = dt.datetime.now().isoformat(timespec="seconds")
    date = ev[last]["date"] if last else now[:10]
    e = {"type": "human", "op": op, "by": "human", "evidence": last, "date": date, "at": now}
    if op == "create":
        k, par = body.get("kind"), body.get("parent")
        if k not in KINDS or (k != "epic" and (par not in N or N[par]["kind"] not in PARENT_OK[k])):
            raise ValueError(f"cannot create a {k} under {par}")
        n_k = sum(1 for x in evs if x["type"] == "create" and x["kind"] == k) + 1
        e = {"type": "create", "id": f"{PREFIX[k]}{n_k:03d}" if k != "epic" else f"E{n_k:02d}", "kind": k,
             "by": "human", "evidence": last, "date": date, "at": now,
             "fields": {"title": body.get("title") or "Untitled", "parent": par if k != "epic" else None,
                        "type": body.get("type") or ("action" if k == "task" else None), "ac": body.get("ac") or [],
                        "plan": "Off-plan" if k == "epic" else None}}
    elif op in ("undo", "accept", "reject"):
        if not body.get("target"):
            raise ValueError("target mid required")
        e["target"] = body["target"]
    else:
        if body.get("node") not in N:
            raise ValueError(f"unknown node {body.get('node')!r}")
        e["node"] = body["node"]
        for k in ("set", "text", "ac", "met", "drop", "depends_on", "into"):
            if k in body:
                e[k] = body[k]
        if op == "edit" and not set(e.get("set") or {}) <= HUMAN_FIELDS:
            raise ValueError(f"editable fields: {sorted(HUMAN_FIELDS)}")
    e["mid"] = mid_of("human", now, json.dumps(body, sort_keys=True))
    S.append(store, [e])
    return e
