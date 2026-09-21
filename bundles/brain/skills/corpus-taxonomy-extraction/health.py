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
import copy
import glob
import itertools
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decisions as D  # noqa: E402
import flags  # noqa: E402
import graph_migrate as GM  # noqa: E402
import metrics_gap as MG  # noqa: E402
import taxo_ops  # noqa: E402
import taxonomy_review as R  # noqa: E402
from difflib import SequenceMatcher  # noqa: E402
from taxo_io import CURRENT, atomic_write_bytes, fingerprint, intent, load_json, nid, norm, one_line  # noqa: E402
from taxonomy_merge import plan_additions  # noqa: E402

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


def _read_open_requests(tax_dir):
    """Latest {id: record} in taxonomy/work/requests.jsonl, kept when status == 'open'."""
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
    return [rec for rec in latest.values() if rec.get("status") == "open"]


def _item_subjects(item):
    """Every label a health item's note could plausibly be "about" — the op's own subject
    plus, for a merge/keep on a near-duplicate pair (or a metric merge), the other side."""
    op = item.get("op") or {}
    subjects = set()
    s = taxo_ops.subject_of(op)
    if s:
        subjects.add(s)
    if op.get("type") in ("merge", "metric_merge", "keep"):
        into = op.get("into")
        if into:
            subjects.add(into)
    return subjects


def _open_requests(tax_dir):
    """Resolve each open request's opaque `item_id` against its review file.

    Returns (records, note_map): `records` is the public shape written to problems.json
    ({request_id, review_id, item_id, subject, kind, fingerprint, note}); `note_map` maps
    every subject label the resolved item could be "about" to its note, for attaching
    notes to freshly detected problems of the same subject.
    """
    records, note_map, review_cache = [], {}, {}
    for rec in _read_open_requests(tax_dir):
        review_id, item_id = rec.get("review_id"), rec.get("item_id")
        if review_id not in review_cache:
            review_path = os.path.join(tax_dir, "reviews", f"review_{review_id}.json")
            try:
                review_cache[review_id] = load_json(review_path)
            except (OSError, ValueError) as e:
                print(f"diagnose: open request {rec.get('id')!r} skipped — cannot load {review_path}: {e}",
                      file=sys.stderr)
                review_cache[review_id] = None
        review = review_cache[review_id]
        if not review:
            continue
        item = next((i for i in (review.get("items") or []) if i.get("id") == item_id), None)
        if not item:
            print(f"diagnose: open request {rec.get('id')!r} skipped — item {item_id!r} not found in "
                  f"review {review_id!r}", file=sys.stderr)
            continue
        op = item.get("op") or {}
        subject = taxo_ops.subject_of(op)
        records.append({"request_id": rec.get("id"), "review_id": review_id, "item_id": item_id,
                        "subject": subject, "kind": item.get("kind"), "fingerprint": item.get("fingerprint"),
                        "note": rec.get("note")})
        note = rec.get("note")
        if note:
            for subj in _item_subjects(item):
                note_map[subj] = note
    return records, note_map


def _find_families(tax_dir):
    project = os.path.dirname(os.path.abspath(tax_dir))
    hits = [h for h in sorted(glob.glob(os.path.join(project, "schema", "families.*.json")))
            if not h.endswith("families.example.json")]
    return hits[0] if len(hits) == 1 else None


def detect(taxonomy_path, db, metrics_path=None):
    """Return (problems dict, tax, tax_dir, ro connection-or-None, note_map). Caller closes the connection."""
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

    open_requests, note_map = _open_requests(tax_dir)
    problems["open_requests"] = open_requests

    return problems, tax, tax_dir, c, note_map


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


def _prepare_notags(tax, tax_dir, problems, out_dir, notes, batches):
    entries = problems["no_tags"]
    if not entries:
        return None
    it = intent(tax)
    descs = tax.get("descriptions") or {}
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


def _prepare_structure(tax, tax_dir, problems, out_dir, c, notes, batches):
    entries = problems["near_duplicate"] + problems["off_axis"]
    if not entries:
        return None
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


