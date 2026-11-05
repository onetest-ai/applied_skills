"""evals/quality.py: each key item next to what taska produced and what an independent note-taker (Echo) recorded, with
quality checks that say who decides them; nodes the key lacks and items only Echo has. Invented data only."""
import json
import re
import sys
import textwrap
from pathlib import Path

import pytest

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import echo as E  # noqa: E402
import quality as Q  # noqa: E402
from taska_lib import config as C, ledger as L, store as S  # noqa: E402

MEET = "2026-03-02_meetings_ab12cd34"
S1 = "Nora will draft the warehouse onboarding checklist for Opal and share it before the Thursday review meeting"
S2 = "There is a risk the forklift audit slips a week if the inspector cannot confirm the date by Tuesday"
S3 = "Ravi will rebuild the dock schedule export so the yard team can plan the night shift properly"
S4 = "Opal will collect the scanner serial numbers from both yards and send them to us next week"
S5 = "We agreed the north yard keeps its current layout until the pilot ends in spring"
S6 = "Nora also promised to update the safety poster wording before the next site visit happens"
FILL = " and then people talked about the weather and the lunch menu for quite a while" * 9
TEXT = (FILL + " ").join([S1, S2, S3, S4, S5, S6])
KEY = [
    {"cid": "ab12cd34-001", "evidence": MEET, "quote": S1, "ts": "00:01:00", "speaker": "Nora Quill", "title": "Draft the warehouse onboarding checklist",
     "kind": "task", "commitment": "committed", "owner": "Nora Quill", "due": None, "status": "open"},
    {"cid": "ab12cd34-002", "evidence": MEET, "quote": S2, "ts": "00:05:10", "speaker": "Ravi Tamsin", "title": "Forklift audit slips a week without the inspector's date",
     "kind": "risk", "commitment": "committed", "owner": "Ravi Tamsin", "due": None, "status": "open"},
    {"cid": "ab12cd34-003", "evidence": MEET, "quote": S3, "ts": "00:09:00", "speaker": "Ravi Tamsin", "title": "Rebuild the dock schedule export",
     "kind": "task", "commitment": "committed", "owner": "Ravi Tamsin", "due": None, "status": "open"},
    {"cid": "ab12cd34-004", "evidence": MEET, "quote": "the yard team can plan the night shift properly", "ts": "00:09:05", "speaker": "Ravi Tamsin",
     "title": "Plan the night shift", "kind": "task", "commitment": "committed", "owner": "Ravi Tamsin", "due": None, "status": "open"},
]


def _project(tmp_path):
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
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    S.write_evidence(cfg.store, {"id": MEET, "kind": "meeting", "at": "2026-03-02T12:00:00", "title": "Harbor daily sync",
                                 "source": "meetings", "text": TEXT})
    node = lambda nid, kind, quote, owner: {"type": "create", "mid": f"m{nid}", "id": nid, "kind": kind, "by": "agent", "evidence": MEET,
                                            "date": "2026-03-02", "quote": quote, "fields": {"title": f"{nid} title", "parent": "E01",
                                            "internal_owner": owner, "client_owner": None, "client_owed": False, "ac": [], "due": None, "plan": None}}
    S.append(cfg.store, [{"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-03-01",
                          "fields": {"title": "Onboarding"}},
                         node("T001", "task", S1, "Nora Quill"), node("T003", "task", S3, "Opal Brennick"),
                         node("T004", "task", S4, "Opal Brennick"), node("D005", "decision", S5, None)])
    L.mark_processed(cfg.store, MEET, "read")
    return tmp_path


def _echo(tmp_path):
    d = tmp_path / "echo" / "2026-03-02_180012-harbor"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"started_at": "2026-03-02T18:00:12+03:00", "title": "Harbor sync"}))
    (d / "transcript.md").write_text(TEXT)
    (d / "actions.json").write_text(json.dumps([
        {"task": "Draft the onboarding checklist for Opal", "owner": "Nora", "due": None, "quote": S1, "t": 60, "status": "todo"},
        {"task": "Collect the scanner serial numbers", "owner": "Opal", "due": "next week", "quote": S4, "t": 780, "status": "todo"},
        {"task": "Update the safety poster wording", "owner": "Nora", "due": None, "quote": S6, "t": 1100, "status": "todo"}]))
    (d / "notes.md").write_text("## Decisions\n\n- The forklift audit slips a week if the inspector does not confirm by Tuesday [05:10]\n\n## Action items\n")
    return E.load_calls(tmp_path / "echo")[0]


def _doc(tmp_path, with_echo=True):
    project = _project(tmp_path)
    call = _echo(tmp_path) if with_echo else None
    return Q.build(project, KEY, {MEET: call}, {"record": "r1-2", "code": "abc", "tier": "med"})


def _item(doc, cid):
    return next(i for u in doc["units"] for i in u["items"] if i["id"] == cid)


def _check(item, name):
    return next(c for c in item["checks"] if c["name"] == name)


def test_all_three_sources_agree_and_the_checks_say_who_decided(tmp_path):
    doc = _doc(tmp_path)
    assert doc["schema"] == "eval-quality/1" and doc["units"][0]["independent"] == "2026-03-02_180012-harbor"
    i = _item(doc, "ab12cd34-001")
    assert i["agreement"] == "all three" and i["produced"]["node"] == "T001" and i["produced"]["matched_by"] == "quote"
    assert i["independent"]["what"] == "Draft the onboarding checklist for Opal" and i["independent"]["at"] == "01:00"
    assert (_check(i, "Found")["verdict"], _check(i, "Found")["by"]) == ("ok", "code")
    assert (_check(i, "Owner")["verdict"], _check(i, "Owner")["by"]) == ("ok", "code")
    assert (_check(i, "Same work as the key item")["verdict"], _check(i, "Same work as the key item")["by"]) == ("check", "judge")


def test_an_item_the_key_and_echo_hold_but_taska_missed_is_named(tmp_path):
    doc = _doc(tmp_path)
    i = _item(doc, "ab12cd34-002")
    assert i["produced"]["node"] is None and i["produced"]["stage"] == "read, never proposed"
    assert i["independent"]["kind"] == "decision" and i["independent"]["matched_by"] == "words (needs the judge)"
    assert i["agreement"] == "key + Echo, taska missed" and doc["summary"]["agreed_missed"] == 1


def test_a_wrong_owner_and_one_node_for_two_items_are_caught(tmp_path):
    doc = _doc(tmp_path)
    i3, i4 = _item(doc, "ab12cd34-003"), _item(doc, "ab12cd34-004")
    assert _check(i3, "Owner")["verdict"] == "no" and "Opal Brennick" in _check(i3, "Owner")["why"]
    for i in (i3, i4):
        c = _check(i, "One node for one key item")
        assert c["verdict"] == "check" and "ab12cd34-003" in c["why"] and "ab12cd34-004" in c["why"]


def test_nodes_the_key_lacks_are_split_by_whether_echo_agrees_and_echo_only_items_are_listed(tmp_path):
    doc = _doc(tmp_path)
    u = doc["units"][0]
    groups = {x["produced"]["node"]: x["group"] for x in u["extras"]}
    assert groups == {"T004": "taska + Echo, not in the key", "D005": "taska only"}
    assert [x["what"] for x in u["independent_only"]] == ["Update the safety poster wording"]


def test_without_an_echo_call_agreement_is_between_the_key_and_taska(tmp_path):
    doc = _doc(tmp_path, with_echo=False)
    assert doc["units"][0]["independent"] is None and _item(doc, "ab12cd34-001")["agreement"] == "key + taska"
    assert _item(doc, "ab12cd34-002")["agreement"] == "key only"
    assert {x["group"] for x in doc["units"][0]["extras"]} == {"taska only"}


# ── the judge: questions out, verdicts checked, merged into the checks ─────────────────────────────────────────────

def test_the_judge_is_asked_only_what_code_cannot_decide(tmp_path):
    doc = _doc(tmp_path)
    batches = Q.judge_questions(doc)
    qs = {q["qid"]: q for q in batches[MEET]}
    assert set(qs["ab12cd34-001"]["ask"]) == {"same_work", "title_faithful", "right_parent", "deliverable_explicit"}
    assert qs["ab12cd34-002"]["ask"] == ["which_node", "deliverable_explicit", "independent_same"]   # unmatched; Echo's by words only
    assert qs[f"node:{MEET}:D005"]["ask"] == ["is_work", "duplicate_of"] and qs[f"echo:{MEET}:0"]["ask"] == ["is_work"]


def test_verdicts_are_checked_before_they_count():
    batch = [{"qid": "a", "ask": ["same_work", "title_faithful"]}, {"qid": "b", "ask": ["is_work"]}]
    good = [{"qid": "a", "verdicts": {"same_work": "yes", "title_faithful": "no"}, "why": "the node quotes the same sentence and names the same work"},
            {"qid": "b", "verdicts": {"is_work": "unsure"}, "why": "it could be a courtesy or a deliverable, the turn is ambiguous"}]
    assert Q.check_verdicts(good, batch) == []
    bad = [{"qid": "a", "verdicts": {"same_work": "maybe"}, "why": "short"}]
    problems = Q.check_verdicts(bad, batch)
    assert any("b" in p and "not answered" in p for p in problems)
    assert any("maybe" in p for p in problems) and any("title_faithful" in p for p in problems) and any("8" in p for p in problems)


def test_judge_verdicts_become_checks_and_a_rejected_match_is_not_found_correctly(tmp_path):
    doc = _doc(tmp_path)
    verdicts = [{"qid": "ab12cd34-003", "verdicts": {"same_work": "no", "title_faithful": "yes", "right_parent": "yes"},
                 "why": "the node is another piece of work that only sits close by in the transcript"},
                {"qid": f"node:{MEET}:D005", "verdicts": {"is_work": "yes"}, "why": "a layout decision that changes how the yard pilot runs"}]
    merged = Q.apply_verdicts(doc, verdicts)
    i3 = _item(merged, "ab12cd34-003")
    assert (_check(i3, "Same work as the key item")["verdict"], _check(i3, "Same work as the key item")["by"]) == ("no", "judge")
    assert "another piece of work" in _check(i3, "Same work as the key item")["why"]
    assert merged["summary"]["found_correct"] == [0, 4] and merged["summary"]["judged_items"] == [1, 4]   # only judged items count
    d5 = next(x for x in merged["units"][0]["extras"] if x["produced"]["node"] == "D005")
    assert d5["checks"][0]["verdict"] == "ok"


WHY = "the transcript names this exact piece of work in the same turn"


def test_an_unmatched_key_item_asks_which_node_among_every_node_of_its_meeting(tmp_path):
    q = {q["qid"]: q for q in Q.judge_questions(_doc(tmp_path))[MEET]}["ab12cd34-002"]
    assert {c["id"] for c in q["candidates"]} == {"T001", "T003", "T004", "D005"}
    t4 = next(c for c in q["candidates"] if c["id"] == "T004")
    assert (t4["kind"], t4["title"], t4["quote"]) == ("task", "T004 title", S4)


def test_a_node_the_key_lacks_asks_is_work_and_duplicate_of_among_the_other_nodes(tmp_path):
    q = {q["qid"]: q for q in Q.judge_questions(_doc(tmp_path))[MEET]}[f"node:{MEET}:D005"]
    assert {c["id"] for c in q["candidates"]} == {"T001", "T003", "T004"}


def test_which_node_takes_only_a_candidate_or_none_and_how_only_with_a_node():
    batch = [{"qid": "k", "ask": ["which_node"], "candidates": [{"id": "T004"}, {"id": "D005"}]},
             {"qid": "n", "ask": ["is_work", "duplicate_of"], "candidates": [{"id": "T001"}]}]
    ok = lambda k, n: [{"qid": "k", "verdicts": k, "why": WHY}, {"qid": "n", "verdicts": n, "why": WHY}]
    assert Q.check_verdicts(ok({"which_node": "T004", "how": "inside"}, {"is_work": "yes", "duplicate_of": "T001"}), batch) == []
    assert Q.check_verdicts(ok({"which_node": "none"}, {"is_work": "no", "duplicate_of": "none"}), batch) == []
    for k, n, says in [({"which_node": "T999", "how": "same"}, {"is_work": "yes", "duplicate_of": "none"}, "T999"),
                       ({"which_node": "yes"}, {"is_work": "yes", "duplicate_of": "none"}, "'yes'"),
                       ({"which_node": "none", "how": "same"}, {"is_work": "yes", "duplicate_of": "none"}, "how"),
                       ({"which_node": "T004"}, {"is_work": "yes", "duplicate_of": "none"}, "how"),
                       ({"which_node": "T004", "how": "close"}, {"is_work": "yes", "duplicate_of": "none"}, "close"),
                       ({"which_node": "none"}, {"is_work": "yes", "duplicate_of": "D005"}, "D005"),
                       ({"which_node": "none"}, {"is_work": "yes", "duplicate_of": "yes"}, "'yes'")]:
        problems = Q.check_verdicts(ok(k, n), batch)
        assert problems and any(says in p for p in problems), (k, n, problems)


def test_before_the_judge_nodes_the_key_lacks_are_not_yet_inventions(tmp_path):
    s = _doc(tmp_path)["summary"]
    assert s["not_in_key_unjudged"] == 2 and s["inventions"] is None and s["key_gap_candidates"] is None
    assert s["found"] == [3, 4] and s["found_correct"] is None and s["kind_errors"] is None


def test_a_key_item_the_judge_finds_counts_as_found_and_its_node_leaves_not_in_the_key(tmp_path):
    doc = _doc(tmp_path)
    merged = Q.apply_verdicts(doc, [{"qid": "ab12cd34-002", "verdicts": {"which_node": "T004", "how": "inside"}, "why": WHY},
                                    {"qid": f"node:{MEET}:D005", "verdicts": {"is_work": "no", "duplicate_of": "none"}, "why": WHY}])
    found = _check(_item(merged, "ab12cd34-002"), "Found")
    assert (found["verdict"], found["by"]) == ("ok", "judge") and "T004" in found["why"] and "inside" in found["why"]
    assert [x["produced"]["node"] for x in merged["units"][0]["extras"]] == ["D005"]
    s = merged["summary"]
    assert s["found"] == [3, 4] and s["found_correct"] == [1, 4] and s["kind_errors"] == []
    assert (s["inventions"], s["key_gap_candidates"], s["not_in_key_unjudged"]) == (1, 0, 0)


def test_found_as_another_kind_is_a_kind_error_and_none_leaves_the_item_missed(tmp_path):
    doc = _doc(tmp_path)
    merged = Q.apply_verdicts(doc, [{"qid": "ab12cd34-002", "verdicts": {"which_node": "D005", "how": "other_kind"}, "why": WHY}])
    assert merged["summary"]["kind_errors"] == [{"item": "ab12cd34-002", "node": "D005"}] and merged["summary"]["found_correct"] == [1, 4]
    assert merged["summary"]["not_in_key_unjudged"] == 1 and merged["summary"]["inventions"] == 0
    missed = Q.apply_verdicts(doc, [{"qid": "ab12cd34-002", "verdicts": {"which_node": "none"}, "why": WHY}])
    found = _check(_item(missed, "ab12cd34-002"), "Found")
    assert (found["verdict"], found["by"]) == ("no", "judge") and missed["summary"]["found_correct"] == [0, 4]
    assert len(missed["units"][0]["extras"]) == 2


def test_after_the_judge_a_duplicate_is_an_invention_and_new_work_a_key_gap_candidate(tmp_path):
    doc = _doc(tmp_path)
    merged = Q.apply_verdicts(doc, [{"qid": f"node:{MEET}:T004", "verdicts": {"is_work": "yes", "duplicate_of": "none"}, "why": WHY},
                                    {"qid": f"node:{MEET}:D005", "verdicts": {"is_work": "yes", "duplicate_of": "T001"}, "why": WHY}])
    s = merged["summary"]
    assert (s["inventions"], s["key_gap_candidates"], s["not_in_key_unjudged"]) == (1, 1, 0)
    d5 = next(x for x in merged["units"][0]["extras"] if x["produced"]["node"] == "D005")
    dup = next(c for c in d5["checks"] if c["name"] == Q.ASK["duplicate_of"])
    assert (dup["verdict"], dup["by"]) == ("no", "judge") and "T001" in dup["why"]


def test_a_verdict_the_question_did_not_ask_is_refused_and_never_merged(tmp_path):
    batch = [{"qid": "m", "ask": ["same_work"]}, {"qid": "k", "ask": ["which_node"], "candidates": [{"id": "T004"}]}]
    answers = [{"qid": "m", "verdicts": {"same_work": "yes", "which_node": "T004", "how": "same"}, "why": WHY},
               {"qid": "k", "verdicts": {"which_node": "none", "is_work": "maybe"}, "why": WHY}]
    problems = Q.check_verdicts(answers, batch)
    assert any("'m'" in p and "which_node" in p and "not asked" in p for p in problems)
    assert any("'m'" in p and "how" in p for p in problems)
    assert any("'k'" in p and "is_work" in p and "not asked" in p for p in problems)
    doc = _doc(tmp_path)
    merged = Q.apply_verdicts(doc, [{"qid": "ab12cd34-001", "verdicts": {"same_work": "yes", "which_node": "T004", "how": "same"}, "why": WHY},
                                    {"qid": "ab12cd34-002", "verdicts": {"which_node": "none", "is_work": "maybe"}, "why": WHY}])
    assert "found_by_judge" not in _item(merged, "ab12cd34-001") and len(merged["units"][0]["extras"]) == 2


