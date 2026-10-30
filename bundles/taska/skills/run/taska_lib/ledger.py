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
import re
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
CLOSING = {"done", "held", "cancelled"}  # agent status effects that close a node: each waits for the user's Confirm
HUMAN_FIELDS = {"title", "internal_owner", "client_owner", "due", "state", "parent", "type", "client_owed",
                "waiting_for"}
AGENT_OPS = {"create", "propose", "status", "revise", "ac_met", "link", "waiting", "suggest_move", "suggest_merge", "note"}
HUMAN_OPS = {"edit", "ac_add", "ac_set", "link", "unlink", "merge", "delete", "accept", "reject", "undo", "create"}
MAX_AC = 6
HIST_KEYS = ("mid", "type", "op", "effect", "evidence", "date", "ts", "speaker", "quote", "note", "due_text", "due",
             "by", "kind", "set", "text", "met", "ac", "ac_text", "depends_on", "into", "title", "ac_add", "who", "what", "source",
             "pending", "at", "from_proposal")


def _chase(N, i):
    """Follow `merged_into` to the surviving node. Cycle-safe: A merged into B and B into A (two accepted
    suggestions that point at each other) must not hang every later fold."""
    seen = set()
    while i in N and N[i]["merged_into"] and i not in seen:
        seen.add(i)
        i = N[i]["merged_into"]
    return i


def _visible_ac(n):
    """The criteria a person or agent sees and numbers from 1 (dropped ones are hidden but kept in order)."""
    return [x for x in n["ac"] if not x.get("dropped")]


