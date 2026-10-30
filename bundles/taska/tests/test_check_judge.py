"""Tests for check_judge.py and gold_from_judged.py — invented data only."""
import json
import sys
from pathlib import Path

import pytest

TASKA_ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = TASKA_ROOT / "evals"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

from check_judge import check  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

EV_ID = "2026-03-02_meetings_ab12cd34"


def _batch(cids):
    return [
        {
            "cid": c,
            "evidence": EV_ID,
            "quote": f"nora will prepare the onboarding checklist for item {c}",
            "ts": "00:01:00",
            "speaker": "Nora Quill",
        }
        for c in cids
    ]


def _judged(cids, commitment="committed", kind="task", status="open", note=None):
    items = []
    for i, c in enumerate(cids):
        item = {
            "cid": c,
            "commitment": commitment,
            "kind": kind,
            "title": f"Prepare onboarding checklist item {c}",
            "owner": "Nora Quill",
            "due": None,
            "status": status,
            "client_owed": False,
            "covered_by": None,
            "note": note or f"Nora commits to preparing the warehouse onboarding checklist for item {i}",
        }
        if commitment == "not_work":
            del item["title"]
        items.append(item)
    return items


# ── check_judge tests ─────────────────────────────────────────────────────────

def test_valid_output_ok():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    result = check(judged, batch)
    assert result["ok"] is True
    assert result["problems"] == []


def test_missing_cid_rejected():
    batch = _batch(["ab12cd34-001", "ab12cd34-002"])
    judged = _judged(["ab12cd34-001"])
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("ab12cd34-002" in p for p in result["problems"])


def test_extra_cid_rejected():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001", "ab12cd34-999"])
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("ab12cd34-999" in p for p in result["problems"])


def test_duplicate_cid_rejected():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"]) + _judged(["ab12cd34-001"])
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("ab12cd34-001" in p and ("duplicate" in p.lower() or "twice" in p.lower())
               for p in result["problems"])


def test_bad_commitment_enum_rejected():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    judged[0]["commitment"] = "maybe"
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("commitment" in p for p in result["problems"])


def test_not_work_is_valid_commitment():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"], commitment="not_work")
    result = check(judged, batch)
    assert result["ok"] is True, result["problems"]


def test_title_required_when_not_not_work():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    del judged[0]["title"]
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("title" in p for p in result["problems"])


def test_bad_kind_enum_rejected():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    judged[0]["kind"] = "action"
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("kind" in p for p in result["problems"])


def test_bad_status_enum_rejected():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    judged[0]["status"] = "pending"
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("status" in p for p in result["problems"])


def test_bad_due_format_rejected():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    judged[0]["due"] = "March 6, 2026"
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("due" in p for p in result["problems"])


def test_invalid_date_due_rejected():
    """A due value with correct format but invalid date is rejected."""
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    judged[0]["due"] = "2026-13-01"  # month 13
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("due" in p for p in result["problems"])


def test_null_due_allowed():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    judged[0]["due"] = None
    result = check(judged, batch)
    assert result["ok"] is True


def test_client_owed_must_be_bool():
    """client_owed must be a bool, not an int or string."""
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    judged[0]["client_owed"] = 1  # int, not bool
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("client_owed" in p for p in result["problems"])


def test_short_note_rejected():
    batch = _batch(["ab12cd34-001"])
    judged = _judged(["ab12cd34-001"])
    judged[0]["note"] = "Too short"
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("note" in p for p in result["problems"])


def test_stock_notes_rejected():
    cids = ["ab12cd34-001", "ab12cd34-002", "ab12cd34-003"]
    batch = _batch(cids)
    stock_note = "nora committed to completing the warehouse onboarding checklist before thursday"
    judged = []
    for i, c in enumerate(cids):
        j = _judged([c])[0]
        j["note"] = stock_note
        judged.append(j)
    result = check(judged, batch)
    assert result["ok"] is False
    assert any("note" in p.lower() for p in result["problems"])


def test_note_reused_twice_allowed():
    cids = ["ab12cd34-001", "ab12cd34-002"]
    batch = _batch(cids)
    shared_note = "nora committed to completing the warehouse onboarding checklist before the client review"
    judged = []
    for c in cids:
        j = _judged([c])[0]
        j["note"] = shared_note
        judged.append(j)
    result = check(judged, batch)
    assert result["ok"] is True


