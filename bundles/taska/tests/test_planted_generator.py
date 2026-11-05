"""Tests for the planted meeting generator. Invented data only (Harbor Logistics world)."""
import hashlib
import json
import re
import sys
import textwrap
from collections import Counter
from pathlib import Path

import pytest

# Ensure run dir and planted dir are on sys.path
_TASKA_ROOT = Path(__file__).resolve().parents[1]
_PLANTED_DIR = _TASKA_ROOT / "evals" / "planted"
_RUN_DIR = _TASKA_ROOT / "skills" / "run"
for _p in (_RUN_DIR, _PLANTED_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from generate import (_TEMPLATES, call_model, generate, generate_planning, generate_workshop, keep, not_work_recorded,
                      per_form, prepare, vtt_name, vtt_name_planning, vtt_name_workshop)
from taska_lib import coverage as CV, store as S, config as C, transcripts as TR
from taska_lib.leftovers import DECISION_CUES, MARKERS
from taska_lib.sensitive import people_matter
from taska_lib.text import norm

CAST = ("Nora Quill", "Ravi Tamsin", "Opal Brennick")
DAILY_SYNC_SEED1_SHA = "2207ec497629343e4e7d45513afbf2c97857d591a7446d5647c092fa1b81812f"   # generate(1) before the planning review
FORMS = {"implicit_decision": 3, "client_decision": 1, "cadence_decision": 1, "implicit_risk": 1,
         "open_question": 1, "hypothetical": 1, "people_matter": 2}
DECISION_FORMS = ("implicit_decision", "client_decision", "cadence_decision", "implicit_risk")


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


def test_keep_saves_a_run_that_replays_anywhere(tmp_path):
    """A kept run holds what scoring needs (config, gold, the ledger and its evidence, the meeting) and no path of
    the machine it ran on, so it can be committed and replayed by a test."""
    run = tmp_path / "run"
    (run / "calls").mkdir(parents=True)
    (run / "calls" / "m.vtt").write_text("WEBVTT\n")
    (run / ".taska" / "evidence").mkdir(parents=True)
    (run / ".taska" / "events.jsonl").write_text("{}\n")
    (run / ".taska" / "evidence" / "e.json").write_text("{}")
    (run / "gold.json").write_text("[]")
    (run / "taska.toml").write_text(f'[project]\nname = "Harbor Logistics"\n[sources.meetings]\npath = "{run / "calls"}"\n')
    dest = keep(run, tmp_path / "kept", 3)
    assert dest == tmp_path / "kept" / "seed-3"
    assert {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()} == {
        "calls/m.vtt", ".taska/events.jsonl", ".taska/evidence/e.json", "gold.json", "taska.toml"}
    assert str(tmp_path) not in (dest / "taska.toml").read_text() and 'path = "calls"' in (dest / "taska.toml").read_text()


def test_a_configured_model_command_is_called_once_with_the_arguments(tmp_path, monkeypatch):
    """TASKA_AI names the program that reaches the model (a launcher choosing its own sign-in); without it the
    runner keeps calling claude itself."""
    log = tmp_path / "calls"
    ai = tmp_path / "ai"
    ai.write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    ai.chmod(0o755)
    monkeypatch.setenv("TASKA_AI", str(ai))
    assert call_model(["-p", "hello", "--permission-mode", "acceptEdits"]) == 0
    assert log.read_text().splitlines() == ["-p hello --permission-mode acceptEdits"]


# ── the planning review ──────────────────────────────────────────────────────

def test_daily_sync_unchanged():
    assert hashlib.sha256(generate(1)[0].encode()).hexdigest() == DAILY_SYNC_SEED1_SHA


def test_planning_meeting_has_every_form():
    _, gold = generate_planning(1)
    assert Counter(g["form"] for g in gold) == FORMS


def test_planning_is_deterministic_and_differs_by_seed():
    assert generate_planning(3) == generate_planning(3)
    assert generate_planning(1)[0] != generate_planning(2)[0]


def test_planning_quotes_are_verbatim_and_the_meeting_is_one_part(tmp_path):
    # the decision gate reads it as one part: the per-third gate is exercised on real three-part meetings, not here
    vtt, gold = generate_planning(1)
    assert vtt.startswith("WEBVTT")
    assert all(g["quote"] in vtt for g in gold)
    (tmp_path / "m.vtt").write_text(vtt)
    assert CV.parts_for({"turns": TR.stitch(TR.read_cues(tmp_path / "m.vtt"))}) == 1


def test_decisions_carry_no_cue_words():
    for s in _TEMPLATES["decision_slots"]:
        if s["form"] in DECISION_FORMS:
            for text in (s["utterance"].lower(), norm(s["utterance"])):
                assert not DECISION_CUES.search(text) and not MARKERS.search(text), s["utterance"]


def test_people_matter_slots_are_caught_by_the_guard():
    slots = _TEMPLATES["decision_slots"]
    for s in slots:   # a people matter is caught by its wording about a person, not by a cue word
        if s["form"] == "people_matter":
            assert not DECISION_CUES.search(norm(s["utterance"])) and not MARKERS.search(norm(s["utterance"]))
    assert all(people_matter(s["utterance"], names=CAST) for s in slots if s["form"] == "people_matter")
    # the rest is work: a guard hit would refuse it before the reader is measured
    assert not any(people_matter(s["utterance"], names=CAST) for s in slots if s["form"] != "people_matter")


def test_cast_is_invented():
    assert {s["speaker"] for s in _TEMPLATES["decision_slots"]} <= set(CAST)


def test_planning_gold_fields():
    _, gold = generate_planning(1)
    for g in gold:
        assert g["file"] == vtt_name_planning(1) and g["evidence"] is None and g["owner"] is None
        assert g["kind"] == {"implicit_risk": "risk"}.get(g["form"], "decision" if g["form"] in DECISION_FORMS else None)
        assert g["commitment"] == ("committed" if g["form"] in DECISION_FORMS else "not_work")
        assert g.get("people_matter", False) is (g["form"] == "people_matter")
    assert all(g["file"] == vtt_name(1) for g in generate(1)[1])


def test_planning_name_is_dated_like_the_daily_sync():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} Harbor planning review\.vtt", vtt_name_planning(1))
    assert vtt_name_planning(4)[:10] == vtt_name(4)[:10]


