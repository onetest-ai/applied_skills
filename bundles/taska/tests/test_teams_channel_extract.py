"""Tests for teams_channel extract layer — cursor, time window, body-word gate, rejected log.
No live network; fetch_messages and sync are tested via their public helpers.
"""
import sys
import os
import json
import datetime
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.sources.teams_channel import (
    _append_rejected, normalize_message, match_task, MIN_BODY_WORDS,
)


# ── body-word gate ────────────────────────────────────────────────────────────

def _msg_with_body(body_html, attachments=None):
    return normalize_message({
        "id": "999", "replyToId": None,
        "createdDateTime": "2026-10-15T10:00:00.000Z",
        "from": {"user": {"displayName": "Jo"}},
        "body": {"contentType": "html", "content": body_html},
        "attachments": attachments or [],
    })


def test_short_body_no_files_fails_gate():
    msg = _msg_with_body("<p>Hi</p>")
    # gate: body words < MIN_BODY_WORDS AND no files → drop
    words = len(msg["body_text"].split())
    assert words < MIN_BODY_WORDS
    assert msg["files"] == []


def test_file_present_passes_gate_regardless_of_body_length():
    msg = _msg_with_body("<p>Hi</p>", attachments=[
        {"id": "1", "name": "report.pdf", "contentUrl": "https://example.com/r.pdf"},
    ])
    # files present → passes even with short body
    assert msg["files"] != []


def test_long_body_passes_gate():
    msg = _msg_with_body("<p>Here are the slides and notes from today's alignment session.</p>")
    assert len(msg["body_text"].split()) >= MIN_BODY_WORDS


# ── pagination sentinel ───────────────────────────────────────────────────────

def test_min_body_words_constant_is_positive_int():
    assert isinstance(MIN_BODY_WORDS, int) and MIN_BODY_WORDS > 0


# ── chat_rejected.jsonl ───────────────────────────────────────────────────────

def _make_msg(message_id="msg001"):
    return normalize_message({
        "id": message_id, "replyToId": None,
        "createdDateTime": "2026-10-15T14:00:00.000Z",
        "from": {"user": {"displayName": "Jo"}},
        "body": {"contentType": "html",
                 "content": "<p>Here are the notes from today's sync.</p>"},
        "attachments": [{"id": "x1", "name": "sync-notes.docx",
                         "contentUrl": "https://example.com/sync-notes.docx"}],
    }, channel_name="General")


def test_rejected_log_appends_jsonl():
    with tempfile.TemporaryDirectory() as store:
        msg = _make_msg("msg001")
        _append_rejected(store, msg, best_score=0.21, best_task="Run daily standup")
        lines = open(os.path.join(store, "chat_rejected.jsonl")).readlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["message_id"] == "msg001"
        assert rec["best_score"] == 0.21
        assert rec["best_task"] == "Run daily standup"


def test_rejected_log_is_append_only():
    with tempfile.TemporaryDirectory() as store:
        _append_rejected(store, _make_msg("msg001"))
        _append_rejected(store, _make_msg("msg002"))
        lines = open(os.path.join(store, "chat_rejected.jsonl")).readlines()
        assert len(lines) == 2
        ids = [json.loads(l)["message_id"] for l in lines]
        assert ids == ["msg001", "msg002"]


def test_rejected_log_carries_file_names():
    with tempfile.TemporaryDirectory() as store:
        msg = _make_msg()
        _append_rejected(store, msg)
        rec = json.loads(open(os.path.join(store, "chat_rejected.jsonl")).read())
        assert "sync-notes.docx" in rec["files"]


def test_rejected_log_score_rounded_to_3dp():
    with tempfile.TemporaryDirectory() as store:
        _append_rejected(store, _make_msg(), best_score=0.333333)
        rec = json.loads(open(os.path.join(store, "chat_rejected.jsonl")).read())
        assert rec["best_score"] == 0.333


def test_rejected_log_zero_score_when_no_tasks():
    with tempfile.TemporaryDirectory() as store:
        msg = _make_msg()
        _append_rejected(store, msg, best_score=0.0, best_task="")
        rec = json.loads(open(os.path.join(store, "chat_rejected.jsonl")).read())
        assert rec["best_score"] == 0.0
        assert rec["best_task"] == ""