def _prepare_metrics(tax, tax_dir, problems, out_dir, notes, batches):
    entries = problems["similar_metrics"] + problems["metric_not_governed"]
    if not entries:
        return None
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


def _prepare_describe(tax, taxonomy_path, db, problems, out_dir, notes, batches):
    if not problems["missing_description"]:
        return None
    task_dir = os.path.join(out_dir, "describe")
    res = R.describe_prep(taxonomy_path, db, task_dir, batches=batches)
    if not res["batches"]:
        return None
    if notes:
        for k in range(res["batches"]):
            batch_path = os.path.join(task_dir, f"batch_{k}.json")
            group = load_json(batch_path)
            changed = False
            for entry in group:
                note = notes.get(entry.get("node"))
                if note:
                    entry["note"] = note
                    changed = True
            if changed:
                atomic_write_bytes(batch_path, (json.dumps(group, indent=1, ensure_ascii=False)
                                                + "\n").encode("utf-8"))
    return {"kind": "describe", "dir": task_dir, "batches": res["batches"]}


def _prepare_untagged(tax_dir, db, taxonomy_path, problems, out_dir, batches, warnings):
    total_untagged = problems["untagged_sections"][0]["count"] if problems["untagged_sections"] else 0
    if not total_untagged:
        return None
    task_dir = os.path.join(out_dir, "untagged")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taxonomy_refine_prep.py")
    cmd = [sys.executable, script, "--db", db, "--taxonomy", taxonomy_path, "--out", task_dir,
           "--batches", str(batches)]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        msg = f"untagged: {' '.join(cmd)} exited {result.returncode}: {result.stderr.strip()}"
        print(f"diagnose: {msg}", file=sys.stderr)
        warnings.append(msg)
        return None
    n = len(glob.glob(os.path.join(task_dir, "batch_*.json")))
    if not n:
        return None
    return {"kind": "untagged", "dir": task_dir, "batches": n}


