#!/usr/bin/env python3
"""Taxonomy review: plan a review, record decisions, serve the review app.

It never writes a taxonomy file — `taxonomy_merge.py --review … --apply` does that.
Every subcommand prints one JSON line on stdout (serve prints it on exit).

  plan          --mode draft|drift|browse|describe|health [--taxonomy taxonomy/current.json] [--proposals DIR]
                [--work DIR] [--consolidated F] [--db K.sqlite] [--decisions D]
  describe-prep --taxonomy taxonomy/current.json --db K.sqlite --out DIR [--batches 5] [--all]
  diagnose  --taxonomy taxonomy/current.json --db K.sqlite --out DIR [--metrics F] [--batches 4]
  serve     --review R --db K.sqlite [--metrics F] [--port 0] [--no-browser] [--timeout 3600] [--reviewer NAME]
            [--watch-hint]
  respond   --review R --request ID --op JSON [--reason TEXT] [--check]
  redo-prep --review R --item ID [--request ID] [--work DIR] [--out DIR]
  record    --review R (--action A [--item ID] [--op JSON] [--reason T] | --submit) [--reviewer NAME]
  status    --review R
  export-md --review R --out F
  import-md --review R --md F [--submit]
  adopt     --taxonomy taxonomy/taxonomy_vN.json --db K.sqlite [--meta-only] [--force] [--provisional]
  gap       [--taxonomy F] [--metrics F] [--out F]

In a Claude Code session, run `serve` in the BACKGROUND and end the turn: it exits when the
reviewer clicks Submit, and the exit output (one JSON line) re-invokes the agent.
`record` is for scripts and browserless hosts and only enters additions — renames, merges,
moves, splits, removals and metric edits come from the review app or an imported Markdown file.
"""
import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decisions as D  # noqa: E402
import graph_migrate as GM  # noqa: E402
from taxo_io import (CURRENT, atomic_write_bytes, fingerprint, intent, is_provisional, load_json, locate,  # noqa: E402
                     nid, node_ids, one_line, reviewer_name, sha256_bytes, utc_now, write_provisional)
from taxonomy_merge import load_proposals, plan_additions  # noqa: E402

DESCRIBE_INSTRUCTIONS = """# Drafting category descriptions

The nodes to describe are in the `batch_k.json` files next to this one (`batch_0.json`,
`batch_1.json`, …), one batch per file. For each `batch_k.json` you process, write a
`result_k.json` with the same `k` — one result file per batch — shaped:

```json
{"descriptions": [{"node": "<label>", "description": "<text>"}]}
```

Rules:
- One or two plain sentences.
- Say what the category covers and how it differs from its siblings.
- Base it on the samples; no numbers or claims that are not in the samples.
- If there are no samples, describe it from the label and its siblings only, and keep it short.
"""


def tax_dir_of(review_path):
    return os.path.dirname(os.path.dirname(os.path.abspath(review_path)))


def _ro(db):
    if not db:
        return None
    return sqlite3.connect(f"file:{os.path.abspath(db)}?mode=ro", uri=True, check_same_thread=False)


def _counts(c):
    if not c or not GM.has_table(c, "chunk_topics"):
        return {}
    return dict(c.execute("SELECT category_id, COUNT(DISTINCT chunk_id) FROM chunk_topics GROUP BY category_id"))


def _brief(row):
    return {"chunk_id": row[0], "source": row[1], "title": row[2], "preview": " ".join((row[3] or "").split())[:240]}


def _samples(c, node_id, k=3):
    if not c or not GM.has_table(c, "chunk_topics"):
        return []
    return [_brief(r) for r in c.execute(
        "SELECT c.id, c.source, c.title, substr(c.text,1,400) FROM chunk_topics t JOIN chunks c ON c.id=t.chunk_id "
        "WHERE t.category_id=? GROUP BY c.id ORDER BY c.id LIMIT ?", (node_id, k))]


def _chunk_brief(c, cid):
    r = c.execute("SELECT id, source, title, substr(text,1,400) FROM chunks WHERE id=?", (cid,)).fetchone()
    return _brief(r) if r else None