def test_found_correctly_counts_only_judged_items(tmp_path):
    doc = _doc(tmp_path)
    assert doc["summary"]["judged_items"] == [0, 4]
    partial = Q.apply_verdicts(doc, [{"qid": "ab12cd34-001", "verdicts": {"same_work": "yes"}, "why": WHY},
                                     {"qid": "ab12cd34-003", "verdicts": {"same_work": "unsure"}, "why": WHY}])
    assert partial["summary"]["found_correct"] == [1, 4] and partial["summary"]["judged_items"] == [2, 4]
    extras_only = Q.apply_verdicts(doc, [{"qid": f"node:{MEET}:D005", "verdicts": {"is_work": "no", "duplicate_of": "none"}, "why": WHY}])
    assert extras_only["summary"]["found_correct"] is None and extras_only["summary"]["judged_items"] == [0, 4]


def test_a_proposal_card_is_never_found_correctly_by_the_judge(tmp_path):
    doc = _doc(tmp_path)
    card = _item(doc, "ab12cd34-002")
    card["produced"] = {"node": "proposal card", "title": "(a pending proposal card)", "stage": "card"}
    merged = Q.apply_verdicts(doc, [{"qid": "ab12cd34-001", "verdicts": {"same_work": "yes"}, "why": WHY},
                                    {"qid": "ab12cd34-002", "verdicts": {"which_node": "T004", "how": "same"}, "why": WHY}])
    assert merged["summary"]["found_correct"] == [1, 4] and len(merged["units"][0]["extras"]) == 2


def test_a_task_the_judge_finds_takes_its_parent_story_out_of_not_in_the_key(tmp_path):
    project = _project(tmp_path)
    cfg = C.load(project)
    S.append(cfg.store, [{"type": "create", "mid": "mS006", "id": "S006", "kind": "story", "by": "agent", "evidence": MEET, "date": "2026-03-02",
                          "quote": S6, "fields": {"title": "Safety poster", "parent": "E01", "internal_owner": None, "client_owner": None,
                                                  "client_owed": False, "ac": [], "due": None, "plan": None}},
                         {"type": "create", "mid": "mT007", "id": "T007", "kind": "task", "by": "agent", "evidence": MEET, "date": "2026-03-02",
                          "quote": S6, "fields": {"title": "Update the poster", "parent": "S006", "internal_owner": "Nora Quill", "client_owner": None,
                                                  "client_owed": False, "ac": [], "due": None, "plan": None}}])
    doc = Q.build(project, KEY, {MEET: None}, {"record": "r", "code": "c", "tier": "med"})
    assert {"S006", "T007"} <= {x["produced"]["node"] for x in doc["units"][0]["extras"]}
    merged = Q.apply_verdicts(doc, [{"qid": "ab12cd34-002", "verdicts": {"which_node": "T007", "how": "same"}, "why": WHY}])
    assert not {"S006", "T007"} & {x["produced"]["node"] for x in merged["units"][0]["extras"]}


def test_a_meeting_without_nodes_asks_no_which_node(tmp_path):
    project = _project(tmp_path)
    other = "2026-03-03_meetings_ef56ab78"
    S.write_evidence(C.load(project).store, {"id": other, "kind": "meeting", "at": "2026-03-03T12:00:00", "title": "Quiet call",
                                             "source": "meetings", "text": TEXT})
    lone = dict(KEY[0], cid="ef56ab78-001", evidence=other)
    doc = Q.build(project, [*KEY, lone], {MEET: None, other: None}, {"record": "r", "code": "c", "tier": "med"})
    assert Q.judge_questions(doc)[other] == []
    found = _check(_item(doc, "ef56ab78-001"), "Found")
    assert (found["verdict"], found["by"]) == ("no", "code") and doc["summary"]["judged_items"] == [0, 4]


def test_is_work_unsure_leaves_a_node_unjudged(tmp_path):
    merged = Q.apply_verdicts(_doc(tmp_path), [{"qid": f"node:{MEET}:D005", "verdicts": {"is_work": "unsure", "duplicate_of": "none"}, "why": WHY}])
    s = merged["summary"]
    assert (s["not_in_key_unjudged"], s["inventions"], s["key_gap_candidates"]) == (2, 0, 0)


NEAR = dict(KEY[0], cid="ab12cd34-005", quote="and then people talked about the weather and the lunch menu", title="Talk about lunch")


def test_before_the_judge_field_checks_count_only_on_quote_matches(tmp_path):
    doc = Q.build(_project(tmp_path), [*KEY, NEAR], {MEET: None}, {"record": "r", "code": "c", "tier": "med"})
    near = _item(doc, "ab12cd34-005")
    assert near["produced"]["matched_by"] == "position"
    owner = _check(near, "Owner")
    assert (owner["verdict"], owner["by"], owner["why"]) == ("check", "judge", "waits for the judge")
    assert doc["summary"]["owner"] == [1, 3] and doc["summary"]["status"] == [3, 3] and doc["summary"]["fields_scope"] == "quote matches"


def test_after_the_judge_field_checks_count_only_on_judged_same_work(tmp_path):
    doc = Q.build(_project(tmp_path), [*KEY, NEAR], {MEET: None}, {"record": "r", "code": "c", "tier": "med"})
    merged = Q.apply_verdicts(doc, [{"qid": "ab12cd34-001", "verdicts": {"same_work": "yes"}, "why": WHY},
                                    {"qid": "ab12cd34-003", "verdicts": {"same_work": "no"}, "why": WHY},
                                    {"qid": "ab12cd34-005", "verdicts": {"same_work": "yes"}, "why": WHY}])
    s = merged["summary"]
    assert s["fields_scope"] == "judged same work" and s["owner"] == [2, 2] and s["status"] == [2, 2]
    assert not [c for c in _item(merged, "ab12cd34-003")["checks"] if c["name"] in ("Owner", "Status")]
    owner = _check(_item(merged, "ab12cd34-005"), "Owner")
    assert (owner["verdict"], owner["by"]) == ("ok", "code")


def test_a_judge_found_item_is_checked_against_the_node_the_judge_named(tmp_path):
    merged = Q.apply_verdicts(_doc(tmp_path), [{"qid": "ab12cd34-002", "verdicts": {"which_node": "T004", "how": "same"}, "why": WHY}])
    owner = _check(_item(merged, "ab12cd34-002"), "Owner")
    assert (owner["verdict"], owner["by"]) == ("no", "code") and "Opal Brennick" in owner["why"]
    assert merged["summary"]["owner"] == [0, 1] and merged["summary"]["fields_scope"] == "judged same work"


def test_after_a_partial_judge_an_unanswered_quote_match_waits_and_drops_out(tmp_path):
    project = _project(tmp_path)
    store = C.load(project).store
    other = "2026-03-03_meetings_ef56ab78"
    S.write_evidence(store, {"id": other, "kind": "meeting", "at": "2026-03-03T12:00:00", "title": "Second sync", "source": "meetings", "text": TEXT})
    S.append(store, [{"type": "create", "mid": "mT101", "id": "T101", "kind": "task", "by": "agent", "evidence": other, "date": "2026-03-03",
                      "quote": S1, "fields": {"title": "T101 title", "parent": "E01", "internal_owner": "Nora Quill", "client_owner": None,
                                              "client_owed": False, "ac": [], "due": None, "plan": None}}])
    L.mark_processed(store, other, "read")
    lone = dict(KEY[0], cid="ef56ab78-001", evidence=other)
    doc = Q.build(project, [*KEY, lone], {MEET: None, other: None}, {"record": "r", "code": "c", "tier": "med"})
    assert _check(_item(doc, "ef56ab78-001"), "Owner")["by"] == "code" and doc["summary"]["owner"] == [2, 4]
    merged = Q.apply_verdicts(doc, [{"qid": "ab12cd34-001", "verdicts": {"same_work": "yes"}, "why": WHY}])
    owner = _check(_item(merged, "ef56ab78-001"), "Owner")
    assert (owner["verdict"], owner["by"], owner["why"]) == ("check", "judge", "waits for the judge")
    assert merged["summary"]["owner"] == [1, 1] and merged["summary"]["fields_scope"] == "judged same work"


def test_a_judge_found_node_inside_a_broader_one_shows_its_fields_but_does_not_count_them(tmp_path):
    merged = Q.apply_verdicts(_doc(tmp_path), [{"qid": "ab12cd34-002", "verdicts": {"which_node": "T004", "how": "inside"}, "why": WHY}])
    owner = _check(_item(merged, "ab12cd34-002"), "Owner")
    assert owner["verdict"] == "check" and "Opal Brennick" in owner["why"] and merged["summary"]["owner"] == [0, 0]


def test_independent_coverage_is_meetings_with_notes_over_all(tmp_path):
    assert _doc(tmp_path)["summary"]["independent_coverage"] == [1, 1]


def test_independent_coverage_without_a_call_is_none_of_one(tmp_path):
    assert _doc(tmp_path, with_echo=False)["summary"]["independent_coverage"] == [0, 1]


# ── the judge against your own labels ────────────────────────────────────────────────────────────────────────────

def test_judge_agreement_is_counted_per_validation_source():
    answers = [{"qid": "k1", "verdicts": {"same_work": "yes"}}, {"qid": "k2", "verdicts": {"which_node": "T004", "how": "inside"}},
               {"qid": "n1", "verdicts": {"is_work": "no", "duplicate_of": "T001"}}]
    validation = [{"qid": "k1", "ask": "same_work", "expected": "yes", "source": "you"},
                  {"qid": "k2", "ask": "which_node", "expected": "T004", "source": "you"},
                  {"qid": "n1", "ask": "is_work", "expected": "yes", "source": "you"},
                  {"qid": "n1", "ask": "duplicate_of", "expected": "T001", "source": "adjudication"},
                  {"qid": "k2", "ask": "which_node", "expected": "none", "source": "adjudication"},
                  {"qid": "k9", "ask": "same_work", "expected": "yes", "source": "adjudication"}]   # never answered: not counted
    a = Q.agreement(answers, validation)
    assert (a["you"]["n"], a["you"]["agree"], a["you"]["rate"]) == (3, 2, 0.667)
    assert a["you"]["disagreements"] == [{"qid": "n1", "ask": "is_work", "judge": "no", "expected": "yes"}]
    assert (a["adjudication"]["n"], a["adjudication"]["agree"]) == (2, 1)


LDOC = {"run": {"record": "rec-a"}, "units": [{"id": MEET, "items": [{"id": "ab12cd34-001"}, {"id": "ab12cd34-002"}],
                                                 "nodes": [{"id": "T001", "quote": S1}, {"id": "T004", "quote": S4}, {"id": "D005", "quote": S5}]}]}


def _labels(answers, record="rec-a"):
    return {"schema": "eval-labels/1", "stamp": f"{record}-abcdef12", "record": record, "key_version": "v1", "key_sha": "k", "answers": answers}


def test_page_labels_on_nodes_become_is_work_and_duplicate_of_rows_in_the_judges_qid_space():
    labels = _labels({f"node:{MEET}:T004": {"action": "add", "kind": "task", "commitment": "committed", "quote": S4, "seen_judge": False},
                      f"node:{MEET}:D005": {"action": "not_work", "reason": "status", "seen_judge": True},
                      f"node:{MEET}:T001": {"action": "duplicate", "dup_of": "T004", "dup_of_quote": S4},
                      f"node:{MEET}:T009": {"action": "duplicate", "dup_of": "ab12cd34-001", "quote": S1},
                      "ab12cd34-001": {"action": "drop", "reason": "vague"}, "ab12cd34-002": {"action": "keep"}})
    assert Q.validation_from_page_labels(labels, LDOC) == [
        {"qid": f"node:{MEET}:T004", "ask": "is_work", "expected": "yes", "source": "you", "seen_judge": False},
        {"qid": f"node:{MEET}:D005", "ask": "is_work", "expected": "no", "source": "you", "seen_judge": True},
        {"qid": f"node:{MEET}:T001", "ask": "duplicate_of", "expected": "T004", "source": "you", "seen_judge": None}]


def test_judge_answer_labels_become_rows_for_that_ask():
    answers = [{"qid": "ab12cd34-001", "verdicts": {"same_work": "no"}}, {"qid": f"node:{MEET}:T004", "verdicts": {"is_work": "yes"}}]
    labels = _labels({"judge:ab12cd34-001:same_work": {"action": "disagree", "expected": "yes", "seen_judge": True},
                      f"judge:node:{MEET}:T004:is_work": {"action": "agree", "seen_judge": True}})
    assert Q.validation_from_page_labels(labels, LDOC, answers) == [
        {"qid": "ab12cd34-001", "ask": "same_work", "expected": "yes", "source": "you", "seen_judge": True},
        {"qid": f"node:{MEET}:T004", "ask": "is_work", "expected": "yes", "source": "you", "seen_judge": True}]
    rows, skipped = Q.page_label_rows(_labels(labels["answers"], record="rec-b"), LDOC, answers)
    assert rows == []     # another run's: no node quote for the key item's pairing, and an agree names that run's judge answer
    assert [s["label"] for s in skipped] == ["judge:ab12cd34-001:same_work", f"judge:node:{MEET}:T004:is_work"]


def test_node_labels_from_another_run_are_matched_by_quote_and_an_ambiguous_quote_is_skipped():
    doc = {"run": {"record": "rec-a"}, "units": [{"id": MEET, "items": [], "nodes": [
        {"id": "T101", "quote": S4}, {"id": "T102", "quote": S5}, {"id": "T103", "quote": S5}, {"id": "T004", "quote": S1}]}]}
    labels = _labels({f"node:{MEET}:T004": {"action": "add", "kind": "task", "commitment": "committed", "quote": S4},
                      f"node:{MEET}:D005": {"action": "not_work", "reason": "status", "quote": S5},
                      f"node:{MEET}:T006": {"action": "not_work", "reason": "status", "quote": S6},
                      f"node:{MEET}:T007": {"action": "not_work", "reason": "status"}}, record="rec-b")
    rows, skipped = Q.page_label_rows(labels, doc)
    assert rows == [{"qid": f"node:{MEET}:T101", "ask": "is_work", "expected": "yes", "source": "you", "seen_judge": None}]
    why = {s["label"]: s["why"] for s in skipped}
    assert "ambiguous" in why[f"node:{MEET}:D005"] and "T102" in why[f"node:{MEET}:D005"]
    assert "no node" in why[f"node:{MEET}:T006"] and "quote" in why[f"node:{MEET}:T007"]


def test_the_old_label_rows_function_is_gone():
    assert not hasattr(Q, "validation_from_labels")


def test_build_with_labels_adds_source_you_with_its_blind_numbers(tmp_path, capsys):
    record = _record(tmp_path)
    judge = record / "quality" / "judge"
    judge.mkdir(parents=True)
    (judge / f"answers_{MEET}.json").write_text(json.dumps([
        {"qid": "ab12cd34-001", "verdicts": {"same_work": "yes", "title_faithful": "yes", "right_parent": "yes"}, "why": WHY},
        {"qid": "ab12cd34-003", "verdicts": {"same_work": "yes", "title_faithful": "yes", "right_parent": "yes"}, "why": WHY}]))
    from taska_lib.evalscore import load_gold
    head = load_gold(json.loads((record / "meta.json").read_text())["key_path"])[0]
    answers = {"judge:ab12cd34-001:same_work": {"action": "agree", "seen_judge": False},
               "judge:ab12cd34-003:same_work": {"action": "disagree", "expected": "no", "seen_judge": True}}
    f = tmp_path / "labels.json"
    f.write_text(json.dumps(_labels(answers, record="rec") | {"key_sha": head["sha256"]}))
    other = tmp_path / "other-key.json"
    other.write_text(json.dumps(_labels(answers, record="rec") | {"key_sha": "f" * 64}))
    capsys.readouterr()
    assert Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo"), "--labels", str(other)]) == 2
    assert "key" in json.loads(capsys.readouterr().out)["error"]
    capsys.readouterr()
    assert Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo"), "--labels", str(f)]) == 0
    you = json.loads((record / "quality" / "quality.json").read_text())["summary"]["judge_agreement"]["you"]
    assert (you["n"], you["agree"]) == (2, 1) and (you["blind"]["n"], you["blind"]["agree"]) == (1, 1)
    overall = json.loads((record / "quality" / "quality.json").read_text())["summary"]["judge_agreement_overall"]
    assert (overall["n"], overall["blind"]["n"], overall["blind"]["agree"]) == (2, 1, 1)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"qid": "x"}]))
    assert Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo"), "--labels", str(bad)]) == 2


def test_your_judge_measures_pass_only_on_blind_labels():
    blind = {"n": 10, "agree": 10, "rate": 1.0, "by_ask": ASKS((8, 8), (2, 2), (0, 0), (0, 0))}
    full = {"n": 20, "agree": 14, "rate": 0.7, "by_ask": ASKS((10, 14), (2, 2), (2, 4), (0, 0))}
    by = {r["measure"]: r for r in Q.pass_rule(JUDGED | {"judge_agreement": {"you": full | {"blind": blind, "seen": {"n": 10}}}}, BARS | GATE)}
    assert by["Judge agrees with you"]["value"] == 1.0 and by["Judge agrees with you"]["pass"] is True
    assert all(by[m]["pass"] is True for m in FOUND)                    # found asks: 10 blind labels, all agree
    assert all(by[m]["pass"] is None and by[m]["flag"] == "validated only on labels made after seeing the judge" for m in WORK)
    seen = _YOU
    few = {r["measure"]: r for r in Q.pass_rule(JUDGED | {"judge_agreement": {"you": seen | {"blind": EMPTY, "seen": seen}}}, BARS | GATE)}
    assert all(few[m]["pass"] is None and few[m]["flag"] == "validated only on labels made after seeing the judge" for m in FOUND + WORK)
    assert few["Judge agrees with you"]["value"] == 0.9 and few["Judge agrees with you"]["flag"] == "validated only on labels made after seeing the judge"
    unknown = {r["measure"]: r for r in Q.pass_rule(JUDGED | {"judge_agreement": {"you": seen}}, BARS | GATE)}   # no seen_judge at all
    assert all(unknown[m]["pass"] is None and "label state unknown" in unknown[m]["flag"] and "after seeing" not in unknown[m]["flag"]
               for m in FOUND + WORK)
    rca = {r["measure"]: r for r in Q.pass_rule(JUDGED | {"judge_agreement": {"rca": seen}}, BARS | GATE | {"judge_agreement_source": "rca"})}
    assert all(rca[m]["pass"] is True for m in FOUND + WORK)            # other sources: no blind rule
    assert all(f in Q.UNVALIDATED for f in ("validated only on labels made after seeing the judge", unknown["Inventions"]["flag"]))