def diagnose(taxonomy_path, db, out_dir, metrics_path=None, batches=4):
    if os.path.basename(taxonomy_path) != CURRENT:
        raise ValueError(f"diagnose is planned on taxonomy/{CURRENT}, not {taxonomy_path}")
    problems, tax, tax_dir, c, notes = detect(taxonomy_path, db, metrics_path)
    os.makedirs(out_dir, exist_ok=True)
    atomic_write_bytes(os.path.join(out_dir, "problems.json"),
                       (json.dumps(problems, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))

    warnings = []
    tasks = []
    for builder in (
        lambda: _prepare_describe(tax, taxonomy_path, db, problems, out_dir, notes, batches),
        lambda: _prepare_notags(tax, tax_dir, problems, out_dir, notes, batches),
        lambda: _prepare_structure(tax, tax_dir, problems, out_dir, c, notes, batches),
        lambda: _prepare_metrics(tax, tax_dir, problems, out_dir, notes, batches),
        lambda: _prepare_untagged(tax_dir, db, taxonomy_path, problems, out_dir, batches, warnings),
    ):
        t = builder()
        if t:
            tasks.append(t)

    if c:
        c.close()

    result = {"problems": {k: len(v) for k, v in problems.items()}, "tasks": tasks, "out": out_dir}
    if warnings:
        result["warnings"] = warnings
    return result




# ---------------------------------------------------------------------------
# health_items: problems.json + agents' result_k.json -> grouped review items (B3/B4)
# ---------------------------------------------------------------------------
#
# Binding product rule: every problem `diagnose` detected reaches the inbox — with the
# agent's fix when one was found and usable, else a documented fallback (a safe default op,
# reason "no fix proposed" unless the agent DID respond with something unusable, in which
# case its own reason is kept) plus "template" alternatives (an intentionally invalid or
# empty-payload op, e.g. `describe{node, description:""}` or `tag{node, chunk_ids:[]}`) a
# human can amend into a real one. Templates are refused as-is by `taxo_ops.validate` — that
# is correct; they exist only as an amend target (see `decisions.health_amend_ok`), never as
# something Approve alone could apply. `untagged_sections` is the one kind whose "problem" is
# a single aggregate record (not one per node); when its solve step produced nothing usable it
# gets one informational item with the sentinel op `{"type": "keep", "node": "__untagged__"}`.
# That sentinel is safe specifically because `taxo_ops._run` skips every `keep` op before it
# ever reaches a node-existence check (`_OPS` is never consulted), so no real node needs to
# exist under that name — never introduce a non-`keep` op with this sentinel.


def _sanitize_entry(e):
    """Field-level type guards (mutates `e` in place): chunk_ids/example_ids keep only real
    ints (bools excluded — `isinstance(True, int)` is true in Python); node/subject/into/
    description must be strings when present. Returns False when the entry is unusable
    (a non-string on a string-only field), so the caller can skip + count it."""
    for k in ("node", "subject", "into", "description"):
        v = e.get(k)
        if v is not None and not isinstance(v, str):
            return False
    for k in ("chunk_ids", "example_ids"):
        v = e.get(k)
        if isinstance(v, list):
            e[k] = [i for i in v if isinstance(i, int) and not isinstance(i, bool)]
    return True


def _load_entries(task_dir, list_key, skipped_files):
    """Every dict entry of data[list_key] from each result_*.json in task_dir.

    Defensive per the global constraint: a wrong-shaped file goes to skipped_files (named on
    stderr) and is never allowed to crash planning; a non-dict entry, or one that fails
    `_sanitize_entry`'s field guards, is skipped and counted, without failing the rest of the
    file.
    """
    out = []
    if not task_dir or not os.path.isdir(task_dir):
        return out
    for rf in sorted(glob.glob(os.path.join(task_dir, "result_*.json"))):
        try:
            with open(rf, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"health: skip {rf}: {e}", file=sys.stderr)
            skipped_files.append(rf)
            continue
        if not isinstance(data, dict) or not isinstance(data.get(list_key), list):
            print(f"health: skip {rf}: expected {{{list_key!r}: [...]}}", file=sys.stderr)
            skipped_files.append(rf)
            continue
        bad = 0
        for entry in data[list_key]:
            if not isinstance(entry, dict) or not _sanitize_entry(entry):
                bad += 1
                continue
            out.append(entry)
        if bad:
            print(f"health: skip {bad} malformed entries in {rf}", file=sys.stderr)
    return out


def _make_item(tax, kind, group, op, alternatives, evidence, support, reason, title, rejections, fallback=False):
    """Build one health item, validating `op` (single-op, against `tax`) the way describe mode
    validates a description target: a failure marks the item invalid instead of crashing or
    silently dropping the problem. `fallback` is a private bookkeeping flag (stripped by
    `build_plan` before the review is written) so the health context's "fixes proposed" count
    can exclude items that carry a safe default rather than an agent's actual recommendation."""
    errs = taxo_ops.validate(tax, [op])
    item = {"origin": "health", "kind": kind, "group": group, "title": title, "reason": reason,
            "op": op, "alternatives": alternatives, "evidence": evidence, "support": support,
            "status": "proposed", "_fallback": fallback}
    if errs:
        item["status"] = "invalid"
        item["reason"] = "; ".join(errs)
        return item
    prior = D.match_rejection(fingerprint(op), rejections)
    if prior:
        item["status"] = "suppressed"
        item["prior"] = {k: prior.get(k) for k in ("review_id", "reason", "reviewer", "ts")}
    return item


def _best_sibling(it, label, level, parent):
    sibs = _siblings_of(it, label, level, parent)
    if not sibs:
        return None
    ln = norm(label)
    return max(sibs, key=lambda s: SequenceMatcher(None, ln, norm(s)).ratio())


def _missing_description_items(tax, work_dir, problems, counts, skipped_files, rejections):
    entries = _load_entries(os.path.join(work_dir, "describe"), "descriptions", skipped_files)
    by_node = {}
    for e in entries:
        node = (e.get("node") or "").strip()
        if not node or node in by_node:
            continue
        text = one_line(e.get("description") or "").strip()
        if text:
            by_node[node] = text
    probs = problems.get("missing_description") or []
    title = f"{len(probs)} categories have no description"
    items = []
    for p in probs:
        node = p["node"]
        text = by_node.get(node)
        support = {"tags": counts.get(nid(node), 0)}
        if text:
            op = {"type": "describe", "node": node, "description": text}
            items.append(_make_item(tax, "missing_description", "missing_description", op, [], [],
                                    support, None, title, rejections))
        else:
            op = {"type": "keep", "node": node}
            template = {"type": "describe", "node": node, "description": ""}
            items.append(_make_item(tax, "missing_description", "missing_description", op, [template], [],
                                    support, "no fix proposed", title, rejections, fallback=True))
    return items


def _no_tags_items(tax, work_dir, problems, skipped_files, rejections):
    entries = _load_entries(os.path.join(work_dir, "notags"), "fixes", skipped_files)
    by_node = {}
    for e in entries:
        node = (e.get("node") or "").strip()
        if node and node not in by_node:
            by_node[node] = e
    it = intent(tax)
    probs = problems.get("no_tags") or []
    title = f"{len(probs)} categories have no tagged sections"
    items = []
    for p in probs:
        node, level, parent = p["node"], p["level"], p["parent"]
        candidates = p.get("candidates") or []
        cand_ids = {c["chunk_id"] for c in candidates}
        fix = by_node.get(node)
        fix_type = fix.get("fix") if fix else None
        sibling = _best_sibling(it, node, level, parent)
        merge_op = {"type": "merge", "from": node, "into": sibling} if sibling else None
        remove_op = {"type": "remove", "node": node, "disposition": "demote"}
        tag_template = {"type": "tag", "node": node, "chunk_ids": []}
        fallback = False
        if fix_type == "tag":
            ids = [i for i in (fix.get("chunk_ids") or []) if i in cand_ids]
            if ids:
                op = {"type": "tag", "node": node, "chunk_ids": ids}
                alts = [a for a in (merge_op, remove_op) if a]
                evidence = [c for c in candidates if c["chunk_id"] in ids]
                reason = fix.get("reason")
            else:
                # the agent engaged but found nothing usable to tag — fall back to remove,
                # but keep its reason (it is informative even though we didn't take its op)
                op, evidence = remove_op, []
                alts = [a for a in (merge_op, tag_template) if a]
                reason = fix.get("reason") or "agent found no fitting candidates"
                fallback = True
        elif fix_type == "merge" and (fix.get("into") or "").strip():
            op = {"type": "merge", "from": node, "into": fix["into"]}
            alts = [remove_op]
            evidence = []
            reason = fix.get("reason")
        elif fix_type == "remove":
            op = remove_op
            alts = [a for a in (merge_op, tag_template) if a]
            evidence = []
            reason = fix.get("reason")
        else:
            op, evidence = remove_op, []
            alts = [a for a in (merge_op, tag_template) if a]
            reason = "no fix proposed"
            fallback = True
        items.append(_make_item(tax, "no_tags", "no_tags", op, alts, evidence,
                                {"candidates": len(candidates)}, reason, title, rejections, fallback=fallback))
    return items


def _structure_items(tax, work_dir, problems, skipped_files, rejections):
    entries = _load_entries(os.path.join(work_dir, "structure"), "fixes", skipped_files)
    nd_fix, oa_fix = {}, {}
    for e in entries:
        subj = (e.get("subject") or "").strip()
        if not subj:
            continue
        if e.get("kind") == "near_duplicate":
            nd_fix.setdefault(subj, e)
        elif e.get("kind") == "off_axis":
            oa_fix.setdefault(subj, e)
    items = []
    for p in problems.get("near_duplicate") or []:
        a, b = p["a"], p["b"]
        fix = nd_fix.get(a) or nd_fix.get(b)
        keep_op = {"type": "keep", "node": a}
        merge_ab = {"type": "merge", "from": b, "into": a}   # b merged away into a
        merge_ba = {"type": "merge", "from": a, "into": b}   # a merged away into b
        fallback = False
        if fix and fix.get("fix") == "merge" and (fix.get("into") or "").strip():
            subj = fix.get("subject") if fix.get("subject") in (a, b) else a
            into = fix["into"]
            op = {"type": "merge", "from": subj, "into": into}
            reverse = {"type": "merge", "from": into, "into": subj}
            alts, reason = [keep_op, reverse], fix.get("reason")
        elif fix and fix.get("fix") == "keep":
            op = keep_op
            alts, reason = [merge_ab, merge_ba], fix.get("reason")
        else:
            op = keep_op
            alts, reason, fallback = [merge_ab, merge_ba], "no fix proposed", True
        title = f"{a} looks like {b}"
        support = {"tags_a": p.get("tags_a"), "tags_b": p.get("tags_b"), "overlap": p.get("overlap")}
        items.append(_make_item(tax, "near_duplicate", None, op, alts, [], support, reason, title,
                                rejections, fallback=fallback))
    for p in problems.get("off_axis") or []:
        node = p["node"]
        fix = oa_fix.get(node)
        keep_op = {"type": "keep", "node": node}
        default_remove = {"type": "remove", "node": node, "disposition": "demote"}
        fallback = False
        disp = (fix.get("disposition") or "").strip() if fix else ""
        if fix and fix.get("fix") == "remove" and (disp == "demote" or disp.startswith("entity:")):
            op = {"type": "remove", "node": node, "disposition": disp or "demote"}
            alts, reason = [keep_op], fix.get("reason")
        elif fix and fix.get("fix") == "keep":
            op = keep_op
            alts, reason = [default_remove], fix.get("reason")
        else:
            op, alts, reason, fallback = keep_op, [default_remove], "no fix proposed", True
        title = f"{node} doesn't look like a call reason"
        items.append(_make_item(tax, "off_axis", None, op, alts, [], {"tags": p.get("tags")}, reason, title,
                                rejections, fallback=fallback))
    return items


def _metrics_items(tax, work_dir, problems, skipped_files, rejections):
    entries = _load_entries(os.path.join(work_dir, "metrics"), "fixes", skipped_files)
    sm_fix, mg_fix = {}, {}
    for e in entries:
        subj = (e.get("subject") or "").strip()
        if not subj:
            continue
        if e.get("kind") == "similar_metrics":
            sm_fix.setdefault(subj, e)
        elif e.get("kind") == "metric_not_governed":
            mg_fix.setdefault(subj, e)
    items = []
    for p in problems.get("similar_metrics") or []:
        a, b = p["a"], p["b"]
        fix = sm_fix.get(a) or sm_fix.get(b)
        keep_op = {"type": "keep", "metric": a}
        merge_ab = {"type": "metric_merge", "from": b, "into": a}
        merge_ba = {"type": "metric_merge", "from": a, "into": b}
        fallback = False
        if fix and fix.get("fix") == "metric_merge" and (fix.get("into") or "").strip():
            subj = fix.get("subject") if fix.get("subject") in (a, b) else a
            into = fix["into"]
            op = {"type": "metric_merge", "from": subj, "into": into}
            reverse = {"type": "metric_merge", "from": into, "into": subj}
            alts, reason = [keep_op, reverse], fix.get("reason")
        elif fix and fix.get("fix") == "keep":
            op = keep_op
            alts, reason = [merge_ab, merge_ba], fix.get("reason")
        else:
            op, alts, reason, fallback = keep_op, [merge_ab, merge_ba], "no fix proposed", True
        title = f"{a} ≈ {b}"
        items.append(_make_item(tax, "similar_metrics", None, op, alts, [], {"score": p.get("score")},
                                reason, title, rejections, fallback=fallback))
    probs = problems.get("metric_not_governed") or []
    title = f"{len(probs)} metrics can't be computed yet"
    for p in probs:
        metric = p["metric"]
        fix = mg_fix.get(metric)
        keep_op = {"type": "keep", "metric": metric}
        template = {"type": "metric_govern", "metric": metric, "draft": {}}
        if fix and fix.get("fix") == "metric_govern" and isinstance(fix.get("draft"), dict) and fix["draft"]:
            op = {"type": "metric_govern", "metric": metric, "draft": fix["draft"]}
            items.append(_make_item(tax, "metric_not_governed", "metric_not_governed", op, [], [],
                                    {"sources": p.get("sources")}, fix.get("reason"), title, rejections))
        elif fix and fix.get("fix") == "keep":
            items.append(_make_item(tax, "metric_not_governed", "metric_not_governed", keep_op, [template], [],
                                    {"sources": p.get("sources")}, fix.get("reason"), title, rejections))
        else:
            items.append(_make_item(tax, "metric_not_governed", "metric_not_governed", keep_op, [template], [],
                                    {"sources": p.get("sources")}, "no fix proposed", title, rejections,
                                    fallback=True))
    return items


def _untagged_batch_ids(task_dir):
    """The chunk ids diagnose actually sent out for review (from batch_k.json), used to keep a
    `map` entry from tagging a chunk outside the untagged set it was asked about."""
    ids = set()
    for bf in sorted(glob.glob(os.path.join(task_dir, "batch_*.json"))):
        try:
            with open(bf, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and isinstance(row.get("id"), int) and not isinstance(row.get("id"), bool):
                    ids.add(row["id"])
    return ids


def _untagged_items(tax, work_dir, problems, skipped_files, rejections):
    probs = problems.get("untagged_sections") or []
    count = probs[0].get("count") if probs else 0
    if not count:
        return []
    title = f"{count} sections have no category"
    task_dir = os.path.join(work_dir, "untagged")
    untagged_ids = _untagged_batch_ids(task_dir) if os.path.isdir(task_dir) else set()
    result_files = sorted(glob.glob(os.path.join(task_dir, "result_*.json"))) if os.path.isdir(task_dir) else []
    proposals, chunk_map = [], {}
    for rf in result_files:
        try:
            with open(rf, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"health: skip {rf}: {e}", file=sys.stderr)
            skipped_files.append(rf)
            continue
        if not isinstance(data, dict):
            print(f"health: skip {rf}: expected an object", file=sys.stderr)
            skipped_files.append(rf)
            continue
        props, m = data.get("proposals"), data.get("map")
        if (props is not None and not isinstance(props, list)) or (m is not None and not isinstance(m, dict)):
            print(f"health: skip {rf}: expected {{\"proposals\": [...], \"map\": {{...}}}}", file=sys.stderr)
            skipped_files.append(rf)   # a bad map invalidates the whole file — never half-consume it
            continue
        bad = 0
        for p in (props or []):
            if isinstance(p, dict) and _sanitize_entry(p):
                proposals.append(p)
            else:
                bad += 1
        for cid, labels in (m or {}).items():
            if not isinstance(labels, list) or not str(cid).lstrip("-").isdigit():
                bad += 1
                continue
            ok = [l for l in labels if isinstance(l, str) and l.strip()]
            bad += len(labels) - len(ok)
            if ok:
                chunk_map.setdefault(int(cid), []).extend(ok)
        if bad:
            print(f"health: skip {bad} malformed entries in {rf}", file=sys.stderr)

    if not proposals and not chunk_map:
        support = {"count": count, "total": probs[0].get("total")}
        return [{"origin": "health", "kind": "untagged_sections", "group": "untagged_sections", "title": title,
                 "reason": "no fix proposed", "op": {"type": "keep", "node": "__untagged__"}, "alternatives": [],
                 "evidence": [], "support": support, "status": "proposed", "_fallback": True}]

    items = []
    add_items, skipped = plan_additions(tax, proposals)
    running = copy.deepcopy(tax)
    for add in add_items:
        op = {"type": "add", "level": add["level"], "name": add["name"], "parent": add["parent"]}
        if add.get("description"):
            op["description"] = add["description"]
        ids = sorted({x for src in add["sources"] for x in (src.get("example_ids") or [])
                     if isinstance(x, int) and not isinstance(x, bool)})
        evidence = [{"quote": s.get("evidence"), "chunk_ids": [x for x in (s.get("example_ids") or [])
                    if isinstance(x, int) and not isinstance(x, bool)]} for s in add["sources"]]
        item = _make_item(running, "untagged_sections", "untagged_sections", op, [], evidence,
                          {"chunks": len(ids)}, None, title, rejections)
        items.append(item)
        if item["status"] != "invalid":
            try:
                running, _mig, _ap = taxo_ops.apply_ops(running, [op])
            except taxo_ops.ChangesetError:
                pass   # keep validating the rest against the last tax that DID apply cleanly
    for s in skipped:
        if s["kind"] == "dup_new":
            continue
        op = {"type": "add", "level": s.get("level") or "L2", "name": s["name"], "parent": s.get("parent")}
        status = "invalid" if s["kind"] == "invalid" else "auto_skipped"
        items.append({"origin": "health", "kind": "untagged_sections", "group": "untagged_sections", "title": title,
                      "reason": s["reason"], "op": op, "alternatives": [], "evidence": [], "support": {},
                      "status": status})
    label_to_ids = {}
    for cid, labels in chunk_map.items():
        if untagged_ids and cid not in untagged_ids:
            continue
        for label in labels:
            label_to_ids.setdefault(label, set()).add(cid)
    for label, ids in label_to_ids.items():
        if not ids:
            continue
        op = {"type": "tag", "node": label, "chunk_ids": sorted(ids)}
        items.append(_make_item(running, "untagged_sections", "untagged_sections", op, [], [],
                                {"chunks": len(ids)}, None, title, rejections))
    return items


def health_items(tax, work_dir, counts, rejections, skipped_files):
    """Grouped health review items from `work_dir/problems.json` and each task's result_k.json.

    Every problem `diagnose` recorded gets exactly one item (the binding rule: nothing detected
    is ever silently dropped from the inbox): the agent's fix when `result_k.json` proposed a
    usable one, else a safe default op (a `keep`/`remove`) carrying "no fix proposed" (or, when
    the agent DID answer but with nothing usable, its own reason) plus template alternatives —
    an intentionally-empty or intentionally-invalid op (`describe{..., description:""}`,
    `tag{..., chunk_ids:[]}`, `metric_govern{..., draft:{}}`) that only exists so a human can
    amend it into a real one (see `decisions.health_amend_ok`); Approving a template as-is is
    refused by `taxo_ops.validate`, by design. `untagged_sections` is the exception forced by
    its shape (one aggregate record, not one per node): with no usable agent results it gets a
    single informational item using the sentinel op `{"type": "keep", "node": "__untagged__"}`
    — safe only because `taxo_ops._run` skips every `keep` op before any node lookup runs.
    Every built item's op is validated (single-op, `taxo_ops.validate`) against `tax`; a
    failure (a merge with no `into`, a bad off_axis disposition, an untagged `map` label that
    isn't a real or newly-proposed category, …) marks the item `status: "invalid"` with the
    validator's reason instead of crashing or emitting an op nothing could ever apply.
    """
    problems = load_json(os.path.join(work_dir, "problems.json"))
    items = []
    items += _missing_description_items(tax, work_dir, problems, counts, skipped_files, rejections)
    items += _no_tags_items(tax, work_dir, problems, skipped_files, rejections)
    items += _untagged_items(tax, work_dir, problems, skipped_files, rejections)
    items += _structure_items(tax, work_dir, problems, skipped_files, rejections)
    items += _metrics_items(tax, work_dir, problems, skipped_files, rejections)
    return items
