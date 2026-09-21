#!/usr/bin/env python3
"""Taxonomy MERGE — the only writer of taxonomy versions and current.json.

Two ways in:

1. A submitted review (the normal path). taxonomy_review.py plans a review and serves the
   review app; people decide there; this script applies exactly what was submitted:
     taxonomy_merge.py --review taxonomy/reviews/review_<id>.json --apply
   It writes taxonomy_v<N+1>.json + current.json, records every op and its tag migrations in
   history[], and appends an `applied` record to decisions.jsonl. build_graph.py then runs the
   migrations (tags move, never vanish silently).

2. Agent proposals directly (legacy). Default is a DRY RUN that prints the additive diff:
     taxonomy_merge.py --taxonomy taxonomy/current.json --proposals <dir>
   Applying proposals with no human review is refused unless --without-review is passed —
   which agents do ONLY when the user explicitly asked to skip the review. It stays add-only
   and marks history with "without_review": true.

Agents only add. Renames, merges, moves, splits and removals are human decisions made in the
review app (or an imported review Markdown file).
"""
import argparse, difflib, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decisions as D  # noqa: E402
from taxo_io import (CURRENT, atomic_write_bytes, dump_bytes, fingerprint, intent, load_json,  # noqa: E402
                     reviewer_name, sha256_file, utc_now, write_version_and_current)
from taxo_ops import ChangesetError, apply_ops  # noqa: E402


class Refused(Exception):
    pass


def near(name, pool, thresh=0.88):
    """Return an existing label that's an exact/fuzzy duplicate of `name`, else None."""
    from taxo_io import nid
    k = nid(name)
    for p in pool:
        if nid(p) == k:
            return p
    m = difflib.get_close_matches(name, list(pool), n=1, cutoff=thresh)
    return m[0] if m else None


