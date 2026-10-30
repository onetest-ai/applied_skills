"""Tests for the planted meeting generator. Invented data only (Harbor Logistics world)."""
import json
import sys
import textwrap
from pathlib import Path

import pytest

# Ensure run dir and planted dir are on sys.path
_TASKA_ROOT = Path(__file__).resolve().parents[1]
_PLANTED_DIR = _TASKA_ROOT / "evals" / "planted"
_RUN_DIR = _TASKA_ROOT / "skills" / "run"
for _p in (_RUN_DIR, _PLANTED_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from generate import generate, per_form, vtt_name
from taska_lib import store as S, config as C


# ── helpers ──────────────────────────────────────────────────────────────────

def _ts_sec(ts):
    """'HH:MM:SS' -> seconds."""
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


# ── generator tests ───────────────────────────────────────────────────────────

def test_generator_is_deterministic():
    vtt1, gold1 = generate(42)
    vtt2, gold2 = generate(42)
    assert vtt1 == vtt2
    assert gold1 == gold2


def test_different_seeds_differ():
    vtt1, _ = generate(1)
    vtt2, _ = generate(2)
    assert vtt1 != vtt2


def test_gold_has_14_items():
    _, gold = generate(1)
    assert len(gold) == 14


def test_every_third_has_four_items():
    """First temporal third has exactly 4 items; second and third have 5 each (4/5/5)."""
    vtt, gold = generate(1)
    total = 30 * 60  # default minutes=30
    counts = [0, 0, 0]
    for g in gold:
        s = _ts_sec(g["ts"])
        counts[min(2, int(s * 3 / total))] += 1
    assert counts == [4, 5, 5], f"expected [4,5,5], got {counts}"


def test_gold_quotes_are_verbatim_in_the_vtt():
    vtt, gold = generate(1)
    for g in gold:
        assert g["quote"] in vtt, f"quote not found in VTT: {g['quote']!r}"


def test_only_invented_names():
    """Only Harbor Logistics invented names; no real-world identifiers. The generator composes templates.json
    (scanned by test_public_data.py) with the speakers below, so an address is the one thing left to check."""
    _ALLOWED_SPEAKERS = {"nora quill", "ravi tamsin", "opal brennick"}
    vtt, gold = generate(1)
    combined = (vtt + " " + json.dumps(gold)).lower()
    for forbidden in ("@",):
        assert forbidden not in combined, f"found forbidden string {forbidden!r}"
    for g in gold:
        assert g["speaker"].lower() in _ALLOWED_SPEAKERS, f"unexpected speaker: {g['speaker']!r}"


def test_all_forms_covered():
    """All required forms appear in the gold list."""
    _, gold = generate(1)
    forms = {g["form"] for g in gold}
    required = {
        "commitment", "request", "due_phrase", "client_owed",
        "proposal_team", "proposal_none", "conditional_decision",
        "risk", "declined",
    }
    assert not required - forms, f"missing forms: {required - forms}"


def test_evidence_is_null():
    """Gold evidence ids are None before taska sync."""
    _, gold = generate(1)
    assert all(g["evidence"] is None for g in gold)


def test_due_phrase_items_have_due_dates():
    """due_phrase form items carry a resolved due date."""
    _, gold = generate(1)
    due_items = [g for g in gold if g["form"] == "due_phrase"]
    assert due_items, "no due_phrase items in gold"
    for g in due_items:
        assert g["due"] is not None, f"due_phrase item missing due: {g}"


def test_client_owed_item():
    """client_owed items are committed, flag client_owed, and have Opal as owner."""
    _, gold = generate(1)
    co = [g for g in gold if g["form"] == "client_owed"]
    assert co, "no client_owed items"
    for g in co:
        assert g["client_owed"] is True
        assert g["owner"] == "Opal Brennick"
        assert g["commitment"] == "committed"


def test_proposal_team_owner():
    _, gold = generate(1)
    pt = [g for g in gold if g["form"] == "proposal_team"]
    assert pt
    for g in pt:
        assert g["owner"] == "Yard team"
        assert g["commitment"] == "proposed"


def test_declined_items():
    _, gold = generate(1)
    dec = [g for g in gold if g["form"] == "declined"]
    assert dec
    for g in dec:
        assert g["commitment"] == "declined"


def test_vtt_name_format():
    name = vtt_name(1)
    assert name.endswith(" Harbor daily sync.vtt")
    # Date part is YYYY-MM-DD
    import re
    assert re.match(r"\d{4}-\d{2}-\d{2} Harbor daily sync\.vtt", name)


def test_vtt_starts_with_webvtt():
    vtt, _ = generate(1)
    assert vtt.startswith("WEBVTT")


# ── per_form tests ────────────────────────────────────────────────────────────

def _make_store(tmp_path):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
        side = "internal"
        [[people]]
        name = "Ravi Tamsin"
        side = "internal"
        [[people]]
        name = "Opal Brennick"
        side = "client"
        [[teams]]
        name = "Yard team"
        members = ["Nora Quill", "Ravi Tamsin"]
        aliases = ["yard guys", "the yard people"]
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    return cfg


def test_incomplete_run_reports_no_score(tmp_path):
    """per_form returns {"incomplete": True} when no processed event for the meeting."""
    cfg = _make_store(tmp_path)
    evid = "2026-03-02_meetings_ab12cd34"
    S.write_evidence(cfg.store, {
        "id": evid, "kind": "meeting", "at": "2026-03-02T10:00:00",
        "title": "Harbor daily sync", "source": "meetings",
        "turns": [{"ts": "00:00:05", "speaker": "Nora Quill",
                   "text": "I will draft the checklist.", "bc": False}],
    })
    # Patch gold with the evidence id but do NOT emit a "processed" event
    _, gold = generate(1)
    for g in gold:
        g["evidence"] = evid

    result = per_form(cfg.store, gold)
    assert result == {"incomplete": True}


def test_per_form_with_no_evidence_in_gold_is_incomplete(tmp_path):
    """per_form returns {"incomplete": True} when gold evidence ids are all None."""
    cfg = _make_store(tmp_path)
    _, gold = generate(1)
    # evidence remains None
    result = per_form(cfg.store, gold)
    assert result == {"incomplete": True}


def _declined_store(tmp_path, kind):
    (tmp_path / "taska.toml").write_text('[project]\nname = "Harbor Logistics"\n[[people]]\nname = "Nora Quill"\n')
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    ev = "2026-03-03_meetings_dd000001"
    q = "The proposal to add a second reporting cycle per month was reviewed and the team decided not to proceed."
    S.write_evidence(cfg.store, {"id": ev, "kind": "meeting", "at": "2026-03-03T10:00:00", "title": "Harbor daily sync",
                                 "source": "meetings", "turns": [{"speaker": "Nora Quill", "ts": "00:01:00", "text": q}]})
    S.append(cfg.store, [
        {"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Ops"}},
        {"type": "create", "mid": "s1", "id": "S001", "kind": "story", "by": "agent", "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Reporting", "parent": "E01", "ac": ["x"]}},
        {"type": "create", "mid": "n1", "id": "D001" if kind == "decision" else "T001", "kind": kind, "by": "agent",
         "evidence": ev, "date": "2026-03-03", "quote": q, "quote_norm": q,
         "fields": {"title": "No second monthly reporting cycle", "parent": "S001" if kind == "task" else "E01",
                    "type": "action" if kind == "task" else None}},
        {"type": "processed", "evidence": ev, "by": "agent"}])
    gold = [{"evidence": ev, "quote": q, "form": "declined", "commitment": "declined", "kind": "task"}]
    return cfg, gold


def test_a_declined_item_recorded_as_a_decision_passes(tmp_path):
    # B16 is "declined work created anyway": a decision recording the "no" is not the work (ruling, Oct 25)
    cfg, gold = _declined_store(tmp_path, "decision")
    assert per_form(cfg.store, gold)["forms"]["declined"]["ok"] == 1


def test_a_declined_item_turned_into_a_task_fails(tmp_path):
    cfg, gold = _declined_store(tmp_path, "task")
    assert per_form(cfg.store, gold)["forms"]["declined"]["ok"] == 0