def test_prepare_syncs_both_meetings_and_gives_each_item_its_own_evidence(tmp_path):
    """A real sync of both meetings: each gold item is patched with the evidence of the file it came from."""
    taska = [sys.executable, str(_RUN_DIR / "taska.py")]
    gold = prepare(tmp_path, 2, taska)
    assert gold is not None and len(gold) == 35
    ev = S.evidence(tmp_path / ".taska")
    by_file = {g["file"]: g["evidence"] for g in gold}
    assert set(by_file) == {vtt_name(2), vtt_name_planning(2), vtt_name_workshop(2)}
    assert len({g["evidence"] for g in gold}) == 3
    for g in gold:
        assert ev[g["evidence"]]["title"] == Path(g["file"]).stem
        assert S.quote_offset(ev[g["evidence"]], g["quote"]) >= 0
    assert json.loads((tmp_path / "gold.json").read_text()) == gold


# ── per_form on the planning review ─────────────────────────────────────────

PLAN_EV = "2026-03-03_meetings_pp000001"


def _planning_store(tmp_path, nodes, cards=(), notes=()):
    """A processed planning review whose ledger holds `nodes` ((form, kind) pairs: a node of that kind quoting
    each utterance of that form), pending propose cards quoting each utterance of the `cards` forms, and notes
    ((form, node id) pairs) quoting that form's utterances on a node already made."""
    cfg = _make_store(tmp_path)
    vtt, gold = generate_planning(1)
    (tmp_path / "m.vtt").write_text(vtt)
    S.write_evidence(cfg.store, {"id": PLAN_EV, "kind": "meeting", "at": "2026-03-03T10:00:00",
                                 "title": "2026-03-03 Harbor planning review", "source": "meetings",
                                 "turns": TR.stitch(TR.read_cues(tmp_path / "m.vtt"))})
    quotes = [(g["form"], g["quote"]) for g in gold]
    events = [{"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None,
               "date": "2026-03-01", "fields": {"title": "Harbor"}},
              {"type": "create", "mid": "s1", "id": "S001", "kind": "story", "by": "agent", "evidence": None,
               "date": "2026-03-01", "fields": {"title": "Pilot", "parent": "E01", "ac": ["x"]}}]
    prefix = {"decision": "D", "risk": "R", "task": "T"}
    made = [(kind, q) for form, kind in nodes for f, q in quotes if f == form]
    for i, (kind, q) in enumerate(made, start=1):
        events.append({"type": "create", "mid": f"n{i}", "id": f"{prefix[kind]}{i:03d}", "kind": kind, "by": "agent",
                       "evidence": PLAN_EV, "date": "2026-03-03", "quote": q, "quote_norm": q,
                       "fields": {"title": q[:40], "parent": "S001" if kind == "task" else "E01",
                                  "type": "action" if kind == "task" else None}})
    for i, q in enumerate([q for form in cards for f, q in quotes if f == form], start=1):
        events.append({"type": "agent", "op": "propose", "mid": f"p{i}", "by": "agent", "evidence": PLAN_EV,
                       "date": "2026-03-03", "node": "S001", "quote": q, "quote_norm": q,
                       "pending": "human", "proposal": {"kind": "task", "title": q[:40], "type": "action"}})
    for i, (form, node) in enumerate(notes, start=1):
        for q in [q for f, q in quotes if f == form]:
            events.append({"type": "note", "mid": f"m{i}", "node": node, "by": "agent", "evidence": PLAN_EV,
                           "date": "2026-03-03", "quote": q, "quote_norm": q, "text": "context"})
    events.append({"type": "processed", "evidence": PLAN_EV, "by": "agent"})
    S.append(cfg.store, events)
    for g in gold:
        g["evidence"] = PLAN_EV
    return cfg, gold