def test_a_judge_label_on_a_key_item_from_another_run_counts_only_on_the_same_pairing():
    doc = {"run": {"record": "rec-a"}, "units": [{"id": MEET, "nodes": [{"id": "T101", "quote": S1}, {"id": "T104", "quote": S4}],
                                                 "items": [{"id": "ab12cd34-001", "produced": {"node": "T101", "quote": S1}},
                                                           {"id": "ab12cd34-002", "produced": {"node": None}}]}]}
    labels = _labels({"judge:ab12cd34-001:same_work": {"action": "disagree", "expected": "no", "quote": S1},
                      "judge:ab12cd34-001:title_faithful": {"action": "disagree", "expected": "no", "quote": S3},
                      "judge:ab12cd34-001:right_parent": {"action": "disagree", "expected": "no"},
                      "judge:ab12cd34-002:which_node": {"action": "disagree", "expected": "T004", "quote": S4},
                      "judge:ab12cd34-002:independent_same": {"action": "disagree", "expected": "none"}}, record="rec-b")
    rows, skipped = Q.page_label_rows(labels, doc)
    assert [(r["qid"], r["ask"], r["expected"]) for r in rows] == [("ab12cd34-001", "same_work", "no"), ("ab12cd34-002", "which_node", "T104")]
    assert {s["label"] for s in skipped} == {"judge:ab12cd34-001:title_faithful", "judge:ab12cd34-001:right_parent",
                                             "judge:ab12cd34-002:independent_same"}
    none = _labels({"judge:ab12cd34-002:which_node": {"action": "disagree", "expected": "none"}}, record="rec-b")
    assert Q.page_label_rows(none, doc)[0] == []


def test_your_rows_are_one_per_question_and_an_explicit_judge_label_wins():
    labels = _labels({f"node:{MEET}:T004": {"action": "add", "kind": "task", "commitment": "committed"},
                      f"judge:node:{MEET}:T004:is_work": {"action": "disagree", "expected": "no", "seen_judge": False}})
    assert Q.validation_from_page_labels(labels, LDOC) == [
        {"qid": f"node:{MEET}:T004", "ask": "is_work", "expected": "no", "source": "you", "seen_judge": False}]


# ── the pass rule: your bars, never a default ────────────────────────────────────────────────────────────────────

def test_the_pass_rule_reads_not_set_without_bars_and_judges_each_measure_against_yours():
    summary = {"found_correct": [6, 11], "agreed_missed": 2, "owner": [3, 5], "status": [3, 5], "inventions": 1,
               "judge_agreement": {"you": (_you := {"n": 10, "agree": 9, "rate": 0.9, "by_ask": {"same_work": {"n": 5, "agree": 5},
                                                                                                   "is_work": {"n": 5, "agree": 4}}}) | {"blind": _you}}}
    unset = Q.pass_rule(summary, {})
    assert {r["bar"] for r in unset} == {None} and {r["pass"] for r in unset} == {None}
    rule = Q.pass_rule(summary, {"found_correct_min": 0.5, "agreed_missed_max": 0, "inventions_max": 1, "judge_agreement_min": 0.8,
                                 "judge_agreement_min_n": 5})
    by = {r["measure"]: r["pass"] for r in rule}
    assert by["Work found, correctly"] is True and by["Nothing two readers agree on is missed"] is False
    assert by["Inventions"] is True and by["Owner right"] is None


ASKS = lambda sw, wn, iw, do: {"same_work": {"n": sw[1], "agree": sw[0]}, "which_node": {"n": wn[1], "agree": wn[0]},
                                "is_work": {"n": iw[1], "agree": iw[0]}, "duplicate_of": {"n": do[1], "agree": do[0]}}
EMPTY = {"n": 0, "agree": 0, "rate": None, "by_ask": {}}
_YOU = {"n": 20, "agree": 18, "rate": 0.9, "by_ask": ASKS((8, 8), (2, 2), (6, 8), (2, 2))}
JUDGED = {"found_correct": [8, 10], "judged_items": [10, 10], "agreed_missed": 0, "owner": [4, 5], "status": [5, 5],
          "fields_scope": "judged same work", "inventions": 1, "key_gap_candidates": 2,
          "judge_agreement": {"you": _YOU | {"blind": _YOU},
                              "adjudication": {"n": 20, "agree": 14, "rate": 0.7, "by_ask": ASKS((5, 8), (1, 2), (6, 8), (2, 2))}}}
BARS = {"found_correct_min": 0.5, "owner_min": 0.5, "status_min": 0.5, "inventions_max": 3, "key_gap_candidates_max": 5}
GATE = {"judge_agreement_min": 0.8, "judge_agreement_min_n": 5}
FOUND = ("Work found, correctly", "Owner right", "Status right")
WORK = ("Inventions", "Key gap candidates")


def test_judge_measures_count_only_when_the_judge_is_validated_on_the_asks_they_rest_on():
    by = {r["measure"]: r for r in Q.pass_rule(JUDGED, BARS | GATE)}
    assert by["Judge agrees with you"]["value"] == 0.9 and by["Judge agrees with you"]["pass"] is True
    assert all(by[m]["pass"] is True and not by[m].get("flag") for m in FOUND + WORK)
    tight = {r["measure"]: r for r in Q.pass_rule(JUDGED, BARS | GATE | {"judge_agreement_min": 0.9})}   # found asks 10/10, work asks 8/10
    assert all(tight[m]["pass"] is True for m in FOUND) and all(tight[m]["flag"] == "not validated" for m in WORK)
    for rule in (BARS | GATE | {"judge_agreement_min_n": 11}, BARS | {"judge_agreement_min": 0.8}, BARS,
                 BARS | GATE | {"judge_agreement_source": "adjudication"}):
        by = {r["measure"]: r for r in Q.pass_rule(JUDGED, rule)}
        for m in FOUND + WORK:
            if rule.get("judge_agreement_source") == "adjudication" and m in WORK:
                continue   # adjudication: work asks 8/10 pass, found asks 6/10 do not
            assert by[m]["pass"] is None and by[m]["flag"] == "not validated" and by[m]["value"] is not None, (rule, m)
    no_source = {r["measure"]: r for r in Q.pass_rule(JUDGED, BARS | GATE | {"judge_agreement_source": "panel"})}
    assert no_source["Judge agrees with you"]["value"] is None and no_source["Inventions"]["flag"] == "not validated"


def test_the_judge_agreement_row_needs_enough_answers_to_pass():
    _one = {"n": 1, "agree": 1, "rate": 1.0, "by_ask": ASKS((1, 1), (0, 0), (0, 0), (0, 0))}
    one = JUDGED | {"judge_agreement": {"you": _one | {"blind": _one}}}
    row = Q.pass_rule(one, BARS | GATE)[-1]
    assert row["measure"] == "Judge agrees with you" and row["pass"] is None and row["flag"] == "too few answers"
    assert Q.pass_rule(one, BARS | {"judge_agreement_min": 0.8})[-1]["pass"] is None


def test_code_measures_are_not_gated_and_a_partly_judged_found_rate_does_not_pass():
    quote_scope = JUDGED | {"fields_scope": "quote matches", "judge_agreement": {}}
    by = {r["measure"]: r for r in Q.pass_rule(quote_scope, BARS | GATE)}
    assert by["Owner right"]["pass"] is True and not by["Owner right"].get("flag")
    partial = {r["measure"]: r for r in Q.pass_rule(JUDGED | {"judged_items": [6, 10]}, BARS | GATE)}
    assert partial["Work found, correctly"]["pass"] is None and partial["Work found, correctly"]["flag"] == "partly judged"


def test_judge_measures_on_a_batch_judged_on_the_readers_tier_do_not_pass():
    by = {r["measure"]: r for r in Q.pass_rule(JUDGED | {"judge_same_tier": [MEET]}, BARS | GATE)}
    assert all(by[m]["pass"] is None and by[m]["flag"] == "judge tier not independent" for m in FOUND + WORK)


def test_agreement_counts_per_ask_and_overall_once_per_question_preferring_your_labels():
    answers = [{"qid": "k1", "verdicts": {"same_work": "yes"}}, {"qid": "n1", "verdicts": {"is_work": "no", "duplicate_of": "none"}}]
    validation = [{"qid": "k1", "ask": "same_work", "expected": "yes", "source": "rca"},
                  {"qid": "k1", "ask": "same_work", "expected": "no", "source": "you"},
                  {"qid": "n1", "ask": "is_work", "expected": "no", "source": "rca"},
                  {"qid": "n1", "ask": "duplicate_of", "expected": "none", "source": "rca"}]
    a = Q.agreement(answers, validation)
    assert a["rca"]["by_ask"]["is_work"] == {"n": 1, "agree": 1, "rate": 1.0} and a["you"]["by_ask"]["same_work"]["agree"] == 0
    assert Q.overall(answers, validation) == {"n": 3, "agree": 2, "rate": 0.667}


def test_a_validation_row_without_qid_ask_or_expected_is_refused(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"qid": "k1", "ask": "same_work", "expected": "yes"}, {"qid": "k2", "ask": "same_work"}]))
    try:
        Q.load_validation([bad])
        raise AssertionError("not refused")
    except ValueError as e:
        assert "bad.json" in str(e) and "row 2" in str(e) and "expected" in str(e)
    record = _record(tmp_path)
    capsys.readouterr()
    assert Q.main(["agreement", "--record", str(record), "--validation", str(bad)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def _record(tmp_path, tier="med"):
    import key_tools
    project = _project(tmp_path)
    key = key_tools.write_version(tmp_path, KEY, "v1", "2026-03-03", "invented")
    record = tmp_path / "rec"
    record.mkdir()
    (record / "meta.json").write_text(json.dumps({"project": str(project), "key_path": str(key), "key_version": "v1",
                                                  "git_commit": "abc1234def", "model_tier": tier, "key_sha": "k1sha"}))
    (tmp_path / "echo").mkdir(exist_ok=True)
    return record


STUB = """import json, re, sys
prompt = sys.argv[sys.argv.index('-p') + 1]
qs = json.load(open(re.search(r'questions in (\\S+) and', prompt).group(1)))
open(sys.argv[1], 'a').write(sys.argv[sys.argv.index('--model') + 1] + '\\n')
print(json.dumps([{"qid": q["qid"], "verdicts": {n: "none" if n in ("which_node", "duplicate_of") or (n == "deliverable_explicit" and "which_node" in q["ask"]) else "title" if n == "deliverable_explicit" else "yes" for n in q["ask"]},
                   "why": "the transcript says exactly this work in those words"} for q in qs]))
"""


def _stub(tmp_path):
    model, calls = tmp_path / "model.py", tmp_path / "calls.txt"
    model.write_text(STUB)
    return [sys.executable, str(model), str(calls)], calls


def test_the_readers_tier_comes_from_meta_pinned_or_used_models():
    assert Q._reader_tiers({"model_tier": "med"}) == {"med"}
    assert Q._reader_tiers({"model_pinned": "claude-opus-5-5"}) == {"high"}
    assert Q._reader_tiers({"models": ["claude-sonnet-5-5", "claude-haiku-5-5"]}) == {"med", "low"}
    assert Q._reader_tiers({"models": ["some-other-model"]}) == set()
    assert Q._reader_tiers({"model_pinned": "claude-sonnet-5-5", "models": ["claude-sonnet-5-5", "claude-opus-5-5"]}) == {"med", "high"}


def test_judge_run_refuses_a_tier_the_reader_used_beside_its_pinned_one(tmp_path, capsys):
    record = _record(tmp_path)
    meta = json.loads((record / "meta.json").read_text())
    (record / "meta.json").write_text(json.dumps({k: v for k, v in meta.items() if k != "model_tier"}
                                                 | {"model_pinned": "claude-sonnet-5-5", "models": ["claude-sonnet-5-5", "claude-opus-5-5"]}))
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(record)])
    ai, calls = _stub(tmp_path)
    capsys.readouterr()
    assert Q.main(["judge-run", "--record", str(record), "--model", "high", "--ai", *ai]) == 2 and not calls.exists()


def test_judge_run_refuses_the_readers_tier_or_an_unknown_one_unless_told(tmp_path, capsys):
    record = _record(tmp_path, tier="high")
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(record)])
    ai, calls = _stub(tmp_path)
    capsys.readouterr()
    assert Q.main(["judge-run", "--record", str(record), "--ai", *ai]) == 2   # default high = the reader's tier
    assert "same tier" in json.loads(capsys.readouterr().out)["error"] and not calls.exists()
    meta = json.loads((record / "meta.json").read_text())
    (record / "meta.json").write_text(json.dumps({k: v for k, v in meta.items() if k != "model_tier"}))
    assert Q.main(["judge-run", "--record", str(record), "--ai", *ai]) == 2
    assert "unknown" in json.loads(capsys.readouterr().out)["error"] and not calls.exists()
    assert Q.main(["judge-run", "--record", str(record), "--model", "high", "--same-tier-ok", "--ai", *ai]) == 0
    assert calls.read_text().split() == ["claude-opus-5-5"]
    assert json.loads((record / "quality" / "judge" / "models.json").read_text()) == {MEET: {"model": "high", "same_tier_ok": True}}


def test_the_doc_names_the_judge_tiers_behind_the_merged_answers_and_flags_the_readers_tier(tmp_path, capsys):
    record = _record(tmp_path, tier="med")
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(record)])
    ai, _ = _stub(tmp_path)
    assert Q.main(["judge-run", "--record", str(record), "--model", "high", "--ai", *ai]) == 0
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    doc = json.loads((record / "quality" / "quality.json").read_text())
    assert doc["run"]["tier"] == "med" and doc["run"]["judge_tiers"] == ["high"] and doc["summary"]["judge_same_tier"] == []
    (record / "quality" / "judge" / "models.json").write_text(json.dumps({MEET: {"model": "med", "same_tier_ok": False}}))
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    doc = json.loads((record / "quality" / "quality.json").read_text())
    assert doc["run"]["judge_tiers"] == ["med"] and doc["summary"]["judge_same_tier"] == [MEET]
    (record / "quality" / "judge" / "models.json").unlink()   # answers no judge-run recorded: the tier is unknown
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    doc = json.loads((record / "quality" / "quality.json").read_text())
    assert doc["run"]["judge_tiers"] == ["unknown"] and doc["summary"]["judge_same_tier"] == [MEET]


def test_a_rejected_batch_records_no_judge_tier(tmp_path, capsys):
    record = _record(tmp_path)
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(record)])
    model = tmp_path / "bad.py"
    model.write_text("print('[]')")
    Q.main(["judge-run", "--record", str(record), "--ai", sys.executable, str(model)])
    assert not (record / "quality" / "judge" / "models.json").exists()


def test_the_cli_compares_the_judge_with_validation_files_and_the_page_gets_it(tmp_path, capsys):
    record = _record(tmp_path)
    judge = record / "quality" / "judge"
    judge.mkdir(parents=True)
    (judge / f"answers_{MEET}.json").write_text(json.dumps([{"qid": "ab12cd34-001", "verdicts": {"same_work": "yes", "title_faithful": "yes",
                                                                                                    "right_parent": "yes"}, "why": WHY}]))
    v1, v2 = tmp_path / "mine.json", tmp_path / "rca.json"
    v1.write_text(json.dumps([{"qid": "ab12cd34-001", "ask": "same_work", "expected": "yes", "source": "you"}]))
    v2.write_text(json.dumps([{"qid": "ab12cd34-001", "ask": "same_work", "expected": "no"}]))     # no source: the file's name
    capsys.readouterr()
    assert Q.main(["agreement", "--record", str(record), "--validation", str(v1), "--validation", str(v2)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["agreement"]["you"]["rate"] == 1.0 and out["agreement"]["rca"]["rate"] == 0.0
    assert Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo"), "--validation", str(v1), "--validation", str(v2)]) == 0
    doc = json.loads((record / "quality" / "quality.json").read_text())
    assert doc["summary"]["judge_agreement"]["you"]["agree"] == 1 and doc["summary"]["judge_agreement_overall"] == {"n": 1, "agree": 1, "rate": 1.0, "blind": {"n": 1, "agree": 0, "rate": 0.0}}   # one question, your label decides; blind: your unmarked label leaves, rca's counts


def test_the_cli_builds_preps_and_merges_only_checked_judge_answers(tmp_path, capsys):
    import key_tools
    project = _project(tmp_path)
    _echo(tmp_path)
    key = key_tools.write_version(tmp_path, KEY, "v1", "2026-03-03", "invented")
    record = tmp_path / "rec"
    record.mkdir()
    (record / "meta.json").write_text(json.dumps({"project": str(project), "key_path": str(key), "key_version": "v1",
                                                  "git_commit": "abc1234def", "model_tier": "med"}))
    assert Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")]) == 0
    doc = json.loads((record / "quality" / "quality.json").read_text())
    assert doc["key"] == "v1" and doc["run"]["code"] == "abc1234" and doc["judged"] == 0 and doc["rule"][0]["bar"] is None
    assert Q.main(["judge-prep", "--record", str(record)]) == 0
    batch = json.loads((record / "quality" / "judge" / f"judge_{MEET}.json").read_text())
    good = [{"qid": q["qid"], "verdicts": {n: "none" if n in ("which_node", "duplicate_of") or (n == "deliverable_explicit" and "which_node" in q["ask"]) else "title" if n == "deliverable_explicit" else "yes" for n in q["ask"]},
             "why": "the transcript says exactly this work in those words"} for q in batch]
    good[0]["verdicts"]["same_work"] = "no"
    model = tmp_path / "model.py"
    model.write_text(f"import sys; print('here you are:', {json.dumps(json.dumps(good))})")
    capsys.readouterr()
    assert Q.main(["judge-run", "--record", str(record), "--ai", sys.executable, str(model)]) == 0
    assert json.loads(capsys.readouterr().out)["batches"] == {MEET: "ok"}
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    doc = json.loads((record / "quality" / "quality.json").read_text())
    assert doc["judged"] == len(batch) and doc["summary"]["found_correct"][0] == doc["summary"]["found"][0] - 1


def test_a_judge_answer_that_fails_the_check_is_kept_aside_never_merged(tmp_path, capsys):
    import key_tools
    project = _project(tmp_path)
    key = key_tools.write_version(tmp_path, KEY, "v1", "2026-03-03", "invented")
    record = tmp_path / "rec"
    record.mkdir()
    (record / "meta.json").write_text(json.dumps({"project": str(project), "key_path": str(key), "git_commit": "abc", "model_tier": "med"}))
    (tmp_path / "echo").mkdir()
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(record)])
    model = tmp_path / "model.py"
    model.write_text("print('[{\"qid\": \"nope\", \"verdicts\": {}, \"why\": \"short\"}]')")
    capsys.readouterr()
    Q.main(["judge-run", "--record", str(record), "--ai", sys.executable, str(model)])
    assert json.loads(capsys.readouterr().out)["batches"][MEET].startswith("rejected")
    assert not list((record / "quality" / "judge").glob("answers_*")) and (record / "quality" / "judge" / f"rejected_{MEET}.json").exists()


