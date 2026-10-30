"""Answer-key tooling (r2 plan, R12): merge the judged set with the user's answers, collapse near-duplicates, write a new
key version without ever overwriting one, and a labelling page that asks the two questions separately."""
import json
import sys
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
sys.path.insert(0, str(EVALS))
import key_tools as K  # noqa: E402

J = [{"cid": "m1-001", "commitment": "committed", "kind": "task", "title": "Book the yard visit", "owner": None,
      "covered_by": None, "note": "Nora books it"},
     {"cid": "m1-002", "commitment": "committed", "kind": "task", "title": "Schedule the dock call", "owner": "Nora Quill",
      "covered_by": None, "note": "a scheduling ask"},
     {"cid": "m1-003", "commitment": "not_work", "kind": None, "title": None, "owner": None, "covered_by": None,
      "note": "small talk"}]


def test_merge_order_last_wins():
    recheck = [{"cid": "m1-002", "verdict": "not_work", "rule": "Scheduling", "note": "scheduling a call is not work"}]
    user = {"m1-003": {"kind": "proposed", "title": "Chase the forklift list"}, "m1-001": {"kind": "committed", "owner": "Ravi Tamsin"}}
    out = {j["cid"]: j for j in K.merge_key(J, recheck, user)}
    assert out["m1-002"]["commitment"] == "not_work"                       # recheck drop
    assert out["m1-003"]["commitment"] == "proposed" and out["m1-003"]["title"] == "Chase the forklift list"  # user wins
    assert out["m1-001"]["owner"] == "Ravi Tamsin" and out["m1-001"]["source"] == "user"
    assert out["m1-002"]["source"] == "judge"


def test_near_duplicates_collapse():
    gold = [{"cid": "a", "evidence": "m1", "quote": "first wording", "covered_by": "T101", "title": "x"},
            {"cid": "b", "evidence": "m1", "quote": "second wording", "covered_by": "T101", "title": "y"},
            {"cid": "c", "evidence": "m2", "quote": "other meeting", "covered_by": "T101", "title": "z"},
            {"cid": "d", "evidence": "m1", "quote": "uncovered", "covered_by": None, "title": "w"}]
    out = K.collapse_near_duplicates(gold)
    assert [g["cid"] for g in out] == ["a", "c", "d"]
    assert out[0]["quotes"] == ["first wording", "second wording"] and out[0]["merged_cids"] == ["b"]


def test_new_key_version_never_overwrites(tmp_path):
    items = [{"cid": "a", "evidence": "m1", "quote": "q"}]
    p = K.write_version(tmp_path, items, "v2", "2026-03-06", "added the Mar 2 meetings")
    assert p.name == "items.v2.jsonl" and p.read_text().splitlines()[0].startswith("# gold v2 2026-03-06 n=1 sha256=")
    assert "v2" in (tmp_path / "CHANGELOG.md").read_text()
    with pytest.raises(FileExistsError):
        K.write_version(tmp_path, items, "v2", "2026-03-07", "again")


def test_label_page_asks_work_then_new_and_carries_a_stamp():
    page = K.label_page([{"cid": "a", "meeting": "Daily sync", "date": "2026-03-02", "turns": [], "quote": "q",
                          "speaker": "Nora", "ts": "00:01:00", "existing": {"id": "T101", "title": "Book the yard visit"}}],
                        stamp="abc123")
    assert page.index("Is it work?") < page.index("Is it new?")
    assert "taska already has: T101" in page
    assert 'const STAMP = "abc123"' in page


def test_stale_page_refuses_to_save():
    page = K.label_page([], stamp="abc123")
    assert "items.json" in page and "STAMP" in page and "older page" in page
