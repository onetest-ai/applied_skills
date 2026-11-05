"""gold_from_judged.py --pool pool_full.json --judged FILE… --out items.jsonl

Join blind-judge answers to pool candidates and write the Task-3 gold format.

- not_work candidates are dropped.
- declined candidates are kept with commitment=declined.
- Items marked duplicate_of are dropped.
- Header line: "# gold v1 <ISO-date> n=<count>".

Validation:
- Runs check_judge on each judged file against its corresponding batch file
  (judged_NN.json → judge_NN.json in the same directory).
- Exits 1 with unjudged=<k> when any pool cid in blind batches is not answered.
- Exits 1 on duplicate cids across files.

CLI:
    python3 gold_from_judged.py --pool pool_full.json
                                 --judged judged_01.json [--judged judged_02.json …]
                                 --out items.jsonl

Importable:
    from gold_from_judged import gold_from_judged
    gold_from_judged(pool_file, judged_files, out_file)
"""
import datetime
import json
import sys
from pathlib import Path

# Ensure taska_lib (and check_judge) are importable
_HERE = Path(__file__).resolve().parent
_RUN_DIR = _HERE.parent / "skills" / "run"
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from check_judge import check as _check_judge


def gold_from_judged(pool_file, judged_files, out_file):
    """Join judged answers to pool candidates and write gold items.

    Parameters
    ----------
    pool_file : Path
        pool_full.json produced by pool.py.
    judged_files : list[Path]
        One or more judged JSON files (each a list of judge answer dicts).
        Each file named judged_NN.json is validated against judge_NN.json
        in the same directory.
    out_file : Path
        Output .jsonl file; will be overwritten.

    Exits with code 1 when:
    - Any verifiable pool cid is unjudged.
    - Duplicate cids appear across judged files.
    """
    pool_file = Path(pool_file)
    out_file = Path(out_file)

    # Load pool: flatten to {cid: candidate}
    pool_data = json.loads(pool_file.read_text(encoding="utf-8"))
    cid_to_candidate = {}
    # Collect all verifiable pool cids (the ones that appear in blind batches)
    verifiable_pool_cids = set()
    for candidates in pool_data.values():
        for c in candidates:
            cid_to_candidate[c["cid"]] = c
            if c.get("verifiable", True):
                verifiable_pool_cids.add(c["cid"])

    # Load judged answers with dedup check across files
    judged_by_cid = {}
    seen_cids = set()
    all_problems = []

    for jf in judged_files:
        jf = Path(jf)
        answers = json.loads(jf.read_text(encoding="utf-8"))

        # Validate against corresponding batch file
        batch_file = jf.parent / jf.name.replace("judged_", "judge_")
        if batch_file.exists():
            batch = json.loads(batch_file.read_text(encoding="utf-8"))
            result = _check_judge(answers, batch)
            if not result["ok"]:
                all_problems.extend(
                    f"{jf.name}: {p}" for p in result["problems"]
                )

        # Collect answers, check for cross-file duplicates
        for ans in answers:
            cid = ans.get("cid")
            if not cid:
                continue
            if cid in seen_cids:
                all_problems.append(f"duplicate cid {cid!r} across judged files")
            else:
                seen_cids.add(cid)
                judged_by_cid[cid] = ans

    # Check coverage: every verifiable pool cid must be judged
    unjudged = sorted(verifiable_pool_cids - set(judged_by_cid))
    if unjudged:
        msg = f"unjudged={len(unjudged)} pool cids not answered: {unjudged[:5]}"
        if all_problems:
            msg += f"; also {len(all_problems)} validation problem(s)"
        print(f"error: {msg}", file=sys.stderr)
        sys.exit(1)

    if all_problems:
        for p in all_problems[:10]:
            print(f"error: {p}", file=sys.stderr)
        sys.exit(1)

    # Build gold items: drop not_work and duplicate_of; keep everything else
    gold_items = []
    for cid in sorted(judged_by_cid):
        ans = judged_by_cid[cid]
        if ans.get("commitment") == "not_work":
            continue
        if ans.get("duplicate_of"):
            continue
        candidate = cid_to_candidate.get(cid)
        if candidate is None:
            print(f"warning: cid {cid!r} in judged output but not in pool; skipping",
                  file=sys.stderr)
            continue

        item = {
            "cid": cid,
            "evidence": candidate["evidence"],
            "quote": candidate["quote"],
            "ts": candidate.get("ts"),
            "speaker": candidate.get("speaker"),
            "offset": candidate.get("offset"),
            "title": ans.get("title"),
            "kind": ans.get("kind"),
            "commitment": ans.get("commitment"),
            "owner": ans.get("owner"),
            "due": ans.get("due"),
            "status": ans.get("status"),
            "client_owed": ans.get("client_owed", False),
            "covered_by": ans.get("covered_by"),
            "note": ans.get("note"),
        }
        gold_items.append(item)

    today = datetime.date.today().isoformat()
    header = f"# gold v1 {today} n={len(gold_items)}"
    lines = [header] + [json.dumps(item, ensure_ascii=False) for item in gold_items]
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return gold_items


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse

    ap = argparse.ArgumentParser(description="Join judged answers to pool and write gold items")
    ap.add_argument("--pool", required=True, metavar="FILE")
    ap.add_argument("--judged", action="append", default=[], metavar="FILE")
    ap.add_argument("--out", required=True, metavar="FILE")
    args = ap.parse_args()

    if not args.judged:
        ap.error("at least one --judged FILE is required")

    items = gold_from_judged(
        Path(args.pool),
        [Path(f) for f in args.judged],
        Path(args.out),
    )
    print(json.dumps({"ok": True, "items": len(items)}))


if __name__ == "__main__":
    main()