def test_a_match_by_position_alone_never_reads_as_agreement_until_the_judge_rules(tmp_path):
    near = dict(KEY[0], cid="ab12cd34-005", quote="and then people talked about the weather and the lunch menu", title="Talk about lunch")
    doc = Q.build(_project(tmp_path), [*KEY, near], {MEET: None}, {"record": "r", "code": "c", "tier": "med"})
    i = _item(doc, "ab12cd34-005")
    assert i["produced"]["matched_by"] == "position" and i["agreement"] == "key + taska (taska only by position: the judge decides)"
    assert _item(doc, "ab12cd34-001")["agreement"] == "key + taska"


def test_found_correctly_has_no_value_until_the_judge_has_ruled(tmp_path):
    doc = _doc(tmp_path)
    assert doc["summary"]["found_correct"] is None
    assert Q.pass_rule(doc["summary"], {"found_correct_min": 0.5})[0] == {"measure": "Work found, correctly", "value": None, "bar": 0.5, "pass": None,
                                                                             "flag": "not judged yet"}


# ── stability: the same verdict across runs on one key ───────────────────────────────────────────────────────────

STAB_RULE = {"judge_agreement_min": 0.8, "judge_agreement_min_n": 5}


def _qrecord(root, name, verdicts, key_sha="k1sha", flag=None):
    """An invented record with a built quality document: k1 matched (same_work), k2 unmatched (which_node), k3 a card."""
    sw = {"yes": [_c(Q.ASK["same_work"], "ok", "judge")], "no": [_c(Q.ASK["same_work"], "no", "judge")],
          "unsure": [_c(Q.ASK["same_work"], "check", "judge")], "pending": [_c(Q.ASK["same_work"], "check", "judge")]}
    found = {"yes": [_c("Found", "ok", "judge")], "no": [_c("Found", "no", "judge")], "pending": [_c("Found", "no", "code")]}
    items = [{"id": "k1", "produced": {"node": "T1"}, "checks": [_c("Found", "ok", "code")] + sw[verdicts[0]]},
             {"id": "k2", "produced": {"node": None}, "checks": found[verdicts[1]]},
             {"id": "k3", "produced": {"node": "proposal card"}, "checks": [_c("Found", "ok", "code")]}]
    agree = 10 if flag is None else 5
    doc = {"schema": "eval-quality/1", "units": [{"id": "m1", "nodes": [{"id": "T1"}], "items": items}],
           "summary": {"found_correct": [1, 3], "judged_items": [2, 2], "judge_same_tier": [],
                       "judge_agreement": {"you": (you := {"n": 10, "agree": agree, "rate": agree / 10, "by_ask": ASKS((agree, 10), (0, 0), (0, 0), (0, 0))})
                                           | {"blind": you}}}}
    rec = root / name
    (rec / "quality").mkdir(parents=True)
    (rec / "meta.json").write_text(json.dumps({"key_version": "v1", "key_sha": key_sha}))
    (rec / "quality" / "quality.json").write_text(json.dumps(doc))
    return rec


def _c(name, verdict, by):
    return {"name": name, "verdict": verdict, "by": by, "why": ""}


def test_stable_means_every_planned_record_judged_it_the_same_way(tmp_path):
    recs = [_qrecord(tmp_path, "r1", ("yes", "yes")), _qrecord(tmp_path, "r2", ("no", "no"), flag="not validated"),
            _qrecord(tmp_path, "r3", ("yes", "pending"))]
    out = Q.stability(recs, STAB_RULE)
    items = {i["id"]: i for i in out["items"]}
    assert items["k1"]["verdicts"] == {"r1": "yes", "r2": "no", "r3": "yes"} and (items["k1"]["found"], items["k1"]["of"]) == (2, 3)
    assert items["k1"]["stable"] is False and items["k1"]["flaky"] is True and items["k1"]["success_fraction"] == 0.667
    assert items["k2"]["verdicts"]["r3"] == "not judged" and items["k2"]["missing"] == 1 and items["k2"]["stable"] is False
    assert items["k3"]["verdicts"] == {"r1": "no", "r2": "no", "r3": "no"} and items["k3"]["stable"] is True   # a card is never found correctly
    assert items["k3"]["consistently_failed"] is True and items["k3"]["all_found"] is False
    assert out["summary"]["stable"] == [1, 3] and out["summary"]["stable_judged"] == [0, 2]   # k3 (a card) is decided by code
    assert out["summary"]["flaky"] == 2 and out["summary"]["consistently_failed"] == 1 and out["summary"]["missing"] == 1
    assert {r["record"]: r["judge"] for r in out["records"]} == {"r1": "validated", "r2": "not validated", "r3": "validated"}


def test_an_item_judged_in_one_record_only_is_not_stable(tmp_path):
    recs = [_qrecord(tmp_path, "r1", ("yes", "yes")), _qrecord(tmp_path, "r2", ("unsure", "pending"))]
    items = {i["id"]: i for i in Q.stability(recs, STAB_RULE)["items"]}
    assert items["k1"]["of"] == 1 and items["k1"]["stable"] is False


def test_stability_refuses_records_on_different_keys(tmp_path, capsys):
    r1, r2 = _qrecord(tmp_path, "r1", ("yes", "yes")), _qrecord(tmp_path, "r2", ("yes", "yes"), key_sha="other")
    try:
        Q.stability([r1, r2])
        raise AssertionError("not refused")
    except ValueError as e:
        assert "different answer keys" in str(e)
    capsys.readouterr()
    assert Q.main(["stability", "--records", str(r1), str(r2)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_the_stability_cli_writes_stability_json_and_build_adds_a_stable_row(tmp_path, capsys):
    recs = [_qrecord(tmp_path, f"r{n}", ("yes", "yes")) for n in (1, 2, 3)]
    out = tmp_path / "stab"
    rule = tmp_path / "rule.json"
    rule.write_text(json.dumps(STAB_RULE))
    assert Q.main(["stability", "--records", *map(str, recs), "--out", str(out), "--rule", str(rule)]) == 0
    st = json.loads((out / "stability.json").read_text())
    assert st["summary"]["stable"] == [3, 3] and st["summary"]["validated"] is True and len(st["rule_sha256"]) == 64
    assert (st["key"], st["key_sha"]) == ("v1", "k1sha")
    rows = {r["measure"]: r for r in Q.pass_rule({"stability": [2, 3], "stability_validated": True}, {"stability_min": 0.6})}
    assert rows["Stable"]["value"] == 0.667 and rows["Stable"]["pass"] is True
    assert "Stable" not in {r["measure"] for r in Q.pass_rule({}, {})}
    (tmp_path / "real").mkdir()
    record = _record(tmp_path / "real")
    build = ["build", "--record", str(record), "--echo", str(tmp_path / "real" / "echo"), "--stability", str(out / "stability.json")]
    capsys.readouterr()
    assert Q.main(build) == 2 and "rec" in json.loads(capsys.readouterr().out)["error"]          # this record is not in the file
    (out / "stability.json").write_text(json.dumps(st | {"records": st["records"] + [{"record": "rec", "judge": "validated"}]}))
    assert Q.main(build) == 0
    doc = json.loads((record / "quality" / "quality.json").read_text())
    assert doc["summary"]["stability"] == [3, 3] and doc["summary"]["stability_validated"] is True
    assert any(r["measure"] == "Stable" for r in doc["rule"])


def test_stable_needs_every_record_to_agree_and_a_majority_is_flaky(tmp_path):
    def stable(*verdicts):
        root = tmp_path / str(len(list(tmp_path.iterdir())))
        recs = [_qrecord(root, f"r{n}", (v, "yes")) for n, v in enumerate(verdicts)]
        return next(i for i in Q.stability(recs)["items"] if i["id"] == "k1")["stable"]
    assert stable("yes", "yes") is True and stable("yes", "no") is False
    assert stable("yes", "no", "yes") is False and stable("no", "no", "no") is True
    assert stable("yes", "yes", "unsure") is False                       # a record with no verdict is a missing trial


def test_the_stable_row_counts_only_when_every_records_judge_is_validated(tmp_path):
    rows = {r["measure"]: r for r in Q.pass_rule({"stability": [3, 3], "stability_validated": False}, {"stability_min": 0.6})}
    assert rows["Stable"]["pass"] is None and rows["Stable"]["flag"] == "not validated" and rows["Stable"]["value"] == 1.0
    assert Q.pass_rule({"stability": [3, 3]}, {"stability_min": 0.6})[-1]["flag"] == "not validated"
    recs = [_qrecord(tmp_path, "r1", ("yes", "yes")), _qrecord(tmp_path, "r2", ("yes", "yes"), flag="not validated")]
    out = tmp_path / "stab"
    Q.main(["stability", "--records", *map(str, recs), "--out", str(out)])                   # no rule: no judge is validated
    assert json.loads((out / "stability.json").read_text())["summary"]["validated"] is False
    assert Q.stability(recs, STAB_RULE)["summary"]["validated"] is False and Q.stability(recs[:1] * 2, STAB_RULE)["summary"]["validated"] is True


def test_stability_refuses_a_record_judged_after_its_quality_page_was_built(tmp_path):
    import os
    r1, r2 = _qrecord(tmp_path, "r1", ("yes", "yes")), _qrecord(tmp_path, "r2", ("yes", "yes"))
    judge = r1 / "quality" / "judge"
    judge.mkdir()
    newer = judge / "answers_m1.json"
    newer.write_text("[]")
    built = (r1 / "quality" / "quality.json").stat().st_mtime
    os.utime(newer, (built + 60, built + 60))
    try:
        Q.stability([r1, r2])
        raise AssertionError("not refused")
    except ValueError as e:
        assert "rebuild r1 first" in str(e)
    os.utime(newer, (built - 60, built - 60))
    assert Q.stability([r1, r2])["summary"]["stable"] == [3, 3]


def test_build_refuses_a_stability_file_on_another_key(tmp_path, capsys):
    record = _record(tmp_path)
    st = tmp_path / "stability.json"
    st.write_text(json.dumps({"schema": "eval-stability/1", "key": "v1", "key_sha": "other", "records": [{"record": "rec", "judge": "validated"}],
                              "summary": {"stable": [1, 1], "validated": True}}))
    capsys.readouterr()
    assert Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo"), "--stability", str(st)]) == 2
    assert "different answer keys" in json.loads(capsys.readouterr().out)["error"]


def test_stability_refuses_a_quality_document_built_before_the_node_lists(tmp_path):
    r1, r2 = _qrecord(tmp_path, "r1", ("yes", "yes")), _qrecord(tmp_path, "r2", ("yes", "yes"))
    f = r2 / "quality" / "quality.json"
    doc = json.loads(f.read_text())
    del doc["units"][0]["nodes"]
    f.write_text(json.dumps(doc))
    try:
        Q.stability([r1, r2], STAB_RULE)
        raise AssertionError("not refused")
    except ValueError as e:
        assert "rebuild r2 first" in str(e)


# ── final pass ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_an_unmatched_item_the_judge_will_place_shows_a_pending_found_check(tmp_path):
    found = _check(_item(_doc(tmp_path), "ab12cd34-002"), "Found")
    assert (found["verdict"], found["by"], found["why"]) == ("check", "judge", "the judge decides which node")


def test_missed_by_two_readers_waits_for_the_judge_and_drops_what_it_finds(tmp_path):
    doc = _doc(tmp_path)
    assert doc["summary"]["agreed_missed"] == 1 and doc["summary"]["agreed_missed_pending"] == 1
    row = next(r for r in Q.pass_rule(doc["summary"], {"agreed_missed_max": 0}) if r["measure"] == "Nothing two readers agree on is missed")
    assert row["value"] == 1 and row["pass"] is None and row["flag"] == "not judged yet"
    merged = Q.apply_verdicts(doc, [{"qid": "ab12cd34-002", "verdicts": {"which_node": "T004", "how": "same"}, "why": WHY}])
    assert merged["summary"]["agreed_missed"] == 0 and merged["summary"]["agreed_missed_pending"] == 0
    rows = lambda summary: {r["measure"]: r for r in Q.pass_rule(summary, {"agreed_missed_max": 0} | GATE)}
    assert rows(merged["summary"])["Nothing two readers agree on is missed"]["flag"] == "not validated"
    blind = {"n": 10, "agree": 10, "rate": 1.0, "by_ask": ASKS((8, 8), (2, 2), (0, 0), (0, 0))}
    good = merged["summary"] | {"judge_agreement": {"you": blind | {"blind": blind}}}
    assert rows(good)["Nothing two readers agree on is missed"]["pass"] is True


def test_mutual_duplicates_count_only_the_later_node(tmp_path):
    merged = Q.apply_verdicts(_doc(tmp_path), [{"qid": f"node:{MEET}:T004", "verdicts": {"is_work": "yes", "duplicate_of": "D005"}, "why": WHY},
                                               {"qid": f"node:{MEET}:D005", "verdicts": {"is_work": "yes", "duplicate_of": "T004"}, "why": WHY}])
    s = merged["summary"]
    assert (s["inventions"], s["key_gap_candidates"]) == (1, 1)
    t4 = next(x for x in merged["units"][0]["extras"] if x["produced"]["node"] == "T004")
    assert next(c for c in t4["checks"] if c["name"] == Q.ASK["duplicate_of"])["verdict"] == "ok"


def test_a_judge_found_node_already_credited_to_another_item_is_flagged(tmp_path):
    merged = Q.apply_verdicts(_doc(tmp_path), [{"qid": "ab12cd34-002", "verdicts": {"which_node": "T001", "how": "inside"}, "why": WHY}])
    one = _check(_item(merged, "ab12cd34-002"), "One node for one key item")
    assert one["verdict"] == "check" and "ab12cd34-001" in one["why"]


def test_owner_and_status_rows_name_their_scope():
    rows = {r["measure"]: r for r in Q.pass_rule(JUDGED, BARS | GATE)}
    assert rows["Owner right"]["note"] == "on judged same work" and rows["Status right"]["note"] == "on judged same work"
    rows = {r["measure"]: r for r in Q.pass_rule(JUDGED | {"fields_scope": "quote matches"}, BARS | GATE)}
    assert rows["Owner right"]["note"] == "on quote matches"


def test_the_judge_prompt_spells_out_the_id_answers():
    text = (EVALS / "QUALITY_JUDGE.md").read_text()
    assert "omit `how`" in text and "never `no`" in text and "every question" in text


def _renumbered(tmp_path, record, name):
    """A second record of the same meeting whose nodes carry other ids (T001 -> T101): node ids differ between runs."""
    import shutil
    src = json.loads((record / "meta.json").read_text())
    proj = tmp_path / f"{name}-copy"
    shutil.copytree(src["project"], proj)
    ev = proj / ".taska" / "events.jsonl"
    renamed = ev.read_text().replace('"T00', '"T10').replace('"D005"', '"D105"')
    ev.write_text(re.sub(r'"T10(\d) title"', r'"T00\1 title"', renamed))   # ids only: the titles the judge reads stay
    rec = tmp_path / name
    rec.mkdir()
    (rec / "meta.json").write_text(json.dumps(src | {"project": str(proj)}))
    return rec


def _judged_once(tmp_path, capsys):
    record = _record(tmp_path)
    ai, calls = _stub(tmp_path)
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(record)])
    assert Q.main(["judge-run", "--record", str(record), "--model", "high", "--ai", *ai]) == 0
    capsys.readouterr()
    return record, ai, calls