ALL_RIGHT = [("implicit_decision", "decision"), ("client_decision", "decision"), ("cadence_decision", "decision"),
             ("implicit_risk", "risk")]


def _rates(cfg, gold):
    r = per_form(cfg.store, gold)
    assert not r["incomplete"]
    return {f: (c["ok"], c["n"]) for f, c in r["forms"].items()}


def test_per_form_a_right_ledger_passes_every_form(tmp_path):
    # the people matters sit within a few hundred characters of real decisions: only a node quoting one fails it
    cfg, gold = _planning_store(tmp_path, ALL_RIGHT)
    assert _rates(cfg, gold) == {f: (n, n) for f, n in FORMS.items()}


def test_per_form_a_decision_needs_a_decision_node(tmp_path):
    cfg, gold = _planning_store(tmp_path, [("client_decision", "task"), ("implicit_risk", "decision")])
    rates = _rates(cfg, gold)
    assert rates["client_decision"] == (0, 1) and rates["implicit_risk"] == (0, 1)
    assert rates["cadence_decision"] == (0, 1)


def test_per_form_a_decision_found_beside_a_task_counts(tmp_path):
    cfg, gold = _planning_store(tmp_path, [("cadence_decision", "task"), ("cadence_decision", "decision")])
    assert _rates(cfg, gold)["cadence_decision"] == (1, 1)


def test_per_form_an_open_question_or_hypothetical_recorded_as_a_decision_fails(tmp_path):
    cfg, gold = _planning_store(tmp_path, [("open_question", "decision"), ("hypothetical", "decision")])
    rates = _rates(cfg, gold)
    assert rates["open_question"] == (0, 1) and rates["hypothetical"] == (0, 1)


def test_per_form_an_open_question_kept_as_a_task_is_not_a_decision(tmp_path):
    cfg, gold = _planning_store(tmp_path, [("open_question", "task")])
    assert _rates(cfg, gold)["open_question"] == (1, 1)


def test_per_form_a_people_matter_tracked_in_any_form_fails(tmp_path):
    cfg, gold = _planning_store(tmp_path, [("people_matter", "task")])
    assert _rates(cfg, gold)["people_matter"] == (0, 2)


def test_per_form_a_people_matter_on_a_card_fails(tmp_path):
    cfg, gold = _planning_store(tmp_path, ALL_RIGHT, cards=["people_matter"])
    assert _rates(cfg, gold)["people_matter"] == (0, 2)



def test_per_form_a_hypothetical_noted_on_a_decision_already_made_passes(tmp_path):
    # D001 is the first decision of ALL_RIGHT (an implicit decision); the hypothetical is context on it, not a decision
    cfg, gold = _planning_store(tmp_path, ALL_RIGHT, notes=[("hypothetical", "D001"), ("open_question", "D001")])
    rates = _rates(cfg, gold)
    assert rates["hypothetical"] == (1, 1) and rates["open_question"] == (1, 1)


def test_per_form_a_people_matter_noted_on_a_decision_fails(tmp_path):
    cfg, gold = _planning_store(tmp_path, ALL_RIGHT, notes=[("people_matter", "D001")])
    assert _rates(cfg, gold)["people_matter"] == (0, 2)


