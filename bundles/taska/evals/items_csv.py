"""items.csv for an eval record: one row per key item and bucket, plus each node the run created in key meetings
(precision), with what was asked (the quote), what was expected, what the run produced, the verdict, and how the key
was built and the run graded. Read by a person in a spreadsheet: no bucket codes needed.

  python3 items_csv.py --store RUN/.taska --key items.jsonl --meta meta.json --out items.csv"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS.parent / "skills" / "run"))

COLUMNS = ["key_id", "bucket", "bucket_name", "meeting", "ts", "speaker", "quote", "expected_kind", "expected_title",
           "expected_owner", "expected_due", "expected_status", "commitment", "verdict", "why", "run_node", "run_kind",
           "run_title", "run_owner", "run_due", "run_status", "key_version", "key_sha", "key_built_by", "graded_by",
           "code_version", "record"]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def provenance():
    """How the key was built and how a run is graded, with the hashes of the prompts in force."""
    built = (f"independent readers (evals/READER.md sha256:{_sha(EVALS / 'READER.md')}), judged (evals/JUDGE.md "
             f"sha256:{_sha(EVALS / 'JUDGE.md')}), adjudicated and checked against the owner's labels")
    graded = ("code, not a model: taska_lib/evalscore.py matches each key item to a node by quote containment in the same "
              "meeting and kind; owner, due and status compared field by field")
    return built, graded


def _text(v):
    """A cell a spreadsheet shows as text: one starting with = + - @ (or a tab or CR) would run as a formula."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def write(rows, meta, out):
    built, graded = provenance()
    extra = {"key_version": meta.get("key_version", ""), "key_sha": (meta.get("key_sha") or "")[:12], "key_built_by": built,
             "graded_by": graded, "code_version": meta.get("code_version", ""), "record": meta.get("record", "")}
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow({c: _text(v) for c, v in {**{c: r.get(c, "") for c in COLUMNS}, **extra}.items()})
    return out


def main():
    from taska_lib import evalscore as E
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True); ap.add_argument("--key", required=True)
    ap.add_argument("--meta", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    meta = json.loads(Path(a.meta).read_text())
    head, gold = E.load_gold(a.key)
    meta = {"key_version": head.get("version", ""), "key_sha": head.get("sha256", ""), **meta}
    result = E.score(Path(a.store), gold)
    write(result["rows"], meta, a.out)
    print(json.dumps({"out": a.out, "rows": len(result["rows"])}))


if __name__ == "__main__":
    main()