def test_a_judged_question_is_reused_by_quote_in_another_run_with_no_model_call(tmp_path, capsys):
    record, ai, calls = _judged_once(tmp_path, capsys)
    assert (tmp_path / "judge-cache" / "k1sha-high.v2.jsonl").exists()
    other = _renumbered(tmp_path, record, "rec2")
    Q.main(["build", "--record", str(other), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(other)])
    before = calls.read_text()
    capsys.readouterr()
    assert Q.main(["judge-run", "--record", str(other), "--model", "high", "--cache-only"]) == 0
    assert json.loads(capsys.readouterr().out)["batches"] == {MEET: "ok"} and calls.read_text() == before
    batch = json.loads((other / "quality" / "judge" / f"judge_{MEET}.json").read_text())
    answers = json.loads((other / "quality" / "judge" / f"answers_{MEET}.json").read_text())
    assert not Q.check_verdicts(answers, batch) and {a["source"] for a in answers} == {"cache"}
    ids = {a["verdicts"].get(k) for a in answers for k in ("which_node", "duplicate_of")} - {None, "none"}
    assert all(i.startswith(("T10", "D105")) for i in ids)                     # mapped to this run's ids, never reused raw
    models = json.loads((other / "quality" / "judge" / "models.json").read_text())
    assert models[MEET] == {"model": "high", "same_tier_ok": False, "cached": len(batch)}
    Q.main(["build", "--record", str(other), "--echo", str(tmp_path / "echo")])
    assert json.loads((other / "quality" / "quality.json").read_text())["summary"]["judged_from_cache"] == len(batch)


def test_the_cache_is_per_judge_tier_and_per_key(tmp_path, capsys):
    record, ai, calls = _judged_once(tmp_path, capsys)
    other = _renumbered(tmp_path, record, "rec2")
    Q.main(["build", "--record", str(other), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(other)])
    capsys.readouterr()
    Q.main(["judge-run", "--record", str(other), "--model", "low", "--cache-only"])
    assert json.loads(capsys.readouterr().out)["batches"][MEET].startswith("cache: 0 of")
    meta = json.loads((other / "meta.json").read_text())
    (other / "meta.json").write_text(json.dumps(meta | {"key_sha": "k2sha"}))
    Q.main(["judge-run", "--record", str(other), "--model", "high", "--cache-only"])
    assert json.loads(capsys.readouterr().out)["batches"][MEET].startswith("cache: 0 of")
    assert not list((other / "quality" / "judge").glob("answers_*"))


def test_a_paid_run_asks_the_model_only_what_the_cache_cannot_answer(tmp_path, capsys):
    record, ai, calls = _judged_once(tmp_path, capsys)
    cache = tmp_path / "judge-cache" / "k1sha-high.v2.jsonl"
    lines = cache.read_text().splitlines()
    cache.write_text("\n".join(lines[1:]) + "\n")                                    # one question forgotten
    other = _renumbered(tmp_path, record, "rec2")
    Q.main(["build", "--record", str(other), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(other)])
    capsys.readouterr()
    Q.main(["judge-run", "--record", str(other), "--model", "high", "--ai", *ai])
    assert json.loads(capsys.readouterr().out)["batches"] == {MEET: "ok"}
    answers = json.loads((other / "quality" / "judge" / f"answers_{MEET}.json").read_text())
    assert sum(a.get("source") == "cache" for a in answers) == len(answers) - 1
    asked = json.loads((other / "quality" / "judge" / f"todo_{MEET}.json").read_text())
    assert len(asked) == 1


BIND = {"prompt": "p1", "model": "claude-opus-5-5", "transcript": "t1"}


def _cache_with(tmp_path, q, verdicts, bind=BIND):
    path = tmp_path / "c.jsonl"
    Q._cache_put(path, [q], [{"qid": q["qid"], "verdicts": verdicts, "why": WHY}], "r", bind)
    return path


def test_a_node_answer_is_not_reused_when_two_candidates_share_its_quote(tmp_path):
    q = {"qid": "k1", "type": "key item", "evidence": MEET, "quote": S1, "produced": {"node": None, "quote": None}, "ask": ["which_node"],
         "candidates": [{"id": "T201", "kind": "task", "title": "Draft checklist", "quote": S1},
                        {"id": "T202", "kind": "task", "title": "Draft checklist", "quote": S1}]}
    path = _cache_with(tmp_path, q, {"which_node": "T201", "how": "same"})
    assert Q._from_cache(path, [q], BIND) == ([], [q])


def _key_q():
    return {"qid": "ab12cd34-001", "type": "key item", "evidence": MEET, "quote": S1, "ts": "00:01", "speaker": "Nora",
            "expected": {"title": "Draft the onboarding checklist", "owner": "Nora", "due": None, "status": "open"},
            "produced": {"node": "T001", "title": "Draft onboarding checklist", "parent": "S002 · Warehouse onboarding", "quote": S1},
            "independent": {"what": "Nora drafts checklist", "owner": "Nora", "due": None, "at": "00:01", "kind": "action",
                            "matched_by": "words (needs the judge)"},
            "ask": ["same_work", "title_faithful", "right_parent", "independent_same"]}


def _lacks_q():
    return {"qid": f"node:{MEET}:T004", "type": "node the key lacks", "evidence": MEET,
            "produced": {"node": "T004", "kind": "task", "title": "Book the review room", "parent": "S002 · Warehouse onboarding", "quote": S1},
            "independent": None, "ask": ["is_work", "duplicate_of"],
            "candidates": [{"id": "T001", "kind": "task", "title": "Draft onboarding checklist", "quote": S1, "parent": "S002"},
                           {"id": "S002", "kind": "story", "title": "Warehouse onboarding", "quote": S1, "parent": None}]}


def _echo_q():
    return {"qid": f"echo:{MEET}:0", "type": "only the independent reader has it", "evidence": MEET,
            "independent": {"what": "Order new pallet labels", "owner": "Ravi", "due": None, "at": "00:09", "kind": "action",
                            "matched_by": "quote"}, "ask": ["is_work"]}


def _set_path(q, path, value):
    import copy
    q = copy.deepcopy(q)
    *head, last = path
    at = q
    for k in head:
        at = at[k]
    at[last] = value
    return q


MUTATIONS = [
    (_key_q, ("produced", "title"), "Draft the loading checklist"),
    (_key_q, ("produced", "parent"), "S002 · Pallet labelling"),
    (_key_q, ("produced", "quote"), "Nora will draft something else entirely for the warehouse team"),
    (_key_q, ("expected", "title"), "Share the onboarding checklist"),
    (_key_q, ("expected", "owner"), "Ravi"),
    (_key_q, ("quote",), "Ravi will draft the warehouse onboarding checklist before Thursday"),
    (_key_q, ("independent", "what"), "Ravi drafts checklist"),
    (_key_q, ("ask",), ["same_work", "title_faithful", "right_parent"]),
    (_lacks_q, ("produced", "kind"), "story"),
    (_lacks_q, ("produced", "title"), "Book a bigger room"),
    (_lacks_q, ("produced", "parent"), "S009 · Pallet labelling"),
    (_lacks_q, ("candidates", 0, "title"), "Draft the loading checklist"),
    (_lacks_q, ("candidates", 0, "kind"), "decision"),
    (_lacks_q, ("candidates", 1, "title"), "Pallet labelling"),
    (_echo_q, ("independent", "what"), "Order new shelf labels"),
    (_echo_q, ("independent", "owner"), "Nora"),
    (_echo_q, ("independent", "at"), "00:12"),
    (_echo_q, ("independent", "matched_by"), "unaligned"),
]


def test_every_field_the_judge_sees_changes_the_fingerprint():
    for make, path, value in MUTATIONS:
        q = make()
        assert Q._fingerprint(_set_path(q, path, value), BIND) != Q._fingerprint(q, BIND), (make.__name__, path)


def test_a_changed_judge_prompt_model_or_transcript_is_a_cache_miss(tmp_path):
    q = _key_q()
    path = _cache_with(tmp_path, q, {n: "yes" for n in q["ask"]})
    assert len(Q._from_cache(path, [q], BIND)[0]) == 1
    for k in BIND:
        assert Q._from_cache(path, [q], BIND | {k: "other"}) == ([], [q]), k


def test_the_binding_names_the_exact_model_and_hashes_prompts_and_transcript(tmp_path):
    t = tmp_path / "t.md"
    t.write_text("one")
    a = Q.judge_binding("high", t)
    t.write_text("two")
    b = Q.judge_binding("high", t)
    assert a["model"] == "claude-opus-5-5" and a["prompt"] == b["prompt"] and a["transcript"] != b["transcript"]
    assert Q.judge_binding("low", t)["model"] != a["model"]


def test_a_mutated_question_is_asked_of_the_model_not_answered_from_the_cache(tmp_path):
    for make, path, value in MUTATIONS:
        q = make()
        cache = tmp_path / f"{make.__name__}-{'-'.join(map(str, path))}"
        cache.mkdir()
        verdicts = {n: ("T001" if n == "duplicate_of" else "yes") for n in q["ask"]}
        stored = _cache_with(cache, q, verdicts)
        changed = _set_path(q, path, value)
        assert Q._from_cache(stored, [changed], BIND) == ([], [changed]), (make.__name__, path)


def test_a_pure_node_id_renaming_still_reuses_the_answer_and_maps_ids(tmp_path):
    q = _lacks_q()
    path = _cache_with(tmp_path, q, {"is_work": "yes", "duplicate_of": "T001"})
    renamed = json.loads(json.dumps(q).replace("T001", "T101").replace("T004", "T104").replace("S002", "S102"))
    found, missing = Q._from_cache(path, [renamed], BIND)
    assert not missing and found[0]["qid"] == f"node:{MEET}:T104" and found[0]["verdicts"] == {"is_work": "yes", "duplicate_of": "T101"}
    k = _key_q()
    path = _cache_with(tmp_path, k, {n: "yes" for n in k["ask"]})
    assert len(Q._from_cache(path, [_set_path(_set_path(k, ("produced", "node"), "T101"), ("produced", "parent"),
                                              "S102 · Warehouse onboarding")], BIND)[0]) == 1


def test_candidates_sharing_a_quote_with_other_titles_never_take_each_others_answer(tmp_path):
    q = {"qid": "ab12cd34-002", "type": "key item", "evidence": MEET, "quote": S1, "produced": {"node": None, "quote": None},
         "ask": ["which_node"], "candidates": [{"id": "T201", "kind": "task", "title": "Draft checklist", "quote": S1},
                                               {"id": "T202", "kind": "task", "title": "Share checklist", "quote": S1}]}
    path = _cache_with(tmp_path, q, {"which_node": "T201", "how": "same"})
    found, _ = Q._from_cache(path, [q], BIND)
    assert found[0]["verdicts"]["which_node"] == "T201"
    swapped = _set_path(_set_path(q, ("candidates", 0, "title"), "Share checklist"), ("candidates", 1, "title"), "Draft checklist")
    found, _ = Q._from_cache(path, [swapped], BIND)
    assert found[0]["verdicts"]["which_node"] == "T202"                          # the answer follows the title, not the position
    lost = _set_path(q, ("candidates", 0, "title"), "Share checklist")            # now both read alike: ambiguous
    assert Q._from_cache(path, [lost], BIND) == ([], [lost])


def test_an_older_cache_file_is_never_read_and_never_touched(tmp_path, capsys):
    record = _record(tmp_path)
    old = tmp_path / "judge-cache" / "k1sha-high.jsonl"
    old.parent.mkdir()
    old.write_text('{"fp": "x", "verdicts": {}, "why": "w", "from": "r:q"}\n')
    before = old.read_bytes()
    ai, calls = _stub(tmp_path)
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(record)])
    assert Q.main(["judge-run", "--record", str(record), "--model", "high", "--ai", *ai]) == 0
    assert old.read_bytes() == before and (tmp_path / "judge-cache" / "k1sha-high.v2.jsonl").exists()


def _srecord(root, name, items):
    rec = root / name
    (rec / "quality").mkdir(parents=True)
    (rec / "meta.json").write_text(json.dumps({"key_version": "v1", "key_sha": "k1sha"}))
    doc = {"schema": "eval-quality/1", "units": [{"id": "m1", "nodes": [{"id": "T1"}], "items": items}],
           "summary": {"found_correct": None, "judged_items": [0, 0], "judge_same_tier": [], "judge_agreement": {}}}
    (rec / "quality" / "quality.json").write_text(json.dumps(doc))
    return rec


def test_stable_judged_counts_an_item_only_when_the_judge_actually_ruled_on_it(tmp_path):
    card = {"id": "k1", "produced": {"node": "proposal card"}, "checks": [_c("Found", "ok", "code")]}
    asked = {"id": "k1", "produced": {"node": None}, "checks": [_c("Found", "check", "judge")]}
    ruled = {"id": "k2", "produced": {"node": "T1"}, "checks": [_c("Found", "ok", "code"), _c(Q.ASK["same_work"], "ok", "judge")]}
    pending = {"id": "k2", "produced": {"node": "T1"}, "checks": [_c("Found", "ok", "code"), _c(Q.ASK["same_work"], "check", "judge")]}
    recs = [_srecord(tmp_path, "r1", [card, ruled]), _srecord(tmp_path, "r2", [card, ruled]),
            _srecord(tmp_path, "r3", [asked, pending]), _srecord(tmp_path, "r4", [asked, pending])]
    out = Q.stability(recs)
    items = {i["id"]: i for i in out["items"]}
    assert not items["k1"]["by_judge"] and items["k1"]["missing"] == 2     # by code twice, the judge only asked
    assert items["k2"]["by_judge"] and not items["k2"]["stable"]             # judged in r1 and r2, missing in r3 and r4
    assert out["summary"]["stable_judged"] == [0, 1]


# ── the page's labels: what the doc carries for Save, earlier answers pre-selected ───────────────────────────────

def _labelled_record(tmp_path):
    """A built record whose judge answered a key item, a node the key lacks and an item only the independent reader has."""
    record = _record(tmp_path)
    _echo(tmp_path)
    judge = record / "quality" / "judge"
    judge.mkdir(parents=True)
    (judge / f"answers_{MEET}.json").write_text(json.dumps([
        {"qid": "ab12cd34-001", "verdicts": {"same_work": "yes", "title_faithful": "yes", "right_parent": "no"}, "why": WHY},
        {"qid": "ab12cd34-002", "verdicts": {"which_node": "none", "independent_same": "yes"}, "why": WHY},
        {"qid": f"node:{MEET}:T004", "verdicts": {"is_work": "yes", "duplicate_of": "none"}, "why": WHY},
        {"qid": f"echo:{MEET}:0", "verdicts": {"is_work": "no"}, "why": WHY}]))
    return record


def _built(record, tmp_path, *extra):
    assert Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo"), *extra]) == 0
    return json.loads((record / "quality" / "quality.json").read_text())


def test_the_doc_carries_the_stamp_the_key_and_each_cards_qid_and_judge_answers(tmp_path, capsys):
    import relabel
    from taska_lib.evalscore import load_gold
    record = _labelled_record(tmp_path)
    doc = _built(record, tmp_path)
    key = Path(json.loads((record / "meta.json").read_text())["key_path"])
    head = load_gold(key)[0]
    assert (doc["stamp"], doc["record"], doc["key_version"], doc["key_sha"]) == (relabel._stamp(record, key), "rec", "v1", head["sha256"])
    assert len(doc["key_sha"]) == 64
    u = doc["units"][0]
    i1 = next(i for i in u["items"] if i["id"] == "ab12cd34-001")
    assert i1["qid"] == "ab12cd34-001"
    assert i1["edit"] == {"title": KEY[0]["title"], "kind": "task", "commitment": "committed", "owner": "Nora Quill", "due": None}
    assert [(j["qid"], j["ask"], j["value"]) for j in i1["judge"]] == [
        ("ab12cd34-001", "same_work", "yes"), ("ab12cd34-001", "title_faithful", "yes"), ("ab12cd34-001", "right_parent", "no")]
    assert i1["judge"][0]["options"] == ["yes", "no"] and i1["judge"][0]["why"] == WHY
    i2 = next(i for i in u["items"] if i["id"] == "ab12cd34-002")
    which = next(j for j in i2["judge"] if j["ask"] == "which_node")
    assert which["value"] == "none" and which["options"] == ["none", "T001", "T003", "T004", "D005"]
    t4 = next(x for x in u["extras"] if x["produced"]["node"] == "T004")
    assert t4["qid"] == f"node:{MEET}:T004" and t4["produced"]["quote"] == S4
    dup = next(j for j in t4["judge"] if j["ask"] == "duplicate_of")
    assert dup["options"] == ["none", "T001", "T003", "D005"]
    assert next(x for x in u["extras"] if x["produced"]["node"] == "D005").get("judge") is None
    only = u["independent_only"][0]
    assert only["qid"] == f"echo:{MEET}:0" and [(j["ask"], j["value"]) for j in only["judge"]] == [("is_work", "no")]


def test_build_yours_marks_each_cards_earlier_answer_and_counts_what_matches_no_card(tmp_path, capsys):
    record = _labelled_record(tmp_path)
    page = tmp_path / "key-labels-rec.json"
    page.write_text(json.dumps({"stamp": "rec-abcdef12", "saved_at": "2026-03-01T10:00:00Z", "answers": {
        "ab12cd34-003": {"kind": "not_work"}, "node:T004": {"kind": "committed"}, "ab12cd34-999": {"kind": "proposed"}}}))
    chat = tmp_path / "rulings.json"
    chat.write_text(json.dumps({"ab12cd34-001": {"kind": "proposed"}}))
    items = tmp_path / "labels.json"
    items.write_text(json.dumps({"date": "2026-02-28", "items": [{"evidence": MEET, "quote": S5, "user": {"kind": "not_work"}}]}))
    saved = tmp_path / "labels-old.json"
    saved.write_text(json.dumps({"schema": "eval-labels/1", "stamp": "rec-old-abcdef12", "record": "rec-old", "key_version": "v1",
                                 "key_sha": "k", "saved_at": "2026-03-02T09:00:00Z", "answers": {
                                     "ab12cd34-004": {"action": "drop", "reason": "vague"},
                                     "judge:ab12cd34-001:same_work": {"action": "agree", "expected": "yes"}}}))
    doc = _built(record, tmp_path, *[x for f in (page, chat, items, saved) for x in ("--yours", str(f))])
    u = doc["units"][0]
    card = {i["id"]: i for i in u["items"]} | {x["qid"]: x for x in u["extras"]}
    assert card["ab12cd34-003"]["yours"] == {"answer": "not_work", "source": "you, 2026-03-01 (key-labels-rec)", "entry": {"action": "drop"}}
    assert card["ab12cd34-001"]["yours"]["entry"] == {"action": "modify", "fields": {"commitment": "proposed"}}
    assert card[f"node:{MEET}:T004"]["yours"]["entry"] == {"action": "add", "commitment": "committed"}
    assert card[f"node:{MEET}:D005"]["yours"] == {"answer": "not_work", "source": "you, 2026-02-28 (labels)", "entry": {"action": "not_work"}}
    assert card["ab12cd34-004"]["yours"] == {"answer": "drop", "source": "you, 2026-03-02 (labels-old)",
                                             "entry": {"action": "drop", "reason": "vague"}}
    assert "yours" not in card["ab12cd34-002"]
    assert doc["summary"]["yours_unmatched"] == 2      # an item not in this key; a judge label made on another run
    assert "yours_unmatched" not in _built(record, tmp_path)["summary"]


