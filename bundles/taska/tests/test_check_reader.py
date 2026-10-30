"""Tests for check_reader.py — all meeting data is invented (Harbor Logistics world)."""
import json
import sys
from pathlib import Path

import pytest

TASKA_ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = TASKA_ROOT / "evals"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

from check_reader import check  # noqa: E402

# ── shared meeting fixture ────────────────────────────────────────────────────

# Harbor Logistics daily sync — invented names and content.
MEETING_TEXT = """\
# Daily sync — 2026-03-05

**Meeting date:** 2026-03-05

---

00:02:10 | Nora Quill: I will draft the warehouse onboarding checklist and have it ready for Opal by Thursday.
00:05:22 | Ravi Tamsin: We need to submit the inventory count to Opal by end of the week so she can review it before Monday.
00:08:33 | Nora Quill: Can you check with the yard team about the new stock system rollout schedule, Ravi?
00:11:45 | Ravi Tamsin: Sure, I will follow up with the yard team today and send you a summary by tomorrow morning.
00:14:02 | Opal Brennick: We also need someone to prepare the client-facing progress slides for the Thursday review meeting.
00:17:19 | Nora Quill: I can take that on. I will prepare the progress slides and share them with the full team by Wednesday evening.
"""

EV_ID = "2026-03-05_meetings_ab12cd34"


def _good_item() -> dict:
    """One valid item whose quote appears verbatim in MEETING_TEXT."""
    return {
        "quote": "I will draft the warehouse onboarding checklist and have it ready for Opal by Thursday",
        "ts": "00:02:10",
        "speaker": "Nora Quill",
        "title": "Draft warehouse onboarding checklist",
        "kind": "task",
        "commitment": "committed",
        "owner": "Nora Quill",
        "due": "2026-03-06",
        "status": "open",
        "client_owed": False,
        "covered_by": None,
        "note": "Nora committed to drafting the onboarding checklist before the client review on Thursday",
    }


def _output(ev_id: str, items: list, empty_reason: str = "") -> list:
    entry: dict = {"evidence": ev_id, "items": items}
    if empty_reason:
        entry["empty_reason"] = empty_reason
    return [entry]


def _write_meeting(tmp_path: Path) -> Path:
    p = tmp_path / f"{EV_ID}.md"
    p.write_text(MEETING_TEXT, encoding="utf-8")
    return p


# ── tests ─────────────────────────────────────────────────────────────────────


def test_valid_output_ok(tmp_path):
    _write_meeting(tmp_path)
    result = check(_output(EV_ID, [_good_item()]), tmp_path)
    assert result["ok"] is True
    assert result["problems"] == []
    assert result["meetings"] == 1
    assert result["items"] == 1


def test_non_verbatim_quote_rejected(tmp_path):
    _write_meeting(tmp_path)
    item = _good_item()
    # This text was never spoken in the meeting.
    item["quote"] = "I will prepare a comprehensive warehouse onboarding checklist and submit it to Opal"
    result = check(_output(EV_ID, [item]), tmp_path)
    assert result["ok"] is False
    assert any("verbatim" in p for p in result["problems"])


def test_stock_notes_rejected(tmp_path):
    """Three different items all share the same boilerplate note → rejected."""
    _write_meeting(tmp_path)
    stock_note = "Nora committed to drafting the onboarding checklist before the client review on Thursday"
    quotes = [
        "I will draft the warehouse onboarding checklist and have it ready for Opal by Thursday",
        "We need to submit the inventory count to Opal by end of the week so she can review it before Monday",
        "I will follow up with the yard team today and send you a summary by tomorrow morning",
    ]
    items = []
    for q in quotes:
        item = _good_item()
        item["quote"] = q
        item["note"] = stock_note
        items.append(item)
    result = check(_output(EV_ID, items), tmp_path)
    assert result["ok"] is False
    assert any("note reused" in p for p in result["problems"])


def test_note_reused_three_times_rejected(tmp_path):
    """Exactly three items share one note — the reuse limit (> 2) is triggered."""
    _write_meeting(tmp_path)
    repeated_note = "Ravi agreed to follow up with the yard team and report back to Nora by tomorrow"
    quotes = [
        "I will draft the warehouse onboarding checklist and have it ready for Opal by Thursday",
        "We need to submit the inventory count to Opal by end of the week so she can review it before Monday",
        "I will follow up with the yard team today and send you a summary by tomorrow morning",
    ]
    items = []
    for q in quotes:
        item = _good_item()
        item["quote"] = q
        item["note"] = repeated_note
        items.append(item)
    result = check(_output(EV_ID, items), tmp_path)
    assert result["ok"] is False
    assert any("note reused" in p for p in result["problems"])


def test_too_sparse_rejected(tmp_path):
    """Zero items for a meeting with content → density check fails (minimum 1 required)."""
    _write_meeting(tmp_path)
    result = check(_output(EV_ID, []), tmp_path)
    assert result["ok"] is False
    assert any("sparse" in p for p in result["problems"])


def test_empty_reason_allows_quiet_meeting(tmp_path):
    """empty_reason suppresses the density check; zero items is acceptable."""
    _write_meeting(tmp_path)
    result = check(
        _output(
            EV_ID,
            [],
            empty_reason="No commitments or decisions were made in this brief logistics check-in.",
        ),
        tmp_path,
    )
    assert result["ok"] is True
    assert result["problems"] == []


def test_bad_due_format_rejected(tmp_path):
    """Due date that is not YYYY-MM-DD is rejected."""
    _write_meeting(tmp_path)
    item = _good_item()
    item["due"] = "March 6, 2026"  # human-readable, not ISO
    result = check(_output(EV_ID, [item]), tmp_path)
    assert result["ok"] is False
    assert any("due" in p for p in result["problems"])


def test_bad_kind_rejected(tmp_path):
    """A kind value outside the allowed set is rejected."""
    _write_meeting(tmp_path)
    item = _good_item()
    item["kind"] = "action"  # not in {task, story, decision, risk}
    result = check(_output(EV_ID, [item]), tmp_path)
    assert result["ok"] is False
    assert any("kind" in p for p in result["problems"])


def test_short_note_rejected(tmp_path):
    """A note with fewer than 8 words is rejected."""
    _write_meeting(tmp_path)
    item = _good_item()
    item["note"] = "Too short"  # 2 words
    result = check(_output(EV_ID, [item]), tmp_path)
    assert result["ok"] is False
    assert any("note too short" in p for p in result["problems"])


def test_missing_field_rejected(tmp_path):
    """An item missing a required field is rejected."""
    _write_meeting(tmp_path)
    item = _good_item()
    del item["owner"]  # remove a required field
    result = check(_output(EV_ID, [item]), tmp_path)
    assert result["ok"] is False
    assert any("missing" in p for p in result["problems"])
