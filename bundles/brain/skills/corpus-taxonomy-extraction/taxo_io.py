#!/usr/bin/env python3
"""Shared primitives for taxonomy review: ids, fingerprints, structure, files, current.json.

`nid` MUST stay identical to build_graph.nid — graph node ids are slug(label) and every
tag in chunk_topics points at one.
"""
import getpass
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

CURRENT = "current.json"


def nid(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "n"


def norm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fingerprint(op):
    """Identity of an idea across refreshes (used for suppression and item ids)."""
    t = op["type"]
    if t == "add":
        return f"add|{op['level'].lower()}|{norm(op['name'])}|{norm(op.get('parent'))}"
    subject = op.get("node") or op.get("from") or op.get("metric") or ""
    target = op.get("new_name") or op.get("into") or op.get("new_parent") or ""
    if isinstance(target, list):
        target = " ".join(target)
    return f"{t}|{norm(subject)}|{norm(target)}"


def intent(tax):
    """Normalize and return tax['intent_taxonomy'] (every l1 is a tree key; unassigned_l2 exists)."""
    it = tax.setdefault("intent_taxonomy", {})
    tree = it.setdefault("tree", {})
    for l1 in it.get("l1", []):
        tree.setdefault(l1, [])
    it.setdefault("unassigned_l2", [])
    return it


def locate(tax, label):
    it = intent(tax)
    if label in it["tree"]:
        return "L1", None
    for l1, kids in it["tree"].items():
        if label in kids:
            return "L2", l1
    if label in it["unassigned_l2"]:
        return "L2", None
    if label in (tax.get("entities") or {}):
        return "entity", None
    return None, None


def node_ids(tax):
    """The intent + entity-kind node ids build_graph creates from this taxonomy."""
    it = intent(tax)
    ids = set()
    for l1, kids in it["tree"].items():
        ids.add(nid(l1))
        ids |= {nid(k) for k in kids}
    ids |= {nid(x) for x in it["unassigned_l2"]}
    ids |= {nid(k) for k in (tax.get("entities") or {})}
    return ids


def label_taken(tax, name):
    """Existing label or alias `name` collides with (same graph id or same normalized text)."""
    it = intent(tax)
    labels = list(it["tree"]) + [k for kids in it["tree"].values() for k in kids]
    labels += list(it["unassigned_l2"]) + list(tax.get("entities") or {}) + list(tax.get("aliases") or {})
    i, n = nid(name), norm(name)
    for lbl in labels:
        if nid(lbl) == i or norm(lbl) == n:
            return lbl
    return None


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_bytes(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def atomic_write_bytes(path, data):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_version_and_current(tax_dir, tax):
    """Write taxonomy_v<version>.json (must not exist), then current.json with identical bytes."""
    data = dump_bytes(tax)
    out = os.path.join(tax_dir, f"taxonomy_v{tax['version']}.json")
    if os.path.exists(out):
        raise FileExistsError(f"{out} already exists; ratified versions are immutable")
    atomic_write_bytes(out, data)
    atomic_write_bytes(os.path.join(tax_dir, CURRENT), data)
    return out, sha256_bytes(data)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reviewer_name(explicit=None):
    if explicit:
        return explicit
    try:
        r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("USER") or getpass.getuser()
