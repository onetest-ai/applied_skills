"""Teams messages → evidence → pending task proposals. Shared by the channel and the chat adapters.

Fetch goes through the user's msgraph CLI (`channels … --messages`, `teams --messages`). Each message is one
evidence item (kind `message`, its own text as the only turn; the messages before it are context in `meta`, so a
quote can only come from the message itself). For each message the BM25 candidates are judged by the local
decision model; the best (task, change) at or above the model card's `propose_at` becomes an agent status op
flagged `pending: "human"` — it changes nothing until the user confirms it in the workbench. Everything else is
logged to chat_rejected.jsonl with its reason and scores. Nothing is dropped silently.
"""
import datetime as dt
import json
import os

from .. import candidates as CA, judge as J, ledger as L, store as S
from . import msgraph as MG
from .teams_channel import normalize_message

MIN_WORDS = 5
CTX_N = 3
CHANGES = ("done", "progress")      # "cancelled" proposals were noise in every judged case (eval 2026-10-23)
MIN_WORDS_TO_JUDGE = 8
TARGET = {"done": "done", "cancelled": "cancelled", "progress": "in_progress"}
SAYS = {"done": "the work is finished or shared", "cancelled": "the work is dropped", "progress": "the work is under way"}


def _rows(out):
    d = json.loads(out or "[]")
    return d if isinstance(d, list) else d.get("value") or []


def to_msg(g, container, container_name=""):
    n = normalize_message(g, channel_name=container_name)
    ts = g.get("createdDateTime") or ""
    return {"id": n["message_id"], "ts": ts, "from": n["sender"], "text": n["body_text"],
            "files": [f["name"] for f in n["files"]], "file_urls": {f["name"]: f["url"] for f in n["files"]},
            "link": g.get("webUrl") or "", "reply_to": g.get("replyToId") or None,
            "container": container, "container_name": container_name or container, "ctx": ""}


def _with_context(msgs, threaded):
    msgs.sort(key=lambda m: m["ts"])
    by_id = {m["id"]: m for m in msgs}
    for i, m in enumerate(msgs):
        if threaded and m["reply_to"] in by_id:
            sib = [x["text"] for x in msgs[:i] if x["reply_to"] == m["reply_to"]][-(CTX_N - 1):]
            parts = [by_id[m["reply_to"]]["text"]] + sib
        elif threaded:
            parts = []
        else:
            parts = [x["text"] for x in msgs[max(0, i - CTX_N):i] if x["ts"][:10] == m["ts"][:10]]
        m["ctx"] = " | ".join(p for p in parts if p)[:1500]
    return msgs


def fetch_channel(scfg, team_id, channel_id, channel_name=""):
    out = MG.run(scfg, "channels", "--team-id", team_id, "--channel-id", channel_id, "--messages", "--limit", "50",
                 "--expand-replies", "--json")
    flat = []
    for g in _rows(out):
        flat.append(g)
        for r in g.get("replies") or []:
            flat.append(dict(r, replyToId=r.get("replyToId") or g["id"]))
    msgs = [to_msg(g, channel_id, channel_name) for g in flat if g.get("messageType") == "message"]
    return _with_context(msgs, threaded=True)


def fetch_chat(scfg, chat_id, chat_name="", max_messages=500):
    out = MG.run(scfg, "teams", "--messages", chat_id, "--max", str(max_messages), "--json")
    msgs = [to_msg(g, chat_id, chat_name) for g in _rows(out) if g.get("messageType") == "message"]
    return _with_context(msgs, threaded=False)


def _local(ts, tz):
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if tz and t.tzinfo:
        from zoneinfo import ZoneInfo
        t = t.astimezone(ZoneInfo(tz))
    return t.replace(tzinfo=None, microsecond=0)


def evidence_item(m, tz=None):
    t = _local(m["ts"], tz)
    at = t.isoformat()
    first = " ".join(m["text"].split()[:8]) or ", ".join(m["files"])
    return {"id": S.make_id(at, "teams", f"{m['container']}:{m['id']}"), "kind": "message", "at": at,
            "title": f"{m['from'] or 'Someone'} in Teams: {first}", "source": "teams", "text": "",
            "turns": [{"speaker": m["from"], "ts": t.strftime("%H:%M"), "text": m["text"]}], "url": m["link"],
            "meta": {"container": m["container_name"], "message_id": m["id"], "files": m["files"], "context": m["ctx"]}}


