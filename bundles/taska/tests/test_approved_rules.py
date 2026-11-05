"""The approved product rules (evals/rules.json): stable ids, approved status, one shared block in every prompt that
reads meetings, positive and negative fixtures for each, and DUE-RELATIVE in the date resolver. Invented data only."""
import json
import re

import pytest

from conftest import TASKA_ROOT
from taska_lib.text import resolve_due

RULES = json.loads((TASKA_ROOT / "evals" / "rules.json").read_text())
FIXTURES = [json.loads(l) for l in (TASKA_ROOT / "evals" / "fixtures" / "rules.jsonl").read_text().splitlines() if l.strip()]
PROMPTS = [TASKA_ROOT / "evals" / "READER.md", TASKA_ROOT / "evals" / "JUDGE.md", TASKA_ROOT / "skills" / "run" / "EXTRACT.md"]


def _block(path):
    m = re.search(r"<!-- READER-BLOCK:approved-rules:START -->(.*?)<!-- READER-BLOCK:approved-rules:END -->", path.read_text(), re.S)
    return m.group(1) if m else ""


APPROVED = [r for r in RULES if r["status"] == "approved"]


def test_every_rule_has_a_stable_id_a_version_and_a_status():
    assert [r["id"] for r in APPROVED] == ["DUE-RELATIVE", "DELIVERABLE-IN-PARENT", "REMINDER-ATTACH", "DECISION-STANDING",
                                           "PREP-RESEARCH", "CLIENT-COMMITMENT", "JOINT-WORK"]
    assert all(r["version"] == 1 and r["approved_on"] == "2026-11-04" and r["applies_to"] for r in APPROVED)
    assert all(r["status"] in ("approved", "proposed") for r in RULES)


@pytest.mark.parametrize("path", PROMPTS + [TASKA_ROOT / "evals" / "QUALITY_JUDGE.md"], ids=lambda p: p.name)
def test_a_proposed_rule_is_in_no_prompt_until_approved(path):
    text = path.read_text()
    assert not [r["id"] for r in RULES if r["status"] == "proposed" and r["id"] in text]


def test_fixtures_of_a_proposed_rule_say_so_and_approved_ones_do_not():
    for f in FIXTURES:
        rule = next(r for r in RULES if r["id"] == f["rule"])
        assert f.get("status", "approved") == rule["status"], f
    assert all(re.fullmatch(r"[A-Z][A-Z0-9]*(-[A-Z0-9]+)*", r["id"]) for r in RULES)


@pytest.mark.parametrize("path", PROMPTS, ids=[p.name for p in PROMPTS])
def test_every_prompt_carries_each_rule_at_its_version(path):
    block = _block(path)
    assert all(f"**{r['id']} v{r['version']}.**" in block for r in APPROVED)


def test_quality_judge_names_the_rules_it_scores():
    text = (TASKA_ROOT / "evals" / "QUALITY_JUDGE.md").read_text()
    assert "DELIVERABLE-IN-PARENT" in text and "approved-rules" in text


@pytest.mark.parametrize("rule", [r["id"] for r in RULES])
def test_each_rule_has_positive_and_negative_fixtures(rule):
    pol = {f["polarity"] for f in FIXTURES if f["rule"] == rule}
    assert pol == {"positive", "negative"}


@pytest.mark.parametrize("words, due", [("by end of the week", "2026-03-06"), ("end of week", "2026-03-06"),
                                        ("soon", None), ("after the readout", None)])
def test_due_relative_resolves_end_of_week_to_the_meetings_friday_and_leaves_vague_dates_open(words, due):
    assert resolve_due(words, "2026-03-03") == due                 # a Tuesday


def test_end_of_week_said_on_a_friday_is_that_friday():
    assert resolve_due("end of the week", "2026-03-06") == "2026-03-06"


# ---------- "before <day>": no approved convention, so the phrase is kept and the date stays unresolved ----------

@pytest.mark.parametrize("words", ["before Monday", "finish before Monday", "before Monday morning", "before the 12th",
                                   "before end of week"])
