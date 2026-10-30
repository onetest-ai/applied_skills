"""Scorer tests for evalscore. Invented data only (Harbor Logistics world)."""
import json
import math
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from taska_lib import config as C, ledger as L, store as S
from taska_lib.evalscore import _wilson, match_item, score

# Path for CLI subprocess tests
from conftest import run_cli

EVID = "2026-03-02_meetings_ab12cd34"
QUOTE_A = "Nora will write the onboarding checklist and send it to Opal by Friday morning"
QUOTE_B = "Ravi needs to book the warehouse site visit and confirm the date with Opal"
QUOTE_DEC = "The team decided not to proceed with the legacy system migration project"


def _cfg(tmp_path):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
        [[people]]
        name = "Ravi Tamsin"
        [[people]]
        name = "Opal Brennick"
        side = "client"
        [[teams]]
        name = "Yard team"
        members = ["Nora Quill", "Ravi Tamsin"]
        aliases = ["yard guys"]
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    return cfg


def _meeting(cfg, evid=EVID, text="placeholder"):
    item = {"id": evid, "kind": "meeting", "at": "2026-03-02T10:00:00",
            "title": "Daily sync", "source": "meetings", "text": text}
    S.write_evidence(cfg.store, item)
    return item


def _epics(cfg):
    S.append(cfg.store, [
        {"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan",
         "evidence": None, "date": "2026-03-01", "fields": {"title": "Onboarding"}},
        {"type": "create", "mid": "s1", "id": "S001", "kind": "story", "by": "agent",
         "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Onboarding docs", "parent": "E01", "ac": ["Docs ready"],
                    "internal_owner": None, "client_owner": None, "client_owed": False, "plan": None}},
    ])


def _task(cfg, nid, evid=EVID, quote=QUOTE_A, owner=None, due=None, client_owed=False):
    S.append(cfg.store, [
        {"type": "create", "mid": f"m_{nid}", "id": nid, "kind": "task", "by": "agent",
         "evidence": evid, "date": "2026-03-02", "quote": quote, "quote_norm": quote,
         "fields": {"title": f"Task {nid}", "parent": "S001", "type": "action",
                    "internal_owner": owner, "client_owner": None, "client_owed": client_owed,
                    "ac": [], "due": due, "due_text": None, "plan": None}}
    ])


def _decision(cfg, nid, evid=EVID, quote=QUOTE_DEC, client_owed=False):
    S.append(cfg.store, [
        {"type": "create", "mid": f"m_{nid}", "id": nid, "kind": "decision", "by": "agent",
         "evidence": evid, "date": "2026-03-02", "quote": quote, "quote_norm": quote,
         "fields": {"title": f"Decision {nid}", "parent": "E01", "type": None,
                    "internal_owner": None, "client_owner": None, "client_owed": client_owed,
                    "ac": [], "due": None, "due_text": None, "plan": None}}
    ])


def _processed(cfg, evid=EVID):
    S.append(cfg.store, [{"type": "processed", "evidence": evid, "by": "agent",
                          "at": "2026-03-02T11:00:00"}])


def _gold(evid=EVID, quote=QUOTE_A, kind="task", commitment="committed",
          owner=None, due=None, status=None, covered_by=None, client_owed=False, offset=None):
    g = {"evidence": evid, "quote": quote, "kind": kind, "commitment": commitment,
         "owner": owner, "due": due, "status": status, "covered_by": covered_by,
         "client_owed": client_owed}
    if offset is not None:
        g["offset"] = offset
    return g


# ---------------------------------------------------------------------------
# Wilson helper
# ---------------------------------------------------------------------------

def test_wilson_reference():
    lb = _wilson(79, 126)
    assert abs(lb - 0.54) <= 0.005, f"_wilson(79, 126) = {lb}, expected ~0.54"


# ---------------------------------------------------------------------------
# covered_by resolution
# ---------------------------------------------------------------------------

