#!/usr/bin/env python3
"""Markdown fallback for a taxonomy review (any host, SSH, no browser).

export_md writes one block per actionable item with a `decision:` line and a "Your changes"
section; import_md validates EVERY line first and returns the records to append (nothing is
written when any line is bad). Only lines starting at column 0 with `decision:` / `propose:`
are parsed — the indented examples are not.
"""
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decisions as D  # noqa: E402

HEADER = re.compile(r"^### (\S+) · ")


class MdImportError(ValueError):
    def __init__(self, errors):
        super().__init__("\n".join(errors))
        self.errors = errors


def _summary(item):
    op = item["op"]
    if op["type"] == "add":
        where = f" (under {op['parent']})" if op.get("parent") else ""
        return f"+ {op['level']} **{op['name']}**{where}"
    if op["type"] == "describe":
        return f"~ describe **{op['node']}**"
    where = f" (under {item['parent']})" if item.get("parent") else ""
    return f"{item['level']} **{op['node']}**{where}"


def _decision_text(rec):
    if not rec:
        return ""
    a = rec["action"]
    if a == "reject":
        return f"reject: {rec.get('reason', '')}"
    if a == "amend":
        return "amend: " + json.dumps(rec["op"], ensure_ascii=False)
    return a if a in ("approve", "reopen") else ""


def export_md(review, records):
    st = D.review_state(records, review["review_id"])
    lines = [f"# Taxonomy review {review['review_id']} ({review['mode']})", "",
             "Fill in each `decision:` line; leave it blank to decide later.",
             "Refresh proposals: approve | reject: <reason> | rename: \"New label\" | reparent: \"L1 label\" | reopen",
             "First-build nodes: keep | rename: \"New\" | merge: \"Into\" | move: \"L1\" | split: \"A\" \"B\" | "
             "remove: demote <reason> | remove: entity:<kind> | describe: \"New text\"",
             "Description items: approve | describe: \"New text\" | reject: <reason>", ""]
    if not review.get("evidence_available", True):
        lines += ["> Induction evidence not kept for this Brain; showing tagged chunks instead.", ""]
    for item in review["items"]:
        if item["status"] not in ("proposed", "suppressed"):
            continue
        lines.append(f"### {item['id']} · {_summary(item)}")
        sup = item.get("support") or {}
        facts = [f"{k.replace('_', ' ')}: {sup[k]}" for k in ("chunks", "sources", "tags", "projected_coverage_gain_pts")
                 if sup.get(k) is not None]
        if facts:
            lines.append(" · ".join(facts))
        for f in item.get("flags") or []:
            lines.append(f"⚑ {f['kind']}: {f.get('reason') or ', '.join(f.get('with', []))}")
        if item["status"] == "suppressed":
            lines.append(f"(rejected before: {item['prior'].get('reason')}; write `reopen` to bring it back)")
        if item["op"].get("description"):
            lines.append(f"> proposed: {item['op']['description']}")
        if sup.get("current"):
            lines.append(f"> current: {sup['current']}")
        for e in (item.get("evidence") or [])[:2]:
            if e.get("quote"):
                lines.append(f"> \"{e['quote']}\"" + (f" — {e['source']}" if e.get("source") else ""))
        for s in (sup.get("samples") or [])[:2]:
            lines.append(f"> [{s['chunk_id']}] {s['source']}: {s['preview'][:160]}")
        lines += ["", f"decision: {_decision_text(st['latest'].get(item['id']))}", ""]
    lines += ["## Your changes", "", "Add one line per change, starting at the beginning of the line. Examples:", "",
              '    propose: rename "Old label" -> "New label"',
              '    propose: merge "From" -> "Into"',
              '    propose: move "L2 label" -> "New L1"',
              '    propose: split "Label" -> "Part A" "Part B"',
              '    propose: remove "Label" demote <reason>',
              '    propose: remove "Label" entity:<kind>',
              '    propose: add L1 "Name"',
              '    propose: add L2 "Name" under "Parent"',
              '    propose: add L2 "Name" under "Parent" description="Text"',
              '    propose: describe "Label" "New text."',
              '    propose: metric_edit "Metric" source_type=computable grain=branch',
              '    propose: metric_merge "From" -> "Into"',
              '    propose: metric_remove "Metric" <reason>',
              '    propose: metric_add "Metric" source_type=computable grain=branch', ""]
    return "\n".join(lines) + "\n"


def _arg(tokens, i, what):
    if i >= len(tokens):
        raise ValueError(f"expected {what}")
    return tokens[i]


def _remove_op(node, args):
    disp = _arg(args, 0, "`demote <reason>` or `entity:<kind>`")
    if disp == "demote" or disp.startswith("entity:"):
        return {"type": "remove", "node": node, "disposition": disp, "reason": " ".join(args[1:])}
    raise ValueError("remove needs `demote <reason>` or `entity:<kind>`")