def test_non_list_input_reports_problem_not_crash():
    """A non-list judged input reports a problem instead of crashing."""
    result = check({"oops": "not a list"}, _batch(["ab12cd34-001"]))
    assert result["ok"] is False
    assert result["problems"]


def test_duplicate_of_field_accepted():
    """duplicate_of is an optional field; its presence does not fail validation."""
    batch = _batch(["ab12cd34-001", "ab12cd34-002"])
    judged = _judged(["ab12cd34-001", "ab12cd34-002"])
    judged[1]["duplicate_of"] = "ab12cd34-001"
    result = check(judged, batch)
    assert result["ok"] is True, result["problems"]


# ── gold_from_judged tests ────────────────────────────────────────────────────

def test_gold_from_judged_round_trip(tmp_path):
    """gold_from_judged joins judged answers to pool, drops not_work, keeps declined."""
    from gold_from_judged import gold_from_judged

    ev_short = "ab12cd34"
    pool = {
        EV_ID: [
            {
                "cid": f"{ev_short}-001",
                "evidence": EV_ID,
                "quote": "nora will prepare the onboarding checklist for the client team",
                "ts": "00:01:00",
                "speaker": "Nora Quill",
                "offset": 0,
                "sources": ["reader_a"],
                "verifiable": True,
            },
            {
                "cid": f"{ev_short}-002",
                "evidence": EV_ID,
                "quote": "ravi needs to book the warehouse site visit and confirm with opal",
                "ts": "00:05:00",
                "speaker": "Ravi Tamsin",
                "offset": 200,
                "sources": ["taska"],
                "verifiable": True,
            },
            {
                "cid": f"{ev_short}-003",
                "evidence": EV_ID,
                "quote": "the yard scanner firmware update was proposed but nobody agreed to own it",
                "ts": "00:10:00",
                "speaker": "Ravi Tamsin",
                "offset": 400,
                "sources": ["echo"],
                "verifiable": True,
            },
        ]
    }

    judged = [
        {
            "cid": f"{ev_short}-001",
            "commitment": "committed",
            "kind": "task",
            "title": "Prepare onboarding checklist",
            "owner": "Nora Quill",
            "due": "2026-03-06",
            "status": "open",
            "client_owed": False,
            "covered_by": None,
            "note": "Nora commits to preparing the onboarding checklist before thursday review meeting",
        },
        {
            "cid": f"{ev_short}-002",
            "commitment": "not_work",
            "kind": None,
            "title": None,
            "owner": None,
            "due": None,
            "status": None,
            "client_owed": False,
            "covered_by": None,
            "note": "This appears to be an informal status update with no deliverable assigned to anyone",
        },
        {
            "cid": f"{ev_short}-003",
            "commitment": "declined",
            "kind": "task",
            "title": "Update yard scanner firmware",
            "owner": None,
            "due": None,
            "status": "open",
            "client_owed": False,
            "covered_by": None,
            "note": "The firmware update was raised but explicitly declined with no owner stepping forward",
        },
    ]

    pool_file = tmp_path / "pool_full.json"
    pool_file.write_text(json.dumps(pool))

    judged_file = tmp_path / "judged_01.json"
    judged_file.write_text(json.dumps(judged))

    # Also write corresponding batch file (required by gold_from_judged for validation)
    batch = [
        {"cid": f"{ev_short}-001", "evidence": EV_ID,
         "quote": "nora will prepare the onboarding checklist for the client team",
         "ts": "00:01:00", "speaker": "Nora Quill"},
        {"cid": f"{ev_short}-002", "evidence": EV_ID,
         "quote": "ravi needs to book the warehouse site visit and confirm with opal",
         "ts": "00:05:00", "speaker": "Ravi Tamsin"},
        {"cid": f"{ev_short}-003", "evidence": EV_ID,
         "quote": "the yard scanner firmware update was proposed but nobody agreed to own it",
         "ts": "00:10:00", "speaker": "Ravi Tamsin"},
    ]
    batch_file = tmp_path / "judge_01.json"
    batch_file.write_text(json.dumps(batch))

    out_file = tmp_path / "items.jsonl"
    gold_from_judged(pool_file, [judged_file], out_file)

    lines = [l for l in out_file.read_text().splitlines() if l.strip()]
    assert lines

    header = lines[0]
    assert header.startswith("# gold v1")
    assert "n=2" in header

    items = [json.loads(l) for l in lines[1:]]
    assert len(items) == 2

    item_001 = next((i for i in items if i.get("cid") == f"{ev_short}-001"), None)
    assert item_001 is not None
    assert item_001["commitment"] == "committed"

    assert not any(i.get("cid") == f"{ev_short}-002" for i in items)

    item_003 = next((i for i in items if i.get("cid") == f"{ev_short}-003"), None)
    assert item_003 is not None
    assert item_003["commitment"] == "declined"


