"""items.csv: one row per key item and bucket (plus each invented node), so a person reads what was asked, what
was expected, what the run produced and how it was graded, without bucket codes. Invented data only."""
import csv
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import items_csv as X  # noqa: E402

ROW = {"key_id": "c01", "bucket": "B13", "bucket_name": "Decisions and risks found", "meeting": "2026-03-02_meetings_ab12cd34",
       "ts": "00:12:30", "speaker": "Opal Rivers", "quote": 'We said "lock it", then, on Friday,\nwe locked it in', "expected_kind": "decision",
       "expected_title": "Lock the berth plan", "expected_owner": "", "expected_due": "", "expected_status": "", "commitment": "",
       "verdict": "miss", "why": "no matching decision/risk node", "run_node": "", "run_kind": "", "run_title": "", "run_owner": "",
       "run_due": "", "run_status": ""}
META = {"key_version": "v1", "key_sha": "022b0f748105", "code_version": "786ed6c4b1", "record": "20261031-154912-2"}


def test_items_csv_round_trips_commas_quotes_and_newlines(tmp_path):
    out = tmp_path / "items.csv"
    X.write([ROW], META, out)
    with open(out, newline="", encoding="utf-8") as f:
        back = list(csv.DictReader(f))
    assert len(back) == 1 and back[0]["quote"] == ROW["quote"] and back[0]["bucket_name"] == "Decisions and risks found"
    assert list(back[0]) == X.COLUMNS


def test_items_csv_names_the_judge_and_the_grader(tmp_path):
    out = tmp_path / "items.csv"
    X.write([ROW], META, out)
    with open(out, newline="", encoding="utf-8") as f:
        r = next(csv.DictReader(f))
    assert "READER.md" in r["key_built_by"] and "JUDGE.md" in r["key_built_by"] and "sha256:" in r["key_built_by"]
    assert "evalscore" in r["graded_by"] and "code" in r["graded_by"] and r["record"] == "20261031-154912-2" and r["key_version"] == "v1"
