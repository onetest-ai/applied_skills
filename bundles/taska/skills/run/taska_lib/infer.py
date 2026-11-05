"""Closures a done/held task logically implies, proposed for the user's Confirm (never applied by themselves).

For each task that was closed by evidence (an applied held/done status with a quote):
  (a) rule  — its open recorded prerequisites (`depends_on`) are probably done or no longer needed;
  (b) model — open tasks of the same story, created by then, that the local model says had to be finished first;
  (c) model — the parent story's unmet criterion this task best satisfies (best child only).
Every proposal quotes the evidence that closed the cause, carries `cause`, and waits as `pending: "human"`.
Siblings never imply each other (eval 2026-10-23: invalid logic). Thresholds come from that eval.
"""
from . import judge as J, ledger as L

MISSING_AT = 0.75
CRITERION_AT = 0.90
Q_MUST = "Must `task` be finished before `event` can take place?"
Q_CRIT = "Does `completed_task` being done show that `criterion` is met?"


def _closing(n):
    """The applied held/done status entry that closed n, with its evidence and quote; None when closed by hand."""
    for h in reversed(n["history"]):
        if h.get("op") == "status" and h.get("effect") in ("held", "done") and h.get("applied") and h.get("evidence") \
                and len((h.get("quote") or "").split()) >= 3:
            return h
    return None


def run(store, cfg, ask=J.ask, url=J.DEFAULT_URL):
    N, *_ = L.fold(store)
    rep = {"status": "ok", "causes": 0, "proposed": 0, "skipped": [], "model": "ok"}
    model_ok = [True]
    cache = {}

    def q(state, question):
        if not model_ok[0]:
            return None
        key = (question, repr(sorted(state.items())))
        if key not in cache:
            try:
                cache[key] = ask(state, question, url=url)
            except J.JudgeUnavailable as e:
                model_ok[0], rep["model"] = False, f"skipped: {e}"
                return None
        return cache[key]

    def is_open(i):
        return i in N and N[i]["state"] not in L.CLOSED

    def waiting(i):
        return any(s["status"] == "pending" and s["op"] == "status" and s.get("effect") in ("done", "held", "cancelled")
                   for s in N[i]["suggestions"])  # an open closure proposal already exists; progress ones don't count

    for c in [n for n in N.values() if n["kind"] == "task" and n["state"] == "done"]:
        story = N.get(c["parent"]) if c.get("parent") else None
        prereq = [d for d in c["depends_on"] if is_open(d) and not waiting(d)]
        sibs = [N[x] for x in (story["children"] if story else []) if x != c["id"] and is_open(x) and N[x]["kind"] == "task"
                and x not in c["depends_on"] and not waiting(x)]
        unmet = [k for k, a in enumerate(story["ac"], 1) if not a["met"]] if story else []
        if not (prereq or sibs or unmet):
            continue
        h = _closing(c)
        if not h:
            rep["skipped"].append({"node": c["id"], "reason": "closed by hand: no evidence to quote"})
            continue
        rep["causes"] += 1
        verb, day = ("was held" if h["effect"] == "held" else "was done"), h["date"]
        base = {"op": "status", "effect": "done", "quote": h["quote"], "pending": "human", "cause": c["id"],
                "suggested": "state: open → done"}
        props = [base | {"node": d, "gate": {"rule": "dependency"},
                         "why": f"{c['id']} “{c['title']}” {verb} on {day}, and it needed {d} first: probably done or no longer needed."}
                 for d in prereq]
        for t in sibs:
            if not t.get("created") or str(t["created"])[:10] > day:
                continue
            p = q({"event": c["title"], "event_date": day, "task": t["title"], "task_created": t["created"]}, Q_MUST)
            if p is not None and p >= MISSING_AT:
                props.append(base | {"node": t["id"], "gate": {"model": "local", "score": round(p, 3), "threshold": MISSING_AT},
                                     "why": f"{c['id']} “{c['title']}” {verb} on {day}; {t['id']} had to be finished before it."})
        done_kids = [N[x] for x in story["children"] if x in N and N[x]["kind"] == "task" and N[x]["state"] == "done"] if story else []
        for k in unmet:
            crit = story["ac"][k - 1]["text"]
            scored = [(q({"criterion": crit, "completed_task": x["title"], "story": story["title"]}, Q_CRIT), x["id"]) for x in done_kids]
            scored = [(s, i) for s, i in scored if s is not None]
            if not scored:
                continue
            best_s, best_i = max(scored)
            if best_i == c["id"] and best_s >= CRITERION_AT:
                props.append({"op": "ac_met", "node": story["id"], "ac": k, "ac_text": crit, "quote": h["quote"], "pending": "human",
                              "cause": c["id"], "gate": {"model": "local", "score": round(best_s, 3), "threshold": CRITERION_AT},
                              "suggested": f"criterion “{crit}” → met",
                              "why": f"{c['id']} “{c['title']}” {verb} on {day}, which is what this criterion asks for."})
        if props:
            r = L.apply(store, cfg, h["evidence"], props)
            rep["proposed"] += r["events"]
            rep["skipped"] += [{"node": props[x["i"]]["node"], "reason": x["why"]} for x in r["rejected"]]
    return rep