def test_gold_drops_duplicate_of_items(tmp_path):
    """gold_from_judged drops items marked duplicate_of from the gold output."""
    from gold_from_judged import gold_from_judged

    ev_short = "ab12cd34"
    pool = {EV_ID: [
        {"cid": f"{ev_short}-001", "evidence": EV_ID,
         "quote": "nora will prepare the onboarding checklist and share it with the team",
         "ts": "00:01:00", "speaker": "Nora Quill", "offset": 0,
         "sources": ["reader_a"], "verifiable": True},
        {"cid": f"{ev_short}-002", "evidence": EV_ID,
         "quote": "ravi needs to book the warehouse site visit and confirm with opal",
         "ts": "00:05:00", "speaker": "Ravi Tamsin", "offset": 100,
         "sources": ["reader_a"], "verifiable": True},
    ]}

    judged = [
        {"cid": f"{ev_short}-001", "commitment": "committed", "kind": "task",
         "title": "Prepare onboarding checklist", "owner": "Nora Quill", "due": None,
         "status": "open", "client_owed": False, "covered_by": None,
         "note": "Nora commits to preparing the warehouse checklist before the client review"},
        {"cid": f"{ev_short}-002", "commitment": "committed", "kind": "task",
         "title": "Book warehouse site visit", "owner": "Ravi Tamsin", "due": None,
         "status": "open", "client_owed": False, "covered_by": None,
         "duplicate_of": f"{ev_short}-001",  # marked as duplicate
         "note": "Ravi needs to book the warehouse visit and confirm the date with Opal"},
    ]

    pool_file = tmp_path / "pool_full.json"
    pool_file.write_text(json.dumps(pool))

    judged_file = tmp_path / "judged_01.json"
    judged_file.write_text(json.dumps(judged))

    batch = [{"cid": f"{ev_short}-001", "evidence": EV_ID,
              "quote": "nora will prepare", "ts": "00:01:00", "speaker": "Nora Quill"},
             {"cid": f"{ev_short}-002", "evidence": EV_ID,
              "quote": "ravi needs to book", "ts": "00:05:00", "speaker": "Ravi Tamsin"}]
    (tmp_path / "judge_01.json").write_text(json.dumps(batch))

    out_file = tmp_path / "items.jsonl"
    gold_from_judged(pool_file, [judged_file], out_file)

    lines = [l for l in out_file.read_text().splitlines() if l.strip()]
    header = lines[0]
    assert "n=1" in header, f"expected n=1 (duplicate dropped), got: {header!r}"

    items = [json.loads(l) for l in lines[1:]]
    assert len(items) == 1
    assert items[0]["cid"] == f"{ev_short}-001"


