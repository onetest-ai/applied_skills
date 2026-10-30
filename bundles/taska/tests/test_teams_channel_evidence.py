"""Unit tests for teams_channel.build_evidence — no store I/O, no network."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.sources.teams_channel import normalize_message, build_evidence

_RAW = {
    "id": "1728000000001",
    "replyToId": None,
    "createdDateTime": "2026-10-15T10:30:00.000Z",
    "from": {"user": {"displayName": "Sam Lee"}},
    "body": {"contentType": "html",
             "content": "<p>Kick-off deck ready for review.</p>"},
    "attachments": [
        {"id": "a1", "name": "kickoff-deck.pptx",
         "contentUrl": "https://example.sharepoint.com/sites/demo/kickoff-deck.pptx"},
    ],
}

_MSG   = normalize_message(_RAW, channel_name="General")
_MATCH = {"task_id": "T001", "task_title": "Prepare kick-off presentation",
          "matched_file": "kickoff-deck.pptx", "score": 0.72}


def test_kind_is_file():
    ev = build_evidence(_MSG, _MATCH)
    assert ev["kind"] == "file"


def test_title_includes_filename_and_sender():
    ev = build_evidence(_MSG, _MATCH)
    assert "kickoff-deck.pptx" in ev["title"]
    assert "Sam Lee" in ev["title"]


def test_id_is_deterministic():
    # Same message always produces same id — idempotent sync
    ev1 = build_evidence(_MSG, _MATCH)
    ev2 = build_evidence(_MSG, _MATCH)
    assert ev1["id"] == ev2["id"]


def test_id_encodes_date_and_source():
    ev = build_evidence(_MSG, _MATCH)
    assert ev["id"].startswith("2026-10-15_teams_channel_")


def test_at_is_naive_iso():
    ev = build_evidence(_MSG, _MATCH)
    assert "Z" not in ev["at"]
    assert ev["at"].startswith("2026-10-15T10:30")


def test_url_is_matched_file_url():
    ev = build_evidence(_MSG, _MATCH)
    assert "sharepoint.com" in ev["url"]
    assert "kickoff-deck.pptx" in ev["url"]


def test_text_contains_body_and_file_list():
    ev = build_evidence(_MSG, _MATCH)
    assert "Kick-off deck ready" in ev["text"]
    assert "kickoff-deck.pptx" in ev["text"]


def test_meta_carries_message_id_and_task_id():
    ev = build_evidence(_MSG, _MATCH)
    assert ev["meta"]["message_id"] == "1728000000001"
    assert ev["meta"]["task_id"] == "T001"
    assert ev["meta"]["match_score"] == 0.72


def test_body_only_match_has_empty_url():
    body_match = {**_MATCH, "matched_file": None}
    ev = build_evidence(_MSG, body_match)
    assert ev["url"] == ""


def test_custom_source_name():
    ev = build_evidence(_MSG, _MATCH, source_name="acme_general")
    assert ev["source"] == "acme_general"
    assert ev["id"].startswith("2026-10-15_acme_general_")