def test_before_a_day_never_becomes_that_day_or_the_day_before(words):
    assert resolve_due(words, "2026-10-16") is None                 # a Friday: neither Monday 19th nor Friday 16th


@pytest.mark.parametrize("words, due", [("by the end of this week", "2026-10-16"), ("by Friday, 16 October", "2026-10-16"),
                                        ("by Monday", "2026-10-19")])
def test_stated_completion_dates_still_resolve(words, due):
    assert resolve_due(words, "2026-10-16") == due


def test_an_unresolved_due_keeps_its_words_on_the_node(tmp_path):
    from taska_lib import config as C, ledger as L, store as S
    (tmp_path / "taska.toml").write_text('[project]\nname = "Harbor Logistics"\n[[people]]\nname = "Nora Quill"\n')
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    q = "Nora will finish the onboarding checklist before Monday so the yard team can use it"
    S.write_evidence(cfg.store, {"id": "2026-10-16_meetings_ab12cd34", "kind": "meeting", "at": "2026-10-16T10:00:00",
                                 "title": "Sync", "source": "meetings", "text": q})
    S.append(cfg.store, [{"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None,
                          "date": None, "fields": {"title": "Onboarding"}}])
    r = L.apply(cfg.store, cfg, "2026-10-16_meetings_ab12cd34", [
        {"op": "create", "kind": "story", "parent": "E01", "title": "Onboarding ready", "ac": ["Checklist done"], "quote": q, "ref": "s"},
        {"op": "create", "kind": "task", "parent": "s", "type": "action", "title": "Finish the onboarding checklist",
         "internal_owner": "Nora Quill", "due_text": "before Monday", "quote": q}])
    assert r["rejected"] == [], r
    create = next(e for e in S.events(cfg.store) if e.get("type") == "create" and e.get("kind") == "task")
    assert create["fields"]["due"] is None and create["fields"]["due_text"] == "before Monday"


@pytest.mark.parametrize("path", PROMPTS[:2], ids=["READER.md", "JUDGE.md"])
def test_labels_keep_deadline_words_beside_an_unresolved_date(path):
    text = " ".join(path.read_text().split())
    assert '"due_text"' in text and '"due_unresolved"' in text and "keep the words in `due_text`, set `due` null" in text


def test_the_quality_judge_asks_for_the_words_that_state_each_deliverable():
    text = " ".join((TASKA_ROOT / "evals" / "QUALITY_JUDGE.md").read_text().split())
    assert "also give `explicit_text`, the words in that place that state this deliverable" in text
    assert "Never give the same words for two different actions." in text


# ---------- the four definitions approved on 4 Nov (round-2 decision packets) ----------

def _flat_block(name):
    m = re.search(rf"<!-- READER-BLOCK:{name}:START -->(.*?)<!-- READER-BLOCK:{name}:END -->", PROMPTS[2].read_text(), re.S)
    return " ".join(m.group(1).split())


def test_client_work_the_client_undertook_is_committed_in_the_shared_commitment_block():
    assert ("work the client explicitly undertook is `committed`, with the client's person as owner and `client_owed` true"
            in _flat_block("commitment"))


def test_the_brainstorming_exclusion_covers_the_ideas_not_stated_preparation_of_them():
    block = _flat_block("not-work-bullets")
    assert "This covers the ideas themselves, not stated preparation of them" in block and "PREP-RESEARCH v1" in block


def test_adopted_practice_suggestion_and_one_off_move_are_three_different_answers():
    block = _flat_block("approved-rules")
    assert "is a decision when the transcript shows it was adopted" in block
    assert "A suggestion alone is a proposed decision; a one-off move" in block and "stays logistics" in block


@pytest.mark.parametrize("path", PROMPTS[:2], ids=["READER.md", "JUDGE.md"])
def test_labels_may_list_contributors_beside_one_accountable_owner(path):
    assert '"contributors"' in path.read_text() and "the tree keeps one owner" in _flat_block("approved-rules")
