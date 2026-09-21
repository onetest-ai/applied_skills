#!/usr/bin/env python3
"""Taxonomy health: deterministic problem detection + agent task preparation.

`diagnose` is read-only against the store (opened via `taxonomy_review._ro`) and never
writes the taxonomy. It writes `out_dir/problems.json` (the detections, grouped by kind)
and, for every kind that needs a judgment call, an agent task directory holding
`instructions.md` plus `batch_k.json` files. A later `plan --mode health` (a different
task) turns `problems.json` + the agents' `result_k.json` files into review items.

Stdlib only. Sibling imports: taxonomy_review, flags, metrics_gap, taxo_io, graph_migrate,
and (for the `untagged/` task) taxonomy_refine_prep, invoked via `sys.executable` so its
batching logic is never duplicated.
"""
import glob
import itertools
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flags  # noqa: E402
import graph_migrate as GM  # noqa: E402
import metrics_gap as MG  # noqa: E402
import taxonomy_review as R  # noqa: E402
from difflib import SequenceMatcher  # noqa: E402
from taxo_io import CURRENT, atomic_write_bytes, intent, load_json, nid, norm  # noqa: E402

SIMILAR_METRIC_THRESHOLD = 0.85

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "are", "was",
    "were", "not", "but", "you", "your", "our", "their", "about", "into", "then", "than",
    "also", "more", "some", "such", "any", "all", "can", "will", "just", "only", "over",
    "under", "each", "when", "what", "how", "who", "which", "these", "those", "its",
}


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------

def _rows(it):
    """(label, level, parent) for every intent L1/L2, matching describe_prep's walk."""
    rows = []
    for l1, kids in it["tree"].items():
        rows.append((l1, "L1", None))
        rows += [(k, "L2", l1) for k in kids]
    rows += [(x, "L2", None) for x in it["unassigned_l2"]]
    return rows


def _siblings_of(it, label, level, parent):
    if level == "L1":
        return [x for x in it["tree"] if x != label]
    pool = it["tree"].get(parent, []) if parent else it["unassigned_l2"]
    return [x for x in pool if x != label]


def _tokens(label, description):
    text = f"{label} {description or ''}".lower()
    words = re.findall(r"[a-z0-9]{3,}", text)
    out = []
    seen = set()
    for w in words:
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _fts_candidates(c, label, description, exclude_id, limit=20):
    if not c or not GM.has_table(c, "chunks_fts"):
        return []
    tokens = _tokens(label, description)
    if not tokens:
        return []
    query = " OR ".join(tokens)
    try:
        rows = c.execute(
            "SELECT c2.id, c2.source, c2.title, substr(c2.text,1,400) FROM chunks_fts f "
            "JOIN chunks c2 ON c2.id = f.rowid "
            "WHERE chunks_fts MATCH ? AND NOT EXISTS "
            "(SELECT 1 FROM chunk_topics t WHERE t.chunk_id = c2.id AND t.category_id = ?) "
            "ORDER BY bm25(chunks_fts) LIMIT ?", (query, exclude_id, limit)).fetchall()
    except Exception:
        return []
    return [R._brief(r) for r in rows]


def _overlap(c, a_id, b_id):
    if not c or not GM.has_table(c, "chunk_topics"):
        return 0
    return c.execute(
        "SELECT COUNT(DISTINCT a.chunk_id) FROM chunk_topics a JOIN chunk_topics b "
        "ON a.chunk_id = b.chunk_id WHERE a.category_id = ? AND b.category_id = ?",
        (a_id, b_id)).fetchone()[0]


def _metric_names(m):
    names = [m.get("metric")] + list(m.get("variants") or [])
    return [n for n in names if n]


