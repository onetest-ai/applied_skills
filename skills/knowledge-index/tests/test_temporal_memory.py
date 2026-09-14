import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from temporal_memory import current_fact, load_ledger, question_status  # noqa: E402


def fixture():
    return json.loads((HERE / "temporal_fixture.json").read_text(encoding="utf-8"))


def store():
    con = sqlite3.connect(":memory:")
    load_ledger(con, fixture())
    return con


def test_latest_explicit_correction_is_current_and_history_is_retained():
    result = current_fact(store(), "project-atlas", "release_date")
    assert result["status"] == "current"
    assert result["value"] == "2025-11-01"
    assert len(result["history"]) == 3
    assert {c["source"] for c in result["citations"]} == {"meeting-2025-09-19.vtt", "meeting-2025-09-20.vtt", "meeting-2025-09-21.vtt"}


def test_as_of_query_returns_what_was_known_on_20_september():
    result = current_fact(store(), "project-atlas", "release_date", "2025-09-20T23:59:59Z")
    assert result["status"] == "current"
    assert result["value"] == "2025-10-15"


def test_late_ingestion_of_an_older_meeting_does_not_change_current_state():
    result = current_fact(store(), "project-atlas", "release_date")
    assert result["value"] == "2025-11-01"


def test_unresolved_contradiction_does_not_choose_a_value():
    result = current_fact(store(), "project-atlas", "approved_budget")
    assert result["status"] == "conflicted"
    assert result["value"] is None
    assert set(result["active_assertion_ids"]) == {"budget-a", "budget-b"}


def test_question_changes_from_open_to_resolved_across_sessions():
    con = store()
    before = question_status(con, "q-atlas-owner", "2025-09-20T23:59:59Z")
    after = question_status(con, "q-atlas-owner")
    assert before["status"] == "open"
    assert after["status"] == "resolved"
    assert after["answer"] == "Marta owns the Atlas rollout."
    assert {c["source"] for c in after["citations"]} == {"meeting-2025-09-20.vtt", "meeting-2025-09-21.vtt"}


def test_similar_but_unlinked_question_stays_open():
    assert question_status(store(), "q-atlas-region")["status"] == "open"


def test_reingestion_is_idempotent_but_changed_id_is_rejected():
    con = store()
    load_ledger(con, fixture())
    assert con.execute("SELECT COUNT(*) FROM memory_assertions").fetchone()[0] == 5
    changed = fixture()
    changed["assertions"][0]["value"] = "2025-12-01"
    try:
        load_ledger(con, changed)
    except ValueError as exc:
        assert "reused with different content" in str(exc)
    else:
        raise AssertionError("changed stable ID must be rejected")


def test_invalid_relation_is_atomic_and_lower_authority_cannot_supersede():
    con = sqlite3.connect(":memory:")
    ledger = {
        "schema_version": "1.0",
        "assertions": [
            {"assertion_id": "old", "entity": "atlas", "predicate": "budget", "value": 10,
             "asserted_at": "2025-09-20T10:00:00Z", "ingested_at": "2025-09-20T10:00:00Z",
             "source": "a.vtt", "segment_id": "a-1", "authority": 10},
            {"assertion_id": "weak", "entity": "atlas", "predicate": "budget", "value": 20,
             "asserted_at": "2025-09-21T10:00:00Z", "ingested_at": "2025-09-21T10:00:00Z",
             "source": "b.vtt", "segment_id": "b-1", "authority": 1, "supersedes": ["old"]},
        ],
    }
    try:
        load_ledger(con, ledger)
    except ValueError as exc:
        assert "equal or higher authority" in str(exc)
    else:
        raise AssertionError("lower-authority assertion must not supersede")
    assert con.execute("SELECT COUNT(*) FROM memory_assertions").fetchone()[0] == 0