def parse_decision(value, item):
    v = value.strip()
    if not v:
        return None
    word, _, rest = v.partition(":")
    word, rest = word.strip().lower(), rest.strip()
    op, node = item["op"], item["op"].get("node")
    if word in ("approve", "keep"):
        return "approve", None, None
    if word == "reopen":
        return "reopen", None, None
    if word == "reject":
        if not rest:
            raise ValueError("reject needs a reason: `reject: <reason>`")
        return "reject", None, rest
    if word == "amend":
        return "amend", json.loads(rest), None
    args = shlex.split(rest)
    if word == "describe":
        if item["origin"] not in ("describe", "induction"):
            raise ValueError(f"describe decisions need a describe or induction item, not {item['origin']!r}")
        return "amend", {"type": "describe", "node": node, "description": _arg(args, 0, "a description")}, None
    if item["origin"] == "refine":
        if word == "rename":
            return "amend", {**op, "name": _arg(args, 0, "a new label")}, None
        if word == "reparent":
            return "amend", {**op, "parent": _arg(args, 0, "an L1 label")}, None
    else:
        if word == "rename":
            return "amend", {"type": "rename", "node": node, "new_name": _arg(args, 0, "a new label")}, None
        if word == "merge":
            return "amend", {"type": "merge", "from": node, "into": _arg(args, 0, "a target label")}, None
        if word == "move":
            return "amend", {"type": "move", "node": node, "new_parent": _arg(args, 0, "an L1 label")}, None
        if word == "split":
            return "amend", {"type": "split", "node": node, "into": args}, None
        if word == "remove":
            return "amend", _remove_op(node, args), None
    raise ValueError(f"unknown decision {word!r}")


def parse_propose(rest):
    t = shlex.split(rest)
    kind = _arg(t, 0, "an op")
    if kind == "describe":
        return {"type": "describe", "node": _arg(t, 1, "a label"), "description": _arg(t, 2, "a description")}
    if kind == "add":
        level, name = _arg(t, 1, "L1 or L2").upper(), _arg(t, 2, "a name")
        parent, idx = None, 3
        if level == "L2":
            if _arg(t, 3, "`under`") != "under":
                raise ValueError('write: add L2 "Name" under "Parent"')
            parent, idx = _arg(t, 4, "a parent"), 5
        op = {"type": "add", "level": level, "name": name, "parent": parent}
        if idx < len(t) and t[idx].startswith("description="):
            op["description"] = t[idx][len("description="):]
        return op
    if kind in ("rename", "merge", "move", "split", "metric_merge"):
        src = _arg(t, 1, "a label")
        if _arg(t, 2, "->") != "->":
            raise ValueError(f'write: {kind} "A" -> "B"')
        dst = t[3:]
        if not dst:
            raise ValueError("missing target after ->")
        return {"rename": {"type": "rename", "node": src, "new_name": dst[0]},
                "merge": {"type": "merge", "from": src, "into": dst[0]},
                "move": {"type": "move", "node": src, "new_parent": dst[0]},
                "split": {"type": "split", "node": src, "into": dst},
                "metric_merge": {"type": "metric_merge", "from": src, "into": dst[0]}}[kind]
    if kind == "remove":
        return _remove_op(_arg(t, 1, "a label"), t[2:])
    if kind == "metric_remove":
        return {"type": "metric_remove", "metric": _arg(t, 1, "a metric"), "reason": " ".join(t[2:])}
    if kind in ("metric_edit", "metric_add"):
        name, kv = _arg(t, 1, "a metric"), {}
        for tok in t[2:]:
            k, eq, v = tok.partition("=")
            if not eq:
                raise ValueError(f"expected key=value, got {tok!r}")
            kv[k] = v
        if kind == "metric_edit":
            return {"type": "metric_edit", "metric": name, "fields": kv}
        return {"type": "metric_add", "metric": name, **kv}
    raise ValueError(f"unknown op {kind!r}")


def _same(latest, rec):
    return bool(latest) and all(latest.get(k) == rec.get(k) for k in ("action", "op", "reason"))


def import_md(text, review, base_tax, records, reviewer):
    rid = review["review_id"]
    items = {i["id"]: i for i in review["items"]}
    latest = D.review_state(records, rid)["latest"]
    errors, parsed, current = [], [], None
    for n, line in enumerate(text.splitlines(), 1):
        m = HEADER.match(line)
        if m:
            current = items.get(m.group(1))
            if current is None:
                errors.append(f"line {n}: unknown item {m.group(1)}")
            continue
        if line.startswith("## "):
            current = None
            continue
        if line.startswith("decision:") and current is not None:
            try:
                res = parse_decision(line[len("decision:"):], current)
            except (ValueError, json.JSONDecodeError) as e:
                errors.append(f"line {n}: {e}")
                continue
            if res is None:
                continue
            action, op, reason = res
            rec = {"review_id": rid, "item_id": current["id"], "action": action, "reviewer": reviewer,
                   "surface": "markdown"}
            if op is not None:
                rec["op"] = op
            if reason:
                rec["reason"] = reason
            if action in ("reject", "reopen"):
                rec["fingerprint"] = current["fingerprint"]
            if not _same(latest.get(current["id"]), rec):
                parsed.append((n, rec))
        elif line.startswith("propose:"):
            try:
                op = parse_propose(line[len("propose:"):])
            except ValueError as e:
                errors.append(f"line {n}: {e}")
                continue
            parsed.append((n, {"review_id": rid, "item_id": D.new_human_id(), "action": "propose", "op": op,
                               "reviewer": reviewer, "surface": "markdown"}))
    seen, out = list(records), []
    for n, rec in parsed:
        errs = D.validate_record(review, base_tax, seen, rec)
        if errs:
            errors += [f"line {n}: {e}" for e in errs]
            continue
        seen.append(rec)
        out.append(rec)
    if errors:
        raise MdImportError(errors)
    return out