def load_proposals(proposals_dir, skipped_files=None):
    """Proposals from every result_*.json. An unreadable file is reported on stderr (stdout
    carries the diff / JSON contract) and its name appended to `skipped_files` if given."""
    out = []
    for rf in sorted(glob.glob(os.path.join(proposals_dir, "result_*.json"))):
        try:
            with open(rf, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print("skip", rf, e, file=sys.stderr)
            if skipped_files is not None:
                skipped_files.append(rf)
            continue
        out += data.get("proposals", []) if isinstance(data, dict) else []
    return out


def plan_additions(tax, proposals, fuzzy=0.88):
    """Dedup agent proposals against the vocabulary (labels + aliases); attach L2s to parents."""
    it = intent(tax)
    tree = it["tree"]
    l1_set = set(tree)
    l2_pool = {l2 for kids in tree.values() for l2 in kids} | set(it["unassigned_l2"])
    aliases = tax.get("aliases") or {}
    items, by_name, skipped = [], {}, []

    def skip(p, name, reason, kind):
        skipped.append({"name": name, "level": (p.get("level") or "").upper(), "parent": p.get("parent"),
                        "reason": reason, "kind": kind})

    for level in ("L1", "L2"):
        for p in proposals:
            if (p.get("level") or "").upper() != level:
                continue
            name = (p.get("name") or "").strip()
            if not name:
                continue
            al = near(name, set(aliases), fuzzy)
            if al:
                skip(p, name, f"alias of existing '{aliases[al]}'", "alias")
                continue
            dup = near(name, l1_set if level == "L1" else l2_pool, fuzzy)
            if dup:
                skip(p, name, f"dup of {level} '{dup}'", "dup_existing")
                continue
            dup = near(name, {i["name"] for i in items if i["level"] == level}, fuzzy)
            if dup:
                by_name[dup]["sources"].append(p)
                skip(p, name, f"dup of {level} '{dup}'", "dup_new")
                continue
            parent = None
            if level == "L2":
                raw_parent = (p.get("parent") or "").strip()
                new_l1 = {i["name"] for i in items if i["level"] == "L1"}
                parent = near(raw_parent, l1_set | new_l1, fuzzy) if raw_parent else None
                if not parent:
                    skip(p, name, f"L2 parent '{raw_parent or '—'}' not an existing/new L1", "invalid")
                    continue
            item = {"level": level, "name": name, "parent": parent, "sources": [p]}
            items.append(item)
            by_name[name] = item
    return items, skipped


def print_dry_run(label, items, skipped, apply=False, suppressed=()):
    add_l1 = [i["name"] for i in items if i["level"] == "L1"]
    add_l2 = [(i["name"], i["parent"]) for i in items if i["level"] == "L2"]
    print(f"=== taxonomy merge (from {label}) — {'APPLY' if apply else 'DRY RUN'} ===")
    print(f"proposed: +{len(add_l1)} L1, +{len(add_l2)} L2 ; dropped {len(skipped)} dup/invalid")
    for n_ in add_l1:
        print(f"  + L1  {n_}")
    for n_, par in add_l2:
        print(f"  + L2  {n_}   (under {par})")
    for s in skipped[:20]:
        print(f"  · skip {s['name']}  — {s['reason']}")
    for s in suppressed:
        print(f"  · suppressed {s['name']}  — rejected before: {s['reason']} ({s['reviewer']})")


def _added(applied):
    return ([o["name"] for o in applied if o["type"] == "add" and o["level"] == "L1"],
            [[o["name"], o["parent"]] for o in applied if o["type"] == "add" and o["level"] == "L2"])


def apply_review(review_path, decisions_path=None):
    review = load_json(review_path)
    rid = review["review_id"]
    tax_dir = os.path.dirname(os.path.dirname(os.path.abspath(review_path)))
    decisions_path = decisions_path or D.default_path(tax_dir)
    records = D.read(decisions_path)
    st = D.review_state(records, rid)
    base, cur = review["base"], os.path.join(tax_dir, CURRENT)
    if not st["submit"]:
        raise Refused(f"review {rid} is not submitted — finish it in the review app first")
    if st["applied"]:
        raise Refused(f"review {rid} was already applied ({st['applied'].get('out')})")
    if not os.path.exists(base["path"]) or sha256_file(base["path"]) != base["sha256"]:
        raise Refused(f"{base['path']} changed since review {rid} was planned; plan a new review")
    is_draft = review.get("mode") == "draft"
    if is_draft and os.path.exists(cur):
        raise Refused("a ratified taxonomy (current.json) already exists; review changes against it "
                      "in browse or drift mode")
    if not is_draft and os.path.abspath(base["path"]) != os.path.abspath(cur):
        raise Refused(f"review {rid} ({review.get('mode')}) was planned on {base['path']}, not on {cur}; a "
                      f"browse or drift review must be planned on current.json. If this Brain predates "
                      f"current.json, run `taxonomy_review.py adopt --taxonomy <the version the store was "
                      f"built from> --db <db>` first, then plan the review again")
    entries = D.effective_ops(review, records)
    errs = D.authorship_errors(entries)
    if errs:
        raise Refused("; ".join(errs))
    tax = load_json(base["path"])
    try:
        new, migs, applied = apply_ops(tax, [e["op"] for e in entries])
    except ChangesetError as e:
        raise Refused("the changeset is invalid: " + "; ".join(e.errors)) from None
    version = (tax.get("version") or 0) + (1 if (applied or is_draft) else 0)
    if not applied and not is_draft:
        D.append(decisions_path, {"review_id": rid, "action": "applied", "out": None, "version": version,
                                  "sha256": base["sha256"], "reviewer": "taxonomy_merge.py", "surface": "script"})
        return {"status": "no_changes", "review_id": rid, "version": version}
    new["version"] = version
    add_l1, add_l2 = _added(applied)
    new.setdefault("history", []).append({
        "version": version, "review_id": rid, "reviewer": st["submit"].get("reviewer"), "ts": utc_now(),
        "ops": applied, "migrations": migs, "added_l1": add_l1, "added_l2": add_l2,
        "rejected": [r["fingerprint"] for r in st["latest"].values()
                     if r["action"] == "reject" and r.get("fingerprint")]})
    try:
        out, sha = write_version_and_current(tax_dir, new)
    except FileExistsError:
        existing = os.path.join(tax_dir, f"taxonomy_v{version}.json")
        raise Refused(f"{existing} already exists but {cur} was not updated to match it; "
                      "the taxonomy directory is in an inconsistent state — investigate before retrying") from None
    D.append(decisions_path, {"review_id": rid, "action": "applied", "out": out, "version": version, "sha256": sha,
                              "reviewer": "taxonomy_merge.py", "surface": "script"})
    return {"status": "applied", "review_id": rid, "out": out, "version": version, "ops": len(applied),
            "migrations": len(migs)}


def legacy_apply(a, tax, items):
    """Legacy add-only apply. Returns a process exit code (0 ok, 2 refused)."""
    tax_dir = os.path.dirname(os.path.abspath(a.taxonomy))
    cur = os.path.join(tax_dir, CURRENT)
    if os.path.exists(cur) and os.path.abspath(a.taxonomy) != os.path.abspath(cur):
        print(f"REFUSED: {cur} exists; run this against {cur}, not {a.taxonomy} — a stale taxonomy file "
              f"would roll current.json back.", file=sys.stderr)
        return 2
    if not items:
        print("\nnothing to apply (all proposals were duplicates, invalid, or suppressed).")
        return 0

    version = (tax.get("version") or 0) + 1
    ops = [{"type": "add", "level": i["level"], "name": i["name"], "parent": i["parent"]} for i in items]
    try:
        new, _, applied = apply_ops(tax, ops)
    except ChangesetError as e:
        print("REFUSED: the changeset is invalid: " + "; ".join(e.errors), file=sys.stderr)
        return 2
    new["version"] = version
    add_l1, add_l2 = _added(applied)
    new.setdefault("history", []).append({
        "version": version, "added_l1": add_l1, "added_l2": add_l2, "from": os.path.abspath(a.proposals),
        "review_id": None, "reviewer": reviewer_name(a.reviewer), "ts": utc_now(), "without_review": True,
        "ops": applied, "migrations": []})

    out = a.out or os.path.join(tax_dir, f"taxonomy_v{version}.json")
    out_dir = os.path.dirname(os.path.abspath(out)) or tax_dir
    if out_dir == tax_dir:
        expected = f"taxonomy_v{version}.json"
        if os.path.basename(out) != expected:
            print(f"REFUSED: writing into {tax_dir} must produce {expected} (ratified versions are named and "
                  f"immutable); got {out}", file=sys.stderr)
            return 2
        try:
            out, _sha = write_version_and_current(tax_dir, new)
        except FileExistsError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        next_taxonomy = cur
    else:
        if os.path.exists(out):
            print(f"REFUSED: {out} already exists", file=sys.stderr)
            return 2
        try:
            atomic_write_bytes(out, dump_bytes(new))
        except FileExistsError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        next_taxonomy = out
    print(f"\napplied WITHOUT REVIEW -> {out} (version {version}). NEXT: build_graph.py --taxonomy "
          f"{next_taxonomy} --db <db>; then reclassify affected chunks.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy")
    ap.add_argument("--proposals")
    ap.add_argument("--review", help="apply a submitted review (taxonomy/reviews/review_<id>.json)")
    ap.add_argument("--decisions", help="decisions.jsonl (default: beside the taxonomy)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--fuzzy", type=float, default=0.88)
    ap.add_argument("--without-review", action="store_true",
                    help="legacy add-only apply with no human review — ONLY when the user explicitly asks")
    ap.add_argument("--reviewer")
    a = ap.parse_args()

    if a.review:
        if not a.apply:
            print("--review needs --apply (inspect reviews with taxonomy_review.py status)", file=sys.stderr)
            return 2
        try:
            res = apply_review(a.review, a.decisions)
        except Refused as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        print(json.dumps(res))
        if res["status"] == "applied":
            tax_dir = os.path.dirname(res["out"])
            print(f"NEXT: build_graph.py --taxonomy {os.path.join(tax_dir, CURRENT)} --db <db>; then reclassify "
                  f"the chunk ids in {os.path.join(tax_dir, 'work', 'reclassify.json')} (classify_prep --chunks … "
                  f"→ agents → classify_write --reclassify-done …)", file=sys.stderr)
        return 0

    if not (a.taxonomy and a.proposals):
        ap.error("--taxonomy and --proposals are required without --review")
    tax = load_json(a.taxonomy)
    items, skipped = plan_additions(tax, load_proposals(a.proposals), a.fuzzy)
    decisions_path = a.decisions or D.default_path(os.path.dirname(os.path.abspath(a.taxonomy)))
    rejections = D.standing_rejections(D.read(decisions_path))
    kept, suppressed = [], []
    for i in items:
        fp = fingerprint({"type": "add", "level": i["level"], "name": i["name"], "parent": i["parent"]})
        rec = D.match_rejection(fp, rejections, a.fuzzy)
        if rec:
            suppressed.append({"name": i["name"], "reason": rec.get("reason"), "reviewer": rec.get("reviewer")})
        else:
            kept.append(i)
    print_dry_run(a.proposals, kept, skipped, apply=a.apply and a.without_review, suppressed=suppressed)
    if not a.apply:
        print("\nreview these in the review app: taxonomy_review.py plan --mode drift … then serve")
        return 0
    if not a.without_review:
        print("REFUSED: --apply without --review applies agent proposals with no human review. Use "
              "taxonomy_review.py (plan → serve → taxonomy_merge --review … --apply), or pass --without-review "
              "if the user explicitly asked to skip the review.", file=sys.stderr)
        return 2
    return legacy_apply(a, tax, kept)


if __name__ == "__main__":
    sys.exit(main())
