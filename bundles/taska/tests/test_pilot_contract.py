"""evals/pilot.py artifact contract v2: one candidate id, explicit dispositions and verdicts, multi-target findings,
assessment provenance, unknown telemetry, and a smoke run of every step on synthetic artifacts. Invented data only
(Harbor Logistics). This proves artifact handling, never model compliance or semantic accuracy."""
import json
import sys

import pytest

from conftest import TASKA_ROOT

sys.path.insert(0, str(TASKA_ROOT / "evals"))
import pilot as P  # noqa: E402

EV = "2026-03-02_meetings_ab12cd34"
Q1 = "Nora will write the onboarding checklist and send it to Opal by Friday morning"
Q2 = "Ravi needs to book the warehouse site visit and confirm the date with Opal"
Q3 = "The team decided not to proceed with the legacy system migration project this quarter"
Q4 = "Opal says the crane permits might not arrive before the berth schedule is due"
Q5 = "We are going to put together the yard capacity summary for the client next week"
Q6 = "So Nora will send the onboarding checklist to Opal on Friday morning as agreed"
QX = "It was a really good session and thanks everyone for the time this afternoon"


def _label(quote, **kw):
    base = {"quote": quote, "ts": "00:01:00", "speaker": "Nora Quill", "title": "A title", "kind": "task", "commitment": "committed",
            "owner": "Nora Quill", "due": None, "status": "open", "client_owed": False, "note": "a note of eight words or more here"}
    return base | kw


def _labels(*items):
    return [{"evidence": EV, "items": list(items)}]


def _inputs(tmp_path):
    lines = [("00:01:00", "Nora Quill", Q1), ("00:02:00", "Ravi Tamsin", Q2), ("00:03:00", "Opal Brennick", Q3),
             ("00:04:00", "Opal Brennick", Q4), ("00:05:00", "Ravi Tamsin", Q5), ("00:06:00", "Ravi Tamsin", Q6),
             ("00:07:00", "Opal Brennick", QX)]
    (tmp_path / f"{EV}.md").write_text("# Yard sync\n" + "".join(f"[{t}] {s}: {q}\n" for t, s, q in lines))
    return tmp_path


# ---------- one candidate identifier; older forms map with a record of every mapping ----------

def test_the_legacy_candidate_field_maps_to_id_and_every_record_is_kept():
    legacy = {"notes": ["x"], "resolutions": [{"candidate": "c001", "decision": "include"},
                                              {"candidate": "c002", "decision": "merged", "duplicate_of": "c001"},
                                              {"candidate": "c003", "decision": "exclude", "rule": "logistics"}]}
    out = P.normalize_resolutions(legacy)
    assert [r["id"] for r in out["resolutions"]] == ["c001", "c002", "c003"]
    assert all("candidate" not in r for r in out["resolutions"])
    assert out["counts"] == {"in": 3, "out": 3} and out["problems"] == []
    assert [r["decision"] for r in out["resolutions"]] == ["include", "merge", "exclude"]
    assert out["mappings"] == [{"id": "c001", "mappings": ["candidate->id"]}, {"id": "c002", "mappings": ["candidate->id", "merged->merge"]},
                               {"id": "c003", "mappings": ["candidate->id"]}]
    assert out["resolutions"][2]["rule"] == "logistics"          # every other field is carried unchanged


def test_a_duplicate_conflicting_or_missing_id_is_refused_not_dropped_or_overwritten():
    out = P.normalize_resolutions([{"id": "c001", "decision": "include"}, {"candidate": "c001", "decision": "exclude"},
                                   {"id": "c002", "candidate": "c003", "decision": "include"}, {"decision": "include"},
                                   {"id": "c004", "decision": "maybe"}])
    assert out["problems"] == ["c001: two resolutions", "record 2: id c002 and candidate c003 differ", "record 3: no candidate id",
                               "c004: decision 'maybe'"]
    assert out["resolutions"][0]["decision"] == "include" and out["counts"] == {"in": 5, "out": 1}   # refused, never kept half-read


def test_the_round_two_encoding_of_a_merge_maps_to_merge():
    out = P.normalize_resolutions([{"id": "c001", "decision": "include"}, {"id": "c002", "decision": "exclude", "duplicate_of": "c001"}])
    assert out["resolutions"][1]["decision"] == "merge" and out["mappings"] == [{"id": "c002", "mappings": ["exclude+duplicate_of->merge"]}]


# ---------- dispositions: a merge names an existing, included destination ----------

def test_a_merge_into_a_nonexistent_or_removed_target_is_rejected():
    union_doc = {"candidates": [{"id": f"c00{n}"} for n in range(1, 6)]}
    res = [{"id": "c001", "decision": "include"}, {"id": "c002", "decision": "merge", "duplicate_of": "c009"},
           {"id": "c003", "decision": "merge", "duplicate_of": "c004"}, {"id": "c004", "decision": "exclude"},
           {"id": "c005", "decision": "merge"}]
    d = P.dispositions(union_doc, res)
    assert d["by_candidate"] == {"c001": "included", "c002": "merged", "c003": "merged", "c004": "excluded", "c005": "merged"}
    assert d["problems"] == ["c002: merged into c009, which is not a candidate", "c003: merged into c004, which is not included",
                             "c005: a merge names no destination"]


