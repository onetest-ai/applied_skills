"""evals/bank.py: the per-meeting task bank is an immutable, fingerprinted file. Invented data only."""
import sys
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
sys.path.insert(0, str(EVALS))
import bank as B  # noqa: E402

TASK = {"task": "7a3c91e5", "evidence": "2026-03-02_meetings_7a3c91e5", "transcript_sha": "ab12cd34",
        "must_create": [{"id": "7a3c91e5-001", "span": [0, 40], "quote": "Nora will draft the onboarding checklist",
                         "kind": "task", "commitment": "committed", "owner": "Nora Quill", "caps": ["committed"],
                         "source": "key v1"}],
        "must_not_create": [{"id": "7a3c91e5-n01", "span": [41, 90], "quote": "we could maybe rebrand the dock",
                             "why": "brainstorm", "caps": ["not_work"], "source": "invented"}],
        "outcome": ["processed", "decisions_read", "no_people_matter", "pinned_untouched"],
        "budget": {"cost_usd": 1.5, "minutes": 12}}


def test_a_bank_is_written_once_and_read_back_with_its_header(tmp_path):
    p = B.write_bank(tmp_path, [TASK], "v1", "2026-03-03", "invented")
    head, tasks = B.load_bank(p)
    assert p.name == "bank.v1.jsonl" and head["version"] == "v1" and head["n"] == 1 and tasks == [TASK]
    assert "v1 (2026-03-03): n=1" in (tmp_path / "CHANGELOG.md").read_text()
    with pytest.raises(FileExistsError):
        B.write_bank(tmp_path, [TASK], "v1", "2026-03-03", "again")


def test_an_edited_bank_body_is_refused(tmp_path):
    p = B.write_bank(tmp_path, [TASK], "v1", "2026-03-03", "invented")
    p.write_text(p.read_text().replace("Nora", "Ravi"))
    with pytest.raises(ValueError, match="sha256"):
        B.load_bank(p)


def test_a_task_with_an_unknown_why_or_outcome_is_refused(tmp_path):
    bad = dict(TASK, outcome=["processed", "happy"])
    with pytest.raises(ValueError, match="happy"):
        B.write_bank(tmp_path, [bad], "v1", "2026-03-03", "x")
    bad = dict(TASK, must_not_create=[{"span": [1, 5], "why": "boredom"}])
    with pytest.raises(ValueError, match="boredom"):
        B.write_bank(tmp_path, [bad], "v1", "2026-03-03", "x")


def test_a_bad_span_is_refused(tmp_path):
    bad = dict(TASK, must_create=[{"span": [40, 40], "title": "x"}])
    with pytest.raises(ValueError, match="span"):
        B.write_bank(tmp_path, [bad], "v1", "2026-03-03", "x")
    assert not list(tmp_path.glob("bank.*"))


def test_a_gold_file_is_not_a_bank(tmp_path):
    p = tmp_path / "bank.v1.jsonl"
    p.write_text("# gold v1 2026-03-03 n=0 sha256=x\n")
    with pytest.raises(ValueError, match="bank"):
        B.load_bank(p)


def test_constants():
    assert B.CHECK_TYPES == ("must_create", "must_not_create", "no_duplicate", "outcome")
    assert B.WHY == ("not_work", "brainstorm", "client_own", "people")


def test_a_quote_with_a_unicode_line_separator_round_trips(tmp_path):
    t = dict(TASK, must_create=[dict(TASK["must_create"][0], quote="Nora will\u2028draft it\u0085now")])
    _, tasks = B.load_bank(B.write_bank(tmp_path, [t], "v1", "2026-03-03", "x"))
    assert tasks == [t]


def test_a_new_folder_is_created(tmp_path):
    p = B.write_bank(tmp_path / "evals" / "bank", [TASK], "v1", "2026-03-03", "x")
    assert p.exists()


def test_errors_name_the_task(tmp_path):
    with pytest.raises(ValueError, match="7a3c91e5"):
        B.write_bank(tmp_path, [dict(TASK, outcome=["happy"])], "v1", "2026-03-03", "x")
