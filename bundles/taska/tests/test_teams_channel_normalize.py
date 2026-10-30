"""Unit tests for teams_channel.normalize_message — no network, no credentials."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.sources.teams_channel import normalize_message

# Invented sample — no real project data
SAMPLE_MSG = {
    "id": "1728000000000",
    "replyToId": "1727999000000",
    "createdDateTime": "2026-10-15T23:54:00.000Z",
    "from": {"user": {"displayName": "Alex Rivera", "userIdentityType": "aadUser"}},
    "body": {
        "contentType": "html",
        "content": (
            "<p>Here are the slides and sign-off list for the kick-off.</p>"
            '<attachment id="abc123"></attachment>'
        ),
    },
    "attachments": [
        {
            "id": "abc123",
            "contentType": "reference",
            "contentUrl": "https://example.sharepoint.com/sites/demo/kickoff-deck.pptx",
            "name": "kickoff-deck.pptx",
        },
        {
            "id": "def456",
            "contentType": "reference",
            "contentUrl": "https://example.sharepoint.com/sites/demo/stakeholders.xlsx",
            "name": "stakeholders.xlsx",
        },
    ],
}


def test_sender_and_timestamp():
    r = normalize_message(SAMPLE_MSG, channel_name="General")
    assert r["sender"] == "Alex Rivera"
    assert r["timestamp"] == "2026-10-15T23:54:00.000Z"


def test_channel_name_passed_through():
    r = normalize_message(SAMPLE_MSG, channel_name="General")
    assert r["channel_name"] == "General"


def test_thread_id_uses_reply_to():
    r = normalize_message(SAMPLE_MSG)
    assert r["thread_id"] == "1727999000000"   # replyToId, not id


def test_root_message_thread_id_equals_id():
    root = {**SAMPLE_MSG, "replyToId": None}
    r = normalize_message(root)
    assert r["thread_id"] == r["message_id"]


def test_body_text_stripped():
    r = normalize_message(SAMPLE_MSG)
    assert "<p>" not in r["body_text"]
    assert "kick-off" in r["body_text"]


def test_files_from_attachments():
    r = normalize_message(SAMPLE_MSG)
    names = [f["name"] for f in r["files"]]
    assert "kickoff-deck.pptx" in names
    assert "stakeholders.xlsx" in names


def test_no_duplicate_files():
    # abc123 appears in both attachments[] and the body HTML — must appear once
    r = normalize_message(SAMPLE_MSG)
    names = [f["name"] for f in r["files"]]
    assert names.count("kickoff-deck.pptx") == 1


def test_file_url_preserved():
    r = normalize_message(SAMPLE_MSG)
    deck = next(f for f in r["files"] if f["name"] == "kickoff-deck.pptx")
    assert "sharepoint.com" in deck["url"]


def test_empty_message_returns_safe_defaults():
    r = normalize_message({})
    assert r["sender"] == ""
    assert r["files"] == []
    assert r["body_text"] == ""
    assert r["thread_id"] == r["message_id"]