def _stats(c):
    if not c or not GM.has_table(c, "chunks"):
        return {}
    total = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    tagged = (c.execute("SELECT COUNT(DISTINCT chunk_id) FROM chunk_topics").fetchone()[0]
              if GM.has_table(c, "chunk_topics") else 0)
    return {"total_chunks": total, "untagged_chunks": total - tagged}


def _flags(tax, label):
    rf = tax.get("review_flags") or {}
    out = [{"kind": "near_duplicate", "with": [x for x in g if x != label]}
           for g in rf.get("near_duplicate_l1_groups", []) if label in g]
    out += [{"kind": "off_axis", "reason": f.get("reason")}
            for f in rf.get("off_axis_l1_candidates", []) if f.get("label") == label]
    return out


def _clusters(consolidated):
    if not consolidated or not os.path.exists(consolidated):
        return None
    con = load_json(consolidated)
    return {cl["canonical_guess"]: cl for cl in con.get("clusters", {}).get("intent_classes", [])}


def _draft_items(tax, clusters, c, counts):
    it = intent(tax)
    rows = []
    for l1, kids in it["tree"].items():
        rows.append((l1, "L1", None))
        rows += [(k, "L2", l1) for k in kids]
    rows += [(x, "L2", None) for x in it["unassigned_l2"]]
    rows += [(k, "entity", None) for k in (tax.get("entities") or {})]
    items = []
    for label, level, parent in rows:
        cl = clusters.get(label) if clusters else None
        evidence = [{"quote": m.get("evidence"), "source": m.get("source"), "confidence": m.get("confidence")}
                    for m in (cl or {}).get("members", []) if m.get("evidence")][:3]
        support = {"tags": counts.get(nid(label), 0)}
        if cl:
            support.update({"variants": cl.get("variants", []), "n_sources": cl.get("n_sources"),
                            "avg_confidence": cl.get("avg_confidence")})
        if clusters is None:
            support["samples"] = _samples(c, nid(label))
        items.append({"origin": "induction", "op": {"type": "keep", "node": label}, "level": level,
                      "parent": parent, "status": "proposed", "flags": _flags(tax, label),
                      "evidence": evidence, "support": support})
    return items


def _drift_items(tax, proposals_dir, c, rejections, stats, skipped_files):
    items, skipped = plan_additions(tax, load_proposals(proposals_dir, skipped_files))
    out = []
    for i in items:
        op = {"type": "add", "level": i["level"], "name": i["name"], "parent": i["parent"]}
        if i.get("description"):
            op["description"] = i["description"]
        ids = sorted({int(x) for p in i["sources"] for x in (p.get("example_ids") or [])
                      if str(x).lstrip("-").isdigit()})
        support = {"chunks": len(ids)}
        if c:
            briefs = [b for b in (_chunk_brief(c, x) for x in ids) if b]
            support["samples"] = briefs[:5]
            support["sources"] = len({b["source"] for b in briefs})
            if stats.get("total_chunks"):
                has_ct = GM.has_table(c, "chunk_topics")
                untagged = [x for x in ids if not has_ct or not c.execute(
                    "SELECT 1 FROM chunk_topics WHERE chunk_id=? LIMIT 1", (x,)).fetchone()]
                support["projected_coverage_gain_pts"] = round(100 * len(untagged) / stats["total_chunks"], 1)
        prior = D.match_rejection(fingerprint(op), rejections)
        item = {"origin": "refine", "op": op, "level": i["level"], "parent": i["parent"],
                "evidence": [{"quote": p.get("evidence"), "chunk_ids": p.get("example_ids") or []}
                             for p in i["sources"]],
                "support": support, "status": "suppressed" if prior else "proposed"}
        if prior:
            item["prior"] = {k: prior.get(k) for k in ("review_id", "reason", "reviewer", "ts")}
        out.append(item)
    for s in skipped:
        if s["kind"] == "dup_new":
            continue
        op = {"type": "add", "level": s["level"] or "L2", "name": s["name"], "parent": s.get("parent")}
        out.append({"origin": "refine", "op": op, "level": op["level"], "parent": op["parent"],
                    "status": "invalid" if s["kind"] == "invalid" else "auto_skipped", "reason": s["reason"]})
    return out


