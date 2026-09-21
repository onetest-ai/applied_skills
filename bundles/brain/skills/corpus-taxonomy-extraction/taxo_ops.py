#!/usr/bin/env python3
"""The taxonomy op set: validate a changeset and apply it as a pure JSON transform.

apply_ops returns the new taxonomy AND the tag migrations build_graph must run, so the
knowledge of which op invalidates which tags lives in exactly one place. Ops are applied
in a fixed type order (adds first) on a deep copy; each op is tried on its own copy so a
failing op never leaves a half-applied state behind.
"""
import copy
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxo_io import intent, label_taken, locate, nid, norm, one_line  # noqa: E402

INTENT_TYPES = ["add", "describe", "rename", "merge", "move", "split", "remove", "tag"]
METRIC_TYPES = ["metric_add", "metric_edit", "metric_merge", "metric_remove", "metric_govern"]
ORDER = {t: i for i, t in enumerate(INTENT_TYPES + METRIC_TYPES)}
KIND = {"L1": "intent_l1", "L2": "intent_l2", "entity": "entity_kind"}
SOURCE_TYPES = {"computable", "stated", "both"}
EDITABLE_METRIC_FIELDS = {"source_type", "grain", "definition"}
_CONSUMES = {"rename", "merge", "remove", "metric_merge", "metric_remove"}


class ChangesetError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def subject_of(op):
    return op.get("node") or op.get("from") or op.get("metric") or op.get("name")


def _target_of(op):
    t = op.get("type")
    if t in ("merge", "metric_merge"):
        return op.get("into")
    if t == "move":
        return op.get("new_parent")
    if t == "add":
        return op.get("parent")
    return None


def _conflicts(ops):
    errs, seen = [], {}
    for op in ops:
        if op.get("type") in ("add", "metric_add", "keep", "tag", "metric_govern"):
            continue
        s = subject_of(op)
        if s in seen:
            errs.append(f"{s!r} is changed twice ({seen[s]} and {op['type']}); withdraw one")
        seen[s] = op.get("type")
    consumed = {subject_of(op): op["type"] for op in ops if op.get("type") in _CONSUMES}
    for op in ops:
        tgt = _target_of(op)
        if tgt and tgt in consumed:
            errs.append(f"{op['type']} targets {tgt!r}, which this review also changes ({consumed[tgt]})")
    retired = {subject_of(op) for op in ops if op.get("type") == "split" and op.get("retire")}
    changed = set(consumed) | retired
    for op in ops:
        if op.get("type") == "tag":
            s = subject_of(op)
            if s in changed:
                errs.append(f"{s!r} is tagged and also changed")
    return errs


# ---- helpers ----

def _need(t, label):
    level, parent = locate(t, label)
    if level is None:
        raise ValueError(f"{label!r} is not in the taxonomy")
    return level, parent


