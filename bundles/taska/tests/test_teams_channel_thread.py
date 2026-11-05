"""Tests for enrich_thread_context — parent body enriches reply context_text."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.sources.teams_channel import normalize_message, enrich_thread_context, match_task

def _norm(msg_id, reply_to, body, attachment_name=None, ts="2026-10-22T10:00:00.000Z"):
    att = [{"id": "a1", "name": attachment_name,
            "contentUrl": f"https://example.com/{attachment_name}"}] if attachment_name else []
    return normalize_message({
        "id": msg_id, "replyToId": reply_to,
        "createdDateTime": ts,
        "from": {"user": {"displayName": "Jo"}},
        "body": {"contentType": "html", "content": f"<p>{body}</p>"},
        "attachments": att,
    })

TASKS = [{"id": "T1", "title": "Complete deliverable tracking sheet"}]


# ── enrich_thread_context ────────────────────────────────────────────────────

def test_root_message_context_text_stays_empty():
    batch = [_norm("root1", None, "Please upload the deliverable tracking sheet.")]
    result = enrich_thread_context(batch)
    assert result[0]["context_text"] == ""


def test_reply_gets_parent_body_as_context():
    batch = [
        _norm("p1",  None,  "Please upload the deliverable tracking sheet.", ts="2026-10-22T09:00:00.000Z"),
        _norm("r1",  "p1",  "Done.", "document-1.xlsx",                      ts="2026-10-22T10:00:00.000Z"),
    ]
    enriched = enrich_thread_context(batch)
    reply = next(m for m in enriched if m["message_id"] == "r1")
    assert "deliverable tracking sheet" in reply["context_text"]


def test_root_message_unchanged_after_enrich():
    batch = [
        _norm("p1", None, "Please upload the deliverable tracking sheet.", ts="2026-10-22T09:00:00.000Z"),
        _norm("r1", "p1", "Done.", "document-1.xlsx",                      ts="2026-10-22T10:00:00.000Z"),
    ]
    enriched = enrich_thread_context(batch)
    root = next(m for m in enriched if m["message_id"] == "p1")
    assert root["context_text"] == ""


def test_sibling_context_included():
    batch = [
        _norm("p1", None, "Upload the tracker please.", ts="2026-10-22T08:00:00.000Z"),
        _norm("s1", "p1", "Working on it now.",         ts="2026-10-22T09:00:00.000Z"),
        _norm("r1", "p1", "Done.", "document-1.xlsx",   ts="2026-10-22T10:00:00.000Z"),
    ]
    enriched = enrich_thread_context(batch)
    reply = next(m for m in enriched if m["message_id"] == "r1")
    assert "tracker" in reply["context_text"]
    assert "Working on it" in reply["context_text"]


def test_sibling_posted_after_reply_not_included():
    batch = [
        _norm("p1", None, "Upload the tracker please.", ts="2026-10-22T08:00:00.000Z"),
        _norm("r1", "p1", "Done.", "document-1.xlsx",   ts="2026-10-22T09:00:00.000Z"),
        _norm("s2", "p1", "Thanks!",                    ts="2026-10-22T10:00:00.000Z"),
    ]
    enriched = enrich_thread_context(batch)
    reply = next(m for m in enriched if m["message_id"] == "r1")
    assert "Thanks" not in reply["context_text"]


# ── match improvement ────────────────────────────────────────────────────────

def test_generic_filename_vague_reply_misses_without_context():
    msg = _norm("r1", None, "Done.", "document-1.xlsx")
    result = match_task(msg, TASKS, threshold=0.4)
    assert result is None   # without context: 0.267 < 0.4


def test_generic_filename_vague_reply_matches_with_context():
    batch = [
        _norm("p1", None,  "Please upload the deliverable tracking sheet.", ts="2026-10-22T09:00:00.000Z"),
        _norm("r1", "p1",  "Done.", "document-1.xlsx",                      ts="2026-10-22T10:00:00.000Z"),
    ]
    enriched = enrich_thread_context(batch)
    reply = next(m for m in enriched if m["message_id"] == "r1")
    result = match_task(reply, TASKS, threshold=0.4)
    assert result is not None
    assert result["task_id"] == "T1"