def test_the_pages_save_is_a_labels_file_that_apply_and_validation_read(tmp_path, capsys):
    import shutil
    import subprocess
    import evalview
    import relabel
    if not shutil.which("node"):
        import pytest
        pytest.skip("node is not installed: the page's Save code cannot run here")
    record = _labelled_record(tmp_path)
    doc = _built(record, tmp_path)
    edit3 = next(i for i in doc["units"][0]["items"] if i["id"] == "ab12cd34-003")["edit"]
    edit4 = next(i for i in doc["units"][0]["items"] if i["id"] == "ab12cd34-004")["edit"]
    state = {"ab12cd34-001": {"action": "drop", "reason": "client_own", "note": " their own fix ", "seen_judge": False},
             "ab12cd34-003": {"action": "modify", "fields": edit3 | {"owner": "Nora Quill", "due": "2026-03-09"}, "seen_judge": False},
             "ab12cd34-004": {"action": "modify", "fields": dict(edit4)},          # nothing changed: incomplete, not saved
             f"node:{MEET}:T004": {"action": "add", "kind": "task", "commitment": "committed", "seen_judge": True,
                                   "fields": {"title": "Collect the scanner serials", "owner": "Opal Brennick", "due": ""}},
             f"node:{MEET}:D005": {"action": "duplicate", "dup_of": "T004"},
             "judge:ab12cd34-001:same_work": {"value": "yes", "seen_judge": False},
             f"judge:node:{MEET}:T004:is_work": {"value": "no", "seen_judge": True}}
    js = evalview.QUALITY_PAGE.split("// LABELS-START")[1].split("// LABELS-END")[0]
    harness = js + f"\nconsole.log(JSON.stringify(labelsFile({json.dumps(doc)}, {json.dumps(state)}, '2026-03-03T09:00:00.000Z')));"
    out = json.loads(subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True).stdout)
    assert out["incomplete"] == 1
    f = out["file"]
    assert {k: f[k] for k in ("schema", "stamp", "record", "key_version", "key_sha", "saved_at")} == {
        "schema": "eval-labels/1", "stamp": doc["stamp"], "record": "rec", "key_version": "v1", "key_sha": doc["key_sha"],
        "saved_at": "2026-03-03T09:00:00.000Z"}
    assert f["answers"] == {
        "ab12cd34-001": {"action": "drop", "reason": "client_own", "note": "their own fix", "seen_judge": False},
        "ab12cd34-003": {"action": "modify", "fields": {"owner": "Nora Quill", "due": "2026-03-09"}, "seen_judge": False},
        f"node:{MEET}:T004": {"action": "add", "kind": "task", "commitment": "committed", "quote": S4, "seen_judge": True,
                              "fields": {"title": "Collect the scanner serials", "owner": "Opal Brennick", "due": ""}},
        f"node:{MEET}:D005": {"action": "duplicate", "dup_of": "T004", "quote": S5, "dup_of_quote": S4},
        "judge:ab12cd34-001:same_work": {"action": "agree", "expected": "yes", "quote": S1, "seen_judge": False},
        f"judge:node:{MEET}:T004:is_work": {"action": "disagree", "expected": "no", "seen_judge": True}}
    saved = tmp_path / "labels-rec.json"
    saved.write_text(json.dumps(f))
    key = Path(json.loads((record / "meta.json").read_text())["key_path"])
    assert relabel.read_answers(saved, relabel._stamp(record, key), key_sha=doc["key_sha"], record="rec", key_version="v1") == f["answers"]
    capsys.readouterr()
    assert relabel.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(saved), "--version", "v2", "--dry-run"]) == 0
    diff = capsys.readouterr().out
    assert "dropped 1 (client_own 1), added 1, modified 1" in diff
    rows, skipped = Q.page_label_rows(f, doc, Q._answers(record / "quality" / "judge"))
    assert {(r["qid"], r["ask"], r["expected"]) for r in rows} == {
        ("ab12cd34-001", "same_work", "yes"), (f"node:{MEET}:T004", "is_work", "no"), (f"node:{MEET}:D005", "duplicate_of", "T004")}
    assert skipped == []


HARNESS = """
const fs = require('fs'), cfg = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const els = {}, on = {};
const el = id => els[id] || (els[id] = {innerHTML: '', textContent: '', style: {}, addEventListener: (t, f) => (on[id + ':' + t] = f)});
global.document = {getElementById: el, querySelectorAll: () => [], createElement: () => ({click() {}, remove() {}}), body: {appendChild() {}}};
global.localStorage = {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); }};
global.fetch = () => new Promise(() => {});
eval(cfg.js + ';globalThis.T = {start, show, labelsFile, opts, reveal: q => { R[q] = true; show(D); }, set: (k, v) => { S[k] = v; show(D); }, S: () => S, D: () => D};');
T.start(cfg.doc);
const out = [];
for (const [step, q, data] of cfg.steps) {
  if (step === 'html') out.push(els.units.innerHTML);
  else if (step === 'lead') out.push([els.lead.innerHTML, els.totals.innerHTML, els.rule.innerHTML]);
  else if (step === 'totals') on['totals:click']({target: {id: 'showtotals'}});
  else if (step === 'reveal') T.reveal(q);
  else if (step === 'set') T.set(q, data);
  else if (step === 'labels') out.push(T.labelsFile(T.D(), T.S(), 't'));
  else if (step === 'click') { const sec = {dataset: {q}}, b = {dataset: data, closest: s => s.startsWith('button') ? b : sec}; on['units:click']({target: b}); }
}
console.log(JSON.stringify(out));
"""


def _page(tmp_path, doc, *steps):
    """Runs the quality page's script on doc in node with a stub DOM (no storage) and returns each step's output."""
    import shutil
    import subprocess
    import evalview
    if not shutil.which("node"):
        import pytest
        pytest.skip("node is not installed: the page's code cannot run here")
    cfg = tmp_path / "page-cfg.json"
    cfg.write_text(json.dumps({"js": evalview.QUALITY_PAGE.split("<script>")[1].split("</script>")[0], "doc": doc, "steps": steps}))
    return json.loads(subprocess.run(["node", "-e", HARNESS, str(cfg)], capture_output=True, text=True, check=True).stdout)


def _section(html, q):
    """One card's markup without its actions (what the person reads, not what they click)."""
    start = html.index(f"data-q='{q}'")
    return html[start:html.index("</section>", start)].split("<div class=acts>")[0]


def _extras(html):
    return html.split("<h3>Created, not expected</h3>")[1].split("<h3>Only")[0]


def test_before_you_reveal_it_a_judged_card_and_the_extras_read_as_if_unjudged(tmp_path, capsys):
    for side in ("a", "b"):
        (tmp_path / side).mkdir()
    unjudged = _record(tmp_path / "a")
    _echo(tmp_path / "a")
    plain = _built(unjudged, tmp_path / "a")
    record = _record(tmp_path / "b")
    _echo(tmp_path / "b")
    judge = record / "quality" / "judge"
    judge.mkdir(parents=True)
    (judge / f"answers_{MEET}.json").write_text(json.dumps([
        {"qid": "ab12cd34-002", "verdicts": {"which_node": "T004", "how": "same", "independent_same": "yes"}, "why": WHY}]))
    doc = _built(record, tmp_path / "b")
    u = doc["units"][0]
    item = next(i for i in u["items"] if i["id"] == "ab12cd34-002")
    was = next(i for i in plain["units"][0]["items"] if i["id"] == "ab12cd34-002")
    assert item["before_judge"] == {"checks": was["checks"], "agreement": was["agreement"]} and item["checks"] != was["checks"]
    assert [x["produced"]["node"] for x in u["extras"]] == ["D005"]
    assert [(x["produced"]["node"], x["taken_by_judge"]) for x in u["extras_taken"]] == [("T004", "ab12cd34-002")]
    before, = _page(tmp_path, plain, ["html"])
    hidden, shown = _page(tmp_path, doc, ["html"], ["reveal", "ab12cd34-002"], ["html"])
    assert _section(hidden, "ab12cd34-002") == _section(before, "ab12cd34-002")
    assert _extras(hidden) == _extras(before)
    assert _section(shown, "ab12cd34-002") != _section(before, "ab12cd34-002") and f"node:{MEET}:T004" not in _extras(shown)


def test_disagree_saves_nothing_until_another_value_is_chosen(tmp_path, capsys):
    doc = _built(_labelled_record(tmp_path), tmp_path)
    q, k = "ab12cd34-002", "judge:ab12cd34-002:which_node"
    opened, same, other = _page(tmp_path, doc, ["reveal", q], ["click", q, {"act": "disagree", "j": "which_node"}], ["labels"],
                                ["set", k, {"value": "none", "disagree": True, "seen_judge": True}], ["labels"],
                                ["set", k, {"value": "T004", "disagree": True, "seen_judge": True}], ["labels"])
    for out in (opened, same):
        assert k not in out["file"]["answers"] and out["incomplete"] == 1
    assert other["file"]["answers"][k] == {"action": "disagree", "expected": "T004", "quote": S4, "seen_judge": True}


def test_drop_and_not_work_need_a_reason_and_other_needs_a_note(tmp_path, capsys):
    doc = _built(_labelled_record(tmp_path), tmp_path)
    n = f"node:{MEET}:T004"
    out, = _page(tmp_path, doc, ["set", "ab12cd34-001", {"action": "drop"}], ["set", "ab12cd34-003", {"action": "drop", "reason": "other"}],
                 ["set", "ab12cd34-004", {"action": "drop", "reason": "other", "note": "a one-off chat"}],
                 ["set", n, {"action": "not_work"}], ["labels"])
    assert list(out["file"]["answers"]) == ["ab12cd34-004"] and out["incomplete"] == 3


def test_a_current_value_outside_a_select_list_is_shown_selected(tmp_path, capsys):
    doc = _built(_labelled_record(tmp_path), tmp_path)
    html, = _page(tmp_path, doc, ["set", "ab12cd34-001", {"action": "modify", "fields": {"title": "t", "kind": "epic", "commitment": "committed"}}],
                  ["html"])
    assert "<option value='epic' selected>epic</option>" in _section(html.replace("<div class=acts>", "<div class=ACTS>"), "ab12cd34-001")


def test_an_earlier_answer_left_unchanged_is_saved_with_where_it_came_from_and_not_counted_blind(tmp_path, capsys):
    record = _labelled_record(tmp_path)
    chat = tmp_path / "rulings.json"
    chat.write_text(json.dumps({"ab12cd34-001": {"kind": "committed"}, "ab12cd34-003": {"kind": "committed"}}))
    doc = _built(record, tmp_path, "--yours", str(chat))
    kept, changed = _page(tmp_path, doc, ["labels"], ["click", "ab12cd34-003", {"act": "set", "v": "drop"}],
                          ["click", "ab12cd34-003", {"act": "set", "v": "keep"}], ["labels"])
    assert kept["file"]["answers"]["ab12cd34-001"] == {"action": "keep", "carried_from": "you (chat ruling, rulings)"}
    assert changed["file"]["answers"]["ab12cd34-003"] == {"action": "keep", "seen_judge": False}
    labels = {"schema": "eval-labels/1", "record": "rec", "answers": {
        "judge:ab12cd34-001:same_work": {"action": "agree", "expected": "yes", "seen_judge": False, "carried_from": "you, 2026-03-01 (x)"}}}
    rows, _ = Q.page_label_rows(labels, doc)
    assert rows == [{"qid": "ab12cd34-001", "ask": "same_work", "expected": "yes", "source": "you", "seen_judge": None}]


def test_the_judges_run_wide_totals_stay_behind_one_control_until_revealed(tmp_path, capsys):
    for side in ("a", "b"):
        (tmp_path / side).mkdir()
    plain = _built(_record(tmp_path / "a"), tmp_path / "a")
    _echo(tmp_path / "a")
    record = _record(tmp_path / "b")
    _echo(tmp_path / "b")
    judge = record / "quality" / "judge"
    judge.mkdir(parents=True)
    (judge / f"answers_{MEET}.json").write_text(json.dumps([
        {"qid": "ab12cd34-002", "verdicts": {"which_node": "T004", "how": "same", "independent_same": "yes"}, "why": WHY}]))
    doc = _built(record, tmp_path / "b")
    assert doc["judged"] > 0 and plain["judged"] == 0
    q = "ab12cd34-002"
    before, = _page(tmp_path, plain, ["lead"])
    assert "Show the judge's totals" not in before[1] and "Found correctly" not in before[0] and "Inventions" in before[2]
    unanswered, veiled, shown, labels = _page(
        tmp_path, doc, ["lead"], ["set", q, {"action": "drop", "reason": "vague", "seen_judge": False}], ["lead"], ["totals"], ["lead"],
        ["click", q, {"act": "set", "v": "keep"}], ["labels"])
    assert "Show the judge's totals" not in unanswered[1] and "Found correctly" in unanswered[0]   # nothing answered: nothing to protect
    assert "Show the judge's totals" in veiled[1] and "every card" in veiled[1]
    assert "Found correctly" not in veiled[0] and "inventions" not in veiled[0] and "agreed" not in veiled[0].lower()
    assert "Inventions" not in veiled[2] and "Work found, correctly" not in veiled[2] and "Judge agrees with you" not in veiled[2]
    assert "Found correctly" in shown[0] and shown[1] == "" and "Work found, correctly" in shown[2]
    assert labels["file"]["answers"][q] == {"action": "keep", "seen_judge": True}   # a label set after the reveal is seen


# ── judged precision: open world, one-to-one, not_work + duplicate, labels win, gated ─────────────────────────────

def _pdoc(*units):
    return {"run": {"record": "rec-a"}, "units": list(units)}


def _punit(evid=MEET, items=(), extras=(), taken=(), nodes=("T001", "S001", "T002", "T003", "T004", "D005")):
    parent = {"T001": "S001"}
    return {"id": evid, "items": [dict(i) for i in items], "extras": [{"produced": {"node": n}} for n in extras],
            "extras_taken": [{"produced": {"node": n}} for n in taken],
            "nodes": [{"id": n, "kind": "story" if n.startswith("S") else "task", "parent": parent.get(n)} for n in nodes]}


def _ans(qid, **verdicts):
    return {"qid": qid, "verdicts": verdicts, "why": WHY}


def _nq(nid, evid=MEET):
    return f"node:{evid}:{nid}"


ITEM1 = {"id": "k1", "produced": {"node": "T001"}}
ITEM2 = {"id": "k2", "produced": {"node": None}}
PKEYS = {"correct", "wrong", "unjudged", "n", "rate", "lb95", "meetings", "per_meeting_to_delete", "validated", "flag"}


def test_precision_counts_correct_not_work_and_duplicate_and_leaves_the_rest_unjudged():
    doc = _pdoc(_punit(items=[ITEM1, ITEM2 | {"found_by_judge": {"node": "T003", "how": "same"}}], extras=["T002", "T004", "D005"]))
    answers = [_ans("k1", same_work="yes"), _ans(_nq("T002"), is_work="yes", duplicate_of="T001"),
               _ans(_nq("T004"), is_work="no", duplicate_of="none"), _ans(_nq("D005"), is_work="yes", duplicate_of="none")]
    p = Q.precision(doc, answers, {})
    assert set(p) == PKEYS
    assert (p["correct"], p["wrong"], p["unjudged"], p["n"]) == (3, {"not_work": 1, "duplicate": 1}, 1, 5)   # S001 unjudged


def test_precision_a_node_that_is_a_key_items_work_is_never_wrong():
    """No double penalty: a node the judge found for a key item counts once, as correct, whatever its node answers said."""
    doc = _pdoc(_punit(items=[ITEM2 | {"found_by_judge": {"node": "T004", "how": "inside"}}], taken=["T004"], nodes=("T004",)))
    p = Q.precision(doc, [_ans(_nq("T004"), is_work="no", duplicate_of="none")], {})
    assert (p["correct"], p["wrong"], p["unjudged"], p["n"]) == (1, {"not_work": 0, "duplicate": 0}, 0, 1)


def test_precision_two_nodes_for_one_key_item_are_one_correct_and_one_duplicate():
    doc = _pdoc(_punit(items=[ITEM1, {"id": "k3", "produced": {"node": None}, "found_by_judge": {"node": "T001", "how": "same"}}],
                       extras=["T002"], nodes=("T001", "T002")))
    p = Q.precision(doc, [_ans("k1", same_work="yes"), _ans(_nq("T002"), is_work="yes", duplicate_of="T001")], {})
    assert (p["correct"], p["wrong"]["duplicate"], p["n"]) == (1, 1, 2)   # T001 credited by two items counts once