def mid_of(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def needs_verdict(e):
    if e.get("pending") == "human":  # a human click decides it; a verifier pass would only spend tokens
        return False
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
            "waiting_for": None, "unlinked": [], "created": e.get("date"), "created_in": e.get("evidence"),
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
        return _chase(N, i)

    for e in evs:
        if ev.get(e.get("evidence"), {}).get("duplicate_of"):
            # a re-synced copy of a meeting: whatever was read from it (your edits and the agent's work alike) is placed
            # at the original meeting, so nothing is lost; the copy itself never counts as a meeting (S.order)
            e = dict(e, evidence=ev[e["evidence"]]["duplicate_of"])
        if e.get("evidence") and e.get("by") != "human" and pos.get(e["evidence"], 1e9) > lim:
            continue
        if e.get("evidence") and e.get("by") == "human" and e["evidence"] in pos and pos[e["evidence"]] > lim:
            continue  # playback: a human edit appears once the evidence it was made against has been reached
        if e.get("mid") in undone:
            continue
        x = ev.get(e.get("evidence")) if e.get("by") != "human" else None
        if x and x.get("date") and e.get("date") != x["date"]:
            e = dict(e, date=x["date"])   # the item was re-dated (repair.py): its events move with it
        t = e["type"]
        if t == "create" and e.get("from_proposal") and decided.get(e["from_proposal"]) != "accept":
            continue   # its Confirm was undone: the proposal is pending again and the node does not exist
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
            if op == "status" and e.get("pending") == "human":
                st = decided.get(e["mid"], "pending")
                n["suggestions"].append({"mid": e["mid"], "op": op, "effect": e.get("effect"), "due": e.get("due"),
                                         "quote": e.get("quote"), "note": e.get("note"), "why": e.get("why"),
                                         "suggested": e.get("suggested"), "link": e.get("link"),
                                         "gate": e.get("gate"), "source": e.get("source"), "speaker": e.get("speaker"),
                                         "cause": e.get("cause"), "status": st, "evidence": e.get("evidence"),
                                         "date": e.get("date")})
                ok = st == "accept" and n["state"] not in ("merged", "deleted") and _apply_status(n, e, setf)
                hist(n, e, ok, status=st)
            elif op == "status":
                if ok and n["state"] not in ("merged", "deleted"):
                    ok = _apply_status(n, e, setf)
                hist(n, e, ok)
            elif op == "revise":
                ok = setf(n, "title", e["title"], e) if e.get("title") else True
                added = [{"text": x, "met": False, "met_by": None, "source": "agent"} for x in e.get("ac_add") or []]
                n["ac"] += added
                hist(n, e, ok or bool(added))  # ac_add applies even when a pinned title is refused — audit it
            elif op == "ac_met" and e.get("pending") == "human":
                st = decided.get(e["mid"], "pending")
                n["suggestions"].append({"mid": e["mid"], "op": op, "ac": e["ac"], "ac_text": e.get("ac_text"),
                                         "quote": e.get("quote"), "why": e.get("why"), "suggested": e.get("suggested"),
                                         "gate": e.get("gate"), "cause": e.get("cause"), "source": e.get("source"),
                                         "speaker": e.get("speaker"), "status": st, "evidence": e.get("evidence"),
                                         "date": e.get("date")})
                vis = _visible_ac(n)
                k = next((j for j, a in enumerate(vis) if a["text"] == e["ac_text"]), -1) if e.get("ac_text") \
                    else e["ac"] - 1
                ok = st == "accept" and 0 <= k < len(vis) and not vis[k].get("human")
                if ok:
                    vis[k].update(met=True, met_by=e["mid"])
                hist(n, e, ok, status=st)
            elif op == "ac_met":
                # by the criterion the claim was about (its text), wherever it sits now; the number is only a
                # fallback for events recorded before ac_text existed
                vis = _visible_ac(n)
                k = next((j for j, a in enumerate(vis) if a["text"] == e["ac_text"]), -1) if e.get("ac_text") \
                    else e["ac"] - 1
                if ok and 0 <= k < len(vis) and not vis[k].get("human"):
                    vis[k].update(met=True, met_by=e["mid"])
                hist(n, e, ok)
            elif op == "link":
                d = resolve(e["depends_on"])
                if ok and d in N and d not in n["depends_on"] and d not in n["unlinked"] and d != n["id"]:
                    n["depends_on"].append(d)
                hist(n, e, ok)
            elif op == "waiting":
                if ok:
                    ok = setf(n, "waiting_for", {"who": e.get("who"), "what": e.get("what"), "since": e["date"],
                                                 "evidence": e["evidence"]} if e.get("what") else None, e)
                hist(n, e, ok)
            elif op == "propose":
                st = decided.get(e["mid"], "pending")
                pr = e["proposal"]
                n["suggestions"].append({"mid": e["mid"], "op": op, "kind": pr["kind"], "title": pr["title"],
                                         "type": pr.get("type"), "owner": pr.get("internal_owner") or pr.get("client_owner"),
                                         "owner_options": pr.get("owner_options") or [], "due": pr.get("due"),
                                         "ac": pr.get("ac") or [], "quote": e.get("quote"), "why": e.get("why"),
                                         "speaker": e.get("speaker"), "source": e.get("source"), "status": st,
                                         "evidence": e.get("evidence"), "date": e.get("date"),
                                         "suggested": f"new {pr['kind']}: {pr['title']}"})
                hist(n, e, False, status=st)
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
                vis, k = _visible_ac(n), e["ac"] - 1
                if 0 <= k < len(vis):
                    if "text" in e:
                        vis[k]["text"] = e["text"]
                    if "met" in e:
                        vis[k].update(met=bool(e["met"]), human=True)
                    if e.get("drop"):
                        vis[k]["dropped"] = True
            elif op == "link":
                d = resolve(e["depends_on"])
                if d in N and d not in n["depends_on"] and d != n["id"]:
                    n["depends_on"].append(d)
                n["unlinked"] = [x for x in n["unlinked"] if x != d]
            elif op == "unlink":
                n["depends_on"] = [x for x in n["depends_on"] if x != e["depends_on"]]
                n["unlinked"].append(e["depends_on"])
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
    if eff == "mention":  # not in STATUS, so never verified: it may be recorded, never applied
        return False
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
    if eff not in ("delayed", "mention") and e.get("due"):
        if "due" in n["pinned"]:
            return eff not in ("due",)
        if not n["due_history"] or n["due_history"][-1]["due"] != e["due"]:
            n["due_history"].append({"due": e["due"], "date": e["date"], "ts": e.get("ts"),
                                     "evidence": e["evidence"], "text": e.get("due_text", "")})
    return True


def _structural(N, n, op, to, e, setf):
    to = _chase(N, to)
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
    from .ingest_audit import room_audio   # local: ingest_audit is an audit module, not needed by the fold
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
        sug = [f"{s['op'][8:]} -> {s['to']}" for s in n["suggestions"]
               if s["status"] == "pending" and s["op"].startswith("suggest_")]
        if sug:
            d["pending_suggestions"] = sug
        tp = [f"{s['effect']}@{s['date']}" for s in n["suggestions"] if s["status"] == "pending" and s["op"] == "status"]
        if tp:
            d["awaiting_confirm"] = tp  # waiting for the user's Confirm; do not propose the same change again
        pw = [f"{s['kind']}: {s['title']}" for s in n["suggestions"] if s["status"] == "pending" and s["op"] == "propose"]
        if pw:
            d["proposed_work"] = pw  # already waiting for Confirm: do not propose it again
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
            "teams": [{"name": t.name, "members": t.members, "aliases": t.aliases} for t in getattr(cfg, "teams", [])],
            # meetings recorded by one room microphone: their speaker tags are not who spoke (owners come from names said)
            "room_recordings": [r["id"] for r in room_audio(ev)],
            "evidence_processed": {"count": len(order),
                                   "latest": [f"{ev[c]['date']} {ev[c]['kind']} {ev[c]['title'][:70]}" for c in order[-5:]]},
            "epics": [dict(view(e), window="→".join(x for x in (e.get("window") or []) if x)) for e in epics
                      if e["children"]],
            "epics_without_work": [f"{e['id']} {e['title']}" for e in epics if not e["children"]]}