# ── the workshop prep meeting ────────────────────────────────────────────────

BULLETS = {"deck_handling", "agenda_invite", "poster", "session_order", "how_might_we", "client_pain"}
WORK_FORMS = ("commitment", "request", "due_phrase")


def test_workshop_is_deterministic_and_differs_by_seed():
    assert generate_workshop(3) == generate_workshop(3)
    assert generate_workshop(1)[0] != generate_workshop(2)[0]


def test_workshop_has_three_work_items_and_the_rest_not_work():
    vtt, gold = generate_workshop(1)
    work = [g for g in gold if g["form"] != "not_work"]
    not_work = [g for g in gold if g["form"] == "not_work"]
    assert len(work) == 3 and all(g["form"] in WORK_FORMS and g["owner"] and g["commitment"] == "committed" for g in work)
    assert len(not_work) == 8
    assert all(g["commitment"] == "not_work" and g["kind"] is None and g["owner"] is None for g in not_work)
    assert Counter(g["bullet"] for g in not_work) == {"deck_handling": 2, "agenda_invite": 1, "poster": 1,
                                                        "session_order": 1, "how_might_we": 1, "client_pain": 2}
    assert sum(g["bullet"] != "client_pain" for g in not_work) >= 6
    assert not any("bullet" in g for g in work)


def test_workshop_pain_points_are_phrased_as_problems():
    _, gold = generate_workshop(1)
    pains = [g for g in gold if g.get("bullet") == "client_pain"]
    assert len(pains) == 2 and all(g["speaker"] == "Opal Brennick" for g in pains)
    assert any("intro pricing invites fraud" in g["quote"] for g in pains)


def test_workshop_work_items_never_talk_about_decks():
    _, gold = generate_workshop(1)
    for g in gold:
        if g["form"] != "not_work":
            assert not re.search(r"\b(deck|slides?|poster|agenda|invite)\b", g["quote"], re.I), g["quote"]


def test_workshop_vtt_is_parseable_with_verbatim_quotes_and_one_file_name(tmp_path):
    vtt, gold = generate_workshop(1)
    assert vtt.startswith("WEBVTT")
    assert all(g["quote"] in vtt and g["evidence"] is None and g["file"] == vtt_name_workshop(1) for g in gold)
    (tmp_path / "m.vtt").write_text(vtt)
    text = " ".join(t["text"] for t in TR.stitch(TR.read_cues(tmp_path / "m.vtt")))
    assert all(g["quote"] in text for g in gold)
    assert {g["speaker"] for g in gold} <= set(CAST)


def test_workshop_name_is_dated_like_the_other_meetings():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} Harbor workshop prep\.vtt", vtt_name_workshop(1))
    assert vtt_name_workshop(4)[:10] == vtt_name(4)[:10] == vtt_name_planning(4)[:10]


def test_workshop_templates_are_invented_and_flagged():
    slots = _TEMPLATES["workshop_slots"]
    assert len(slots) == 11 and {s["speaker"] for s in slots} <= set(CAST)
    assert all(("bullet" in s) == (s["form"] == "not_work") for s in slots)
    assert {s["bullet"] for s in slots if s["form"] == "not_work"} == BULLETS


PLANNING_SEED1_SHA = "d5381026db0bc711141ef3ad147af6c8eedabfdbaaa3e44f25071be6bf6726fe"


def test_planning_review_unchanged_by_the_workshop():
    assert hashlib.sha256(generate_planning(1)[0].encode()).hexdigest() == PLANNING_SEED1_SHA


def test_daily_sync_unchanged_by_the_workshop():
    assert hashlib.sha256(generate(1)[0].encode()).hexdigest() == DAILY_SYNC_SEED1_SHA


# ── not_work_recorded ────────────────────────────────────────────────────────

WS_EV = "2026-03-03_meetings_ww000001"


def _workshop_store(tmp_path, events):
    cfg = _make_store(tmp_path)
    _, gold = generate_workshop(1)
    by_bullet = {g.get("bullet"): g for g in gold if g["form"] == "not_work"}
    work = next(g for g in gold if g["form"] == "commitment")
    deck, pain = by_bullet["deck_handling"], by_bullet["client_pain"]
    # one turn holds a deck-handling utterance and a second sentence of the same turn
    extra = "Also the second table needs a caption."
    S.write_evidence(cfg.store, {"id": WS_EV, "kind": "meeting", "at": "2026-03-03T10:00:00", "title": "ws",
                                 "source": "meetings", "turns": [
        {"speaker": deck["speaker"], "ts": "00:01:00", "text": deck["quote"] + " " + extra},
        {"speaker": pain["speaker"], "ts": "00:02:00", "text": pain["quote"]},
        {"speaker": work["speaker"], "ts": "00:03:00", "text": work["quote"]}]})
    S.append(cfg.store, events(deck, pain, work, extra))
    for g in gold:
        g["evidence"] = WS_EV
    return cfg, gold


