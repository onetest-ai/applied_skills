#!/usr/bin/env python3
"""The append-only decisions log (taxonomy/decisions.jsonl) and everything derived from it.

A review is a changeset. Its items are frozen in review_<id>.json; what people decided —
approve / reject / amend plan items, propose / withdraw their own ops, submit — is appended
here. Nothing ever rewrites this file.
"""
import difflib
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxo_io import utc_now  # noqa: E402
from taxo_ops import subject_of, validate as validate_ops  # noqa: E402

SCHEMA = 1
ITEM_ACTIONS = {"approve", "reject", "amend", "propose", "withdraw", "reopen", "clear"}
REVIEW_ACTIONS = {"submit", "applied"}
HUMAN_SURFACES = {"browser", "markdown"}
AGENT_ALLOWED = {"add", "keep", "describe", "tag", "metric_govern"}


class DecisionLogError(ValueError):
    pass


def default_path(tax_dir):
    return os.path.join(tax_dir, "decisions.jsonl")


def new_human_id():
    return "h-" + uuid.uuid4().hex[:6]


def append(path, record):
    rec = {"schema": SCHEMA, "ts": utc_now(), **record}
    action = rec.get("action")
    if action not in ITEM_ACTIONS | REVIEW_ACTIONS:
        raise DecisionLogError(f"unknown action {action!r}")
    if action in ITEM_ACTIONS and not rec.get("item_id"):
        raise DecisionLogError(f"{action} needs an item_id")
    if action == "reject" and not (rec.get("reason") or "").strip():
        raise DecisionLogError("reject needs a reason")
    if action in ("propose", "amend") and not isinstance(rec.get("op"), dict):
        raise DecisionLogError(f"{action} needs an op")
    line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return rec