def test_precision_mutual_duplicates_count_only_the_later_node():
    doc = _pdoc(_punit(extras=["T002", "T004"], nodes=("T002", "T004")))
    p = Q.precision(doc, [_ans(_nq("T002"), is_work="yes", duplicate_of="T004"), _ans(_nq("T004"), is_work="yes", duplicate_of="T002")], {})
    assert (p["correct"], p["wrong"]["duplicate"]) == (1, 1)


def test_precision_is_open_world_an_unjudged_node_is_never_wrong():
    doc = _pdoc(_punit(items=[ITEM1, {"id": "k2", "produced": {"node": "T003"}}], extras=["T002", "T004", "D005"]))
    answers = [_ans("k2", same_work="no"), _ans(_nq("T004"), is_work="unsure", duplicate_of="none"),
               _ans(_nq("D005"), is_work="yes", duplicate_of="none")]
    p = Q.precision(doc, answers, {})   # T001: a quote match with no same_work answer yet; T003: not the key item's work
    assert (p["correct"], p["wrong"], p["unjudged"], p["n"], p["rate"]) == (1, {"not_work": 0, "duplicate": 0}, 5, 1, 1.0)


def test_precision_labels_override_the_judge_per_node():
    doc = _pdoc(_punit(items=[ITEM1], extras=["T002", "T004", "D005"], nodes=("T001", "S001", "T002", "T004", "D005")))
    answers = [_ans("k1", same_work="yes"), _ans(_nq("T002"), is_work="no", duplicate_of="none"),
               _ans(_nq("T004"), is_work="yes", duplicate_of="none")]
    labels = {_nq("T001"): "not_work", _nq("T002"): "add", _nq("T004"): "duplicate"}
    p = Q.precision(doc, answers, labels)
    assert (p["correct"], p["wrong"], p["unjudged"]) == (1, {"not_work": 1, "duplicate": 1}, 2)   # S001, D005 unjudged


def test_precision_gives_no_automatic_credit_to_the_story_of_a_found_task():
    doc = _pdoc(_punit(items=[ITEM1], nodes=("T001", "S001")))
    p = Q.precision(doc, [_ans("k1", same_work="yes")], {})
    assert (p["correct"], p["unjudged"]) == (1, 1)
    p = Q.precision(doc, [_ans("k1", same_work="yes"), _ans(_nq("S001"), is_work="yes", duplicate_of="none")], {})
    assert (p["correct"], p["unjudged"]) == (2, 0)


def test_precision_arithmetic_rate_lb95_and_to_delete_per_meeting():
    from taska_lib.evalscore import _wilson
    other = "2026-03-09_meetings_ffffffff"
    doc = _pdoc(_punit(extras=["T002", "T004", "D005"], nodes=("T002", "T004", "D005")),
                _punit(evid=other, extras=["T002", "T004"], nodes=("T002", "T004")))
    answers = [_ans(_nq("T002"), is_work="yes", duplicate_of="none"), _ans(_nq("T004"), is_work="yes", duplicate_of="none"),
               _ans(_nq("D005"), is_work="no", duplicate_of="none"),
               _ans(_nq("T002", other), is_work="yes", duplicate_of="none"), _ans(_nq("T004", other), is_work="yes", duplicate_of="T002")]
    p = Q.precision(doc, answers, {})
    assert (p["correct"], p["n"], p["meetings"]) == (3, 5, 2)
    assert p["rate"] == 0.6 and p["lb95"] == _wilson(3, 5) and p["per_meeting_to_delete"] == 1.0
    empty = Q.precision(_pdoc(_punit(nodes=())), [], {})
    assert (empty["n"], empty["rate"], empty["lb95"], empty["per_meeting_to_delete"]) == (0, None, None, None)


def test_precision_before_the_judge_is_not_judged_yet_and_has_no_pass():
    doc = _pdoc(_punit(items=[ITEM1], extras=["T002"]))
    p = Q.precision(doc, [], {})
    assert (p["correct"], p["n"], p["validated"], p["flag"]) == (0, 0, False, "not judged yet")
    row = next(r for r in Q.pass_rule({"precision": p}, {"precision_min": 0.5} | GATE) if r["measure"] == "Precision (judged)")
    assert (row["value"], row["pass"], row["flag"]) == (None, None, "not judged yet")


def test_precision_passes_only_when_the_judge_is_validated_on_the_asks_it_rests_on():
    p = {"correct": 8, "wrong": {"not_work": 1, "duplicate": 1}, "unjudged": 3, "n": 10, "rate": 0.8, "lb95": 0.49, "meetings": 2,
         "per_meeting_to_delete": 1.0, "validated": False, "flag": None}
    by = lambda rule, s=JUDGED: next(r for r in Q.pass_rule(s | {"precision": p}, rule) if r["measure"] == "Precision (judged)")
    assert by(BARS | GATE | {"precision_min": 0.7})["pass"] is True
    assert by(BARS | GATE)["bar"] is None and by(BARS | GATE)["pass"] is None   # no default bar
    tight = BARS | GATE | {"precision_min": 0.7, "judge_agreement_min": 0.95}   # found asks 10/10, work asks 8/10
    assert by(tight)["flag"] == "not validated" and next(r for r in Q.pass_rule(JUDGED, tight) if r["measure"] == "Work found, correctly")["pass"]
    assert by(BARS | {"precision_min": 0.7})["flag"] == "not validated"


def test_node_labels_resolve_page_labels_to_this_docs_nodes():
    labels = _labels({f"node:{MEET}:T004": {"action": "add", "quote": S4}, f"node:{MEET}:D005": {"action": "not_work"},
                      f"node:{MEET}:T001": {"action": "duplicate", "dup_of": "ab12cd34-001"}, "ab12cd34-001": {"action": "drop"},
                      f"node:{MEET}:T009": {"action": "add"}})
    assert Q.node_labels([labels], LDOC) == {_nq("T004"): "add", _nq("D005"): "not_work", _nq("T001"): "duplicate"}


def test_build_puts_precision_in_the_summary_and_a_precision_row_in_the_rule(tmp_path, capsys):
    doc = _built(_labelled_record(tmp_path), tmp_path)
    p = doc["summary"]["precision"]
    assert set(p) == PKEYS and p["meetings"] == 1 and p["n"] >= 2 and p["validated"] is False and p["flag"] == "judge tier not independent"
    row = next(r for r in doc["rule"] if r["measure"] == "Precision (judged)")
    assert row["value"] == p["rate"] and row["flag"] == p["flag"]


def test_build_shows_proposal_cards_from_the_scorer_as_a_rule_row(tmp_path, capsys):
    doc = _built(_labelled_record(tmp_path), tmp_path)
    assert doc["summary"]["proposal_cards"] == [0, 0]
    assert not any(r["measure"] == "Proposal cards matching the key (lower bound)" for r in doc["rule"])   # no cards: no row
    s = JUDGED | {"proposal_cards": [1, 2], "proposal_cards_burden": 0.5}
    row = next(r for r in Q.pass_rule(s, BARS | {"proposal_cards_min": 0.1}) if r["measure"] == "Proposal cards matching the key (lower bound)")
    assert (row["value"], row["bar"], row["pass"], row["flag"]) == (0.5, None, None, "lower bound")   # a lower bound never passes
    assert "0.5" in row["note"]   # its burden: unmatched cards per meeting
    assert not any(r["measure"] == "Proposal cards matching the key (lower bound)" for r in Q.pass_rule(JUDGED, BARS))


def _container_doc(tmp_path):
    project = _project(tmp_path)
    cfg = C.load(project)
    fields = lambda title, parent: {"title": title, "parent": parent, "internal_owner": None, "client_owner": None, "client_owed": False,
                                    "ac": [], "due": None, "plan": None}
    S.append(cfg.store, [{"type": "create", "mid": "mS006", "id": "S006", "kind": "story", "by": "agent", "evidence": MEET,
                          "date": "2026-03-02", "quote": S6, "fields": fields("Safety poster", "E01")},
                         {"type": "create", "mid": "mT007", "id": "T007", "kind": "task", "by": "agent", "evidence": MEET,
                          "date": "2026-03-02", "quote": S6, "fields": fields("Update the poster", "S006")}])
    key = KEY + [dict(KEY[0], cid="ab12cd34-005", quote=S6, title="Update the safety poster")]
    return Q.build(project, key, {MEET: None}, {"record": "r", "code": "c", "tier": "med"})


def test_the_story_of_a_code_matched_task_is_asked_like_any_node_but_stays_out_of_not_in_the_key(tmp_path):
    doc = _container_doc(tmp_path)
    u = doc["units"][0]
    assert [x["produced"]["node"] for x in u["containers"]] == ["S006"]
    assert "S006" not in {x["produced"]["node"] for x in u["extras"]}
    assert doc["summary"]["not_in_key_unjudged"] == len(u["extras"])
    q = next(q for q in Q.judge_questions(doc)[MEET] if q["qid"] == f"node:{MEET}:S006")
    assert q["ask"] == ["is_work", "duplicate_of"] and "S006" not in {c["id"] for c in q["candidates"]}
    assert f"node:{MEET}:S006" in Q.label_cards(doc)
    assert Q.precision(doc, [], {})["unjudged"] >= 1 and Q.precision(doc, [], {})["n"] == 0   # before the judge: unjudged


def test_a_container_story_judged_not_work_counts_wrong_and_leaves_the_extras_counts_alone(tmp_path):
    doc = _container_doc(tmp_path)
    answers = [{"qid": "ab12cd34-005", "verdicts": {"same_work": "yes", "title_faithful": "yes", "right_parent": "yes"}, "why": WHY},
               {"qid": f"node:{MEET}:S006", "verdicts": {"is_work": "no", "duplicate_of": "none"}, "why": WHY}]
    base = Q.apply_verdicts(doc, answers[:1])["summary"]
    merged = Q.apply_verdicts(doc, answers)
    s = merged["summary"]
    assert (s["inventions"], s["key_gap_candidates"], s["not_in_key_unjudged"]) == (base["inventions"], base["key_gap_candidates"], base["not_in_key_unjudged"])
    c = merged["units"][0]["containers"][0]
    assert next(x for x in c["checks"] if x["name"] == Q.ASK["is_work"])["verdict"] == "no"
    p = Q.precision(merged, answers, {})
    assert p["wrong"]["not_work"] == 1 and p["correct"] == 1   # T007 credited, S006 not work


def test_a_judged_container_story_keeps_its_pre_judge_checks_for_the_blind_view(tmp_path):
    before = _container_doc(tmp_path)
    answers = [{"qid": f"node:{MEET}:S006", "verdicts": {"is_work": "no", "duplicate_of": "none"}, "why": WHY}]
    doc = Q.apply_verdicts(before, answers)
    Q.attach_judge(doc, answers)
    Q.keep_pre_judge(doc, before)
    c = doc["units"][0]["containers"][0]
    assert [j["ask"] for j in c["judge"]] == ["is_work", "duplicate_of"]
    assert {x["verdict"] for x in c["before_judge"]["checks"]} == {"check"}


def test_a_matched_node_judged_not_the_same_work_is_asked_like_a_container_and_then_judged(tmp_path):
    doc = _doc(tmp_path)
    no = [{"qid": "ab12cd34-001", "verdicts": {"same_work": "no", "title_faithful": "yes", "right_parent": "yes"}, "why": WHY}]
    merged = Q.apply_verdicts(doc, no)
    base = Q.apply_verdicts(doc, [])["summary"]
    u = merged["units"][0]
    assert "T001" in {x["produced"]["node"] for x in u["containers"]}
    assert (merged["summary"]["inventions"], merged["summary"]["key_gap_candidates"], merged["summary"]["not_in_key_unjudged"]) == \
        (base["inventions"], base["key_gap_candidates"], base["not_in_key_unjudged"])
    q = next(q for q in Q.judge_questions(merged)[MEET] if q["qid"] == f"node:{MEET}:T001")
    assert q["ask"] == ["is_work", "duplicate_of"] and f"node:{MEET}:T001" in Q.label_cards(merged)
    answers = no + [{"qid": f"node:{MEET}:T001", "verdicts": {"is_work": "no", "duplicate_of": "none"}, "why": WHY}]
    merged = Q.apply_verdicts(doc, answers)
    assert Q.precision(merged, answers, {})["wrong"]["not_work"] == 1


def test_one_duplicate_cycle_rule_for_the_counts_and_for_precision(tmp_path):
    doc = _container_doc(tmp_path)   # node order T001 T003 T004 D005 S006 T007: T004 (an extra) before S006 (a container)
    answers = [{"qid": f"node:{MEET}:T004", "verdicts": {"is_work": "yes", "duplicate_of": "S006"}, "why": WHY},
               {"qid": f"node:{MEET}:S006", "verdicts": {"is_work": "yes", "duplicate_of": "T004"}, "why": WHY}]
    merged = Q.apply_verdicts(doc, answers)
    assert merged["summary"]["inventions"] == 0   # T004 is the earlier of the pair: not the duplicate
    p = Q.precision(merged, answers, {})
    assert p["wrong"]["duplicate"] == 1 and p["correct"] == 1   # S006 the duplicate, T004 work the key lacks


def test_your_judge_labels_override_the_judge_in_precision():
    doc = _pdoc(_punit(items=[ITEM1], extras=["T004"], nodes=("T001", "T004")))
    answers = [_ans("k1", same_work="yes"), _ans(_nq("T004"), is_work="no", duplicate_of="none")]
    assert Q.precision(doc, answers, {})["correct"] == 1
    p = Q.precision(doc, answers, {}, {("k1", "same_work"): "no", (_nq("T004"), "is_work"): "yes"})
    assert (p["correct"], p["wrong"]["not_work"], p["unjudged"]) == (1, 0, 1)   # T001 loses its credit; T004 is work after all
    p = Q.precision(doc, answers, {}, {(_nq("T004"), "duplicate_of"): "T001", (_nq("T004"), "is_work"): "yes"})
    assert (p["correct"], p["wrong"]["duplicate"]) == (1, 1)


def test_stability_refuses_records_of_different_code_and_says_when_the_cohort_is_unverified(tmp_path):
    recs = [_qrecord(tmp_path, f"r{n}", ("yes", "yes")) for n in (1, 2)]
    assert Q.stability(recs)["summary"]["cohort"] == "unverified: the records carry no execution manifest"
    for r, sha in zip(recs, ("a", "a")):
        m = json.loads((r / "meta.json").read_text())
        (r / "meta.json").write_text(json.dumps(m | {"execution_manifest_sha256": sha}))
    assert Q.stability(recs)["summary"]["cohort"] == "one version"
    m = json.loads((recs[1] / "meta.json").read_text())
    (recs[1] / "meta.json").write_text(json.dumps(m | {"execution_manifest_sha256": "b"}))
    try:
        Q.stability(recs)
        raise AssertionError("not refused")
    except ValueError as e:
        assert "different code" in str(e)


# ---------- strict outcomes: a judged same-work node of the right kind, one node per key item ----------

def _strict(doc, cat):
    s = doc["summary"]["strict"][cat]
    return {k: s[k] for k in ("n", "accepted", "rejected", "wrong_kind", "unresolved", "missed")}


def test_before_the_judge_every_found_item_is_unresolved_and_nothing_is_certified(tmp_path):
    doc = Q.apply_verdicts(_doc(tmp_path), [])
    assert _strict(doc, "committed") == {"n": 3, "accepted": 0, "rejected": 0, "wrong_kind": 0, "unresolved": 3, "missed": 0}
    assert _strict(doc, "decisions") == {"n": 1, "accepted": 0, "rejected": 0, "wrong_kind": 0, "unresolved": 1, "missed": 0}
    assert doc["summary"]["strict"]["committed"]["certified"] is False


def test_strict_accepts_judged_same_work_in_its_own_place_and_keeps_other_work_and_kinds_out(tmp_path):
    yes = {"same_work": "yes", "title_faithful": "yes", "right_parent": "yes", "deliverable_explicit": "title"}
    doc = Q.apply_verdicts(_doc(tmp_path), [
        {"qid": "ab12cd34-001", "verdicts": yes, "why": WHY},
        {"qid": "ab12cd34-003", "verdicts": yes, "why": WHY},
        {"qid": "ab12cd34-004", "verdicts": yes | {"same_work": "no"}, "why": WHY},
        {"qid": "ab12cd34-002", "verdicts": {"which_node": "D005", "how": "other_kind"}, "why": WHY}])
    assert _strict(doc, "committed") == {"n": 3, "accepted": 2, "rejected": 1, "wrong_kind": 0, "unresolved": 0, "missed": 0}
    assert doc["summary"]["strict"]["committed"]["items"]["ab12cd34-003"] == "accepted"   # T003's title; 004 is other work
    assert _strict(doc, "decisions") == {"n": 1, "accepted": 0, "rejected": 0, "wrong_kind": 1, "unresolved": 0, "missed": 0}
    assert doc["summary"]["strict"]["decisions"]["certified"] is True and doc["summary"]["strict"]["decisions"]["rate"] == 0.0


@pytest.mark.parametrize("answer, outcome", [({"which_node": "T004", "how": "inside"}, "wrong_kind"),   # a task for a risk
                                             ({"which_node": "none"}, "missed")])
def test_a_found_node_of_another_kind_is_wrong_kind_and_none_is_a_miss(tmp_path, answer, outcome):
    doc = Q.apply_verdicts(_doc(tmp_path), [{"qid": "ab12cd34-002", "verdicts": answer, "why": WHY}])
    assert doc["summary"]["strict"]["decisions"]["items"]["ab12cd34-002"] == outcome


def test_part_of_a_broader_node_waits_for_the_explicit_deliverable_and_the_same_is_accepted():
    item = lambda how: {"kind": "task · committed", "produced": {"node": None}, "checks": [], "found_by_judge": {"node": "T9", "how": how}}
    assert Q._strict_outcome(item("inside"), {"T9": "task"})[0] == "unresolved"     # DELIVERABLE-IN-PARENT: explicit or not?
    assert Q._strict_outcome(item("same"), {"T9": "task"})[0] == "unresolved"       # same work, but where is it said?
    assert Q._strict_outcome(item("same") | {"explicit_in": "title"}, {"T9": "task"})[0] == "accepted"