# ---------- apply: agent proposals ----------
def apply(store, cfg, evid, proposals):
    with S.locked(store):
        return _apply(store, cfg, evid, proposals)


def _apply(store, cfg, evid, proposals):
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
    made = {e["mid"]: e["id"] for e in evs if e["type"] == "create" and e.get("mid")}
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
            elif not str(p.get("title") or "").strip():
                why = "a create needs a title"
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
            cmid = mid_of(evid, q, "create", k, par, p["title"])  # not the new id: re-applying a batch adds nothing
            if cmid in made:
                handles[p.get("ref") or f"#{i}"] = made[cmid]
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
            e["mid"] = cmid
            out.append(e)
            made[cmid] = nid
            N[nid] = _node(e)
            # dependencies named at creation are links like any other: verified before they count
            for j, d in enumerate(p.get("depends_on") or []):
                d = ref(d)
                if d in N and N[d]["kind"] in ("story", "task") and k in ("story", "task"):
                    out.append(base(p, q, "link", nid, d) | {"type": "agent", "op": "link", "node": nid, "depends_on": d})
            continue
        if op == "propose":
            k, par = p.get("kind"), ref(p.get("parent"))
            pk = N[par]["kind"] if par in N else None
            if k not in ("story", "task", "risk", "decision"):
                why = "propose a story, a task, a risk or a decision"
            elif pk not in PARENT_OK[k]:
                why = f"a {k} needs a parent of kind {sorted(x for x in PARENT_OK[k] if x)}, got {par!r} ({pk})"
            elif k == "story" and not p.get("ac"):
                why = "a story needs acceptance criteria (ac: [...]): what must be true for it to be done"
            elif k == "story" and len(p["ac"]) > MAX_AC:
                why = f"more than {MAX_AC} acceptance criteria"
            elif k == "task" and p.get("type") not in TASK_TYPES:
                why = f"task type must be one of {sorted(TASK_TYPES)}"
            elif not p.get("title"):
                why = "a proposal needs a title"
            elif (N[par]["created"] or "") > item["date"]:
                why = f"{par} did not exist yet on {item['date']}"
            if why:
                rej.append({"i": i, "why": why, "title": p.get("title")})
                continue
            io, co = owner(p.get("internal_owner")), owner(p.get("client_owner"))
            team = cfg.team(io or co) if hasattr(cfg, "team") else None
            e = base(p, q, "propose", par, norm(p["title"])) | {
                "type": "agent", "op": "propose", "node": par, "pending": "human", "why": p.get("why"),
                "proposal": {"kind": k, "title": p["title"], "type": p.get("type") if k == "task" else None,
                             "internal_owner": io, "client_owner": co, "owner_options": list(team.members) if team else [],
                             "client_owed": bool(p.get("client_owed")), "ac": p.get("ac") or [],
                             "due": resolve_due(p.get("due_text"), item["date"], wd), "due_text": p.get("due_text")}}
            if e["mid"] not in seen:
                out.append(e)
                seen.add(e["mid"])
            continue
        node = ref(p.get("node"))
        if op == "note":
            if node not in N:
                rej.append({"i": i, "why": f"note needs a known node, got {node!r}"})
                continue
            if not (p.get("note") or "").strip():
                rej.append({"i": i, "why": "note needs its text in `note`"})
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
        key = [p.get("effect"), p.get("depends_on"), p.get("into"), p.get("parent"), p.get("ac"), p.get("what")]
        if op == "revise":  # title/ac_add distinguish two revisions of one node citing the same quote
            key += [p.get("title"), tuple(p.get("ac_add") or ())]
        e = base(p, q, op, node, *key) | {"type": "agent", "op": op, "node": node}
        if e["mid"] in seen:
            continue
        if op == "status":
            if p.get("effect") not in STATUS | {"mention"}:
                rej.append({"i": i, "why": f"bad effect {p.get('effect')!r}"})
                continue
            e |= {"effect": p["effect"], "due_text": p.get("due_text"),
                  "due": resolve_due(p.get("due_text"), item["date"], wd) if p["effect"] != "delayed" else None,
                  "internal_owner": owner(p.get("internal_owner")), "client_owner": owner(p.get("client_owner"))}
            if p.get("pending") == "human" or p["effect"] in CLOSING:
                e |= {"pending": "human"} | {k: p[k] for k in ("why", "suggested", "link", "gate", "cause") if p.get(k) is not None}
        elif op == "revise":
            if not (p.get("title") or p.get("ac_add")):
                rej.append({"i": i, "why": "revise needs a new title and/or ac_add"})
                continue
            if len(N[node]["ac"]) + len(p.get("ac_add") or []) > MAX_AC:
                rej.append({"i": i, "why": f"{node} would have more than {MAX_AC} criteria: create a second story"})
                continue
            e |= {"title": p.get("title"), "ac_add": p.get("ac_add")}
        elif op == "ac_met":
            live = _visible_ac(N[node])
            if N[node]["kind"] != "story" or not isinstance(p.get("ac"), int) or not 0 < p["ac"] <= len(live):
                rej.append({"i": i, "why": "ac_met needs a story node and the 1-based number of one of its criteria"})
                continue
            agent_saw = p.get("ac_text")
            if agent_saw and norm(agent_saw) != norm(live[p["ac"] - 1]["text"]):
                rej.append({"i": i, "why": f"criterion {p['ac']} of {node} is now “{live[p['ac'] - 1]['text']}”, not "
                                          f"“{agent_saw}”: the criteria changed since you read context; re-read it"})
                continue
            e |= {"ac": p["ac"], "ac_text": live[p["ac"] - 1]["text"]}  # what the agent saw, for the verifier
            if p.get("pending") == "human":
                e |= {"pending": "human"} | {k: p[k] for k in ("why", "suggested", "link", "gate", "cause") if p.get(k) is not None}
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
    # look-alikes among open nodes for each new node or card: reported, never a reason to reject (same words != same work)
    from .similar import similar
    look = []
    for e in out:
        title, kind = ((e.get("fields") or {}).get("title"), e.get("kind")) if e["type"] == "create" else \
            ((e.get("proposal") or {}).get("title"), (e.get("proposal") or {}).get("kind"))
        if e["type"] != "create" and e.get("op") != "propose":
            continue
        m = [x for x in similar(N, title or "", kind) if x["state"] not in CLOSED and x["id"] != e.get("id")]
        if m:
            look.append({"title": title, "kind": kind, "matches": m})
    return {"events": len(out), "by_op": dict(by), "rejected": rej, "similar": look}