def read(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise DecisionLogError(f"{path}:{n}: malformed line ({e.msg})") from None
            if not isinstance(rec, dict) or "action" not in rec:
                raise DecisionLogError(f"{path}:{n}: not a decision record")
            out.append(rec)
    return out


def review_state(records, review_id):
    latest, proposals, submit, applied = {}, {}, None, None
    for r in records:
        if r.get("review_id") != review_id:
            continue
        a = r["action"]
        if a == "submit":
            submit = r
        elif a == "applied":
            applied = r
        else:
            latest[r["item_id"]] = r
            if a == "propose":
                proposals[r["item_id"]] = r
            elif a == "withdraw":
                proposals.pop(r["item_id"], None)
    return {"latest": latest, "proposals": proposals, "submit": submit, "applied": applied}


def effective_ops(review, records):
    """The ops a submitted review applies, in item order, then human proposals."""
    st = review_state(records, review["review_id"])
    out = []
    for item in review["items"]:
        if item.get("status") not in ("proposed", "suppressed"):
            continue
        d = st["latest"].get(item["id"])
        if d is None or d["action"] not in ("approve", "amend"):
            continue
        op = item["op"] if d["action"] == "approve" else d["op"]
        if op.get("type") == "keep":
            continue
        out.append({"item_id": item["id"], "op": op, "origin": item["origin"], "surface": d.get("surface")})
    for iid, p in st["proposals"].items():
        out.append({"item_id": iid, "op": p["op"], "origin": "human", "surface": p.get("surface")})
    return out


def authorship_errors(entries):
    return [f"{e['item_id']}: {e['op'].get('type')} must be entered in the review app or an imported "
            f"Markdown file (came from {e.get('surface') or 'unknown'})"
            for e in entries
            if e["op"].get("type") not in AGENT_ALLOWED and e.get("surface") not in HUMAN_SURFACES]


def standing_rejections(records):
    """Rejections in force, keyed by fingerprint.

    A reject stands until a reopen of the same fingerprint (in any review) lifts it. A `clear`
    (the app's Undo) reverts its item's own reject or reopen when that was the item's last
    action: a cleared reject no longer stands, and a cleared reopen restores what it lifted.
    `clear` records carry no fingerprint, so they are matched by (review_id, item_id).
    """
    out, lifted, last, undo = {}, {}, {}, {}
    for r in records:
        a, key = r.get("action"), (r.get("review_id"), r.get("item_id"))
        fp = r.get("fingerprint")
        if a == "clear":
            u = undo.pop(key, None)
            if u and last.get(key) in ("reject", "reopen"):
                kind, ufp, rec, before = u
                if kind == "reject" and out.get(ufp) is rec:
                    if before is None:
                        out.pop(ufp, None)
                    else:
                        out[ufp] = before
                elif kind == "reopen" and before is not None and ufp not in out:
                    out[ufp] = before
        elif a == "reject" and fp:
            undo[key] = ("reject", fp, r, out.get(fp))
            out[fp] = r
        elif a == "reopen" and fp:
            rec = out.pop(fp, None) or lifted.get(fp)
            lifted[fp] = rec
            undo[key] = ("reopen", fp, r, rec)
        elif a in ITEM_ACTIONS:
            undo.pop(key, None)
        if a in ITEM_ACTIONS:
            last[key] = a
    return out


def match_rejection(fp, rejections, fuzzy=0.88):
    if fp in rejections:
        return rejections[fp]
    t, lvl, name, parent = (fp.split("|") + ["", "", "", ""])[:4]
    for rfp, rec in rejections.items():
        rt, rl, rn, rp = (rfp.split("|") + ["", "", "", ""])[:4]
        if (rt, rl, rp) == (t, lvl, parent) and difflib.SequenceMatcher(None, name, rn).ratio() >= fuzzy:
            return rec
    return None


def _item(review, item_id):
    return next((i for i in review["items"] if i["id"] == item_id), None)


def validate_record(review, base_tax, records, rec):
    """Errors that make `rec` unacceptable given the review so far ([] = ok)."""
    st = review_state(records, review["review_id"])
    if st["submit"]:
        return ["this review is already submitted"]
    a, iid = rec.get("action"), rec.get("item_id")
    if a == "reject" and not (rec.get("reason") or "").strip():
        return [f"{iid}: reject needs a reason"]
    op0 = rec.get("op") or {}
    if (a in ("propose", "amend") and rec.get("surface") == "browser" and op0.get("type") == "add"
            and not (op0.get("description") or "").strip()):
        return [f"{iid}: a new category needs a description"]
    if a == "propose":
        if not (iid or "").startswith("h-") or iid in st["latest"]:
            return [f"{iid}: a proposal needs a fresh h- id"]
    elif a == "withdraw":
        if iid not in st["proposals"]:
            return [f"{iid}: no open proposal to withdraw"]
    elif a != "submit":
        item = _item(review, iid)
        if item is None:
            return [f"{iid}: not an item of this review"]
        if item["status"] in ("auto_skipped", "invalid"):
            return [f"{iid}: {item['status']} items take no decision"]
        if item["origin"] == "induction" and a not in ("approve", "amend", "clear"):
            return [f"{iid}: first-build items take approve (keep) or amend, not {a}"]
        if a == "clear":
            last = st["latest"].get(iid)
            if not last or last["action"] == "clear":
                return [f"{iid}: nothing to undo"]
        if a == "reopen" and item["status"] != "suppressed":
            return [f"{iid}: only suppressed items can be reopened"]
        if item["status"] == "suppressed" and a in ("approve", "amend"):
            last = st["latest"].get(iid)
            if not last or last["action"] not in ("reopen", "approve", "amend"):
                return [f"{iid}: reopen this suppressed item before approving it"]
        if a == "amend":
            op = rec.get("op") or {}
            if item["origin"] == "refine" and op.get("type") != "add":
                return [f"{iid}: a refresh proposal can only be amended into another add"]
            if item["origin"] == "induction" and op.get("type") != "add" and subject_of(op) != item["op"]["node"]:
                return [f"{iid}: the amended op must act on {item['op']['node']!r}"]
            if item["origin"] == "describe" and (op.get("type") != "describe" or op.get("node") != item["op"]["node"]):
                return [f"{iid}: a description proposal can only be amended into another description of {item['op']['node']!r}"]
            if item["origin"] == "health":
                alts = item.get("alternatives") or []
                same_fix = op.get("type") == item["op"].get("type") and subject_of(op) == subject_of(item["op"])
                if op not in alts and not same_fix:
                    return [f"{iid}: a health fix can only be amended into one of its alternatives or an edit of the same fix"]
    entries = effective_ops(review, records + ([] if a == "submit" else [rec]))
    return authorship_errors(entries) + validate_ops(base_tax, [e["op"] for e in entries])


def submit_counts(review, records):
    st = review_state(records, review["review_id"])
    actionable = [i for i in review["items"] if i.get("status") in ("proposed", "suppressed")]
    acts = [st["latest"].get(i["id"], {}).get("action") for i in actionable]
    return {"approved": acts.count("approve"), "rejected": acts.count("reject"), "amended": acts.count("amend"),
            "undecided": sum(1 for x in acts if x in (None, "clear")), "proposals": len(st["proposals"])}