def _new_name(t, name, allow=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("a name is required")
    clash = label_taken(t, name)
    if clash and clash != allow:
        # Allow only if clash is a redirect alias pointing to allow
        aliases = t.get("aliases") or {}
        if not (clash in aliases and aliases[clash] == allow):
            raise ValueError(f"{name!r} collides with existing {clash!r}")
    return name


def _remove_from_tree(it, label, level, parent):
    if level == "L1":
        del it["tree"][label]
    elif parent is None:
        it["unassigned_l2"].remove(label)
    else:
        it["tree"][parent].remove(label)


def _alias(t, old, new):
    al = t.setdefault("aliases", {})
    for k, v in list(al.items()):
        if v == old:
            al[k] = new
    al[old] = new
    al.pop(new, None)


def _demote(t, label):
    d = t.setdefault("demoted", [])
    if not any((x[0] if isinstance(x, list) else x) == label for x in d):
        d.append([label, 0])


def _descs(t):
    return t.setdefault("descriptions", {})


def _clean_desc(text):
    text = one_line(text)
    if not text:
        raise ValueError("a description can't be empty")
    return text


# ---- intent ops ----

def _op_add(t, op, mig, applied):
    it = intent(t)
    name = _new_name(t, op.get("name"))
    if op.get("level") == "L1":
        it["tree"][name] = []
    elif op.get("level") == "L2":
        parent = op.get("parent")
        if parent not in it["tree"]:
            raise ValueError(f"parent {parent!r} is not an L1")
        it["tree"][parent].append(name)
    else:
        raise ValueError("level must be L1 or L2")
    if (op.get("description") or "").strip():
        _descs(t)[name] = one_line(op["description"])


def _op_describe(t, op, mig, applied):
    node = op["node"]
    _need(t, node)
    d = _descs(t)
    applied["previous"] = d.get(node)
    d[node] = _clean_desc(op.get("description"))


def _op_rename(t, op, mig, applied):
    it = intent(t)
    node = op["node"]
    level, parent = _need(t, node)
    new = _new_name(t, op.get("new_name"), allow=node)
    if level == "L1":
        it["tree"] = {(new if k == node else k): v for k, v in it["tree"].items()}
    elif level == "L2":
        lst = it["tree"][parent] if parent else it["unassigned_l2"]
        lst[lst.index(node)] = new
    else:
        t["entities"] = {(new if k == node else k): v for k, v in t["entities"].items()}
    _alias(t, node, new)
    if node in (t.get("descriptions") or {}):
        _descs(t)[new] = _descs(t).pop(node)
    mig.append({"kind": "repoint", "from_id": nid(node), "to_id": nid(new), "label": new, "to_kind": KIND[level]})


def _op_merge(t, op, mig, applied):
    it = intent(t)
    src, dst = op["from"], op["into"]
    if src == dst:
        raise ValueError("cannot merge a node into itself")
    ls, ps = _need(t, src)
    ld, pd = _need(t, dst)
    if ls != ld or ls == "entity":
        raise ValueError(f"merge needs two L1s or two L2s ({src!r} is {ls}, {dst!r} is {ld})")
    if ls == "L1":
        for kid in it["tree"][src]:
            if kid not in it["tree"][dst]:
                it["tree"][dst].append(kid)
        del it["tree"][src]
    else:
        if ps != pd:
            mig.append({"kind": "reclassify_node", "node_id": nid(src),
                        "reason": f"merged into {dst} under a different parent"})
        _remove_from_tree(it, src, "L2", ps)
    d = _descs(t)
    src_desc = d.pop(src, None)
    if src_desc and not d.get(dst):
        d[dst] = src_desc
    _alias(t, src, dst)
    mig.append({"kind": "repoint", "from_id": nid(src), "to_id": nid(dst), "label": dst, "to_kind": KIND[ld]})


def _op_move(t, op, mig, applied):
    it = intent(t)
    node, newp = op["node"], op["new_parent"]
    level, parent = _need(t, node)
    if level != "L2":
        raise ValueError(f"only L2s move ({node!r} is {level})")
    if newp not in it["tree"]:
        raise ValueError(f"new parent {newp!r} is not an L1")
    if newp == parent:
        raise ValueError(f"{node!r} is already under {newp!r}")
    _remove_from_tree(it, node, "L2", parent)
    it["tree"][newp].append(node)
    mig.append({"kind": "reclassify_node", "node_id": nid(node), "reason": f"moved to {newp}"})


def _op_split(t, op, mig, applied):
    it = intent(t)
    node, names = op["node"], list(op.get("into") or [])
    level, parent = _need(t, node)
    if level == "entity":
        raise ValueError("entity kinds cannot be split")
    if len(names) < 2:
        raise ValueError("split needs at least two new names")
    if op.get("retire") and level == "L1":
        raise ValueError("an L1 keeps its place when split; only an L2 can be retired")
    target = it["tree"][node] if level == "L1" else (it["tree"][parent] if parent else it["unassigned_l2"])
    for n in names:
        target.append(_new_name(t, n))
    for n, txt in (op.get("descriptions") or {}).items():
        if n in names and (txt or "").strip():
            _descs(t)[n] = one_line(txt)
    mig.append({"kind": "reclassify_node", "node_id": nid(node), "reason": f"split into {', '.join(names)}"})
    if op.get("retire"):
        _remove_from_tree(it, node, "L2", parent)
        _demote(t, node)
        _descs(t).pop(node, None)
        mig.append({"kind": "delete_node", "node_id": nid(node)})


def _op_remove(t, op, mig, applied):
    it = intent(t)
    node, disp = op["node"], op.get("disposition") or "demote"
    level, parent = _need(t, node)
    if not (disp == "demote" or (disp.startswith("entity:") and len(disp) > len("entity:"))):
        raise ValueError("disposition must be 'demote' or 'entity:<kind>'")
    victims = [node] + (list(it["tree"][node]) if level == "L1" else [])
    for v in victims:
        mig.append({"kind": "reclassify_node", "node_id": nid(v), "reason": f"{node} removed"})
    for v in victims:
        mig.append({"kind": "delete_node", "node_id": nid(v)})
    for v in victims:
        _descs(t).pop(v, None)
    if level == "entity":
        del t["entities"][node]
    else:
        _remove_from_tree(it, node, level, parent)
    if disp == "demote":
        _demote(t, node)
    else:
        cur = t.setdefault("entities", {}).setdefault(disp.split(":", 1)[1], [])
        if isinstance(cur, dict):
            cur[node] = {}
        elif node not in cur:
            cur.append(node)


# ---- metric ops ----

def _metrics(t):
    return t.setdefault("metrics", [])


def _find_metric(t, name):
    for m in _metrics(t):
        if m.get("metric") == name:
            return m
    raise ValueError(f"metric {name!r} is not in the inventory")


def _metric_taken(t, name):
    n = norm(name)
    for m in _metrics(t):
        if norm(m.get("metric")) == n or any(norm(v) == n for v in m.get("variants") or []):
            return m["metric"]
    return None


def _op_metric_add(t, op, mig, applied):
    name = (op.get("metric") or "").strip()
    if not name:
        raise ValueError("a metric name is required")
    clash = _metric_taken(t, name)
    if clash:
        raise ValueError(f"metric {name!r} already exists as {clash!r}")
    if op.get("source_type") not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of {sorted(SOURCE_TYPES)}")
    _metrics(t).append({"metric": name, "source_type": op["source_type"], "grain": op.get("grain"),
                        "n_sources": 0, "sources": [], "stated_values": [], "definition": op.get("definition"),
                        "variants": [name], "avg_confidence": None, "origin": "human"})


def _op_metric_edit(t, op, mig, applied):
    m = _find_metric(t, op["metric"])
    fields = op.get("fields") or {}
    if not fields or set(fields) - EDITABLE_METRIC_FIELDS:
        raise ValueError(f"editable fields are {sorted(EDITABLE_METRIC_FIELDS)}")
    if "source_type" in fields and fields["source_type"] not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of {sorted(SOURCE_TYPES)}")
    applied["previous"] = {k: m.get(k) for k in fields}
    m.update(fields)


def _op_metric_merge(t, op, mig, applied):
    src, dst = _find_metric(t, op["from"]), _find_metric(t, op["into"])
    if src is dst:
        raise ValueError("cannot merge a metric into itself")
    for key in ("variants", "sources", "stated_values"):
        extra = {src["metric"]} if key == "variants" else set()
        dst[key] = sorted(set(dst.get(key) or []) | set(src.get(key) or []) | extra)
    dst["n_sources"] = len(dst["sources"])
    _metrics(t).remove(src)


def _op_metric_remove(t, op, mig, applied):
    if not (op.get("reason") or "").strip():
        raise ValueError("removing a metric needs a reason")
    _metrics(t).remove(_find_metric(t, op["metric"]))
    _demote(t, op["metric"])


def _op_tag(t, op, mig, applied):
    node = op.get("node")
    level, parent = _need(t, node)
    if level == "entity":
        raise ValueError(f"{node!r} is an entity, not an intent node — only L1/L2 nodes can be tagged")
    ids = op.get("chunk_ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError("chunk_ids must be a non-empty list")
    seen, out = set(), []
    for i in ids:
        if not isinstance(i, int) or isinstance(i, bool):
            raise ValueError("chunk_ids must be ints")
        if i not in seen:
            seen.add(i)
            out.append(i)
    applied["chunk_ids"] = out


def _op_metric_govern(t, op, mig, applied):
    _find_metric(t, op.get("metric"))
    draft = op.get("draft") or {}
    key = draft.get("key") or ""
    if not re.fullmatch(r"[a-z0-9_]+", key):
        raise ValueError("draft.key is required and must match [a-z0-9_]+")


_OPS = {"add": _op_add, "describe": _op_describe, "rename": _op_rename, "merge": _op_merge, "move": _op_move,
        "split": _op_split, "remove": _op_remove, "tag": _op_tag, "metric_add": _op_metric_add,
        "metric_edit": _op_metric_edit, "metric_merge": _op_metric_merge, "metric_remove": _op_metric_remove,
        "metric_govern": _op_metric_govern}


def _run(tax, ops):
    t = copy.deepcopy(tax)
    intent(t)
    t["intent_taxonomy"].pop("l1", None)  # remove stale l1 list so intent() won't re-add removed L1s during ops
    errors, migrations, applied_ops = _conflicts(ops), [], []
    # fixed type order; within adds, L1 before L2 so an L2 can hang under an L1 added here
    key = lambda o: (ORDER.get(o.get("type"), 99), 0 if o.get("level") == "L1" else 1)
    for op in sorted(ops, key=key):
        if op.get("type") == "keep":
            continue
        fn = _OPS.get(op.get("type"))
        if fn is None:
            errors.append(f"unknown op type {op.get('type')!r}")
            continue
        trial, mig, applied = copy.deepcopy(t), [], copy.deepcopy(op)
        try:
            fn(trial, op, mig, applied)
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"{op['type']} {subject_of(op)!r}: {e}")
            continue
        t = trial
        migrations += mig
        applied_ops.append(applied)
    it = t["intent_taxonomy"]
    it["l1"] = list(it["tree"].keys())
    it.setdefault("unassigned_l2", [])
    if not t.get("descriptions"):
        t.pop("descriptions", None)
    return t, migrations, applied_ops, errors


def validate(tax, ops):
    return _run(tax, ops)[3]


def apply_ops(tax, ops):
    t, migrations, applied_ops, errors = _run(tax, ops)
    if errors:
        raise ChangesetError(errors)
    return t, migrations, applied_ops
