"""Teams message helpers: Graph message → canonical dict (HTML stripped, guest suffixes removed, files from both
attachment sources), thread context, and the older lexical matcher. Fetching and the proposal pipeline live in
teams_common.py. No credentials here — Graph calls go through the user's msgraph CLI.
"""
import datetime as _dt
import html.parser
import json as _json
import os as _os
import re

# Score below this → clear miss (logged as no_match)
MATCH_THRESHOLD = 0.4
# Score in [GREY_LOW, MATCH_THRESHOLD) → ambiguous (logged as ambiguous_score, no fallback)
GREY_LOW = 0.3
MIN_BODY_WORDS = 5   # body must have >= this many words (unless files present)

# Trailing guest/external markers Graph adds to cross-tenant display names
_GUEST_RE = re.compile(r'\s*\((?:Guest|External|ext|Ext)\)\s*$', re.I)


# ── HTML → plain text ────────────────────────────────────────────────────────

class _TextExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag == "br":
            self._parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def text(self):
        return re.sub(r'\s+', ' ', ''.join(self._parts)).strip()


def _strip_html(raw: str) -> str:
    p = _TextExtractor()
    p.feed(raw or "")
    return p.text()


def _norm_sender(display_name: str) -> str:
    """Strip trailing guest/external markers from Graph display names.

    'Alex Rivera (Guest)' → 'Alex Rivera'
    'Jo Smith (External)' → 'Jo Smith'
    """
    return _GUEST_RE.sub('', display_name or '').strip()


# ── Normalization ─────────────────────────────────────────────────────────────

def normalize_message(msg: dict, channel_name: str = "") -> dict:
    """Return a canonical dict from one raw Graph API channel message.

    Extracts files from both msg['attachments'] (structured list) and
    <attachment> tags embedded in msg['body']['content'] HTML.
    Returns: message_id, thread_id, channel_name, timestamp,
    sender (guest markers stripped), body_text, context_text, files [{name, url}].
    """
    message_id = str(msg.get("id") or "")
    thread_id  = str(msg.get("replyToId") or message_id)

    raw_sender = ((msg.get("from") or {}).get("user") or {}).get("displayName") or ""
    sender     = _norm_sender(raw_sender)
    timestamp  = msg.get("createdDateTime") or ""

    body_raw  = ((msg.get("body") or {}).get("content") or "")
    body_text = _strip_html(body_raw)

    files: list = []
    seen_names: set = set()
    for att in msg.get("attachments") or []:
        name = (att.get("name") or "").strip()
        url  = att.get("contentUrl") or att.get("content") or ""
        if name and name not in seen_names:
            files.append({"name": name, "url": url})
            seen_names.add(name)

    att_map = {str(a.get("id") or ""): a for a in (msg.get("attachments") or [])}
    for m in re.finditer(r'<attachment\s+id=["\']([^"\']+)["\']', body_raw, re.I):
        att = att_map.get(m.group(1))
        if att:
            name = (att.get("name") or "").strip()
            if name and name not in seen_names:
                files.append({"name": name, "url": att.get("contentUrl") or ""})
                seen_names.add(name)

    return {
        "message_id":            message_id,
        "thread_id":             thread_id,
        "channel_name":          channel_name,
        "timestamp":             timestamp,
        "sender":                sender,
        "body_text":             body_text,
        "context_text":          "",   # filled by enrich_thread_context()
        "parent_outside_window": False,
        "files":                 files,
    }


# ── Thread context ────────────────────────────────────────────────────────────

import difflib  # noqa: E402 — stdlib, kept near its use


def enrich_thread_context(msgs: list, max_preceding: int = 3) -> list:
    """Set context_text on each reply from its parent + up to max_preceding siblings.

    Root messages (thread_id == message_id) keep context_text=''.
    When a reply's parent is not in the batch (outside the fetch window),
    parent_outside_window is set to True — the caller knows context is incomplete.
    """
    by_id = {m["message_id"]: m for m in msgs}
    threads: dict = {}
    for m in sorted(msgs, key=lambda x: x["timestamp"]):
        threads.setdefault(m["thread_id"], []).append(m)

    for m in msgs:
        if m["thread_id"] == m["message_id"]:
            continue
        parent = by_id.get(m["thread_id"])
        if not parent:
            # reply to a message outside the current fetch window
            m["parent_outside_window"] = True
            continue
        parts = [parent["body_text"]]
        siblings = [s for s in threads.get(m["thread_id"], [])
                    if s["message_id"] != m["message_id"]
                    and s["timestamp"] < m["timestamp"]][-max_preceding:]
        parts.extend(s["body_text"] for s in siblings if s["body_text"])
        m["context_text"] = " ".join(p for p in parts if p).strip()

    return msgs


# ── Task matching ─────────────────────────────────────────────────────────────

def _file_stem(name: str) -> str:
    return re.sub(r'\.[^.]+$', '', name)


def _tokens(text: str) -> str:
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text.lower()).split())