def test_gold_refuses_unjudged_cids(tmp_path):
    """gold_from_judged returns unjudged > 0 when pool cids are missing from judged output."""
    from gold_from_judged import gold_from_judged

    ev_short = "ab12cd34"
    pool = {EV_ID: [
        {"cid": f"{ev_short}-001", "evidence": EV_ID,
         "quote": "nora will prepare the onboarding checklist and share with client",
         "ts": "00:01:00", "speaker": "Nora Quill", "offset": 0,
         "sources": ["reader_a"], "verifiable": True},
        {"cid": f"{ev_short}-002", "evidence": EV_ID,
         "quote": "ravi needs to book the warehouse site visit and confirm with opal",
         "ts": "00:05:00", "speaker": "Ravi Tamsin", "offset": 100,
         "sources": ["reader_a"], "verifiable": True},
    ]}

    # Only cid-001 is judged; cid-002 is missing
    judged = [
        {"cid": f"{ev_short}-001", "commitment": "committed", "kind": "task",
         "title": "Prepare onboarding checklist", "owner": "Nora Quill", "due": None,
         "status": "open", "client_owed": False, "covered_by": None,
         "note": "Nora commits to preparing the checklist before the client review meeting"},
    ]

    pool_file = tmp_path / "pool_full.json"
    pool_file.write_text(json.dumps(pool))

    judged_file = tmp_path / "judged_01.json"
    judged_file.write_text(json.dumps(judged))

    batch = [
        {"cid": f"{ev_short}-001", "evidence": EV_ID,
         "quote": "nora will prepare", "ts": "00:01:00", "speaker": "Nora Quill"},
        {"cid": f"{ev_short}-002", "evidence": EV_ID,
         "quote": "ravi needs to book", "ts": "00:05:00", "speaker": "Ravi Tamsin"},
    ]
    (tmp_path / "judge_01.json").write_text(json.dumps(batch))

    out_file = tmp_path / "items.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        gold_from_judged(pool_file, [judged_file], out_file)
    assert exc_info.value.code != 0  # non-zero exit


# ── drift test ────────────────────────────────────────────────────────────────

def test_judge_md_blocks_match_reader_md():
    """Shared blocks in JUDGE.md are byte-identical to the corresponding READER.md blocks."""
    reader_md = (TASKA_ROOT / "evals" / "READER.md").read_text(encoding="utf-8")
    judge_md = (TASKA_ROOT / "evals" / "JUDGE.md").read_text(encoding="utf-8")

    def _extract_blocks(text):
        """Extract <!-- READER-BLOCK:name:START --> ... <!-- READER-BLOCK:name:END --> blocks."""
        import re
        blocks = {}
        pattern = re.compile(
            r"<!-- READER-BLOCK:(\w[\w-]*):START -->\n(.*?)\n<!-- READER-BLOCK:\1:END -->",
            re.DOTALL
        )
        for m in pattern.finditer(text):
            blocks[m.group(1)] = m.group(2)
        return blocks

    reader_blocks = _extract_blocks(reader_md)
    judge_blocks = _extract_blocks(judge_md)

    assert reader_blocks, "READER.md must contain <!-- READER-BLOCK:NAME:START --> markers"
    assert judge_blocks, "JUDGE.md must contain <!-- READER-BLOCK:NAME:START --> markers"

    for name, reader_block in reader_blocks.items():
        assert name in judge_blocks, f"JUDGE.md missing block {name!r} found in READER.md"
        assert judge_blocks[name] == reader_block, (
            f"JUDGE.md block {name!r} differs from READER.md block:\n"
            f"  READER: {reader_block[:120]!r}\n"
            f"  JUDGE:  {judge_blocks[name][:120]!r}"
        )


def test_reader_and_judge_share_the_calibrated_rules():
    """Both docs carry the commitment and client-owed rules as shared blocks, with the production meaning
    of client_owed (the client owes it to us, as in EXTRACT.md) and hedged commitments counted as committed."""
    import re
    for doc in ("READER.md", "JUDGE.md"):
        text = (TASKA_ROOT / "evals" / doc).read_text(encoding="utf-8")
        names = set(re.findall(r"<!-- READER-BLOCK:([\w-]+):START -->", text))
        assert {"four-kinds", "not-work-bullets", "commitment", "client-owed"} <= names, doc
        owed = text.split("READER-BLOCK:client-owed:START")[1].split("READER-BLOCK:client-owed:END")[0]
        assert "client's side owes the deliverable to us" in owed, doc
        assert "goes directly to the client" not in text, doc
        commit = " ".join(text.split("READER-BLOCK:commitment:START")[1].split("READER-BLOCK:commitment:END")[0].split())
        assert "Hedging never downgrades" in commit, doc
        assert "outlives the meeting" in commit, doc
        notwork = " ".join(text.split("READER-BLOCK:not-work-bullets:START")[1].split("READER-BLOCK:not-work-bullets:END")[0].split())
        for rule in ("Scheduling, booking or moving a session", "Same-day errands", "Workshop brainstorming",
                     'A generic "we should', "Setting up a teammate's access",
                     "how a workshop session runs", "client's guidance", "track the client-owed delivery",
                     "A status update on work already tracked"):
            assert rule in notwork, (doc, rule)