def _similar_metrics(metrics):
    out = []
    for a, b in itertools.combinations(metrics, 2):
        if norm(a.get("metric")) == norm(b.get("metric")):
            continue
        best = 0.0
        for na in _metric_names(a):
            for nb in _metric_names(b):
                na_n, nb_n = norm(na), norm(nb)
                if not na_n or not nb_n:
                    continue
                s = 1.0 if na_n == nb_n else SequenceMatcher(None, na_n, nb_n).ratio()
                best = max(best, s)
        if best >= SIMILAR_METRIC_THRESHOLD:
            out.append({"a": a.get("metric"), "b": b.get("metric"), "score": round(best, 2)})
    return out


def _open_requests(tax_dir):
    path = os.path.join(tax_dir, "work", "requests.jsonl")
    if not os.path.exists(path):
        return []
    latest = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                latest[rec["id"]] = rec
    return [{"request_id": rec["id"], "item_fingerprint": rec.get("item_id"), "note": rec.get("note")}
            for rec in latest.values() if rec.get("status") == "open"]


def _find_families(tax_dir):
    project = os.path.dirname(os.path.abspath(tax_dir))
    hits = [h for h in sorted(glob.glob(os.path.join(project, "schema", "families.*.json")))
            if not h.endswith("families.example.json")]
    return hits[0] if len(hits) == 1 else None


def detect(taxonomy_path, db, metrics_path=None):
    """Return (problems dict, tax, tax_dir, ro connection-or-None). Caller closes the connection."""
    tax = load_json(taxonomy_path)
    tax_dir = os.path.dirname(os.path.abspath(taxonomy_path))
    c = R._ro(db)
    counts = R._counts(c)
    it = intent(tax)
    descs = tax.get("descriptions") or {}
    rows = _rows(it)

    problems = {"missing_description": [], "no_tags": [], "untagged_sections": [],
                "near_duplicate": [], "off_axis": [], "similar_metrics": [],
                "metric_not_governed": [], "open_requests": []}

    for label, level, parent in rows:
        if not (descs.get(label) or "").strip():
            problems["missing_description"].append({"node": label, "level": level, "parent": parent})

    for label, level, parent in rows:
        if counts.get(nid(label), 0) == 0:
            candidates = _fts_candidates(c, label, descs.get(label), nid(label))
            problems["no_tags"].append({"node": label, "level": level, "parent": parent,
                                        "candidates": candidates})

    if c and GM.has_table(c, "chunks"):
        total = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        tagged = (c.execute("SELECT COUNT(DISTINCT chunk_id) FROM chunk_topics").fetchone()[0]
                  if GM.has_table(c, "chunk_topics") else 0)
        problems["untagged_sections"].append({"count": total - tagged, "total": total})

    l1s = list(it["tree"])
    for group in flags.near_duplicate_labels(l1s):
        for a, b in itertools.combinations(group, 2):
            problems["near_duplicate"].append({
                "a": a, "b": b, "level": "L1", "parent": None,
                "tags_a": counts.get(nid(a), 0), "tags_b": counts.get(nid(b), 0),
                "overlap": _overlap(c, nid(a), nid(b))})
    for l1, kids in it["tree"].items():
        for group in flags.near_duplicate_labels(kids):
            for a, b in itertools.combinations(group, 2):
                problems["near_duplicate"].append({
                    "a": a, "b": b, "level": "L2", "parent": l1,
                    "tags_a": counts.get(nid(a), 0), "tags_b": counts.get(nid(b), 0),
                    "overlap": _overlap(c, nid(a), nid(b))})

    for l1 in l1s:
        reason = flags.off_axis_l1(l1)
        if reason:
            problems["off_axis"].append({"node": l1, "reason": reason, "tags": counts.get(nid(l1), 0)})

    metrics = tax.get("metrics") or []
    problems["similar_metrics"] = _similar_metrics(metrics)

    governed_path = metrics_path or MG.find_governed(tax_dir)
    if governed_path:
        governed = MG.load_governed(governed_path)
        for m in MG.annotate(metrics, governed):
            if m.get("source_type") in ("computable", "both") and m["governed"]["status"] == "no":
                problems["metric_not_governed"].append({
                    "metric": m.get("metric"), "grain": m.get("grain"),
                    "definition": m.get("definition"), "sources": m.get("sources") or []})

    problems["open_requests"] = _open_requests(tax_dir)

    return problems, tax, tax_dir, c


