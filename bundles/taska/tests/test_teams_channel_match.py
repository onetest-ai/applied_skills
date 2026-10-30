"""Unit tests for teams_channel.match_task — invented data only, no network."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.sources.teams_channel import normalize_message, match_task

OPEN_TASKS = [
    {"id": "T001", "title": "Prepare kick-off presentation"},
    {"id": "T002", "title": "Compile stakeholder contact list"},
    {"id": "T003", "title": "Schedule retrospective meeting"},
    {"id": "T004", "title": "Draft project charter"},
]

_BASE_MSG = {
    "id": "1728000000001",
    "replyToId": None,
    "createdDateTime": "2026-10-15T10:00:00.000Z",
    "from": {"user": {"displayName": "Sam Lee"}},
    "body": {"contentType": "html", "content": ""},
    "attachments": [],
}


def _msg(body_html="", attachments=None):
    return normalize_message(
        {**_BASE_MSG, "body": {"contentType": "html", "content": body_html},
         "attachments": attachments or []},
        channel_name="General",
    )


# ── file-stem matching ────────────────────────────────────────────────────────

def test_pptx_matches_kickoff_task():
    msg = _msg(attachments=[{"id": "1", "name": "kickoff-deck.pptx",
                              "contentUrl": "https://example.com/kickoff-deck.pptx"}])
    result = match_task(msg, OPEN_TASKS)
    assert result is not None
    assert result["task_id"] == "T001"
    assert result["matched_file"] == "kickoff-deck.pptx"


def test_xlsx_matches_stakeholder_task():
    msg = _msg(attachments=[{"id": "2", "name": "stakeholders.xlsx",
                              "contentUrl": "https://example.com/stakeholders.xlsx"}])
    result = match_task(msg, OPEN_TASKS)
    assert result is not None
    assert result["task_id"] == "T002"


def test_unrelated_file_returns_none():
    msg = _msg(attachments=[{"id": "3", "name": "random-photo.jpg",
                              "contentUrl": "https://example.com/photo.jpg"}])
    result = match_task(msg, OPEN_TASKS)
    assert result is None


# ── body-text matching ────────────────────────────────────────────────────────

def test_body_text_matches_when_file_name_generic():
    # Generic file name, but body says what it is
    msg = _msg(
        body_html="<p>Here is the updated project charter for your review.</p>",
        attachments=[{"id": "4", "name": "document-v3.docx",
                      "contentUrl": "https://example.com/doc.docx"}],
    )
    result = match_task(msg, OPEN_TASKS)
    assert result is not None
    assert result["task_id"] == "T004"


def test_body_only_no_files_can_still_match():
    msg = _msg(body_html="<p>Retrospective session scheduled for next Friday.</p>")
    result = match_task(msg, OPEN_TASKS)
    assert result is not None
    assert result["task_id"] == "T003"
    assert result["matched_file"] is None   # body was the signal


# ── threshold ────────────────────────────────────────────────────────────────

def test_high_threshold_rejects_weak_match():
    msg = _msg(attachments=[{"id": "5", "name": "notes.txt",
                              "contentUrl": "https://example.com/notes.txt"}])
    assert match_task(msg, OPEN_TASKS, threshold=0.9) is None


def test_score_is_between_0_and_1():
    msg = _msg(attachments=[{"id": "6", "name": "kickoff-deck.pptx",
                              "contentUrl": "https://example.com/kickoff-deck.pptx"}])
    result = match_task(msg, OPEN_TASKS)
    assert result is not None
    assert 0.0 < result["score"] <= 1.0


def test_empty_task_list_returns_none():
    msg = _msg(attachments=[{"id": "7", "name": "kickoff-deck.pptx",
                              "contentUrl": "https://example.com/kickoff-deck.pptx"}])
    assert match_task(msg, []) is None


def test_tasks_missing_title_skipped_safely():
    msg = _msg(attachments=[{"id": "8", "name": "kickoff-deck.pptx",
                              "contentUrl": "https://example.com/kickoff-deck.pptx"}])
    tasks_with_blank = [{"id": "TX", "title": ""}, *OPEN_TASKS]
    result = match_task(msg, tasks_with_blank)
    assert result is not None
    assert result["task_id"] != "TX"