def match_task(msg: dict, open_tasks: list,
               threshold: float = MATCH_THRESHOLD) -> "dict | None":
    """Return best-matching open task or None.

    Three scoring passes: file stem, body text, thread context.
    Returns {task_id, task_title, matched_file, score} or None when no pair
    exceeds threshold. Caller inspects score vs GREY_LOW to distinguish
    ambiguous from clear miss.
    """
    best_score = 0.0
    best: "dict | None" = None
    body_tokens    = _tokens(msg.get("body_text", ""))
    context_tokens = _tokens(msg.get("context_text", ""))

    for task in open_tasks:
        task_tokens = _tokens(task.get("title", ""))
        if not task_tokens:
            continue

        for f in msg.get("files", []):
            score = difflib.SequenceMatcher(
                None, _tokens(_file_stem(f["name"])), task_tokens).ratio()
            if score > best_score:
                best_score = score
                best = {"task_id": task.get("id"), "task_title": task.get("title"),
                        "matched_file": f["name"], "score": round(score, 3)}

        if body_tokens:
            score = difflib.SequenceMatcher(None, body_tokens, task_tokens).ratio()
            if score > best_score:
                best_score = score
                best = {"task_id": task.get("id"), "task_title": task.get("title"),
                        "matched_file": None, "score": round(score, 3)}

        if context_tokens:
            score = difflib.SequenceMatcher(None, context_tokens, task_tokens).ratio()
            if score > best_score:
                best_score = score
                best = {"task_id": task.get("id"), "task_title": task.get("title"),
                        "matched_file": None, "score": round(score, 3)}

    return best if best_score >= threshold else None


def _reject_reason(best_score: float, parent_outside_window: bool) -> str:
    """Exact failure reason string written to chat_rejected.jsonl.

    Fail-fast: every rejection carries a machine-readable reason so the caller
    knows precisely why — no silent drops.
    """
    if parent_outside_window and best_score < GREY_LOW:
        return f"no_match:score={best_score:.3f},parent_outside_window"
    if parent_outside_window:
        return f"ambiguous_score:score={best_score:.3f},parent_outside_window"
    if GREY_LOW <= best_score < MATCH_THRESHOLD:
        return f"ambiguous_score:score={best_score:.3f}"
    return f"no_match:score={best_score:.3f}"


# ── Evidence building ─────────────────────────────────────────────────────────

def build_evidence(msg: dict, match: dict,
                   source_name: str = "teams_channel") -> dict:
    """Return a taska evidence item dict ready for store.write_evidence().

    When matched_file is set, id is derived from message_id:filename so a
    message that shares multiple files produces one distinct evidence item
    per file-task pair — no silent winner-takes-all.
    """
    from ..store import make_id

    raw_at = msg.get("timestamp") or _dt.datetime.utcnow().isoformat() + "Z"
    at = raw_at.replace("Z", "").replace("+00:00", "")

    file_name = match.get("matched_file") or ""
    sender    = msg.get("sender") or ""
    title = (f"{file_name} shared by {sender}" if file_name
             else f"Teams file shared by {sender}")

    parts: list = []
    if msg.get("body_text"):
        parts.append(msg["body_text"])
    file_names = [f["name"] for f in msg.get("files") or []]
    if file_names:
        parts.append("Files: " + ", ".join(file_names))

    url = ""
    if file_name:
        hit = next((f for f in msg.get("files") or [] if f["name"] == file_name), None)
        if hit:
            url = hit.get("url") or ""

    # per-file dedup key: same message + same file always → same id
    dedup_key = f"{msg['message_id']}:{file_name}" if file_name else msg["message_id"]

    return {
        "id":     make_id(at[:10], source_name, dedup_key),
        "kind":   "file",
        "at":     at,
        "title":  title,
        "source": source_name,
        "text":   "\n".join(parts),
        "url":    url,
        "meta": {
            "channel":               msg.get("channel_name") or "",
            "message_id":            msg["message_id"],
            "thread_id":             msg["thread_id"],
            "sender":                sender,
            "task_id":               match.get("task_id") or "",
            "match_score":           match.get("score") or 0.0,
            "parent_outside_window": msg.get("parent_outside_window", False),
        },
    }


def _append_rejected(store: str, msg: dict, best_score: float = 0.0,
                     best_task: str = "", reason: str = "") -> None:
    """Append one rejected message to chat_rejected.jsonl.

    Append-only. `reason` is a machine-readable fail-fast string:
    'no_match:score=0.267' or 'ambiguous_score:score=0.350' or
    'no_match:score=0.200,parent_outside_window'.
    """
    rec = {
        "at":                    msg.get("timestamp", ""),
        "message_id":            msg["message_id"],
        "channel":               msg.get("channel_name", ""),
        "sender":                msg.get("sender", ""),
        "body_text":             msg.get("body_text", ""),
        "files":                 [f["name"] for f in msg.get("files") or []],
        "best_score":            round(best_score, 3),
        "best_task":             best_task,
        "reason":                reason or _reject_reason(best_score,
                                     msg.get("parent_outside_window", False)),
        "parent_outside_window": msg.get("parent_outside_window", False),
    }
    with open(_os.path.join(store, "chat_rejected.jsonl"), "a") as fh:
        fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