def _describe_items(tax, proposals_dir, counts, rejections, skipped_files):
    descs = tax.get("descriptions") or {}
    seen = {}
    for rf in sorted(glob.glob(os.path.join(proposals_dir, "result_*.json"))):
        try:
            with open(rf, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print("skip", rf, e, file=sys.stderr)
            skipped_files.append(rf)
            continue
        if not isinstance(data, dict) or not isinstance(data.get("descriptions"), list):
            print("skip", rf, 'expected {"descriptions": [...]}', file=sys.stderr)
            skipped_files.append(rf)
            continue
        bad = 0
        for d in data["descriptions"]:
            if not isinstance(d, dict):
                bad += 1
                continue
            node = (d.get("node") or "").strip()
            if not node or node in seen:
                continue
            seen[node] = one_line(d.get("description") or "").strip()
        if bad:
            print(f"skip {bad} malformed entries in {rf}", file=sys.stderr)
    items = []
    for node, text in seen.items():
        op = {"type": "describe", "node": node, "description": text}
        level, parent = locate(tax, node)
        support = {"current": descs.get(node), "tags": counts.get(nid(node), 0)}
        if level is None:
            items.append({"origin": "describe", "op": op, "level": None, "parent": None, "status": "invalid",
                          "reason": f"{node!r} is not in the taxonomy", "support": support})
            continue
        if not text:
            items.append({"origin": "describe", "op": op, "level": level, "parent": parent, "status": "invalid",
                          "reason": "empty description", "support": support})
            continue
        prior = D.match_rejection(fingerprint(op), rejections)
        item = {"origin": "describe", "op": op, "level": level, "parent": parent,
                "status": "suppressed" if prior else "proposed", "support": support}
        if prior:
            item["prior"] = {k: prior.get(k) for k in ("review_id", "reason", "reviewer", "ts")}
        items.append(item)
    return items


def _context(mode, tax, items, stats, version, problems=None, provisional=False):
    it = intent(tax)
    labels = list(it["tree"]) + [k for kids in it["tree"].values() for k in kids] + list(it["unassigned_l2"])
    descs = tax.get("descriptions") or {}
    if mode == "draft":
        flagged = sum(1 for i in items if i.get("flags"))
        return {"title": "First-build review",
                "subtitle": f"Draft taxonomy · {len(labels)} categories · {flagged} flagged"}
    if mode == "drift":
        proposals = sum(1 for i in items if i["status"] in ("proposed", "suppressed"))
        subtitle = f"Taxonomy v{version} → v{version + 1} · {proposals} proposals"
        if stats:
            subtitle += f" · {stats.get('untagged_chunks', 0)} chunks without a category"
        return {"title": "Refresh review", "subtitle": subtitle}
    if mode == "browse":
        without = sum(1 for l in labels if not (descs.get(l) or "").strip())
        return {"title": "Taxonomy editor",
                "subtitle": f"v{version} · {len(labels)} categories · {without} without a description"}
    if mode == "describe":
        drafted = sum(1 for i in items if i["status"] in ("proposed", "suppressed"))
        return {"title": "Description review", "subtitle": f"v{version} · {drafted} drafted descriptions"}
    if mode == "health":
        counted = ("missing_description", "no_tags", "near_duplicate", "off_axis",
                   "similar_metrics", "metric_not_governed")
        n_problems = sum(len((problems or {}).get(k) or []) for k in counted)
        untagged = (problems or {}).get("untagged_sections") or []
        if untagged and untagged[0].get("count"):
            n_problems += 1
        # "fixes proposed" excludes items carrying a safe default (`_fallback`, stripped by
        # build_plan before the review is written) — a problem with no usable agent fix still
        # reaches the inbox, it just isn't counted as a proposed fix here.
        fixes = sum(1 for i in items if i["status"] in ("proposed", "suppressed") and not i.get("_fallback"))
        if provisional:
            return {"title": "First-build review",
                    "subtitle": f"{len(labels)} categories · {fixes} changes proposed · everything else is kept"}
        return {"title": "Health review", "subtitle": f"v{version} · {n_problems} problems · {fixes} fixes proposed"}
    raise ValueError(f"unknown mode {mode!r}")


def describe_prep(taxonomy_path, db, out_dir, batches=5, all_nodes=False):
    tax = load_json(taxonomy_path)
    it = intent(tax)
    descs = tax.get("descriptions") or {}
    c = _ro(db)
    rows = []
    for l1, kids in it["tree"].items():
        rows.append((l1, "L1", None))
        rows += [(k, "L2", l1) for k in kids]
    rows += [(x, "L2", None) for x in it["unassigned_l2"]]
    entries = []
    for label, level, parent in rows:
        if not all_nodes and (descs.get(label) or "").strip():
            continue
        if level == "L1":
            siblings = [x for x in it["tree"] if x != label][:30]
        else:
            pool = it["tree"].get(parent, []) if parent else it["unassigned_l2"]
            siblings = [x for x in pool if x != label][:30]
        entries.append({"node": label, "level": level, "parent": parent, "siblings": siblings,
                        "current": descs.get(label), "samples": _samples(c, nid(label), 5)})
    if c:
        c.close()
    n = len(entries)
    groups = []
    if n:
        b = max(1, min(batches, n))
        size = -(-n // b)  # ceil
        groups = [entries[i:i + size] for i in range(0, n, size)]
    os.makedirs(out_dir, exist_ok=True)
    for k, group in enumerate(groups):
        atomic_write_bytes(os.path.join(out_dir, f"batch_{k}.json"),
                           (json.dumps(group, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    atomic_write_bytes(os.path.join(out_dir, "instructions.md"), DESCRIBE_INSTRUCTIONS.encode("utf-8"))
    return {"nodes": n, "batches": len(groups), "out": out_dir}


def build_plan(mode, taxonomy_path, proposals_dir=None, consolidated=None, db=None, decisions_path=None, now=None,
               work_dir=None):
    if mode in ("drift", "browse", "describe", "health") and os.path.basename(taxonomy_path) != CURRENT:
        raise ValueError(f"{mode} reviews are planned on taxonomy/{CURRENT}, not {taxonomy_path}. If this Brain "
                         f"predates {CURRENT}, run `taxonomy_review.py adopt --taxonomy <the version the store "
                         f"was built from> --db <db>` first")
    raw = open(taxonomy_path, "rb").read()
    tax = json.loads(raw)
    tax_dir = os.path.dirname(os.path.abspath(taxonomy_path))
    sha, version = sha256_bytes(raw), tax.get("version") or 0
    now = now or datetime.now(timezone.utc)
    rid = f"r-{now.strftime('%Y%m%dT%H%M%S')}-{mode}-v{version}-{sha[:4]}"
    c = _ro(db)
    stats, counts = _stats(c), _counts(c)
    rejections = D.standing_rejections(D.read(decisions_path or D.default_path(tax_dir)))
    skipped_files = []
    if mode == "draft":
        if consolidated is None:
            guess = os.path.join(tax_dir, "work", "consolidated.json")
            consolidated = guess if os.path.exists(guess) else None
        clusters = _clusters(consolidated)
        items, evidence = _draft_items(tax, clusters, c, counts), clusters is not None
    elif mode == "drift":
        if not proposals_dir:
            raise ValueError("drift mode needs --proposals")
        items, evidence = _drift_items(tax, proposals_dir, c, rejections, stats, skipped_files), True
    elif mode == "browse":
        items, evidence = [], True
    elif mode == "describe":
        if not proposals_dir:
            raise ValueError("describe mode needs --proposals")
        items, evidence = _describe_items(tax, proposals_dir, counts, rejections, skipped_files), True
    elif mode == "health":
        if not work_dir:
            raise ValueError("health mode needs --work")
        problems_path = os.path.join(work_dir, "problems.json")
        if not os.path.exists(problems_path):
            raise ValueError(f"{problems_path} not found; run `taxonomy_review.py diagnose` first")
        import health as H
        problems = load_json(problems_path)
        items, evidence = H.health_items(tax, work_dir, counts, rejections, skipped_files), True
    else:
        raise ValueError(f"unknown mode {mode!r}")
    for n, item in enumerate(items):
        item["fingerprint"] = fingerprint(item["op"], item.get("kind") if item.get("origin") == "health" else None)
        item["id"] = "i-" + hashlib.sha1(f"{rid}|{n}|{item['fingerprint']}".encode()).hexdigest()[:12]
    context = _context(mode, tax, items, stats, version, problems if mode == "health" else None,
                       provisional=is_provisional(tax_dir))
    if mode == "health":
        for item in items:
            # Public only when true (keeps items small): the app leaves fallbacks — a safe default,
            # not an agent's real recommendation — out of "Accept all remaining".
            if item.pop("_fallback", None):
                item["fallback"] = True
    review = {"schema": 1, "review_id": rid, "mode": mode, "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "base": {"path": os.path.abspath(taxonomy_path), "version": version, "sha256": sha},
              "evidence_available": evidence, "stats": stats, "items": items, "context": context}
    if mode == "health":
        review["work_dir"] = os.path.abspath(work_dir)   # where redo-prep finds the task entries
    if mode in ("drift", "describe", "health"):
        review["skipped_files"] = skipped_files
    path = os.path.join(tax_dir, "reviews", f"review_{rid}.json")
    data = (json.dumps(review, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    if os.path.exists(path):
        if open(path, "rb").read() == data:
            if c:
                c.close()
            return path, review
        if c:
            c.close()
        raise FileExistsError(f"{path} already exists with different content; review files are immutable — "
                              "plan again in a moment or use the existing review")
    atomic_write_bytes(path, data)
    if c:
        c.close()
    return path, review


def review_status(review_path, decisions_path=None):
    review = load_json(review_path)
    records = D.read(decisions_path or D.default_path(tax_dir_of(review_path)))
    st = D.review_state(records, review["review_id"])
    counts = D.submit_counts(review, records)
    return {"review_id": review["review_id"], "mode": review["mode"], **counts,
            "submitted": bool(st["submit"]), "applied": bool(st["applied"])}


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def cmd_plan(a):
    path, rv = build_plan(a.mode, a.taxonomy, a.proposals, a.consolidated, a.db, a.decisions, work_dir=a.work)
    actionable = sum(1 for i in rv["items"] if i["status"] in ("proposed", "suppressed"))
    out = {"review": path, "review_id": rv["review_id"], "mode": rv["mode"], "items": actionable,
           "evidence_available": rv["evidence_available"]}
    if "skipped_files" in rv:
        out["skipped_files"] = rv["skipped_files"]
    _out(out)
    return 0


def cmd_record(a):
    review = load_json(a.review)
    dpath = a.decisions or D.default_path(tax_dir_of(a.review))
    base_tax = load_json(review["base"]["path"])
    records = D.read(dpath)
    who = reviewer_name(a.reviewer)
    if a.submit:
        rec = {"review_id": review["review_id"], "action": "submit", "reviewer": who, "surface": a.surface}
    else:
        if not a.action:
            _out({"status": "refused", "errors": ["--action or --submit is required"]})
            return 2
        rec = {"review_id": review["review_id"], "item_id": a.item, "action": a.action, "reviewer": who,
               "surface": a.surface}
        if a.action == "propose" and not a.item:
            rec["item_id"] = D.new_human_id()
        if a.reason:
            rec["reason"] = a.reason
        if a.op:
            rec["op"] = json.loads(a.op)
        item = next((i for i in review["items"] if i["id"] == a.item), None)
        if item and a.action in ("reject", "reopen"):
            rec["fingerprint"] = item["fingerprint"]
    errs = D.validate_record(review, base_tax, records, rec)
    if errs:
        _out({"status": "refused", "errors": errs})
        return 2
    if rec["action"] == "submit":
        rec["counts"] = D.submit_counts(review, records)
    D.append(dpath, rec)
    _out({"status": "recorded", "action": rec["action"], "item_id": rec.get("item_id")})
    return 0


def cmd_status(a):
    _out(review_status(a.review, a.decisions))
    return 0


def cmd_adopt(a):
    raw = open(a.taxonomy, "rb").read()
    tax = json.loads(raw)
    cur = os.path.join(os.path.dirname(os.path.abspath(a.taxonomy)), CURRENT)
    c = sqlite3.connect(a.db)
    db_ids = ({r[0] for r in c.execute(
        "SELECT id FROM graph_nodes WHERE kind IN ('intent_l1','intent_l2','entity_kind')")}
        if GM.has_table(c, "graph_nodes") else set())
    file_ids = node_ids(tax)
    only_file, only_db = sorted(file_ids - db_ids), sorted(db_ids - file_ids)
    if (only_file or only_db) and not a.force:
        c.close()
        _out({"status": "refused", "reason": "this taxonomy does not match the store's graph",
              "only_in_file": only_file, "only_in_db": only_db})
        return 2
    if a.meta_only and a.provisional:
        c.close()
        _out({"status": "refused", "reason": "--provisional adopts current.json; it is meaningless with "
                                             "--meta-only, which never touches current.json"})
        return 2
    if a.meta_only:
        # The store's tags already match this version; current.json (if any) is left alone —
        # e.g. it is already ahead and build_graph will migrate the store up to it.
        GM.write_version(c, tax.get("version") or 0, sha256_bytes(raw))
        c.commit()
        c.close()
        _out({"status": "adopted", "meta_only": True, "version": tax.get("version") or 0, "nodes": len(file_ids)})
        return 0
    if os.path.exists(cur) and open(cur, "rb").read() != raw and not a.force:
        c.close()
        _out({"status": "refused", "reason": f"{cur} exists and differs; pass --meta-only to record only the "
                                             f"store's version, or --force (only if the user asked) to replace it"})
        return 2
    atomic_write_bytes(cur, raw)
    GM.write_version(c, tax.get("version") or 0, sha256_bytes(raw))
    c.commit()
    out = {"status": "adopted", "current": cur, "version": tax.get("version") or 0, "nodes": len(file_ids)}
    if a.provisional:
        write_provisional(os.path.dirname(cur), tax.get("version") or 0, sha256_bytes(raw))
        out["provisional"] = True
    c.close()
    _out(out)
    return 0


def cmd_serve(a):
    import review_server as S
    app = S.ReviewApp(a.review, db_path=a.db, decisions_path=a.decisions, metrics_path=a.metrics,
                      reviewer=a.reviewer, live_channel=a.watch_hint)
    return S.serve(app, port=a.port, open_browser=not a.no_browser, timeout=a.timeout)


def cmd_export_md(a):
    import review_md as MD
    review = load_json(a.review)
    records = D.read(a.decisions or D.default_path(tax_dir_of(a.review)))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(MD.export_md(review, records))
    _out({"status": "exported", "out": a.out})
    return 0


def cmd_import_md(a):
    import review_md as MD
    review = load_json(a.review)
    dpath = a.decisions or D.default_path(tax_dir_of(a.review))
    base_tax = load_json(review["base"]["path"])
    records = D.read(dpath)
    who = reviewer_name(a.reviewer)
    try:
        new = MD.import_md(open(a.md, encoding="utf-8").read(), review, base_tax, records, who)
    except MD.MdImportError as e:
        _out({"status": "refused", "errors": e.errors})
        return 2
    if a.submit:
        sub = {"review_id": review["review_id"], "action": "submit", "reviewer": who, "surface": "markdown"}
        errs = D.validate_record(review, base_tax, records + new, sub)
        if errs:
            _out({"status": "refused", "errors": errs})
            return 2
    for rec in new:
        D.append(dpath, rec)
    if a.submit:
        sub["counts"] = D.submit_counts(review, records + new)
        D.append(dpath, sub)
    _out({"status": "imported", "records": len(new), "submitted": bool(a.submit)})
    return 0


def cmd_describe_prep(a):
    _out(describe_prep(a.taxonomy, a.db, a.out, batches=a.batches, all_nodes=a.all))
    return 0


def cmd_diagnose(a):
    import health as H
    signals = a.signals or os.path.join(os.path.dirname(os.path.abspath(a.taxonomy)), "work", "signals.json")
    _out(H.diagnose(a.taxonomy, a.db, a.out, metrics_path=a.metrics, batches=a.batches,
                    signals_path=signals if os.path.exists(signals) else None,
                    sparse_max=a.sparse_max, overload_factor=a.overload_factor))
    return 0


def _latest_requests(path):
    """The latest record per `id` in a requests.jsonl — later lines (e.g. a status change)
    supersede earlier ones for the same request, same convention as health._read_open_requests."""
    if not os.path.exists(path):
        return {}
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
    return latest


def cmd_respond(a):
    if not os.path.exists(a.review):
        _out({"status": "refused", "errors": [f"{a.review} not found"]})
        return 2
    review = load_json(a.review)
    tax_dir = tax_dir_of(a.review)
    work_dir = os.path.join(tax_dir, "work")
    dpath = a.decisions or D.default_path(tax_dir)
    records = D.read(dpath)
    if D.review_state(records, review["review_id"])["submit"]:
        _out({"status": "refused", "errors": ["this review is already submitted"]})
        return 2
    req = _latest_requests(os.path.join(work_dir, "requests.jsonl")).get(a.request)
    if req is None:
        _out({"status": "refused", "errors": [f"{a.request!r} is not a known request"]})
        return 2
    if req.get("status") != "open":
        _out({"status": "refused", "errors": [f"{a.request!r} is not open (status={req.get('status')!r})"]})
        return 2
    item = next((i for i in review.get("items", []) if i["id"] == req.get("item_id")), None)
    if item is None:
        _out({"status": "refused",
              "errors": [f"item {req.get('item_id')!r} not found in review {review['review_id']!r}"]})
        return 2
    try:
        op = json.loads(a.op)
    except json.JSONDecodeError as e:
        _out({"status": "refused", "errors": [f"--op is not valid JSON: {e}"]})
        return 2
    if not isinstance(op, dict):
        _out({"status": "refused", "errors": ["--op must be a JSON object"]})
        return 2
    if item.get("origin") != "health" or not D.health_amend_ok(item, op):
        _out({"status": "refused",
              "errors": [f"{item['id']}: the op must be one of its alternatives or an edit of the same fix"]})
        return 2
    # the same taxonomy check an amend gets in validate_record: the revised op together with every
    # other approved op must apply cleanly (refuses empty templates, string drafts, conflicts)
    trial = records + [{"review_id": review["review_id"], "item_id": item["id"], "action": "amend", "op": op}]
    errs = D.validate_ops(load_json(review["base"]["path"]), [e["op"] for e in D.effective_ops(review, trial)])
    if errs:
        _out({"status": "refused", "errors": errs})
        return 2
    if a.check:   # dry run: every check above passed; nothing is written
        _out({"status": "ok", "request_id": a.request, "item_id": item["id"], "op": op})
        return 0
    rec = {"request_id": a.request, "item_id": item["id"], "op": op, "ts": utc_now()}
    if a.reason:
        rec["reason"] = a.reason
    resp_path = os.path.join(work_dir, "responses.jsonl")
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(resp_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    _out({"status": "recorded", "request_id": a.request, "item_id": item["id"]})
    return 0


def cmd_redo_prep(a):
    import health as H
    review = load_json(a.review)
    if review.get("mode") != "health":
        raise ValueError(f"{a.review} is not a health review")
    item = next((i for i in review.get("items", []) if i["id"] == a.item), None)
    if item is None:
        raise ValueError(f"item {a.item!r} not found in review {review['review_id']!r}")
    tax_dir = tax_dir_of(a.review)
    work = a.work or review.get("work_dir") or os.path.join(tax_dir, "work", "health")
    reqs = [r for r in _latest_requests(os.path.join(tax_dir, "work", "requests.jsonl")).values()
            if r.get("item_id") == a.item and (not a.request or r.get("id") == a.request)]
    open_reqs = [r for r in reqs if r.get("status") == "open"]
    note = (open_reqs or reqs or [{}])[-1].get("note")
    _out(H.redo_prep(work, item, note, a.out))
    return 0


def cmd_gap(a):
    import metrics_gap as MG
    tax = load_json(a.taxonomy)
    tax_dir = os.path.dirname(os.path.abspath(a.taxonomy))
    governed = MG.load_governed(a.metrics or MG.find_governed(tax_dir))
    out = a.out or os.path.join(tax_dir, "work", "metrics_gap.md")
    atomic_write_bytes(out, MG.gap_markdown(tax.get("metrics") or [], governed).encode("utf-8"))
    _out({"status": "written", "out": out, "governed_file": bool(governed)})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Plan, record and serve taxonomy reviews.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--mode", required=True, choices=["draft", "drift", "browse", "describe", "health"])
    p.add_argument("--taxonomy", default=os.path.join("taxonomy", CURRENT))
    p.add_argument("--proposals")
    p.add_argument("--consolidated")
    p.add_argument("--db")
    p.add_argument("--decisions")
    p.add_argument("--work", help="health mode: the diagnose --out directory (problems.json + task dirs)")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("record")
    p.add_argument("--review", required=True)
    p.add_argument("--item")
    p.add_argument("--action", choices=["approve", "reject", "amend", "propose", "withdraw", "reopen"])
    p.add_argument("--op")
    p.add_argument("--reason")
    p.add_argument("--submit", action="store_true")
    p.add_argument("--surface", default="terminal", choices=["terminal", "script"])
    p.add_argument("--reviewer")
    p.add_argument("--decisions")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("status")
    p.add_argument("--review", required=True)
    p.add_argument("--decisions")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("adopt")
    p.add_argument("--taxonomy", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--meta-only", action="store_true",
                   help="only record this version in the store's meta (never touches current.json)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--provisional", action="store_true",
                   help="first build: adopt the unreviewed draft as current.json and mark it provisional "
                        "until the first review is applied")
    p.set_defaults(fn=cmd_adopt)
    p = sub.add_parser("serve")
    p.add_argument("--review", required=True)
    p.add_argument("--db")
    p.add_argument("--metrics", help="governed metrics file (default: the one schema/metrics.*.json)")
    p.add_argument("--decisions")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--reviewer")
    p.add_argument("--watch-hint", action="store_true",
                   help="Claude Code will Monitor requests.jsonl; state().live_channel tells the UI to say "
                        "\"Claude is revising…\" instead of \"Queued for next run\"")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("export-md")
    p.add_argument("--review", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--decisions")
    p.set_defaults(fn=cmd_export_md)
    p = sub.add_parser("import-md")
    p.add_argument("--review", required=True)
    p.add_argument("--md", required=True)
    p.add_argument("--submit", action="store_true")
    p.add_argument("--reviewer")
    p.add_argument("--decisions")
    p.set_defaults(fn=cmd_import_md)
    p = sub.add_parser("describe-prep")
    p.add_argument("--taxonomy", default=os.path.join("taxonomy", CURRENT))
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--batches", type=int, default=5)
    p.add_argument("--all", action="store_true", help="include nodes that already have a description")
    p.set_defaults(fn=cmd_describe_prep)
    p = sub.add_parser("diagnose", help="find taxonomy health problems and prepare agent fix tasks")
    p.add_argument("--taxonomy", default=os.path.join("taxonomy", CURRENT))
    p.add_argument("--db", required=True)
    p.add_argument("--out", default=os.path.join("taxonomy", "work", "health"))
    p.add_argument("--metrics")
    p.add_argument("--batches", type=int, default=4)
    p.add_argument("--signals", help="taxonomy_signals.py output (default: <taxonomy dir>/work/signals.json if present)")
    p.add_argument("--sparse-max", type=int, default=2)
    p.add_argument("--overload-factor", type=float, default=2.0)
    p.set_defaults(fn=cmd_diagnose)
    p = sub.add_parser("respond", help="record a revised proposal for a health item's redo request")
    p.add_argument("--review", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--op", required=True)
    p.add_argument("--reason")
    p.add_argument("--decisions")
    p.add_argument("--check", action="store_true", help="dry run: validate the op as respond would, write nothing")
    p.set_defaults(fn=cmd_respond)
    p = sub.add_parser("redo-prep", help="write the one task entry behind a health item, with its redo note")
    p.add_argument("--review", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--request", help="the redo request whose note to use (default: the item's latest open one)")
    p.add_argument("--work", help="the diagnose --out dir (default: recorded in the review)")
    p.add_argument("--out", help="where to write it (default: <task dir>/redo-<item>/)")
    p.set_defaults(fn=cmd_redo_prep)
    p = sub.add_parser("gap", help="write taxonomy/work/metrics_gap.md (ungoverned computable metrics)")
    p.add_argument("--taxonomy", default=os.path.join("taxonomy", CURRENT))
    p.add_argument("--metrics")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_gap)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (D.DecisionLogError, ValueError, FileNotFoundError, FileExistsError) as e:
        _out({"status": "error", "errors": [str(e)]})
        return 2


if __name__ == "__main__":
    sys.exit(main())