def mark_processed(store, evid, note=""):
    S.append(store, [{"type": "processed", "evidence": evid, "by": "agent", "note": note,
                      "at": dt.datetime.now().isoformat(timespec="seconds")}])


# ---------- verify ----------
def _window(text, quote, size=1200):
    """The part of a document the verifier reads: `size` characters around the quote (a file's quote can sit past
    the first `size`, where it was invisible and the claim could only ever be judged weak); the start when the quote
    cannot be located. Quotes are matched normalised, so spacing, case and punctuation may differ in the raw text."""
    words = re.findall(r"\w+", quote)
    m = re.search(r"\W+".join(map(re.escape, words)), text, re.I) if words else None
    if not m:
        return text[:size]
    start = max(0, min(m.start() - (size - (m.end() - m.start())) // 2, len(text) - size))
    return text[start:start + size]


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
            if k is not None else [_window(item.get("text") or "", e["quote"])]
        n = N.get(e["node"], {})
        if e["op"] == "ac_met":
            claim = f"acceptance criterion {e['ac']} met: " + (
                e.get("ac_text") or (n["ac"][e["ac"] - 1]["text"] if 0 < e["ac"] <= len(n.get("ac", [])) else "?"))
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


def _accept_proposal(store, evs, N, prop, body, e):
    """Confirm on proposed work: one accept event plus the human create it stands for, appended together."""
    pr, par = prop["proposal"], prop["node"]
    if par not in N or N[par]["state"] in ("merged", "deleted"):
        raise ValueError(f"{par} no longer exists: reject this proposal instead")
    who = body.get("owner")
    if who is not None:
        known = set(pr.get("owner_options") or []) | {pr.get("internal_owner"), pr.get("client_owner")}
        people = {h.get("speaker") for h in evs if h.get("speaker")} | {
            n.get(k) for n in N.values() for k in ("internal_owner", "client_owner")}
        if who not in known and who not in people:
            raise ValueError(f"{who!r} is not a known person: pick one of {pr.get('owner_options') or 'the people list'}")
    k = pr["kind"]
    n_k = sum(1 for x in evs if x["type"] == "create" and x["kind"] == k) + 1
    owner_field = "client_owner" if pr.get("client_owner") and not pr.get("internal_owner") else "internal_owner"
    fields = {"title": pr["title"], "parent": par, "type": pr.get("type") or ("action" if k == "task" else None),
              "ac": pr.get("ac") or [], "plan": None, "internal_owner": pr.get("internal_owner"),
              "client_owner": pr.get("client_owner"), "client_owed": pr.get("client_owed", False),
              "due": pr.get("due"), "due_text": pr.get("due_text")}
    if who:
        fields[owner_field] = who
    e["target"] = prop["mid"]
    e["mid"] = mid_of("human", e["at"], json.dumps(body, sort_keys=True, default=str))
    c = {"type": "create", "id": f"{PREFIX[k]}{n_k:03d}", "kind": k, "by": "human", "evidence": prop["evidence"],
         "date": prop["date"], "at": e["at"], "from_proposal": prop["mid"], "quote": prop.get("quote"),
         "mid": mid_of("human", e["at"], "create", prop["mid"]), "fields": fields}
    S.append(store, [e, c])
    return c


# ---------- human events (workbench) ----------
HUMAN_STATES = {"open", "in_progress", "blocked", "done", "cancelled"}


def _check_edit(sets, N):
    """Values are checked when the edit is written: the ledger is append-only, so an event that makes every later
    fold or staleness pass raise could only be removed by hand-editing it."""
    if not isinstance(sets, dict) or not set(sets) <= HUMAN_FIELDS:
        raise ValueError(f"editable fields: {sorted(HUMAN_FIELDS)}")
    for k, v in sets.items():
        if k == "due" and v is not None:
            try:
                dt.date.fromisoformat(str(v))
            except ValueError as x:
                raise ValueError(f"due must be an ISO date (YYYY-MM-DD) or null: {x}") from x
        elif k == "state" and v not in HUMAN_STATES:
            raise ValueError(f"state must be one of {sorted(HUMAN_STATES)}")
        elif k == "waiting_for" and v is not None and not (isinstance(v, dict) and (v.get("what") or v.get("who"))):
            raise ValueError("waiting_for is null or {who, what}")
        elif k == "parent" and v not in N:
            raise ValueError(f"unknown parent {v!r}")
        elif k == "client_owed" and not isinstance(v, bool):
            raise ValueError("client_owed is true or false")
        elif k == "title" and not (isinstance(v, str) and v.strip()):
            raise ValueError("title cannot be empty")
        elif k in ("internal_owner", "client_owner", "type") and v is not None and not isinstance(v, str):
            raise ValueError(f"{k} must be text")


def human_event(store, body):
    with S.locked(store):
        return _human_event(store, body)


def _human_event(store, body):
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
        prop = next((x for x in evs if x.get("mid") == body["target"] and x.get("op") == "propose"), None)
        if prop:
            undone = {x["target"] for x in evs if x["type"] == "human" and x["op"] == "undo"}
            done = {x["target"] for x in evs if x["type"] == "human" and x["op"] in ("accept", "reject")
                    and x["mid"] not in undone}   # an undone decision leaves the card open again
            if body["target"] in done:
                raise ValueError("this proposal was already decided")
        if op == "accept" and prop:
            return _accept_proposal(store, evs, N, prop, body, e)
        if op == "accept":
            for n in N.values():
                s_ = next((x for x in n["suggestions"] if x["mid"] == body["target"] and x["op"] == "status"), None)
                if s_ and "state" in n["pinned"] and s_["effect"] not in ("due", "delayed", "reassign"):
                    raise ValueError(f"{n['id']}: the state was set by you, so this Teams proposal cannot change it; "
                                     "edit the state directly or undo your edit first")
                a_ = next((x for x in n["suggestions"] if x["mid"] == body["target"] and x["op"] == "ac_met"), None)
                if a_ and a_.get("ac_text"):   # the fold finds the criterion by this text: say so if it cannot
                    hit = next((x for x in _visible_ac(n) if x["text"] == a_["ac_text"]), None)
                    if hit is None:
                        raise ValueError(f"{n['id']}: the criterion “{a_['ac_text']}” changed since this card was made; "
                                         "reject the card, or re-read the meeting")
                    if hit.get("human"):
                        raise ValueError(f"{n['id']}: you set the criterion “{a_['ac_text']}” yourself, so this card "
                                         "cannot change it; edit it directly or undo your edit first")
        if op == "reject" and body.get("reason"):
            e["reason"] = str(body["reason"])[:80]
    else:
        if body.get("node") not in N:
            raise ValueError(f"unknown node {body.get('node')!r}")
        e["node"] = body["node"]
        for k in ("set", "text", "ac", "met", "drop", "depends_on", "into"):
            if k in body:
                e[k] = body[k]
        if op == "edit":
            _check_edit(e.get("set"), N)
        if op in ("ac_set",) and not isinstance(body.get("ac"), int):
            raise ValueError("ac must be a 1-based integer")
        if op in ("link", "unlink") and body.get("depends_on") not in N:
            raise ValueError(f"unknown node {body.get('depends_on')!r}")
        if op == "merge" and body.get("into") not in N:
            raise ValueError(f"unknown node {body.get('into')!r}")
    e["mid"] = mid_of("human", now, json.dumps(body, sort_keys=True))
    S.append(store, [e])
    return e