def test_a_valid_merge_is_followed_by_comparison():
    key = [{"cid": "k1", "evidence": EV, "quote": Q6, "kind": "task", "commitment": "committed", "title": "Send checklist"}]
    kept = _label(Q1, candidate="c001")
    union_doc = {"candidates": [{"id": "c001", "labels": [_label(Q1) | {"evidence": EV}]}, {"id": "c002", "labels": [_label(Q6) | {"evidence": EV}]}]}
    res = [{"candidate": "c001", "decision": "include"}, {"candidate": "c002", "decision": "merged", "duplicate_of": "c001"}]
    doc = P.compare(_labels(kept), key, res, union_doc)
    assert doc["covered"] == 1 and doc["rows"][0]["via_duplicate"] == "c002"


# ---------- review verdicts say the action; an ambiguous older verdict is never translated globally ----------

def test_uphold_removal_produces_zero_recoveries():
    doc = {"candidates": [{"id": "c001", "labels": [_label(Q1) | {"evidence": EV}]}, {"id": "c002", "labels": [_label(Q2) | {"evidence": EV}]}],
           "found_by": {"c001": ["A", "B"], "c002": ["A"]}}
    res = [{"id": "c001", "decision": "include"}, {"id": "c002", "decision": "exclude", "rule": "logistics"}]
    led = P.stage_ledger(doc, res, [{"candidate": "c002", "by": "removal_reviewer", "verdict": "uphold_removal"}])
    assert led["final"] == ["c001"] and led["by_role"]["removal_reviewer"] == {"uphold_removal": 1, "credited": 0}
    assert {r["id"]: r["path"] for r in led["candidates"]}["c002"] == "found by readers, excluded"


def test_an_older_restore_confirmed_is_unresolved_unless_its_own_explanation_settles_it():
    old = [{"candidate": "c002", "action": "restore", "by": "removal_reviewer", "verdict": "confirmed", "evidence": "same roadmap; nothing lost"},
           {"candidate": "c003", "action": "restore", "by": "removal_reviewer", "verdict": "confirmed", "evidence": "a concrete risk was dropped"},
           {"candidate": "c004", "action": "restore", "by": "removal_reviewer", "verdict": "rejected"},
           {"candidate": "c005", "action": "remove", "by": "skeptic", "verdict": "confirmed"},
           {"candidate": "c006", "action": "remove", "by": "skeptic", "verdict": "rejected"},
           {"candidate": "c007", "action": "fix", "by": "skeptic", "verdict": "confirmed", "finding": "S1"}]
    out = P.normalize_reviews(old, {("removal_reviewer", "c002"): {"verdict": "uphold_removal", "basis": "its evidence says nothing is lost"}})
    v = {r["candidate"]: r["verdict"] for r in out["reviews"]}
    assert v == {"c002": "uphold_removal", "c003": "unresolved", "c004": "uphold_removal", "c005": "remove_label",
                 "c006": "keep_label", "c007": "fix_fields"}
    c3 = next(r for r in out["reviews"] if r["candidate"] == "c003")
    assert c3["as_written"] == {"action": "restore", "verdict": "confirmed"} and "ambiguous" in c3["why"]
    assert next(r for r in out["reviews"] if r["candidate"] == "c002")["basis"] == "its evidence says nothing is lost"


def test_an_unknown_review_verdict_is_refused():
    with pytest.raises(ValueError, match="c001: review verdict 'approve'"):
        P.normalize_reviews([{"candidate": "c001", "by": "skeptic", "verdict": "approve"}])


# ---------- findings: one finding, several targets ----------

def test_one_multi_target_finding_remains_one_finding(tmp_path):
    union_doc = {"candidates": [{"id": f"c00{n}", "labels": []} for n in range(1, 4)]}
    res = [{"id": "c001", "decision": "include"}, {"id": "c002", "decision": "include"}, {"id": "c003", "decision": "include"}]
    findings = [{"id": "S1", "target": "c001,c002, c003", "type": "owner_unsupported", "quote": Q1},
                {"id": "S2", "target": "c002 (absorbs c009)", "type": "due"},
                {"id": "S3", "target": "c001..c066 (one meeting)", "type": "policy_gap"}]
    out = P.check_claims(findings, union_doc, res, _labels(_label(Q1), _label(Q2), _label(Q3)), _inputs(tmp_path))
    assert [f["id"] for f in out["findings"]] == ["S1", "S2", "S3"]
    s1, s2, s3 = out["findings"]
    assert [t["id"] for t in s1["targets"]] == ["c001", "c002", "c003"] and s1["factual"] == "holds"
    assert s2["factual"] == "partly" and "c009 is not a candidate" in s2["evidence"]
    assert s3["targets"] == [] and s3["scope"] == "c001..c066 (one meeting)"
    assert out["finding_counts"] == {"findings": 3, "affected_labels": 3, "with_scope_only": 1}


# ---------- quality: positive claims need an affirmative assessment ----------

def _rec(label, **dims):
    return {"label": label} | dims


SK = {"role": "skeptic", "stage": "review", "basis": "stated in the turn"}
ADJ = {"role": "adjudicator", "stage": "adjudication", "basis": "included"}
RET = {"role": "retrospective", "stage": "retrospective", "basis": "'we' is not an owner"}