def _log(store, rec):
    with open(os.path.join(store, "chat_rejected.jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _quote(text):
    return " ".join(text.split()[:25])


def process(store, cfg, scfg, msgs, cursor_key, judge_fn=J.judge, card=None, k=3):
    """Process messages newer than the cursor. Returns counts. Raises J.JudgeUnavailable (cursor not advanced)."""
    card = card or J.model_card(os.path.expanduser(scfg["model_card"]))
    url = scfg.get("judge_url") or J.DEFAULT_URL
    changes = list(card.get("changes") or CHANGES)
    k = int(card.get("k") or k)
    min_judge = int(card.get("min_words_to_judge") or MIN_WORDS_TO_JUDGE)
    tz = getattr(cfg, "timezone", None)
    since = (S.cursors(store).get(cursor_key) or {}).get("last_at") or ""
    new = [m for m in sorted(msgs, key=lambda m: m["ts"]) if m["ts"] > since]
    rep = {"new": len(new), "proposed": 0, "verified": 0, "rejected": 0, "too_short": 0, "evidence": 0}
    for m in new:
        base = {"at": m["ts"], "message_id": m["id"], "container": m["container_name"], "sender": m["from"],
                "text": m["text"][:300], "files": m["files"]}
        if len(m["text"].split()) < MIN_WORDS and not m["files"]:
            _log(store, base | {"reason": "too_short"}); rep["too_short"] += 1
            S.update_cursor(store, cursor_key, {"last_at": m["ts"]})
            continue
        ev = evidence_item(m, tz)
        rep["evidence"] += S.write_evidence(store, ev)
        if len(m["text"].split()) < min_judge and not m["files"]:
            _log(store, base | {"reason": "not_judged:short"}); rep["rejected"] += 1
            L.mark_processed(store, ev["id"], note="teams")
            S.update_cursor(store, cursor_key, {"last_at": m["ts"]})
            continue
        N, *_ = L.fold(store)
        nodes = [{"id": n["id"], "kind": n["kind"], "title": n["title"], "ac": n["ac"], "parent": n["parent"],
                  "created": n["created"], "state": n["state"]} for n in N.values()]
        cm = {"text": m["text"], "ts": ev["at"], "files": m["files"], "ctx": m["ctx"]}
        best = None
        for nid, bm, rank in CA.candidates(cm, nodes, k=k):
            n = N[nid]
            task = {"id": nid, "title": n["title"], "ac": [a["text"] for a in n["ac"]],
                    "parent_title": (N.get(n["parent"]) or {}).get("title", "")}
            sc = judge_fn({"text": m["text"], "ctx": m["ctx"], "files": m["files"], "from": m["from"], "ts": ev["at"]},
                          task, changes, url=url)
            for c, p in sc.items():
                s = round(p["yes"] + p["partial"], 4)
                if best is None or s > best["score"]:
                    best = {"node": nid, "change": c, "score": s, "scores": sc, "rank": rank, "bm25": bm}
        if best is None or best["score"] < card["propose_at"]:
            _log(store, base | {"reason": f"below_threshold:{card['propose_at']}" if best else "no_candidate",
                                "best_node": best and best["node"], "best_change": best and best["change"],
                                "best_score": best and best["score"], "model": card.get("model")})
            rep["rejected"] += 1
        else:
            rep[_propose(store, cfg, m, ev, N[best["node"]], best, card)] += 1
        L.mark_processed(store, ev["id"], note="teams")
        S.update_cursor(store, cursor_key, {"last_at": m["ts"]})
    return rep


def _propose(store, cfg, m, ev, n, best, card):
    c, target, cur = best["change"], TARGET[best["change"]], n["state"]
    done_already = cur == target or (c == "progress" and cur in ("in_progress", "done"))
    q = _quote(m["text"]) if len(m["text"].split()) >= 3 else ""
    gate = {"model": card.get("model"), "revision": card.get("revision"), "score": best["score"],
            "threshold": card["propose_at"], "scores": best["scores"], "rank": best["rank"], "bm25": best["bm25"]}
    finding = {"id": ev["id"], "message_id": m["id"], "node": n["id"], "title": n["title"], "change": c,
               "quote": q, "link": m["link"], "sender": m["from"], "at": ev["at"], "files": m["files"],
               "verdict": "Verified" if done_already else "Incorrect",
               "severity": "Blocker" if c in ("done", "cancelled") or n["kind"] == "story" else "Major",
               "why": f"{m['from'] or 'Someone'} in {m['container_name']} ({ev['at'][:10]}) says {SAYS[c]}.",
               "suggested": f"state: {cur.replace('_', ' ')} → {target.replace('_', ' ')}", "gate": gate}
    def record():
        with open(os.path.join(store, "out", "teams-findings.jsonl"), "a") as f:
            f.write(json.dumps(finding, ensure_ascii=False) + "\n")
    if done_already:
        record()
        return "verified"
    if not q:
        _log(store, {"at": m["ts"], "message_id": m["id"], "reason": "no_quotable_text", "best_node": n["id"]})
        return "rejected"
    r = L.apply(store, cfg, ev["id"], [{"op": "status", "node": n["id"], "effect": c, "quote": q, "pending": "human",
                                        "why": finding["why"], "suggested": finding["suggested"], "link": m["link"],
                                        "gate": gate}])
    if r["rejected"]:
        _log(store, {"at": m["ts"], "message_id": m["id"], "reason": "ledger_refused:" + r["rejected"][0]["why"],
                     "best_node": n["id"]})
        return "rejected"
    record()
    return "proposed"


def _run_adapter(key, scfg, store, cfg, fetch, cursor_key):
    if not scfg.get("model_card"):
        return {"source": key, "status": "error",
                "error": "MISSING_CONFIG: model_card is required in taska.toml (thresholds measured on your own labelled "
                         "messages); without it no Teams message is judged"}
    try:
        card = J.model_card(os.path.expanduser(scfg["model_card"]))
        rep = process(store, cfg, scfg, fetch(), cursor_key=cursor_key, judge_fn=J.judge, card=card,
                      k=int(scfg.get("candidates", 3)))
    except (J.JudgeUnavailable, MG.GraphError) as e:
        return {"source": key, "status": "error", "error": str(e)}
    except Exception as e:  # one bad source must not stop the meetings/calendar sources after it
        return {"source": key, "status": "error", "error": f"{type(e).__name__}: {e}"}
    return {"source": key, "status": "ok", **rep}


def chat_adapter(cfg, key, scfg, store, cursor):
    """[sources.<key>] adapter="teams_chat", chat_id, chat_name, model_card, judge_url?, max_messages?, cli?"""
    if not scfg.get("chat_id"):
        return cursor, {"source": key, "status": "error", "error": "MISSING_CONFIG: chat_id is required in taska.toml"}
    return cursor, _run_adapter(key, scfg, store, cfg,
                                lambda: fetch_chat(scfg, scfg["chat_id"], scfg.get("chat_name", key),
                                                   int(scfg.get("max_messages", 500))),
                                f"teams_chat:{scfg['chat_id']}")


def channel_adapter(cfg, key, scfg, store, cursor):
    """[sources.<key>] adapter="teams_channel", team_id, channel_id, channel_name, model_card, judge_url?, cli?"""
    if not (scfg.get("team_id") and scfg.get("channel_id")):
        return cursor, {"source": key, "status": "error",
                        "error": "MISSING_CONFIG: team_id and channel_id are required in taska.toml"}
    return cursor, _run_adapter(key, scfg, store, cfg,
                                lambda: fetch_channel(scfg, scfg["team_id"], scfg["channel_id"], scfg.get("channel_name", key)),
                                f"teams_channel:{scfg['channel_id']}")


def ready(scfg):
    """(ok, reason) for `taska doctor`: msgraph signed in, the model card readable, the decision model answering."""
    try:
        if not MG.status(scfg):
            return False, "msgraph is not signed in"
    except MG.GraphError as e:
        return False, str(e)
    try:
        J.model_card(os.path.expanduser(scfg.get("model_card") or ""))
    except J.JudgeUnavailable as e:
        return False, str(e)
    import urllib.request
    url = scfg.get("judge_url") or J.DEFAULT_URL
    try:
        urllib.request.urlopen(url.rsplit("/v1/", 1)[0] + "/", timeout=3)
    except urllib.error.HTTPError:
        pass  # the server answered (404 on /): it is up
    except OSError as e:
        return False, f"decision model not reachable at {url} ({e}); full-run.sh starts it, or run kev.serve"
    return True, ""
