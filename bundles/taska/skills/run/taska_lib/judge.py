"""The local decision model (a `kev.serve`-compatible server on localhost) judging (message, task) pairs.

taska never loads a model: it POSTs to the server and reads probabilities. The questions mirror the ones the
ground-truth judges answered, so a model card measured on that eval applies here unchanged.
"""
import json
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8009/v1/systemone"
CHANGES = {"done": "is finished, delivered, sent, shared or held",
           "cancelled": "is cancelled, dropped or no longer needed",
           "moved": "has a new or moved date or deadline",
           "progress": "has started or advanced"}
CRITERIA = {"yes": "the message states it for this specific task",
            "partial": "clearly about this task and implies it, but does not state it",
            "no": "about other work, too vague, only a plan, question or reminder, or unrelated"}
LB_FOR_AUTO = 0.95


class JudgeUnavailable(RuntimeError):
    pass


def judge(msg, task, changes, url=DEFAULT_URL, timeout=180):
    """{change: {"yes": p, "partial": p}} for msg {text, ctx, files, from, ts} and task {id, title, ac, parent_title}."""
    bad = [c for c in changes if c not in CHANGES]
    if bad:
        raise ValueError(f"unknown change {bad}; known: {sorted(CHANGES)}")
    state = {"message": (msg.get("text") or "")[:3000] or "Shared files: " + ", ".join(msg.get("files") or []),
             "earlier_messages": (msg.get("ctx") or "")[:1500], "files": msg.get("files") or [],
             "sent_by": msg.get("from") or "", "sent_at": msg.get("ts") or "",
             "task": f"{task['id']}: {task['title']}", "task_criteria": task.get("ac") or [],
             "task_part_of": task.get("parent_title") or ""}
    q = {c: {"type": "choice", "criteria": CRITERIA,
             "instructions": f"Does `message` (read with `earlier_messages`) show that the work in `task` {CHANGES[c]}?"}
         for c in changes}
    req = urllib.request.Request(url, json.dumps({"state": state, "model": "kev-latest", "questions": q}).encode(),
                                 {"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ans = json.loads(r.read())["answers"]
        return {c: {"yes": float(ans[c]["probabilities"].get("yes", 0)),
                    "partial": float(ans[c]["probabilities"].get("partial", 0))} for c in changes}
    except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError) as e:
        raise JudgeUnavailable(f"decision model not reachable or answered badly at {url}: {e!r}") from e


def ask(state, question, url=DEFAULT_URL, timeout=120):
    """One yes/no question about a free-form state → P(yes). JudgeUnavailable when unreachable or malformed."""
    body = {"state": state, "model": "kev-latest", "questions": {"q": {"type": "noul", "instructions": question}}}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return float(json.loads(r.read())["answers"]["q"]["noul"])
    except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError) as e:
        raise JudgeUnavailable(f"decision model not reachable or answered badly at {url}: {e!r}") from e


def model_card(path):
    """Thresholds measured on the eval. Auto-apply is allowed only with a measured test precision lower bound ≥ 0.95."""
    try:
        card = json.loads(open(path).read())
    except (OSError, ValueError) as e:
        raise JudgeUnavailable(f"model card not readable at {path}: {e}") from e
    card.setdefault("propose_at", 0.75)
    card["auto_apply_allowed"] = bool(card.get("auto_apply_at") is not None and (card.get("test_precision_lb") or 0) >= LB_FOR_AUTO)
    return card