# ---------------------------------------------------------------------------
# instructions.md templates
# ---------------------------------------------------------------------------

NOTAGS_INSTRUCTIONS = """# Fixing categories with no tags

Each `batch_k.json` next to this file is a list of entries, one per category that has
zero tagged chunks. For each entry, write a `result_k.json` with the same `k` (one result
file per batch), shaped:

```json
{"fixes": [{"node": "<label>", "fix": "tag", "chunk_ids": [123, 456],
            "reason": "these sections describe splitting a bill into installments"}]}
```

Each entry gives you `node`, `level`, `parent`, `siblings` (existing sibling categories,
with their descriptions where known) and `candidates` (up to 20 chunks found by full-text
search on the node's label and description, each with `chunk_id`, `source`, `title`,
`preview`).

For each entry, decide ONE fix:
- `"tag"` — some candidates genuinely belong to this category. Set `chunk_ids` to the ids
  of the candidates that fit (a subset of the given candidates only).
- `"merge"` — the category is real but redundant with a sibling. Set `into` to the
  sibling's exact label.
- `"remove"` — the category doesn't belong (too narrow, mis-scoped, or not a real
  recurring topic). No `chunk_ids` or `into` needed.

Rules:
- Be specific: cite which candidates support your fix and why, in `reason`.
- Prefer `"tag"` with a conservative subset over `"merge"` or `"remove"` when the
  candidates are a reasonable but imperfect fit; when truly nothing fits and none of the
  candidates apply, use `"remove"` only if the category also looks bogus, otherwise leave
  it as `"tag"` with an empty `chunk_ids` list and a reason explaining that no candidate
  fits — a human will decide.
- One fix per entry.
- If an entry has a `note`, the reviewer rejected the earlier proposal for this reason;
  propose something that addresses it — do not repeat the rejected proposal.
"""

STRUCTURE_INSTRUCTIONS = """# Fixing near-duplicate and off-axis categories

Each `batch_k.json` next to this file is a list of entries: some are `near_duplicate`
pairs, some are `off_axis` candidates. For each `batch_k.json` you process, write a
`result_k.json` with the same `k`, shaped:

```json
{"fixes": [
  {"kind": "near_duplicate", "subject": "Billing & Payments Admin", "fix": "merge",
   "into": "Billing & Payments", "reason": "same topic; Admin has 2 tags, the other has 15"},
  {"kind": "off_axis", "subject": "Transform", "fix": "remove",
   "disposition": "demote", "reason": "roadmap phase, not a customer intent"}
]}
```

Each `near_duplicate` entry gives you `a`, `b`, `level`, `parent`, `tags_a`, `tags_b`,
`overlap` (chunks tagged with both) and sample chunks for each side. Each `off_axis`
entry gives you `node`, `reason`, `tags` and sample chunks.

For each entry, decide ONE fix:
- `near_duplicate` → `"merge"` (name the surviving label in `into`, usually the one with
  more tags or the clearer name) or `"keep"` (they are genuinely distinct; explain why in
  `reason`).
- `off_axis` → `"remove"` (set `disposition` to `"demote"` if it should become an entity
  or metadata dimension rather than an intent, or `"delete"` if it has no place at all) or
  `"keep"` (it is a legitimate intent despite the heuristic; explain why).

Rules:
- Be specific: cite the sample chunks and the tag counts/overlap that justify your call.
- Prefer `"keep"` when unsure.
- One fix per entry (`subject` is `a` for a near_duplicate pair — the label proposed to
  be merged away — or the `node` for an off_axis entry).
- If an entry has a `note`, the reviewer rejected the earlier proposal for this reason;
  propose something that addresses it.
"""

