"""Tests for Teams channel new behaviors: sender normalization, grey-zone fail-fast,
multi-file unique evidence IDs, parent_outside_window flag, and reject reason strings.
All scenarios use invented data — no real client names, files, or users.
"""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.sources.teams_channel import (
    normalize_message, enrich_thread_context,
    match_task, build_evidence, _append_rejected, _reject_reason,
    MATCH_THRESHOLD, GREY_LOW,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _raw(msg_id="m1", reply_to=None, body="Some text here for testing.",
         sender="Alex Rivera", attachments=None,
         ts="2026-10-22T10:00:00.000Z"):
    return {
        "id": msg_id,
        "replyToId": reply_to,
        "createdDateTime": ts,
        "from": {"user": {"displayName": sender}},
        "body": {"contentType": "html", "content": f"<p>{body}</p>"},
        "attachments": attachments or [],
    }


def _att(name, url=None):
    return {"id": "a1", "name": name, "contentUrl": url or f"https://example.com/{name}"}


TASKS = [
    {"id": "T1", "title": "Prepare project kickoff deck"},
    {"id": "T2", "title": "Share stakeholder list"},
]


# ── sender normalization ──────────────────────────────────────────────────────

def test_guest_suffix_stripped():
    msg = normalize_message(_raw(sender="Alex Rivera (Guest)"))
    assert msg["sender"] == "Alex Rivera"


def test_external_suffix_stripped():
    msg = normalize_message(_raw(sender="Jo Smith (External)"))
    assert msg["sender"] == "Jo Smith"


def test_ext_lowercase_stripped():
    msg = normalize_message(_raw(sender="Sam Lee (ext)"))
    assert msg["sender"] == "Sam Lee"


def test_ext_mixed_case_stripped():
    msg = normalize_message(_raw(sender="Morgan Blake (Ext)"))
    assert msg["sender"] == "Morgan Blake"


def test_no_suffix_unchanged():
    msg = normalize_message(_raw(sender="Alex Rivera"))
    assert msg["sender"] == "Alex Rivera"


def test_empty_sender_stays_empty():
    msg = normalize_message(_raw(sender=""))
    assert msg["sender"] == ""


def test_guest_suffix_in_evidence_title_uses_stripped_name():
    msg = normalize_message(_raw(
        sender="Chris Taylor (Guest)",
        attachments=[_att("project-charter.docx")],
    ))
    match = {"task_id": "T1", "task_title": "Prepare project kickoff deck",
             "matched_file": "project-charter.docx", "score": 0.5}
    ev = build_evidence(msg, match)
    assert "Chris Taylor" in ev["title"]
    assert "Guest" not in ev["title"]


# ── parent_outside_window ─────────────────────────────────────────────────────

def test_reply_with_no_parent_in_batch_sets_flag():
    msg = normalize_message(_raw(msg_id="reply1", reply_to="parent_not_in_batch"))
    batch = [msg]
    enriched = enrich_thread_context(batch)
    assert enriched[0]["parent_outside_window"] is True


def test_reply_with_parent_in_batch_does_not_set_flag():
    batch = [
        normalize_message(_raw("p1", None, "Upload the tracker.", ts="2026-10-22T09:00:00.000Z")),
        normalize_message(_raw("r1", "p1", "Done.", ts="2026-10-22T10:00:00.000Z")),
    ]
    enriched = enrich_thread_context(batch)
    reply = next(m for m in enriched if m["message_id"] == "r1")
    assert reply["parent_outside_window"] is False


def test_root_message_never_sets_outside_window():
    msg = normalize_message(_raw("root1", None, "Some standalone message here."))
    enriched = enrich_thread_context([msg])
    assert enriched[0]["parent_outside_window"] is False


# ── grey-zone fail-fast reason strings ───────────────────────────────────────

def test_reject_reason_no_match():
    reason = _reject_reason(0.2, False)
    assert reason.startswith("no_match:")
    assert "0.200" in reason


def test_reject_reason_ambiguous_score():
    # GREY_LOW (0.3) to MATCH_THRESHOLD (0.4) is the grey zone
    score = (GREY_LOW + MATCH_THRESHOLD) / 2
    reason = _reject_reason(score, False)
    assert reason.startswith("ambiguous_score:")
    assert f"{score:.3f}" in reason


def test_reject_reason_no_match_with_outside_window():
    reason = _reject_reason(0.15, True)
    assert reason.startswith("no_match:")
    assert "parent_outside_window" in reason


def test_reject_reason_ambiguous_with_outside_window():
    score = (GREY_LOW + MATCH_THRESHOLD) / 2
    reason = _reject_reason(score, True)
    assert reason.startswith("ambiguous_score:")
    assert "parent_outside_window" in reason


def test_append_rejected_writes_reason_field():
    with tempfile.TemporaryDirectory() as store:
        msg = normalize_message(_raw())
        _append_rejected(store, msg, best_score=0.35, reason="ambiguous_score:0.350")
        rec = json.loads(open(os.path.join(store, "chat_rejected.jsonl")).read())
        assert rec["reason"] == "ambiguous_score:0.350"


def test_append_rejected_infers_reason_when_not_passed():
    """When reason is omitted, _append_rejected derives it from score + flag."""
    with tempfile.TemporaryDirectory() as store:
        msg = normalize_message(_raw())
        _append_rejected(store, msg, best_score=0.18)
        rec = json.loads(open(os.path.join(store, "chat_rejected.jsonl")).read())
        assert rec["reason"].startswith("no_match:")
        assert "0.180" in rec["reason"]


def test_append_rejected_includes_parent_outside_window_field():
    with tempfile.TemporaryDirectory() as store:
        msg = normalize_message(_raw(reply_to="gone_parent"))
        enrich_thread_context([msg])  # sets parent_outside_window=True
        _append_rejected(store, msg, best_score=0.1)
        rec = json.loads(open(os.path.join(store, "chat_rejected.jsonl")).read())
        assert "parent_outside_window" in rec


# ── multi-file unique evidence IDs ───────────────────────────────────────────

def test_two_files_produce_different_evidence_ids():
    msg = normalize_message(_raw(
        attachments=[_att("budget.xlsx"), _att("timeline.pptx")],
    ))
    match1 = {"task_id": "T1", "task_title": "Prepare project kickoff deck",
              "matched_file": "budget.xlsx", "score": 0.6}
    match2 = {"task_id": "T2", "task_title": "Share stakeholder list",
              "matched_file": "timeline.pptx", "score": 0.6}
    ev1 = build_evidence(msg, match1)
    ev2 = build_evidence(msg, match2)
    assert ev1["id"] != ev2["id"]


def test_same_file_same_message_produces_same_id():
    """Idempotent: same message + same file always hashes to the same dedup key."""
    msg = normalize_message(_raw(attachments=[_att("report.pdf")]))
    match = {"task_id": "T1", "task_title": "Prepare project kickoff deck",
             "matched_file": "report.pdf", "score": 0.7}
    id1 = build_evidence(msg, match)["id"]
    id2 = build_evidence(msg, match)["id"]
    assert id1 == id2


def test_no_file_match_uses_message_id_as_dedup_key():
    """Body-only match: dedup key is message_id (no file component)."""
    msg = normalize_message(_raw(body="Sharing the project kickoff plan now."))
    match1 = {"task_id": "T1", "task_title": "Prepare project kickoff deck",
              "matched_file": None, "score": 0.55}
    match2 = {"task_id": "T1", "task_title": "Prepare project kickoff deck",
              "matched_file": None, "score": 0.55}
    assert build_evidence(msg, match1)["id"] == build_evidence(msg, match2)["id"]


def test_evidence_meta_carries_parent_outside_window():
    msg = normalize_message(_raw(reply_to="not_in_batch"))
    enrich_thread_context([msg])
    match = {"task_id": "T1", "task_title": "Prepare project kickoff deck",
             "matched_file": None, "score": 0.5}
    ev = build_evidence(msg, match)
    assert ev["meta"]["parent_outside_window"] is True