def _by(entry, value):
    return dict(entry, value=value)


def test_no_reviewer_comment_does_not_become_compliant_or_supported():
    q = P.label_quality([_rec("c012")], labels=["c012"])
    for v in q["views"].values():
        assert v["work"]["not_assessed"] == 1 and v["work"]["supported"] == 0 and v["work"]["coverage"] == {"n": 0, "of": 1}
        assert v["representation"]["compliant"] == 0 and v["unassessed_labels"] == ["c012"]
        assert v["work"]["unsupported_rate"] == "unknown (0 assessed)"


def test_unknown_field_correctness_does_not_become_correct():
    q = P.label_quality([_rec("c1", work="supported", provenance={"work": [_by(SK, "supported")]})], labels=["c1"])
    v = q["views"]["independent"]
    assert v["fields"]["owner"]["correct"] == 0 and v["fields"]["owner"]["not_assessed"] == 1
    assert v["fields"]["owner"]["incorrect_rate"] == "unknown (0 assessed)" and v["unassessed_labels"] == []
    assert v["complete_items"]["fully_assessed"] == 0


def test_a_positive_value_without_provenance_is_refused():
    with pytest.raises(ValueError, match="c1: owner 'correct' has no assessment provenance"):
        P.label_quality([_rec("c1", fields={"owner": "correct"})], labels=["c1"])


def test_a_not_applicable_ruling_needs_its_evidence():
    with pytest.raises(ValueError, match="c1: due 'not_applicable' has no assessment provenance"):
        P.label_quality([_rec("c1", fields={"due": "not_applicable"})], labels=["c1"])
    with pytest.raises(ValueError, match="judgment without role, stage and basis"):
        P.label_quality([_rec("c1", provenance={"due": [{"role": "skeptic", "stage": "review", "value": "not_applicable"}]})], labels=["c1"])


def test_adjudicator_acceptance_is_kept_apart_from_independent_review():
    q = P.label_quality([_rec("c1", provenance={"work": [_by(ADJ, "supported")]}),
                         _rec("c2", provenance={"work": [_by(ADJ, "supported"), _by(SK, "supported")]}),
                         _rec("c3", provenance={"work": [_by(ADJ, "supported"), _by(SK, "supported")], "owner": [_by(RET, "incorrect")]})],
                        labels=["c1", "c2", "c3"])
    init, ind, ret = (q["views"][k] for k in ("initial", "independent", "retrospective"))
    assert init["work"]["supported"] == 3 and ind["work"]["supported"] == 2 and ret["work"]["not_assessed"] == 3
    assert ind["fields"]["owner"]["not_assessed"] == 3 and ret["fields"]["owner"]["incorrect_labels"] == ["c3"]
    assert ret["fields"]["owner"]["incorrect_rate"] == {"n": 1, "of": 1}
    assert init["judgments_by_role"] == {"adjudicator": 3} and ind["judgments_by_role"] == {"skeptic": 2}
    assert ret["judgments_by_role"] == {"retrospective": 1}


def test_a_reviewer_judging_the_date_says_nothing_about_the_work_claim():
    q = P.label_quality([_rec("c1", provenance={"due": [_by(SK, "correct")], "work": [_by(ADJ, "supported")]})], labels=["c1"])
    ind = q["views"]["independent"]
    assert ind["work"]["not_assessed"] == 1 and ind["fields"]["due"]["correct"] == 1 and ind["unassessed_labels"] == []


def _full(label, owner="correct", by=SK, **kw):
    prov = {"work": [_by(by, "supported")], "representation": [_by(by, "compliant")]} | \
           {f: [_by(by, owner if f == "owner" else "not_applicable" if f in ("due", "client_owed") else "correct")] for f in P.QUALITY_FIELDS}
    return _rec(label, provenance=prov, **kw)


def test_the_complete_item_denominator_counts_only_fully_assessed_labels():
    q = P.label_quality([_full("c1"), _full("c2", owner="incorrect"), _full("c3", by=ADJ)], labels=["c1", "c2", "c3"])
    ci = q["views"]["independent"]["complete_items"]
    assert (ci["fully_correct"], ci["fully_assessed"], ci["not_fully_assessed"], ci["eligible"]) == (1, 2, 1, 3)
    assert ci["fully_correct_labels"] == ["c1"] and ci["fully_correct_rate"] == {"n": 1, "of": 2}
    assert q["views"]["independent"]["fields"]["owner"]["incorrect_labels"] == ["c2"]   # a fully reviewed wrong owner stays in


def test_a_missing_required_field_leaves_the_item_not_fully_assessed_but_its_known_error_visible():
    rec = _full("c1", owner="incorrect")
    del rec["provenance"]["status"]
    q = P.label_quality([rec], labels=["c1"])
    v = q["views"]["independent"]
    assert v["complete_items"]["fully_assessed"] == 0 and v["fields"]["status"]["not_assessed"] == 1
    assert v["fields"]["owner"]["incorrect_labels"] == ["c1"]


def test_a_policy_blocked_item_is_not_fully_assessed_and_stays_listed():
    q = P.label_quality([_full("c1", policy="policy_unresolved")], labels=["c1"])
    ci = q["views"]["independent"]["complete_items"]
    assert ci["fully_assessed"] == 0 and ci["policy_blocked"] == ["c1"] and q["policy_unresolved"] == ["c1"]