METRICS_INSTRUCTIONS = """# Fixing similar and ungoverned metrics

Each `batch_k.json` next to this file is a list of entries: some are `similar_metrics`
pairs, some are `metric_not_governed` entries. `families.json` (if present, next to this
file) lists the governed metric families available for a new draft. For each
`batch_k.json` you process, write a `result_k.json` with the same `k`, shaped:

```json
{"fixes": [
  {"kind": "similar_metrics", "subject": "Avg Handle Time", "fix": "metric_merge",
   "into": "Average Handle Time", "reason": "same metric, one is stated-only and a variant"},
  {"kind": "metric_not_governed", "subject": "Porch Rate", "fix": "metric_govern",
   "draft": {"key": "porch_rate", "family": "delivery", "unit": "pct",
             "desc": "share of deliveries left at the porch", "grain": "branch"},
   "reason": "computable, seen in one source, no governed equivalent"}
]}
```

For each entry, decide ONE fix:
- `similar_metrics` → `"metric_merge"` (name the canonical metric in `into`) or `"keep"`
  (they measure genuinely different things; explain why).
- `metric_not_governed` → `"metric_govern"` (draft `{key, family, unit, desc, grain}` —
  prefer a family from `families.json` when one fits) or `"keep"` (not worth governing
  yet; explain why).

Rules:
- Be specific: cite the metric's `definition`, `grain` and `sources` for your reasoning.
- Prefer `"keep"` when unsure — a wrong governed draft is worse than no draft.
- One fix per entry (`subject` is the metric being folded away, for `similar_metrics`;
  the ungoverned metric's name, for `metric_not_governed`).
- If an entry has a `note`, the reviewer rejected the earlier proposal for this reason;
  propose something that addresses it.
"""