def test_covered_by_counts(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001")
    _processed(cfg)

    gold = [_gold(covered_by="T001")]
    result = score(cfg.store, gold)
    assert result["B7"]["n"] == 1
    assert result["B7"]["ok"] == 1


def test_merged_node_still_counts(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001")
    _task(cfg, "T002", quote=QUOTE_B)
    # merge T001 into T002
    S.append(cfg.store, [
        {"type": "human", "op": "merge", "mid": "mg1", "node": "T001", "into": "T002",
         "by": "human", "evidence": None, "date": "2026-03-02", "at": "2026-03-02T11:00:00"}
    ])
    _processed(cfg)

    gold = [_gold(covered_by="T001")]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 1, f"covered_by T001 (merged into T002) should still hit; got {result['B7']}"


def test_deleted_node_is_a_miss(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001")
    S.append(cfg.store, [
        {"type": "human", "op": "delete", "mid": "del1", "node": "T001",
         "by": "human", "evidence": None, "date": "2026-03-02", "at": "2026-03-02T11:00:00"}
    ])
    _processed(cfg)

    gold = [_gold(covered_by="T001")]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 0, "deleted node must not count as a hit"
    assert len(result["B7"]["fails"]) == 1


# ---------------------------------------------------------------------------
# Quote-overlap matching (no covered_by)
# ---------------------------------------------------------------------------

def test_quote_overlap_counts_without_covered_by(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    # Task T001 has the same quote as gold (event stored directly)
    _task(cfg, "T001", quote=QUOTE_A)
    _processed(cfg)

    # gold has no covered_by – must match via shingle overlap (QUOTE_A == event quote → containment 1.0)
    gold = [_gold(covered_by=None, quote=QUOTE_A)]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 1, f"quote overlap should match T001; B7={result['B7']}"


# ---------------------------------------------------------------------------
# Declined
# ---------------------------------------------------------------------------

def test_declined_excluded_from_recall_and_counted_for_B16(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001")  # exists in tree
    _processed(cfg)

    gold = [
        # declined item that was NOT kept → B16 ok
        _gold(commitment="declined", covered_by=None, quote=QUOTE_B),
        # declined item that WAS kept (T001 exists) → B16 miss
        _gold(commitment="declined", covered_by="T001"),
    ]
    result = score(cfg.store, gold)
    assert result["B7"]["n"] == 0, "declined items must not appear in B7"
    assert result["B16"]["n"] == 2
    assert result["B16"]["ok"] == 1   # only the first item (no match) is a B16 hit


# ---------------------------------------------------------------------------
# Owner accuracy
# ---------------------------------------------------------------------------

def test_owner_accuracy_uses_canonical_names(tmp_path):
    """Node owner stored as team alias 'yard guys' → canonical 'Yard team'; gold owner 'Yard team' → match."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001", owner="yard guys")  # alias → Yard team
    _processed(cfg)

    gold = [_gold(covered_by="T001", owner="Yard team")]
    result = score(cfg.store, gold)
    assert result["B10"]["n"] == 1
    assert result["B10"]["ok"] == 1, f"canonical(yard guys)==canonical(Yard team) should match; B10={result['B10']}"


# ---------------------------------------------------------------------------
# B15 precision
# ---------------------------------------------------------------------------

def test_invented_node_lowers_B15(tmp_path):
    """An extra node created from a golden evidence id that no gold item claims lowers B15 precision."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001", quote=QUOTE_A)   # matched by gold item 1
    _task(cfg, "T002", quote=QUOTE_B)   # not matched – invented/false positive
    _processed(cfg)

    gold = [_gold(covered_by="T001")]   # only claims T001; T002 is a false positive
    result = score(cfg.store, gold)
    assert result["B15"]["n"] == 2, "both T001 and T002 are in scope for B15"
    assert result["B15"]["ok"] == 1, "only T001 is matched → precision < 1"


# ---------------------------------------------------------------------------
# B9 by_part / thirds
# ---------------------------------------------------------------------------

def test_parts_use_text_offset(tmp_path):
    """Gold items with an explicit offset are classified into meeting thirds for B9."""
    cfg = _cfg(tmp_path)
    # Evidence text long enough for clear thirds (>= 300 chars normalized)
    _meeting(cfg, text="a " * 200)   # norm ≈ "a a … a daily sync" ~410 chars
    _epics(cfg)
    _task(cfg, "T001", quote=QUOTE_A)
    _task(cfg, "T002", quote=QUOTE_B)
    _processed(cfg)

    gold = [
        _gold(covered_by="T001", offset=10),    # 10/410 ≈ 0.024 → part 1
        _gold(covered_by="T002", quote=QUOTE_B, offset=350),  # 350/410 ≈ 0.854 → part 3
    ]
    result = score(cfg.store, gold)
    # Both are B7 hits
    assert result["B7"]["n"] == 2
    assert result["B7"]["ok"] == 2
    # B9 counts only last-third items
    assert result["B9"]["n"] == 1
    assert result["B9"]["ok"] == 1
    # by_part breakdown
    bp = result["B9"]["by_part"]
    assert bp["1"]["n"] == 1 and bp["1"]["ok"] == 1
    assert bp["3"]["n"] == 1 and bp["3"]["ok"] == 1


# ===========================================================================
# Fix-round-1 tests (these must be RED before the fixes are applied)
# ===========================================================================

# ---------------------------------------------------------------------------
# Fix 1: card then create – match_item must skip propose and find the create
# ---------------------------------------------------------------------------

def test_card_then_create_returns_create_node(tmp_path):
    """A propose (card) before a create with the same quote: match_item returns the create's node, not None."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    # Propose card with QUOTE_A (added before the create)
    S.append(cfg.store, [
        {"type": "agent", "op": "propose", "mid": "pr1", "node": "S001",
         "by": "agent", "evidence": EVID, "date": "2026-03-02",
         "quote": QUOTE_A, "quote_norm": QUOTE_A, "pending": "human",
         "proposal": {"kind": "task", "title": "Draft checklist", "type": "action",
                      "internal_owner": None, "client_owner": None, "client_owed": False,
                      "ac": [], "due": None, "due_text": None}}
    ])
    _task(cfg, "T001", quote=QUOTE_A)  # create with same quote, after the propose
    _processed(cfg)

    gold = [_gold(covered_by=None, quote=QUOTE_A, commitment="committed")]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 1, "create node T001 should win; propose must not block it"
    assert result["carded"] == 0


# ---------------------------------------------------------------------------
# Fix 2: event-scan results routed through _resolve_node
# ---------------------------------------------------------------------------

def test_event_matched_deleted_node_is_a_miss(tmp_path):
    """Event quote matches T001 but T001 is deleted → miss; not a B16 false positive either."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001", quote=QUOTE_A)
    S.append(cfg.store, [
        {"type": "human", "op": "delete", "mid": "del1", "node": "T001",
         "by": "human", "evidence": None, "date": "2026-03-02", "at": "2026-03-02T11:00:00"}
    ])
    _processed(cfg)

    gold = [_gold(covered_by=None, quote=QUOTE_A, commitment="committed")]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 0, "deleted node must be a miss"
    assert result["B16"]["n"] == 0   # the gold item is committed, not declined – must not appear in B16


def test_event_matched_merged_node_uses_surviving_owner(tmp_path):
    """Event quote matches T001 (merged into T002); B10 must compare T002's owner, not T001's."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001", quote=QUOTE_A, owner="Nora Quill")
    _task(cfg, "T002", quote=QUOTE_B, owner="Ravi Tamsin")
    S.append(cfg.store, [
        {"type": "human", "op": "merge", "mid": "mg1", "node": "T001", "into": "T002",
         "by": "human", "evidence": None, "date": "2026-03-02", "at": "2026-03-02T11:00:00"}
    ])
    _processed(cfg)

    # Gold claims T001's quote, expects the surviving node's (T002's) owner
    gold = [_gold(covered_by=None, quote=QUOTE_A, owner="Ravi Tamsin")]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 1, "T001 event → T001 merged → T002 must count as a B7 hit"
    assert result["B10"]["ok"] == 1, "B10 must compare T002's owner"


# ---------------------------------------------------------------------------
# Fix 3: B15 excludes merged nodes from denominator
# ---------------------------------------------------------------------------

def test_B15_merged_node_excluded_from_denominator(tmp_path):
    """A merged node must not inflate the B15 denominator; the surviving node carries the claim."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001", quote=QUOTE_A)
    _task(cfg, "T002", quote=QUOTE_B)
    S.append(cfg.store, [
        {"type": "human", "op": "merge", "mid": "mg1", "node": "T001", "into": "T002",
         "by": "human", "evidence": None, "date": "2026-03-02", "at": "2026-03-02T11:00:00"}
    ])
    _processed(cfg)

    gold = [_gold(covered_by="T002")]   # T001 is merged; only T002 is in B15 scope
    result = score(cfg.store, gold)
    assert result["B15"]["n"] == 1, f"merged T001 must be excluded from B15; B15={result['B15']}"
    assert result["B15"]["ok"] == 1


# ---------------------------------------------------------------------------
# Fix 4: n == 0 → rate None, lb95 None
# ---------------------------------------------------------------------------

def test_gap_n_zero_gives_none_rate_and_lb95(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _processed(cfg)

    result = score(cfg.store, [])
    assert result["B7"]["n"] == 0
    assert result["B7"]["rate"] is None
    assert result["B7"]["lb95"] is None


# ---------------------------------------------------------------------------
# Fix 5: score() raises ValueError for missing 'evidence'; cmd_score refuses malformed lines
# ---------------------------------------------------------------------------

def test_score_raises_for_missing_evidence_key(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _processed(cfg)

    with pytest.raises(ValueError, match="evidence"):
        score(cfg.store, [{"quote": "foo", "kind": "task", "commitment": "committed"}])


def test_cmd_score_refuses_malformed_gold_line(tmp_path):
    """A non-blank, non-header JSONL line that is not valid JSON causes an error with the line number."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _processed(cfg)

    gold_file = tmp_path / "gold.jsonl"
    gold_file.write_text(
        '{"evidence": "2026-03-02_meetings_ab12cd34", "kind": "task", "commitment": "committed"}\n'
        'not-json-at-all\n'
    )
    result = run_cli(tmp_path, "score", "--gold", str(gold_file), check=False)
    assert result.get("status") == "error"
    assert "gold line 2" in result.get("error", ""), f"expected 'gold line 2' in error; got {result}"


# ---------------------------------------------------------------------------
# Fix 6: additional coverage tests
# ---------------------------------------------------------------------------

# text used for offset-proximity tests
OFFSET_TEXT = "This is a short meeting note about tasks and deliverables for the project"
# norm = "this is a short meeting note about tasks and deliverables for the project daily sync"
# "tasks" starts at index 35 in the normalised full_text


def test_offset_only_matching(tmp_path):
    """Gold quote < 5 words (no shingles) matches via offset proximity when the event quote is near."""
    cfg = _cfg(tmp_path)
    _meeting(cfg, text=OFFSET_TEXT)   # "tasks and deliverables" at ~index 35 in norm text
    _epics(cfg)
    _task(cfg, "T001", quote="tasks and deliverables")   # in evidence text; quote_offset ≈ 35
    _processed(cfg)

    # "short" has 1 word → no 5-word shingles; gold_offset=10 → abs(35-10)=25 ≤ 400 → offset match
    gold = [_gold(covered_by=None, quote="short", offset=10)]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 1, "offset proximity (25 chars) should match T001"


def test_event_quote_not_in_text_does_not_match_by_offset(tmp_path):
    """An event quote not found in the evidence text returns offset -1 → never matches by the 400-char rule."""
    cfg = _cfg(tmp_path)
    _meeting(cfg, text=OFFSET_TEXT)
    _epics(cfg)
    _task(cfg, "T001", quote="completely unrelated text not anywhere in the evidence at all")
    _processed(cfg)

    # gold_offset=10; event offset = -1 (quote not in text) → no match
    gold = [_gold(covered_by=None, quote="short", offset=10)]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 0, "offset -1 must never match"


def test_short_quote_no_shingle_match(tmp_path):
    """A gold quote with fewer than 5 words produces no shingles and cannot match via containment."""
    cfg = _cfg(tmp_path)
    _meeting(cfg, text="send the weekly report to opal by thursday morning please")
    _epics(cfg)
    _task(cfg, "T001", quote="send the weekly report to opal by thursday morning please")
    _processed(cfg)

    # "send report" = 2 words → empty shingles; no explicit offset → quote_offset("send report") = -1
    gold = [_gold(covered_by=None, quote="send report")]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 0, "short gold quote must not match via shingles or offset"


def test_B15_declined_only_match_is_false_positive(tmp_path):
    """A node matched only by a declined gold item is still a B15 false positive."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001")
    _processed(cfg)

    gold = [_gold(covered_by="T001", commitment="declined")]  # declined → excluded from matched_nids
    result = score(cfg.store, gold)
    assert result["B15"]["n"] == 1   # T001 is in scope
    assert result["B15"]["ok"] == 0  # only a declined match → false positive


def test_B8_card_hit(tmp_path):
    """A proposed item matched only by a pending propose card counts as a B8 hit."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    S.append(cfg.store, [
        {"type": "agent", "op": "propose", "mid": "pr1", "node": "S001",
         "by": "agent", "evidence": EVID, "date": "2026-03-02",
         "quote": QUOTE_A, "quote_norm": QUOTE_A, "pending": "human",
         "proposal": {"kind": "task", "title": "Draft checklist", "type": "action",
                      "internal_owner": None, "client_owner": None, "client_owed": False,
                      "ac": [], "due": None, "due_text": None}}
    ])
    _processed(cfg)

    gold = [_gold(commitment="proposed", quote=QUOTE_A)]
    result = score(cfg.store, gold)
    assert result["B8"]["n"] == 1
    assert result["B8"]["ok"] == 1, f"propose card should count as B8 hit; B8={result['B8']}"


def test_B11_due_accuracy(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001", due="2026-03-07")
    _task(cfg, "T002", quote=QUOTE_B, due="2026-03-14")
    _processed(cfg)

    gold = [
        _gold(covered_by="T001", due="2026-03-07"),               # ok: dates match
        _gold(covered_by="T002", quote=QUOTE_B, due="2026-03-10"),  # fail: 2026-03-14 ≠ 2026-03-10
    ]
    result = score(cfg.store, gold)
    assert result["B11"]["n"] == 2
    assert result["B11"]["ok"] == 1
    assert len(result["B11"]["fails"]) == 1


def test_B12_status_accuracy(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001")  # state=open
    _task(cfg, "T002", quote=QUOTE_B)  # state=open
    _processed(cfg)

    gold = [
        _gold(covered_by="T001", status="open"),      # ok: open→open
        _gold(covered_by="T002", quote=QUOTE_B, status="progress"),  # fail: open ≠ progress
    ]
    result = score(cfg.store, gold)
    assert result["B12"]["n"] == 2
    assert result["B12"]["ok"] == 1
    assert len(result["B12"]["fails"]) == 1


def test_B13_B14(tmp_path):
    """B13 recall and B14 client_owed accuracy for decisions."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _decision(cfg, "D001", client_owed=True)
    _decision(cfg, "D002", quote=QUOTE_B, client_owed=False)
    _processed(cfg)

    gold = [
        # D001: B13 ok; client_owed True==True → B14 ok
        _gold(covered_by="D001", kind="decision", commitment="committed", client_owed=True),
        # D002: B13 ok; client_owed False!=True → B14 fail
        _gold(covered_by="D002", kind="decision", commitment="committed", quote=QUOTE_B, client_owed=True),
        # unmatched: B13 miss
        _gold(kind="decision", commitment="committed", quote="unrelated decision about unrelated thing here"),
    ]
    result = score(cfg.store, gold)
    assert result["B13"]["n"] == 3
    assert result["B13"]["ok"] == 2
    assert result["B14"]["n"] == 2   # only the two B13 hits
    assert result["B14"]["ok"] == 1
    assert len(result["B14"]["fails"]) == 1


def test_incomplete_missing_evidence(tmp_path):
    """Gold referencing an unknown evidence id sets incomplete=True and lists the id in missing."""
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _processed(cfg)

    unknown_evid = "2026-03-05_meetings_unknown999"
    gold = [_gold(evid=unknown_evid)]
    result = score(cfg.store, gold)
    assert result["incomplete"] is True
    assert unknown_evid in result.get("missing", [])


# ---------------------------------------------------------------------------
# Oct 25 gate: duplicate copies, per-item output, distinct nodes, the key file
# ---------------------------------------------------------------------------

def _copy_of(cfg, cp, original=EVID):
    item = {"id": cp, "kind": "meeting", "at": "2026-03-05T10:00:00", "title": "Daily sync", "source": "meetings",
            "text": "placeholder", "date": "2026-03-05", "duplicate_of": original}
    S.P(cfg.store, "evidence", cp + ".json").write_text(json.dumps(item))


def test_work_found_on_a_duplicate_copy_counts_for_the_original(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    cp = "2026-03-05_meetings_ee55ff66"
    _copy_of(cfg, cp)
    _task(cfg, "T001", evid=cp)          # extracted from the re-synced copy only
    _processed(cfg)
    result = score(cfg.store, [_gold()])
    assert result["B7"]["ok"] == 1


def test_items_list_is_complete(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _processed(cfg)
    gold = [_gold(quote=f"Nora will write onboarding checklist number {i} and send it to Opal today") for i in range(25)]
    for i, g in enumerate(gold):
        g["cid"] = f"c{i:02d}"
    result = score(cfg.store, gold)
    assert len(result["B7"]["fails"]) == 20                       # display stays capped
    assert [it["cid"] for it in result["items"]] == [g["cid"] for g in gold]
    assert all(it["match"] is None for it in result["items"])


def test_distinct_nodes_counted_once(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _task(cfg, "T001")
    _processed(cfg)
    gold = [_gold(covered_by="T001"), _gold(quote=QUOTE_B, covered_by="T001")]
    result = score(cfg.store, gold)
    assert result["B7"]["ok"] == 2
    assert result["B7"]["distinct_nodes"] == 1


def test_load_gold_reads_the_header_and_checks_the_fingerprint(tmp_path):
    from taska_lib.evalscore import freeze_gold, load_gold
    p = tmp_path / "items.jsonl"
    p.write_text("# gold v1 2026-03-06 n=2\n" + json.dumps(_gold()) + "\n" + json.dumps(_gold(quote=QUOTE_B)) + "\n")
    freeze_gold(p)
    head, items = load_gold(p)
    assert head["version"] == "v1" and head["n"] == 2 and len(head["sha256"]) == 64 and len(items) == 2
    p.write_text(p.read_text().replace("Ravi", "Rava"))
    with pytest.raises(ValueError, match="sha256"):
        load_gold(p)


def test_load_gold_accepts_a_file_without_a_header(tmp_path):
    from taska_lib.evalscore import load_gold
    p = tmp_path / "items.jsonl"
    p.write_text(json.dumps(_gold()) + "\n")
    head, items = load_gold(p)
    assert head == {} and len(items) == 1


def _propose(cfg, quote=QUOTE_A, mid="p1"):
    S.append(cfg.store, [{"type": "agent", "op": "propose", "mid": mid, "by": "agent", "evidence": EVID, "date": "2026-03-02",
                          "node": "S001", "quote": quote, "quote_norm": quote, "pending": "human",
                          "proposal": {"kind": "task", "title": "Onboarding checklist", "type": "action"}}])


def test_a_confirmed_card_counts_as_its_node(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _propose(cfg)
    _processed(cfg)
    L.human_event(cfg.store, {"op": "accept", "target": "p1"})
    r = score(cfg.store, [_gold()])
    assert r["B7"]["ok"] == 1 and r["items"][0]["match"].startswith("node:T")


def test_a_rejected_card_is_not_coverage(tmp_path):
    cfg = _cfg(tmp_path)
    _meeting(cfg)
    _epics(cfg)
    _propose(cfg)
    _processed(cfg)
    L.human_event(cfg.store, {"op": "reject", "target": "p1", "reason": "not_agreed"})
    r = score(cfg.store, [_gold()])
    assert r["items"][0]["match"] is None and r["carded"] == 0