def test_conflicting_verdicts_stay_unresolved_until_a_resolution_links_them():
    q = P.label_quality([_rec("c1", provenance={"owner": [_by(SK, "correct")]}),
                         _rec("c1", provenance={"owner": [_by(dict(SK, role="packet_reviewer"), "incorrect")]})], labels=["c1"])
    v = q["views"]["independent"]
    assert v["fields"]["owner"]["unresolved"] == 1 and v["conflicts"] == {"c1": {"owner": {"values": ["correct", "incorrect"], "resolved_by": None}}}
    assert q["merged_records"] == ["c1"] and v["eligible"] == 1
    settle = dict(SK, role="packet_reviewer", resolution="the turn names Opal; the earlier 'correct' read the speaker tag")
    q = P.label_quality([_rec("c1", provenance={"owner": [_by(SK, "correct"), _by(dict(SK, role="packet_reviewer"), "incorrect"),
                                                          _by(settle, "incorrect")]})], labels=["c1"])
    assert q["views"]["independent"]["fields"]["owner"]["incorrect"] == 1
    assert q["views"]["independent"]["conflicts"]["c1"]["owner"]["resolved_by"].startswith("the turn names Opal")


def test_an_assessment_of_a_label_outside_the_eligible_set_is_listed_not_counted():
    q = P.label_quality([_full("c9")], labels=["c1"])
    assert q["outside_labels"] == ["c9"] and q["eligible"] == 1 and q["views"]["independent"]["unassessed_labels"] == ["c1"]


def test_an_archived_v2_assessment_loads_with_each_entry_asserting_the_recorded_value():
    fixture = json.loads((TASKA_ROOT / "evals" / "fixtures" / "assessments.v2.json").read_text())
    q = P.label_quality(fixture, labels=[a["label"] for a in fixture])
    assert q["inherited_values"] == sum(len(es) for a in fixture for es in a["provenance"].values())
    ind, init = q["views"]["independent"], q["views"]["initial"]
    assert ind["work"]["supported"] == 1 and ind["work"]["unresolved"] == 1
    assert init["work"]["supported"] == 2 and init["work"]["unresolved"] == 1   # a v2 entry cannot say the adjudicator's own value
    assert ind["fields"]["owner"]["incorrect_labels"] == [] and q["views"]["retrospective"]["fields"]["owner"]["incorrect_labels"] == ["c003"]


# ---------- evidence-format failures are kept, with a bounded suggestion that is never applied ----------

def test_a_near_verbatim_quote_stays_invalid_with_a_suggestion_never_applied(tmp_path):
    q = "Ravi needs to book the warehouse site visit and confirm date with Opal"      # dropped "the"
    res = P.validate(_labels(_label(q), _label("Ravi wants to buy the berth crane and also repaint the office wall")), _inputs(tmp_path))
    assert res[0]["problems"] == ["quote not verbatim in the transcript"] and res[0]["quote"] == q
    assert res[0]["near_verbatim"] == {"span": "Ravi needs to book the warehouse site visit and confirm the date with Opal", "edits": 1,
                                       "applied": False}
    assert "near_verbatim" not in res[1]


# ---------- telemetry: unknown stays unknown ----------