def test_judged_complete_items_need_every_annotated_field_right_and_a_faithful_title(tmp_path):
    yes = {"same_work": "yes", "title_faithful": "yes", "right_parent": "yes", "deliverable_explicit": "title"}
    base = _doc(tmp_path)
    doc = Q.apply_verdicts(base, [{"qid": "ab12cd34-001", "verdicts": yes, "why": WHY}])
    c = doc["summary"]["strict"]["committed"]["complete"]
    assert (c["n"], c["ok"]) == (3, 1) and c["field_errors"] == {}       # T001: Nora, open, as the key says
    doc = Q.apply_verdicts(base, [{"qid": "ab12cd34-001", "verdicts": yes | {"title_faithful": "no"}, "why": WHY}])
    c = doc["summary"]["strict"]["committed"]["complete"]
    assert c["ok"] == 0 and c["incomplete"] == 1 and c["field_errors"] == {"title": 1}
    assert doc["summary"]["strict"]["committed"]["accepted"] == 1          # still covered


def test_a_found_node_with_a_wrong_owner_is_covered_not_complete(tmp_path):
    yes = {"same_work": "yes", "title_faithful": "yes", "right_parent": "yes"}
    doc = Q.apply_verdicts(_doc(tmp_path), [{"qid": "ab12cd34-003", "verdicts": yes, "why": WHY},
                                            {"qid": "ab12cd34-004", "verdicts": yes | {"same_work": "no"}, "why": WHY}])
    i3 = _item(doc, "ab12cd34-003")
    assert i3["field_errors"] == ["owner"]                                 # T003 is Opal's; the key says Ravi


def test_strict_outcomes_count_for_release_only_with_a_validated_judge():
    summary = {"strict": {"committed": {"certified": True}, "decisions": {"certified": True}}}
    Q.mark_strict_validation(summary, [{"measure": "Work found, correctly", "flag": "not validated", "value": 0.9}])
    assert summary["strict"]["committed"]["validated"] is False and summary["strict"]["committed"]["flag"] == "not validated"
    Q.mark_strict_validation(summary, [{"measure": "Work found, correctly", "flag": None, "value": 0.9}])
    assert summary["strict"]["decisions"]["validated"] is True and summary["strict"]["decisions"]["eligible"] is True
    summary["strict"]["decisions"]["certified"] = False
    Q.mark_strict_validation(summary, [{"measure": "Work found, correctly", "flag": None, "value": 0.9}])
    assert summary["strict"]["decisions"]["eligible"] is False


def _judged_record(tmp_path, capsys):
    import key_tools
    project = _project(tmp_path)
    _echo(tmp_path)
    key = key_tools.write_version(tmp_path, KEY, "v1", "2026-03-03", "invented")
    record = tmp_path / "rec"
    record.mkdir()
    (record / "meta.json").write_text(json.dumps({"project": str(project), "key_path": str(key), "key_version": "v1",
                                                  "git_commit": "abc1234def", "model_tier": "med"}))
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    Q.main(["judge-prep", "--record", str(record)])
    batch = json.loads((record / "quality" / "judge" / f"judge_{MEET}.json").read_text())
    good = [{"qid": q["qid"], "verdicts": {n: "none" if n in ("which_node", "duplicate_of") or (n == "deliverable_explicit" and "which_node" in q["ask"]) else "title" if n == "deliverable_explicit" else "yes" for n in q["ask"]},
             "why": "the transcript says exactly this work in those words"} for q in batch]
    model = tmp_path / "model.py"
    model.write_text(f"import sys; print({json.dumps(json.dumps(good))})")
    Q.main(["judge-run", "--record", str(record), "--ai", sys.executable, str(model)])
    capsys.readouterr()
    return record, model


def test_saved_answers_keep_the_fingerprints_of_the_questions_they_answer(tmp_path, capsys):
    record, _ = _judged_record(tmp_path, capsys)
    judge = record / "quality" / "judge"
    fp = json.loads((judge / f"fingerprints_{MEET}.json").read_text())
    assert fp["model"] == "high" and set(fp["fingerprints"]) == {q["qid"] for q in json.loads((judge / f"judge_{MEET}.json").read_text())}
    assert Q.answer_check(record, record / "quality") == {MEET: "ok"}


def test_answers_to_changed_questions_are_set_aside_and_asked_again(tmp_path, capsys):
    record, model = _judged_record(tmp_path, capsys)
    judge = record / "quality" / "judge"
    batch = json.loads((judge / f"judge_{MEET}.json").read_text())
    batch[0]["produced"] = dict(batch[0].get("produced") or {}, title="A different node title")
    (judge / f"judge_{MEET}.json").write_text(json.dumps(batch))
    assert Q.answer_check(record, record / "quality")[MEET].startswith("questions changed")
    Q.main(["build", "--record", str(record), "--echo", str(tmp_path / "echo")])
    doc = json.loads((record / "quality" / "quality.json").read_text())
    assert MEET in doc["summary"]["unverified_answers"] and all(not s["eligible"] for s in doc["summary"]["strict"].values())
    model.write_text(f"import sys; print({json.dumps(json.dumps([{'qid': q['qid'], 'verdicts': {n: 'none' if n in ('which_node', 'duplicate_of') or (n == 'deliverable_explicit' and 'which_node' in q['ask']) else 'title' if n == 'deliverable_explicit' else 'yes' for n in q['ask']}, 'why': 'the transcript says exactly this work in those words'} for q in batch]))})")
    capsys.readouterr()
    Q.main(["judge-run", "--record", str(record), "--ai", sys.executable, str(model)])
    assert json.loads(capsys.readouterr().out)["batches"] == {MEET: "ok"}
    assert len(list(judge.glob(f"stale_answers_{MEET}_*.json"))) == 1        # kept, never deleted
    assert Q.answer_check(record, record / "quality") == {MEET: "ok"}


def test_answers_saved_without_fingerprints_are_unverified(tmp_path, capsys):
    record, _ = _judged_record(tmp_path, capsys)
    (record / "quality" / "judge" / f"fingerprints_{MEET}.json").unlink()
    assert Q.answer_check(record, record / "quality") == {MEET: "no fingerprints: saved before they were kept"}


def _unit(item_kind, node_kind):
    node = {"id": "S9", "kind": node_kind, "title": "Yard pilot", "quote": "q", "parent": "E01", "checklist": ["Draft the SMS reminder requirements"]}
    item = {"id": "k1", "kind": f"{item_kind} · committed", "quote": "q", "ts": "00:01:00", "speaker": "Nora Quill",
            "expected": {"title": "Draft SMS reminder requirements"}, "independent": None, "checks": [],
            "produced": {"node": "S9", "kind": node_kind, "title": "Yard pilot", "parent": "E01 · Pilot", "quote": "q",
                         "checklist": node["checklist"]}}
    return {"units": [{"id": "m1", "nodes": [node], "items": [item], "extras": [], "independent_only": []}]}


def test_a_key_task_matched_to_a_broader_node_asks_whether_its_deliverable_is_explicit():
    q = Q.judge_questions(_unit("task", "story"))["m1"][0]
    assert "deliverable_explicit" in q["ask"] and q["produced"]["checklist"] == ["Draft the SMS reminder requirements"]
    assert "deliverable_explicit" in Q.judge_questions(_unit("task", "task"))["m1"][0]["ask"]   # a vague task too


@pytest.mark.parametrize("explicit, outcome", [("checklist:1", "accepted"), ("none", "rejected"), (None, "unresolved")])
def test_a_broader_node_covers_a_key_item_only_with_the_deliverable_explicit(explicit, outcome):
    i = _unit("task", "story")["units"][0]["items"][0]
    i["checks"] = [{"name": Q.ASK["same_work"], "verdict": "ok", "by": "judge"}]
    if explicit:
        i["explicit_in"] = explicit
    assert Q._strict_outcome(i, {"S9": "story"})[0] == outcome


@pytest.mark.parametrize("explicit, outcome", [("checklist:1", "accepted"), ("none", "rejected"), (None, "unresolved")])
def test_found_inside_a_broader_node_counts_only_with_the_deliverable_explicit(explicit, outcome):
    item = {"kind": "task · committed", "produced": {"node": None}, "found_by_judge": {"node": "T9", "how": "inside"},
            "checks": []} | ({"explicit_in": explicit} if explicit else {})
    assert Q._strict_outcome(item, {"T9": "task"})[0] == outcome


# ---------- DELIVERABLE-IN-PARENT, behaviour: each deliverable needs its own explicit place on the node ----------

def _parent_unit(kind="story", title="Yard pilot", checklist=("Draft the SMS reminder requirements", "Build the club page mock-ups")):
    node = {"id": "N9", "kind": kind, "title": title, "quote": "q", "parent": "E01", "checklist": list(checklist)}
    def item(cid, expected):
        return {"id": cid, "kind": "task · committed", "quote": "q", "ts": "00:01:00", "speaker": "Nora Quill",
                "expected": {"title": expected}, "independent": None, "agreement": "key + taska",
                "checks": [_check_row("Found", "ok", "code"), _check_row(Q.ASK["same_work"], "check", "judge"),
                           _check_row("One node for one key item", "check", "code")],
                "produced": {"node": "N9", "kind": kind, "title": title, "parent": "E01 · Pilot", "quote": "q",
                             "checklist": list(checklist), "matched_by": "quote"}}
    return {"units": [{"id": "m1", "nodes": [node], "items": [item("k1", "Draft SMS reminder requirements"),
                                                            item("k2", "Build mock-ups for the club page")],
                       "extras": [], "containers": [], "independent_only": []}],
            "summary": {"found": [2, 2]}}


def _check_row(name, verdict, by):
    return {"name": name, "verdict": verdict, "by": by, "why": ""}


def _explicit_ans(cid, explicit, same="yes", text=None):
    v = {"same_work": same, "title_faithful": "yes", "right_parent": "yes", "deliverable_explicit": explicit}
    return {"qid": cid, "verdicts": v | ({"explicit_text": text} if text else {}),
            "why": "the transcript names this deliverable on the yard pilot in those words"}


def _outcomes(doc):
    return doc["summary"]["strict"]["committed"]["items"]


def test_two_distinct_deliverables_on_one_parent_are_both_covered():
    doc = Q.apply_verdicts(_parent_unit(), [_explicit_ans("k1", "checklist:1"), _explicit_ans("k2", "checklist:2")])
    assert _outcomes(doc) == {"k1": "accepted", "k2": "accepted"}


def test_the_same_content_never_covers_two_different_actions_whatever_the_key_order():
    doc = Q.apply_verdicts(_parent_unit(), [_explicit_ans("k1", "checklist:1"), _explicit_ans("k2", "checklist:1")])
    assert _outcomes(doc) == {"k1": "unresolved", "k2": "unresolved"}          # neither wins by coming first


def test_one_entry_stating_two_distinct_actions_covers_both():
    unit = _parent_unit(checklist=("Draft the SMS reminder requirements and build the club page mock-ups",))
    doc = Q.apply_verdicts(unit, [_explicit_ans("k1", "checklist:1", text="Draft the SMS reminder requirements"),
                                  _explicit_ans("k2", "checklist:1", text="build the club page mock-ups")])
    assert _outcomes(doc) == {"k1": "accepted", "k2": "accepted"}


def test_two_entries_repeating_one_action_cannot_cover_two_different_key_items():
    unit = _parent_unit(checklist=("Draft the SMS reminder requirements", "Draft the SMS reminder requirements"))
    doc = Q.apply_verdicts(unit, [_explicit_ans("k1", "checklist:1", text="Draft the SMS reminder requirements"),
                                  _explicit_ans("k2", "checklist:2", text="Draft the SMS reminder requirements")])
    assert _outcomes(doc) == {"k1": "unresolved", "k2": "unresolved"}


def test_reversing_the_key_order_changes_no_verdict():
    answers = [_explicit_ans("k1", "checklist:1"), _explicit_ans("k2", "checklist:1"), ]
    unit = _parent_unit()
    flipped = _parent_unit()
    flipped["units"][0]["items"].reverse()
    assert _outcomes(Q.apply_verdicts(unit, answers)) == _outcomes(Q.apply_verdicts(flipped, answers))
    ok = [_explicit_ans("k1", "checklist:1"), _explicit_ans("k2", "checklist:2")]
    assert _outcomes(Q.apply_verdicts(unit, ok)) == _outcomes(Q.apply_verdicts(flipped, ok)) == {"k1": "accepted", "k2": "accepted"}


def test_explicit_text_must_be_found_in_the_place_it_names():
    batch = Q.judge_questions(_parent_unit())["m1"]
    assert Q.check_verdicts([_explicit_ans("k1", "checklist:1", text="Draft the SMS reminder requirements"),
                             _explicit_ans("k2", "title")], batch) == []
    bad = Q.check_verdicts([_explicit_ans("k1", "checklist:1", text="Paint the warehouse"), _explicit_ans("k2", "title")], batch)
    assert any("explicit_text" in p for p in bad)


def test_a_parent_title_alone_covers_nothing_and_an_unanswered_entry_waits():
    doc = Q.apply_verdicts(_parent_unit(), [_explicit_ans("k1", "none")])
    assert _outcomes(doc) == {"k1": "rejected", "k2": "unresolved"}


def test_a_vague_same_kind_task_cannot_pass_on_its_quote_alone():
    vague = _parent_unit(kind="task", title="Pilot work", checklist=())
    assert all("deliverable_explicit" in q["ask"] for q in Q.judge_questions(vague)["m1"])
    doc = Q.apply_verdicts(vague, [_explicit_ans("k1", "none")])
    assert _outcomes(doc)["k1"] == "rejected"                 # same work by the judge, but the deliverable is nowhere
    noted = _parent_unit(kind="task", title="Pilot work", checklist=("Draft the SMS reminder requirements",))
    assert _outcomes(Q.apply_verdicts(noted, [_explicit_ans("k1", "checklist:1")]))["k1"] == "accepted"
    assert _outcomes(Q.apply_verdicts(noted, [_explicit_ans("k1", "title")]))["k1"] == "accepted"


def test_the_explicit_answer_must_name_the_title_an_entry_that_exists_or_none():
    batch = Q.judge_questions(_parent_unit())["m1"]
    ok = [_explicit_ans("k1", "checklist:2"), _explicit_ans("k2", "title")]
    assert Q.check_verdicts(ok, batch) == []
    bad = Q.check_verdicts([_explicit_ans("k1", "checklist:3"), _explicit_ans("k2", "yes")], batch)
    assert any("checklist:3" in p for p in bad) and any("'yes'" in p for p in bad)


# ---------- the approved rules end to end: what an extraction that follows them writes, scored and asked ----------

def _rule_project(tmp_path):
    project = _project(tmp_path)
    cfg = C.load(project)
    story = {"op": "create", "kind": "story", "parent": "E01", "title": "Warehouse onboarding ready", "ac": ["Checklist drafted"],
             "quote": S1, "ref": "s"}
    r = L.apply(cfg.store, cfg, MEET, [story])
    sid = next(e["id"] for e in S.events(cfg.store) if e.get("type") == "create" and e.get("kind") == "story")
    r = L.apply(cfg.store, cfg, MEET, [
        {"op": "revise", "node": sid, "ac_add": ["Safety poster wording updated before the next site visit"], "quote": S6},   # DELIVERABLE-IN-PARENT
        {"op": "note", "node": "T004", "note": "Reminder: chase Opal for the scanner serial numbers", "quote": S4}])         # REMINDER-ATTACH
    assert r["rejected"] == [], r
    return project, sid


def test_a_deliverable_kept_as_a_checklist_entry_is_matched_to_its_parent_and_asked_for_its_place(tmp_path):
    project, sid = _rule_project(tmp_path)
    key = [{"cid": "k6", "evidence": MEET, "quote": S6, "kind": "task", "commitment": "committed", "title": "Update the safety poster wording",
            "owner": None, "due": None, "status": None}]
    doc = Q.build(project, key, {MEET: None}, {"record": "r", "code": "c", "tier": "med"})
    item = doc["units"][0]["items"][0]
    assert item["produced"]["node"] == sid and item["produced"]["kind"] == "story"
    assert "Safety poster wording updated before the next site visit" in item["produced"]["checklist"]
    q = Q.judge_questions(doc)[MEET][0]
    assert "deliverable_explicit" in q["ask"]
    n = q["produced"]["checklist"].index("Safety poster wording updated before the next site visit") + 1
    merged = Q.apply_verdicts(doc, [{"qid": "k6", "verdicts": {"same_work": "yes", "title_faithful": "yes", "right_parent": "yes",
                                                               "deliverable_explicit": f"checklist:{n}"}, "why": WHY}])
    assert merged["summary"]["strict"]["committed"]["items"]["k6"] == "accepted"
    vague = Q.apply_verdicts(doc, [{"qid": "k6", "verdicts": {"same_work": "yes", "title_faithful": "yes", "right_parent": "yes",
                                                              "deliverable_explicit": "none"}, "why": WHY}])
    assert vague["summary"]["strict"]["committed"]["items"]["k6"] == "rejected"     # the parent's title alone


def test_a_reminder_kept_as_a_note_on_the_chased_task_is_shown_to_the_judge(tmp_path):
    project, _ = _rule_project(tmp_path)
    doc = Q.build(project, KEY, {MEET: None}, {"record": "r", "code": "c", "tier": "med"})
    t4 = next(n for n in doc["units"][0]["nodes"] if n["id"] == "T004")
    assert "Reminder: chase Opal for the scanner serial numbers" in t4["checklist"]