def _split(entries, batches):
    n = len(entries)
    if not n:
        return []
    b = max(1, min(batches, n))
    size = -(-n // b)  # ceil
    return [entries[i:i + size] for i in range(0, n, size)]


def _write_batches(out_dir, entries, batches, instructions):
    os.makedirs(out_dir, exist_ok=True)
    groups = _split(entries, batches)
    for k, group in enumerate(groups):
        atomic_write_bytes(os.path.join(out_dir, f"batch_{k}.json"),
                           (json.dumps(group, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    atomic_write_bytes(os.path.join(out_dir, "instructions.md"), instructions.encode("utf-8"))
    return len(groups)


def _note_map(problems):
    return {p["item_fingerprint"]: p["note"] for p in problems["open_requests"]
            if p.get("item_fingerprint") and p.get("note")}


def _prepare_notags(tax, tax_dir, problems, out_dir, batches):
    entries = problems["no_tags"]
    if not entries:
        return None
    it = intent(tax)
    descs = tax.get("descriptions") or {}
    notes = _note_map(problems)
    out = []
    for p in entries:
        sibs = _siblings_of(it, p["node"], p["level"], p["parent"])
        entry = {"node": p["node"], "level": p["level"], "parent": p["parent"],
                 "siblings": [{"node": s, "description": descs.get(s)} for s in sibs],
                 "candidates": p["candidates"]}
        note = notes.get(p["node"])
        if note:
            entry["note"] = note
        out.append(entry)
    task_dir = os.path.join(out_dir, "notags")
    n = _write_batches(task_dir, out, batches, NOTAGS_INSTRUCTIONS)
    return {"kind": "notags", "dir": task_dir, "batches": n}


def _prepare_structure(tax, tax_dir, problems, out_dir, c, batches):
    entries = problems["near_duplicate"] + problems["off_axis"]
    if not entries:
        return None
    notes = _note_map(problems)
    out = []
    for p in problems["near_duplicate"]:
        entry = dict(p, kind="near_duplicate", samples_a=R._samples(c, nid(p["a"])),
                     samples_b=R._samples(c, nid(p["b"])))
        note = notes.get(p["a"])
        if note:
            entry["note"] = note
        out.append(entry)
    for p in problems["off_axis"]:
        entry = dict(p, kind="off_axis", samples=R._samples(c, nid(p["node"])))
        note = notes.get(p["node"])
        if note:
            entry["note"] = note
        out.append(entry)
    task_dir = os.path.join(out_dir, "structure")
    n = _write_batches(task_dir, out, batches, STRUCTURE_INSTRUCTIONS)
    return {"kind": "structure", "dir": task_dir, "batches": n}


def _prepare_metrics(tax, tax_dir, problems, out_dir, batches):
    entries = problems["similar_metrics"] + problems["metric_not_governed"]
    if not entries:
        return None
    notes = _note_map(problems)
    out = []
    for p in problems["similar_metrics"]:
        entry = dict(p, kind="similar_metrics")
        note = notes.get(p["a"])
        if note:
            entry["note"] = note
        out.append(entry)
    for p in problems["metric_not_governed"]:
        entry = dict(p, kind="metric_not_governed")
        note = notes.get(p["metric"])
        if note:
            entry["note"] = note
        out.append(entry)
    task_dir = os.path.join(out_dir, "metrics")
    os.makedirs(task_dir, exist_ok=True)
    families_path = _find_families(tax_dir)
    if families_path:
        atomic_write_bytes(os.path.join(task_dir, "families.json"), open(families_path, "rb").read())
    n = _write_batches(task_dir, out, batches, METRICS_INSTRUCTIONS)
    return {"kind": "metrics", "dir": task_dir, "batches": n}


def _prepare_describe(tax, taxonomy_path, db, problems, out_dir, batches):
    if not problems["missing_description"]:
        return None
    task_dir = os.path.join(out_dir, "describe")
    res = R.describe_prep(taxonomy_path, db, task_dir, batches=batches)
    if not res["batches"]:
        return None
    return {"kind": "describe", "dir": task_dir, "batches": res["batches"]}


def _prepare_untagged(tax_dir, db, taxonomy_path, problems, out_dir, batches):
    total_untagged = problems["untagged_sections"][0]["count"] if problems["untagged_sections"] else 0
    if not total_untagged:
        return None
    task_dir = os.path.join(out_dir, "untagged")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taxonomy_refine_prep.py")
    subprocess.run([sys.executable, script, "--db", db, "--taxonomy", taxonomy_path,
                    "--out", task_dir, "--batches", str(batches)],
                   check=False, capture_output=True, text=True)
    n = len(glob.glob(os.path.join(task_dir, "batch_*.json")))
    if not n:
        return None
    return {"kind": "untagged", "dir": task_dir, "batches": n}


def diagnose(taxonomy_path, db, out_dir, metrics_path=None, proposals_dir=None, batches=4):
    if os.path.basename(taxonomy_path) != CURRENT:
        raise ValueError(f"diagnose is planned on taxonomy/{CURRENT}, not {taxonomy_path}")
    problems, tax, tax_dir, c = detect(taxonomy_path, db, metrics_path)
    os.makedirs(out_dir, exist_ok=True)
    atomic_write_bytes(os.path.join(out_dir, "problems.json"),
                       (json.dumps(problems, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))

    tasks = []
    for builder in (
        lambda: _prepare_describe(tax, taxonomy_path, db, problems, out_dir, batches),
        lambda: _prepare_notags(tax, tax_dir, problems, out_dir, batches),
        lambda: _prepare_structure(tax, tax_dir, problems, out_dir, c, batches),
        lambda: _prepare_metrics(tax, tax_dir, problems, out_dir, batches),
        lambda: _prepare_untagged(tax_dir, db, taxonomy_path, problems, out_dir, batches),
    ):
        t = builder()
        if t:
            tasks.append(t)

    if c:
        c.close()

    return {"problems": {k: len(v) for k, v in problems.items()}, "tasks": tasks, "out": out_dir}