def test_missing_telemetry_values_are_named_unknown(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text(json.dumps({"type": "assistant", "message": {"model": "m", "usage": {}, "content": []}}) + "\n")
    t = P.role_telemetry({"reader_B": f, "skeptic": tmp_path / "none.jsonl"}, {})
    assert t["roles"]["reader_B"]["unknown"] == ["elapsed_s", "meeting_words"]
    assert t["roles"]["skeptic"] == {"telemetry": "unknown: no transcript"} and t["wall_clock_s"] is None


# ---------- smoke: every step on one synthetic round ----------

def test_contract_smoke_run_on_synthetic_artifacts(tmp_path):
    inputs = _inputs(tmp_path)
    A = _labels(_label(Q1), _label(Q2), _label(QX, kind="task"), _label(Q5, owner="Ravi Tamsin"))
    B = _labels(_label(Q1), _label(Q6), _label(Q3, kind="decision", owner=None))
    O = _labels(_label(Q4, kind="risk", commitment="proposed", owner=None))
    assert all(not r["problems"] for r in P.validate(A, inputs) + P.validate(B, inputs) + P.validate(O, inputs))
    doc = P.add_omissions(P.union(A, B, seed=7), O)
    ids = {c["labels"][0]["quote"]: c["id"] for c in doc["candidates"]}
    # adjudicator output in the older shape: a wrapper, `candidate` for `id`, `merged`
    legacy = {"resolutions": [{"candidate": ids[Q1], "decision": "include"},                       # direct inclusion
                              {"candidate": ids[Q6], "decision": "merged", "duplicate_of": ids[Q1]},  # merge
                              {"candidate": ids[QX], "decision": "exclude", "rule": "courtesy"},     # upheld exclusion
                              {"candidate": ids[Q2], "decision": "exclude", "rule": "logistics"},    # restored later
                              {"candidate": ids[Q3], "decision": "include"},
                              {"candidate": ids[Q4], "decision": "include"},
                              {"candidate": ids[Q5], "decision": "include"}]}
    norm = P.normalize_resolutions(legacy)
    assert norm["problems"] == [] and norm["counts"] == {"in": 7, "out": 7}
    disp = P.dispositions(doc, norm["resolutions"])
    assert disp["problems"] == [] and sorted(disp["by_candidate"].values()).count("included") == 4
    reviews = P.normalize_reviews([{"candidate": ids[QX], "by": "removal_reviewer", "verdict": "uphold_removal"},
                                   {"candidate": ids[Q2], "by": "removal_reviewer", "verdict": "restore_candidate"},
                                   {"candidate": ids[Q6], "by": "removal_reviewer", "verdict": "uphold_removal"}])["reviews"]
    findings = [{"id": "S1", "targets": [ids[Q5], ids[Q3]], "type": "owner_unsupported", "quote": Q5}]
    final_labels = _labels(*[_label(q, candidate=ids[q]) for q in (Q1, Q3, Q4, Q5, Q2)])
    claims = P.check_claims(findings, doc, norm["resolutions"], final_labels, inputs)
    assert claims["finding_counts"] == {"findings": 1, "affected_labels": 2, "with_scope_only": 0}
    key = [{"cid": "k1", "evidence": EV, "quote": Q6}, {"cid": "k2", "evidence": EV, "quote": Q2},
           {"cid": "k3", "evidence": EV, "quote": "Opal will repaint the north gate signs before the inspection next month"}]
    led = P.stage_ledger(doc, norm["resolutions"], reviews, key)
    assert led["final"] == sorted([ids[Q1], ids[Q2], ids[Q3], ids[Q4], ids[Q5]])
    assert led["by_role"]["removal_reviewer"] == {"uphold_removal": 2, "restore_candidate": 1, "credited": 1}
    assert led["reference"]["final_holds"] == ["k1", "k2"] and led["reference"]["final_misses"] == ["k3"]
    cmp_ = P.compare(final_labels, key, norm["resolutions"], doc)
    assert cmp_["covered"] == 2 and cmp_["rows"][0]["via_duplicate"] == ids[Q6]
    q = P.label_quality([
        _rec(ids[Q1], provenance={"work": [_by(ADJ, "supported"), _by(SK, "supported")], "owner": [_by(SK, "correct")]}),
        _rec(ids[Q5], provenance={"work": [_by(ADJ, "supported"), _by(SK, "supported")], "owner": [_by(SK, "incorrect")]}),   # unsupported owner
        _rec(ids[Q4], policy="policy_unresolved", source="unreliable_speaker",
             provenance={"work": [_by(dict(SK, basis="speaker attribution collapsed"), "unresolved")]}),
        _rec(ids[Q3], provenance={"work": [_by(ADJ, "supported")]})],
        labels=led["final"])
    ind = q["views"]["independent"]
    assert ind["unassessed_labels"] == sorted([ids[Q2], ids[Q3]])                 # the unassessed label; adjudicator acceptance alone is not review
    assert q["views"]["initial"]["unassessed_labels"] == sorted([ids[Q2], ids[Q4]])
    assert ind["fields"]["owner"]["incorrect_labels"] == [ids[Q5]] and ind["work"]["supported"] == 2 and ind["work"]["unresolved"] == 1
    assert q["source_limited"] == [ids[Q4]] and q["policy_unresolved"] == [ids[Q4]]
    import pages as G
    views = {EV: G.write_views(inputs / f"{EV}.md", tmp_path / "views")}
    packets = {pk["candidate"]: pk for pk in P.removal_packets(doc, norm["resolutions"], final_labels, views,
                                                                {EV: (inputs / f"{EV}.md").read_text()}, {ids[Q6]: ["Friday morning"]})}
    assert set(packets) == {ids[Q6], ids[QX], ids[Q2]} and packets[ids[Q6]]["missing"] == ["rationale"]   # the merge gave no rule
    up = P.certify(packets[ids[Q6]], {"verdict": "uphold_removal", "details": {"Friday morning": "retained"}}, {(EV, 1)}, final_labels)
    assert up["why"] == ["packet missing rationale"]
    assert P.certify(packets[ids[QX]], {"verdict": "uphold_removal"}, {(EV, 1)}, final_labels)["certified"]
    assert not P.certify(packets[ids[QX]], {"verdict": "uphold_removal"}, set(), final_labels)["certified"]
    tel = P.role_telemetry({"adjudicator": tmp_path / "missing.jsonl"}, {})        # the missing telemetry value
    assert tel["roles"]["adjudicator"] == {"telemetry": "unknown: no transcript"}


def test_an_agent_script_crash_is_an_agent_error_not_unclassified():
    class Call:
        def __init__(self, out):
            self.name, self.input, self.is_error, self.output = "Bash", {"command": out}, True, out
    calls = [Call("Exit code 1\nTraceback (most recent call last):\n  File \"<string>\", line 6\nKeyError: 'differ_on'"),
             Call("Exit code 2\nawk: syntax error at source line 1"), Call("File does not exist.")]
    assert P.classify_calls(calls) == {"avoidable_agent_error": 2, "avoidable_repetition": 0, "recovery_overhead": 0,
                                       "unclassified": 1, "succeeded": 0, "failed": 3}


# ---------- rule propagation: the delivered prompt carries the production block ----------

def test_rule_delivery_reports_each_role_and_block_and_fingerprints(tmp_path):
    src = (TASKA_ROOT / "skills" / "run" / "EXTRACT.md").read_text(encoding="utf-8")
    reader = (TASKA_ROOT / "evals" / "READER.md").read_text(encoding="utf-8")
    (tmp_path / "READER.md").write_text(reader)
    (tmp_path / "OLD.md").write_text(reader.split("<!-- READER-BLOCK:owner:START -->")[0])
    stale = reader.replace("identifies nobody", "identifies someone")
    (tmp_path / "STALE.md").write_text(stale)
    out = P.rule_delivery(src, {"reader_A": [tmp_path / "READER.md"], "reader_B": [tmp_path / "OLD.md"],
                                "skeptic": [tmp_path / "STALE.md"]}, ("owner", "commitment"))
    assert out["roles"] == {"reader_A": {"owner": "same", "commitment": "same"}, "reader_B": {"owner": "missing", "commitment": "same"},
                            "skeptic": {"owner": "differs", "commitment": "same"}}
    assert out["ok"] is False and len(out["fingerprints"]["owner"]) == 64
    with pytest.raises(ValueError, match="source has no block"):
        P.rule_delivery(src, {}, ("nonexistent",))


def test_the_owner_probe_scores_each_case_and_keeps_an_unlabelled_case_apart():
    probe = TASKA_ROOT / "evals" / "fixtures" / "owner-probe"
    expected = json.loads((probe / "expected.json").read_text())
    for c in expected["cases"]:
        assert P.norm(c["quote"]) in P.norm((probe / f"{c['evidence']}.md").read_text())
    by = {c["case"]: c for c in expected["cases"]}
    lab = lambda case, owner, commitment="committed": dict(_label(by[case]["quote"]), owner=owner, commitment=commitment, evidence=by[case]["evidence"])
    labels = [{"evidence": i["evidence"], "items": [i]} for i in (lab("we", None), lab("named_confirmed", "Anna"), lab("i_reliable", "Nora Quill"))]
    out = P.owner_probe(labels, expected)
    assert [r["result"] for r in out["cases"]] == ["followed", "followed", "not_followed", "not_labelled"]
    assert out["counts"] == {"followed": 2, "not_followed": 1, "not_labelled": 1}


# ---------- removal packets: complete evidence before an uphold is certified ----------

def _packet_case(tmp_path):
    import pages as G
    inputs = _inputs(tmp_path)
    views = {EV: G.write_views(inputs / f"{EV}.md", tmp_path / "views")}
    sources = {EV: (inputs / f"{EV}.md").read_text()}
    union_doc = {"candidates": [{"id": c, "labels": [_label(q) | {"evidence": EV}]} for c, q in
                                (("c001", Q1), ("c002", Q6), ("c003", QX), ("c004", Q2))]}
    res = [{"id": "c001", "decision": "include"}, {"id": "c002", "decision": "merge", "duplicate_of": "c001", "rule": "same send",
                                                    "evidence": Q6},
           {"id": "c003", "decision": "exclude", "rule": "courtesy", "evidence": QX}, {"id": "c004", "decision": "merge", "duplicate_of": "c009"}]
    reviewed = _labels(_label(Q1, candidate="c001", note="Nora sends the onboarding checklist to Opal on Friday morning"))
    packets = P.removal_packets(union_doc, res, reviewed, views, sources, {"c002": ["Friday morning"]})
    return {p["candidate"]: p for p in packets}, reviewed


def test_a_packet_carries_candidate_rationale_located_sources_and_destination(tmp_path):
    packets, reviewed = _packet_case(tmp_path)
    p2, p3, p4 = packets["c002"], packets["c003"], packets["c004"]
    assert p2["complete"] and p2["destination"]["id"] == "c001" and p2["destination"]["content"]["candidate"] == "c001"
    assert p2["sources"][0]["located"]["pages"] == [1] and p2["pages"] == [(EV, 1)] and p2["required_details"] == ["Friday morning"]
    assert p2["rationale"] == {"rule": "same send", "evidence": Q6}
    assert p3["destination"] == "not_applicable" and p3["complete"]
    assert p4["complete"] is False and p4["missing"] == ["rationale", "destination c009 content"]
    assert "c001" not in packets


def test_an_uphold_is_refused_until_evidence_delivery_and_detail_retention_are_complete(tmp_path):
    packets, reviewed = _packet_case(tmp_path)
    p2 = packets["c002"]
    up = {"verdict": "uphold_removal", "details": {"Friday morning": "retained"}}
    assert P.certify(p2, up, set(), reviewed)["why"] == [f"page {EV} p01 not delivered whole"]
    assert P.certify(p2, up, {(EV, 1)}, reviewed) == {"candidate": "c002", "verdict": "uphold_removal", "certified": True, "why": []}
    changed = _labels(_label(Q1, candidate="c001", note="Nora sends a checklist"))
    assert P.certify(p2, up, {(EV, 1)}, changed)["why"] == ["destination c001 changed after review: review the changed destination"]
    assert P.certify(p2, {"verdict": "uphold_removal", "details": {"Friday morning": "lost"}}, {(EV, 1)}, reviewed)["why"] == \
        ["detail not judged retained: Friday morning"]
    assert P.certify(packets["c004"], {"verdict": "uphold_removal"}, {(EV, 1)}, reviewed)["certified"] is False
    assert P.certify(packets["c003"], {"verdict": "unresolved"}, {(EV, 1)}, reviewed)["why"] == ["unresolved is not a final verdict"]


# ---------- reviewed equivalences overlay the automated comparison, never replace it ----------

def test_a_reviewed_equivalence_changes_the_overlay_only_and_each_change_is_named():
    key = [{"cid": "k1", "evidence": EV, "quote": Q1, "kind": "task", "commitment": "committed", "title": "Checklist"},
           {"cid": "k2", "evidence": EV, "quote": "Opal will repaint the north gate signs before the inspection next month",
            "kind": "risk", "commitment": "proposed", "title": "Signs"},
           {"cid": "k3", "evidence": EV, "quote": "Ravi will chase the berth crane quote from the vendor before the end of the week",
            "kind": "task", "commitment": "committed", "title": "Quote"}]
    labels = _labels(_label(Q1, candidate="c001"), _label(Q4, candidate="c004", kind="risk", commitment="proposed"), _label(Q2, candidate="c002"))
    doc = P.compare(labels, key)
    by = "packet_reviewer"
    verdicts = [{"key": "k2", "label": "c004", "same_claim": "same_work", "fields": {"status": "incorrect"}, "basis": "same turn", "by": by},
                {"key": "k3", "label": "c002", "same_claim": "different_work", "basis": "booking, not a quote", "by": by}]
    out = P.reviewed_overlay(doc, labels, key, verdicts)
    assert out["automated"]["covered"] == {"n": 1, "of": 3} and out["reviewed"]["covered"] == {"n": 2, "of": 3}
    assert out["reviewed"]["legacy_match"] == {"n": 2, "of": 3} and out["changed_by_review"] == ["k2"]
    assert out["reviewed_but_unchanged"] == ["k3"] and doc["covered"] == 1          # the automated result is untouched
    assert out["rows"][1]["review"]["fields"] == {"status": "incorrect"}
    with pytest.raises(ValueError, match="unreadable verdicts"):
        P.reviewed_overlay(doc, labels, key, [{"key": "k2", "label": "c004", "same_claim": "probably", "by": by}])


def test_a_reviewers_judgments_become_assessment_records_with_provenance():
    raw = [{"label": "c1", "work": {"value": "supported", "basis": "the turn states it"},
            "fields": {"owner": {"value": "incorrect", "basis": "'we' with no doer"}, "due": {"value": "not_applicable", "basis": "no deadline said"},
                       "status": {"value": "not_assessed"}}},
           {"label": "c2", "work": {"value": "unsupported", "basis": ""}}]
    recs = P.assessment_records(raw, "skeptic", "review")
    assert recs[0]["provenance"]["owner"] == [{"role": "skeptic", "stage": "review", "basis": "'we' with no doer", "value": "incorrect"}]
    assert "status" not in recs[0]["fields"] and recs[0]["fields"]["due"] == "not_applicable"
    with pytest.raises(ValueError, match="judgment without role, stage and basis"):
        P.label_quality(recs, labels=["c1", "c2"])                    # a judgment with no basis is refused, not counted
    q = P.label_quality(recs[:1], labels=["c1", "c2"])
    assert q["views"]["independent"]["fields"]["owner"]["incorrect_labels"] == ["c1"] and q["views"]["independent"]["unassessed_labels"] == ["c2"]


def test_collective_commitment_ownership_counts_errors_over_reviewed_eligible_labels_only():
    labels = _labels(_label(Q5, candidate="c1", owner="Ravi Tamsin", speaker="Ravi Tamsin"),
                     _label("We'll draft the berth summary for the authority before Friday's review", candidate="c2", owner=None),
                     _label("We will send it and I'll check the zones against the camera list", candidate="c3"),
                     _label(Q1, candidate="c4"))
    rec = lambda c, v: {"label": c, "provenance": {"owner": [{"role": "skeptic", "stage": "review", "basis": "turn read", "value": v}]}}
    q = P.label_quality([rec("c1", "incorrect")], labels=["c1", "c2", "c3", "c4"])
    out = P.collective_owner(labels, q)
    assert out["eligible"] == 2 and [r["label"] for r in out["rows"]] == ["c1", "c2"]
    assert out["speaker_as_owner"] == ["c1"] and out["owner_incorrect"] == {"n": 1, "of": 1} and out["unreviewed"] == ["c2"]


# ---------- regressions from the independent diff review ----------

def test_an_upheld_merge_with_a_lost_or_unnamed_detail_is_not_certified(tmp_path):
    packets, reviewed = _packet_case(tmp_path)
    p2 = dict(packets["c002"], required_details=[])
    lost = P.certify(p2, {"verdict": "uphold_removal", "details": {"the Friday date": "lost"}}, {(EV, 1)}, reviewed)
    assert lost["why"] == ["detail not judged retained: the Friday date"]
    assert P.certify(p2, {"verdict": "uphold_removal", "details": {}}, {(EV, 1)}, reviewed)["why"] == \
        ["an upheld merge names no detail the destination keeps"]


def test_a_misformatted_judgment_is_refused_not_dropped():
    with pytest.raises(ValueError, match="work must be"):
        P.assessment_records([{"label": "c1", "work": "unsupported"}], "skeptic", "review")
    with pytest.raises(ValueError, match="owner must be"):
        P.assessment_records([{"label": "c1", "fields": {"owner": "incorrect"}}], "skeptic", "review")


def test_a_resolution_contradicted_by_a_later_judgment_does_not_settle_the_conflict():
    pr = dict(SK, role="packet_reviewer")
    q = P.label_quality([_rec("c1", provenance={"owner": [_by(SK, "correct"), _by(dict(pr, resolution="names Opal"), "incorrect"),
                                                          _by(pr, "correct")]})], labels=["c1"])
    assert q["views"]["independent"]["fields"]["owner"]["unresolved"] == 1


def test_an_unknown_role_is_refused_not_counted_as_independent_review():
    with pytest.raises(ValueError, match="unknown assessment role 'Reader_A'"):
        P.label_quality([_rec("c1", provenance={"work": [_by(dict(SK, role="Reader_A"), "supported")]})], labels=["c1"])


def test_a_joint_owner_never_matches_one_person_and_the_closest_label_is_scored():
    assert not P._same_person("Opal Brennick and Ravi Tamsin", "Opal Brennick") and P._same_person("Opal", "Opal Brennick")
    expected = {"rule": "owner", "cases": [{"case": "i", "evidence": EV, "quote": Q1, "commitment": "committed", "owner": "Nora Quill"}]}
    broad = dict(_label(Q1 + " and then review it together", owner=None, kind="story"), evidence=EV)
    exact = dict(_label(Q1, owner="Nora Quill"), evidence=EV)
    out = P.owner_probe([{"evidence": EV, "items": [broad, exact]}], expected)
    assert out["cases"][0]["result"] == "followed"


# ---------- access and operations from recorded events ----------

def test_access_audit_verifies_reads_flags_outside_and_never_verifies_a_shell(tmp_path):
    class Call:
        def __init__(self, name, **inp):
            self.name, self.input = name, inp
    ok, page, out = str(tmp_path / "READER.md"), str(tmp_path / "v" / "p01.md"), str(tmp_path / "A.json")
    calls = [Call("Read", file_path=ok), Call("Read", file_path=page), Call("Write", file_path=out)]
    a = P.access_audit(calls, {ok, page}, {out})
    assert a["verdict"] == "verified" and a["reads"]["verified"] == sorted([ok, page]) and a["writes"]["permitted"] == [out]
    a = P.access_audit(calls + [Call("Bash", command="cat notes.txt")], {ok, page}, {out})
    assert a["verdict"] == "unverified" and a["shell_unverified"] == ["cat notes.txt"]
    a = P.access_audit(calls + [Call("Read", file_path="/keys/items.v1.2.jsonl"), Call("Write", file_path="/tmp/x.py")], {ok, page}, {out})
    assert a["verdict"] == "outside" and a["reads"]["outside"] == ["/keys/items.v1.2.jsonl"] and a["writes"]["outside"] == ["/tmp/x.py"]


def _events(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def _use(i, inp, ts="2026-11-05T10:00:00Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"model": "m", "usage": {}, "content": [{"type": "tool_use", "id": i, "name": "Read", "input": inp}]}}


def _res(i, out, err=False):
    return {"type": "user", "timestamp": "2026-11-05T10:00:01Z", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": out, "is_error": err}]}}


def _say(text):
    return {"type": "assistant", "timestamp": "2026-11-05T10:00:02Z", "message": {"model": "m", "usage": {}, "content": [{"type": "text", "text": text}]}}


def test_role_operations_derive_completion_retries_and_timeouts_from_events(tmp_path):
    done = _events(tmp_path / "a.jsonl", [_use("t1", {"file_path": "x"}), _res("t1", "Error: request timed out", True),
                                          _use("t2", {"file_path": "x"}), _res("t2", "ok"), _say("4 items")])
    assert P.role_operations(done) == {"completed": True, "ended_by": "text", "unanswered_calls": [], "retries": 1, "timeouts": 1,
                                       "failed_calls": 1, "tool_calls": 2}
    hand = {"type": "assistant", "timestamp": "2026-11-05T10:00:03Z", "message": {"model": "m", "usage": {}, "content": [
        {"type": "tool_use", "id": "t9", "name": "SubagentHandback", "input": {"report": "4 items"}}]}}
    handed = _events(tmp_path / "h.jsonl", [_use("t1", {"file_path": "x"}), _res("t1", "ok"), hand, _res("t9", '{"success":true}')])
    assert P.role_operations(handed)["completed"] is True and P.role_operations(handed)["ended_by"] == "handback"
    failed = _events(tmp_path / "f.jsonl", [hand, _res("t9", "delivery failed", True)])
    assert P.role_operations(failed)["completed"] is False
    cut = _events(tmp_path / "b.jsonl", [_use("t1", {"file_path": "x"})])
    assert P.role_operations(cut)["completed"] is False and P.role_operations(cut)["unanswered_calls"] == ["Read"]
    assert P.role_operations(tmp_path / "none.jsonl") == {"completed": "unknown", "retries": "unknown", "timeouts": "unknown", "why": "no transcript"}