def _create(mid, nid, kind, quote, by="agent", ev=WS_EV, **kw):
    return {"type": "create", "mid": mid, "id": nid, "kind": kind, "by": by, "evidence": ev, "date": "2026-03-03",
            "quote": quote, "quote_norm": quote, "fields": {"title": f"title {nid}"}, **kw}


def test_not_work_recorded_counts_agent_creates_and_proposals_on_not_work_turns(tmp_path):
    def events(deck, pain, work, extra):
        return [_create("a", "T001", "task", deck["quote"]),
                _create("b", "R001", "risk", pain["quote"]),
                _create("c", "T002", "task", extra),     # same turn as the deck utterance
                _create("d", "T003", "task", work["quote"]),     # real work: not counted
                _create("e", "T004", "task", deck["quote"], by="human"),
                _create("f", "T005", "task", deck["quote"], ev="2026-03-02_meetings_other0001"),
                {"type": "agent", "op": "propose", "mid": "g", "by": "agent", "evidence": WS_EV, "node": "S001",
                 "date": "2026-03-03", "quote": pain["quote"], "quote_norm": pain["quote"], "pending": "human",
                 "proposal": {"kind": "task", "title": "Fix pain", "type": "action"}},
                {"type": "processed", "evidence": WS_EV, "by": "agent"}]
    cfg, gold = _workshop_store(tmp_path, events)
    r = not_work_recorded(cfg.store, gold)
    assert r["recorded"] == 4
    assert {i["id"] for i in r["items"]} >= {"T001", "R001", "T002"}
    by_id = {i["id"]: i for i in r["items"]}
    assert by_id["T001"]["bullet"] == "deck_handling" and by_id["T001"]["kind"] == "task"
    assert by_id["T002"]["bullet"] == "deck_handling" and by_id["R001"]["bullet"] == "client_pain"
    assert by_id["R001"]["title"] == "title R001"
    assert any(i["title"] == "Fix pain" and i["bullet"] == "client_pain" for i in r["items"])
    assert not any(i["id"] in ("T003", "T004", "T005") for i in r["items"])


def test_not_work_recorded_is_zero_for_a_clean_ledger(tmp_path):
    cfg, gold = _workshop_store(tmp_path, lambda deck, pain, work, extra: [
        _create("d", "T003", "task", work["quote"]), {"type": "processed", "evidence": WS_EV, "by": "agent"}])
    assert not_work_recorded(cfg.store, gold) == {"recorded": 0, "items": []}


def test_per_form_scores_the_work_items_and_ignores_not_work(tmp_path):
    cfg, gold = _workshop_store(tmp_path, lambda deck, pain, work, extra: [
        _create("d", "T003", "task", work["quote"]), {"type": "processed", "evidence": WS_EV, "by": "agent"}])
    r = per_form(cfg.store, gold)
    assert not r["incomplete"] and "not_work" not in r["forms"]
    assert r["forms"]["commitment"] == {"n": 1, "ok": 1, "rate": 1.0}


def test_not_work_recorded_ignores_a_work_item_sharing_a_turn_with_a_not_work_line(tmp_path):
    cfg = _make_store(tmp_path)
    _, gold = generate_workshop(1)
    deck = next(g for g in gold if g.get("bullet") == "deck_handling")
    work = next(g for g in gold if g["form"] == "commitment")
    S.write_evidence(cfg.store, {"id": WS_EV, "kind": "meeting", "at": "2026-03-03T10:00:00", "title": "ws",
                                 "source": "meetings", "turns": [
        {"speaker": deck["speaker"], "ts": "00:01:00", "text": deck["quote"] + " " + work["quote"]}]})
    S.append(cfg.store, [_create("a", "T001", "task", work["quote"]),
                         {"type": "processed", "evidence": WS_EV, "by": "agent"}])
    for g in gold:
        g["evidence"] = WS_EV
    assert not_work_recorded(cfg.store, gold) == {"recorded": 0, "items": []}
