"""evals/bank.py grow: a bank version from eval-labels/1 files. Invented data only."""
import json
import subprocess
import sys

from conftest import TASKA_ROOT
from test_bank_grade import MEET, S1, S2, S3, S4, project, span, task

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402

TASK = MEET.rsplit("_", 1)[1]
NONE = {"added_must_create": [], "added_must_not": [], "dropped": [], "modified": [], "problems": []}


def setup(tmp_path):
    p = project(tmp_path / "proj")
    t = task(copy=p)
    t["must_not_create"][0]["id"] = f"{TASK}-n01"
    t["must_create"].append({"id": "k-002", "span": span(S3), "quote": S3, "kind": "task", "commitment": "committed",
                             "owner": "Ravi Tamsin", "caps": ["committed", "owners"], "source": "invented"})
    return p, B.write_bank(tmp_path / "bank", [t], "v1", "2026-11-03", "base")


def labels(tmp_path, answers, name="l.json"):
    f = tmp_path / name
    f.write_text(json.dumps({"schema": "eval-labels/1", "stamp": "rec1-abcd1234", "record": "rec1", "key_version": "v1",
                             "key_sha": "x", "saved_at": "2026-11-03T10:00:00", "answers": answers}))
    return f


def node(action, quote, **kw):
    return {"action": action, "quote": quote} | kw


def test_add_becomes_a_must_create_at_the_quote(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {f"node:{MEET}:N01": node("add", S4, kind="task", commitment="proposed",
                                                     fields={"owner": "Opal Brennick"})})
    tasks, diff = B.grow(bank, [lab], p)
    assert diff["added_must_create"] == [f"{TASK}-x01"] and diff["problems"] == []
    m = next(e for e in tasks[0]["must_create"] if e["id"] == f"{TASK}-x01")
    assert m["span"] == span(S4) and m["quote"] == S4 and m["source"] == "labels:rec1-abcd1234"
    assert (m["kind"], m["commitment"], m["owner"]) == ("task", "proposed", "Opal Brennick")
    B.write_bank(tmp_path / "bank", tasks, "v2", "2026-11-04", "grown")


def test_not_work_becomes_a_must_not_with_its_reason_and_numbering_continues(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {f"node:{MEET}:N01": node("not_work", S4, reason="client_own")})
    tasks, diff = B.grow(bank, [lab], p)
    assert diff["added_must_not"] == [f"{TASK}-n02"] and diff["problems"] == []
    m = tasks[0]["must_not_create"][-1]
    assert m["why"] == "client_own" and m["span"] == span(S4) and m["caps"] == ["not_work"]


def test_other_reasons_map_to_not_work_and_people_to_people(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {f"node:{MEET}:N01": node("not_work", S4, reason="vague"),
                            f"node:{MEET}:N02": node("not_work", " and then people talked about the weather", reason="people")})
    tasks, diff = B.grow(bank, [lab], p)
    got = {e["why"]: e["caps"] for e in tasks[0]["must_not_create"] if e["source"].startswith("labels:")}
    assert got["not_work"] == ["not_work"] and got["people"] == ["people"]


def test_duplicate_judge_keep_do_nothing(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {f"node:{MEET}:N01": node("duplicate", S4, dup_of="k-001"),
                            "k-001": {"action": "keep"}, "judge:q1:a": {"action": "agree", "expected": "yes"}})
    tasks, diff = B.grow(bank, [lab], p)
    assert tasks == B.load_bank(bank)[1] and diff == NONE


def test_drop_removes_the_check_and_modify_updates_fields(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {"k-001": {"action": "drop", "reason": "vague"},
                            "k-002": {"action": "modify", "fields": {"title": "t", "kind": "decision", "owner": "Nora Quill"}}})
    tasks, diff = B.grow(bank, [lab], p)
    assert diff["dropped"] == ["k-001"] and diff["modified"] == ["k-002"]
    (m,) = tasks[0]["must_create"]
    assert m["id"] == "k-002" and m["kind"] == "decision" and m["owner"] == "Nora Quill" and m["span"] == span(S3)
    assert m["caps"] == ["committed", "decisions", "owners"]


def test_unlocatable_quote_is_a_problem_and_nothing_is_written(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {f"node:{MEET}:N01": node("add", "Nobody ever said this sentence at all", kind="task", commitment="committed")})
    tasks, diff = B.grow(bank, [lab], p)
    assert tasks == B.load_bank(bank)[1] and diff["added_must_create"] == []
    assert any("N01" in x for x in diff["problems"])


def test_overlap_with_the_opposite_check_lists_both_and_writes_neither(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {f"node:{MEET}:N01": node("not_work", S3, reason="vague")})
    tasks, diff = B.grow(bank, [lab], p)
    assert diff["added_must_not"] == [] and len(diff["problems"]) == 1 and "k-002" in diff["problems"][0]
    assert len(tasks[0]["must_not_create"]) == 1


def test_new_against_new_overlap_writes_neither(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {f"node:{MEET}:N01": node("add", S4, kind="task", commitment="committed"),
                            f"node:{MEET}:N02": node("not_work", S4, reason="vague")})
    tasks, diff = B.grow(bank, [lab], p)
    assert diff["added_must_create"] == [] and diff["added_must_not"] == [] and len(diff["problems"]) == 1


def test_a_span_already_present_is_skipped_silently(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {f"node:{MEET}:N01": node("add", S1, kind="task", commitment="committed"),
                            f"node:{MEET}:N02": node("not_work", S2, reason="brainstorm")})
    tasks, diff = B.grow(bank, [lab], p)
    assert diff == NONE and tasks == B.load_bank(bank)[1]


def test_a_meeting_without_a_task_gets_a_new_one(tmp_path):
    p, bank = setup(tmp_path)
    other = "2026-03-03_meetings_ffff0000"
    from taska_lib import config as C, store as S
    S.write_evidence(C.load(p).store, {"id": other, "kind": "meeting", "at": "2026-03-03T12:00:00", "title": "Other",
                                       "source": "meetings", "text": S4 + " and that is all we said today."})
    lab = labels(tmp_path, {f"node:{other}:N01": node("add", S4, kind="task", commitment="committed")})
    tasks, diff = B.grow(bank, [lab], p)
    t = next(x for x in tasks if x["evidence"] == other)
    assert t["task"] == "ffff0000" and diff["added_must_create"] == ["ffff0000-x01"]
    assert set(t["outcome"]) == set(B.OUTCOMES) and t["budget"] == {"cost_usd": None, "minutes": None}
    B.write_bank(tmp_path / "bank", tasks, "v2", "2026-11-04", "grown")


def test_cli_dry_run_prints_the_diff_and_writes_nothing_then_writes_a_new_version(tmp_path):
    p, bank = setup(tmp_path)
    lab = labels(tmp_path, {"k-001": {"action": "drop"}})
    cmd = [sys.executable, str(EVALS / "bank.py"), "grow", "--bank", str(bank), "--labels", str(lab), "--project", str(p),
           "--version", "v2", "--note", "n"]
    r = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["dropped"] == ["k-001"]
    assert not (bank.parent / "bank.v2.jsonl").exists()
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0 and (bank.parent / "bank.v2.jsonl").exists()
    assert len(B.load_bank(bank.parent / "bank.v2.jsonl")[1][0]["must_create"]) == 1
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 2
