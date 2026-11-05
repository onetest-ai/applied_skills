"""evals/pilot.py: the deterministic steps of agent-led labelling. Invented data only (Harbor Logistics)."""
import json

import pytest
import sys

from conftest import TASKA_ROOT

sys.path.insert(0, str(TASKA_ROOT / "evals"))
import pilot as P  # noqa: E402

EV = "2026-03-02_meetings_ab12cd34"
Q1 = "Nora will write the onboarding checklist and send it to Opal by Friday morning"
Q2 = "Ravi needs to book the warehouse site visit and confirm the date with Opal"
Q3 = "The team decided not to proceed with the legacy system migration project this quarter"


def _label(quote, **kw):
    base = {"quote": quote, "ts": "00:01:00", "speaker": "Nora Quill", "title": "A title", "kind": "task", "commitment": "committed",
            "owner": "Nora Quill", "due": None, "status": "open", "client_owed": False, "note": "a note of eight words or more here"}
    return base | kw


def _labels(*items):
    return [{"evidence": EV, "items": list(items)}]


def _inputs(tmp_path):
    (tmp_path / f"{EV}.md").write_text(f"# Daily sync\n[00:01:00] Nora Quill: {Q1}\n[00:02:00] Ravi Tamsin: {Q2}\n[00:03:00] Opal: {Q3}\n")
    return tmp_path


def test_validate_finds_an_invented_quote_a_short_quote_and_bad_values(tmp_path):
    res = P.validate(_labels(_label(Q1), _label("Nora will rebuild the whole dock schedule export by next week then"),
                             _label("too short quote"), _label(Q2, kind="epic", due="Friday")), _inputs(tmp_path))
    assert res[0]["problems"] == []
    assert res[1]["problems"] == ["quote not verbatim in the transcript"]
    assert "quote has 3 words (8-40)" in res[2]["problems"]
    assert res[3]["problems"] == ["kind 'epic'", "due 'Friday'"]


def test_union_pairs_the_same_span_and_names_the_fields_the_readers_disagree_on():
    a = _labels(_label(Q1), _label(Q2), _label(Q3, kind="decision"))
    b = _labels(_label(Q1), _label(Q2, owner="Ravi Tamsin"))
    doc = P.union(a, b, seed=1)
    by = {c["labels"][0]["quote"]: c for c in doc["candidates"]}
    assert by[Q1]["source"] == "both" and by[Q1]["differ_on"] == []
    assert by[Q2]["source"] == "both" and by[Q2]["differ_on"] == ["owner"]
    assert by[Q3]["source"] == "one"
    assert doc["counts"] == {"both_agree": 1, "both_differ": 1, "one_reader": 1}
    assert doc["agreement_sample"] == doc["agreements"]          # fewer than three agreements: all of them


def test_the_agreement_sample_is_fixed_by_the_seed():
    many = [f"Nora will write onboarding checklist number {n} and send it to Opal by Friday morning" for n in range(10)]
    a = _labels(*[_label(q) for q in many])
    s1, s2 = P.union(a, a, seed=20261104), P.union(a, a, seed=20261104)
    assert s1["agreement_sample"] == s2["agreement_sample"] and len(s1["agreement_sample"]) == 3


def test_compare_counts_key_items_covered_and_labels_the_key_lacks():
    key = [{"cid": "k1", "evidence": EV, "quote": Q1, "kind": "story", "commitment": "committed", "title": "Checklist"},
           {"cid": "k2", "evidence": EV, "quote": Q3, "kind": "decision", "commitment": "declined", "title": "No migration"}]
    doc = P.compare(_labels(_label(Q1), _label(Q2)), key)
    assert (doc["covered"], doc["kind_same"], doc["commitment_same"]) == (1, 1, 1)
    assert [r["cid"] for r in doc["rows"] if not r["covered"]] == ["k2"]
    assert [x["quote"] for x in doc["labels_not_in_key"]] == [Q2]


def test_the_cli_refuses_invalid_labels_with_exit_1(tmp_path, capsys):
    f = tmp_path / "A.json"
    f.write_text(json.dumps(_labels(_label("Nora will rebuild the whole dock schedule export by next week then"))))
    assert P.main(["validate", "--labels", str(f), "--inputs", str(_inputs(tmp_path))]) == 1
    assert json.loads(capsys.readouterr().out)["invalid"][0]["problems"] == ["quote not verbatim in the transcript"]


def test_compare_credits_a_key_item_whose_span_the_adjudicator_merged_into_a_kept_label():
    key = [{"cid": "k1", "evidence": EV, "quote": Q2, "kind": "task", "commitment": "committed", "title": "Book the visit"}]
    kept = _label(Q1, candidate="c002")
    resolutions = [{"id": "c001", "decision": "exclude", "duplicate_of": "c002", "final": None, "evidence": Q2},
                   {"id": "c002", "decision": "include", "duplicate_of": None, "final": kept, "evidence": Q1}]
    assert P.compare(_labels(kept), key)["covered"] == 0                       # by span alone: missed
    doc = P.compare(_labels(kept), key, resolutions)
    assert doc["covered"] == 1 and doc["rows"][0]["via_duplicate"] == "c001"


def test_compare_names_how_a_covered_key_item_is_represented():
    key = [{"cid": "k1", "evidence": EV, "quote": Q2, "kind": "task", "commitment": "committed", "title": "Book the visit"}]
    doc = P.compare(_labels(_label(Q2, representation="checklist", linked_to="Yard pilot")), key)
    assert doc["rows"][0]["representation"] == "checklist" and doc["rows"][0]["linked_to"] == "Yard pilot"


def test_legacy_key_and_policy_aware_results_are_reported_apart():
    key = [{"cid": "k1", "evidence": EV, "quote": Q1, "kind": "task", "commitment": "committed", "title": "Checklist"},
           {"cid": "k2", "evidence": EV, "quote": Q2, "kind": "task", "commitment": "committed", "title": "Book the visit"},
           {"cid": "k3", "evidence": EV, "quote": Q3, "kind": "task", "commitment": "committed", "title": "Chase"}]
    labels = _labels(_label(Q1), _label(Q2, representation="checklist", linked_to="Yard pilot"),
                     _label(Q3, representation="reminder", linked_to="unresolved"))
    doc = P.compare(labels, key)
    assert doc["legacy_key"] == {"matches": 1, "of": 3}
    assert doc["policy_aware"] == {"matches": 2, "of": 3, "representation_differs_from_key": ["k2", "k3"]}
    assert doc["validation"] == "agent_adjudicated span coverage, not validated correctness"


def test_verify_passes_a_frozen_protocol_and_names_every_drift(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "m.md").write_text("transcript")
    (tmp_path / "snap.tar.gz").write_bytes(b"snapshot")
    code = tmp_path / "code.py"
    code.write_text("x = 1")
    proto = P.freeze(tmp_path / "in", {"code.py": code}, tmp_path / "snap.tar.gz", {"execution_manifest_sha256": "e1"},
                     {"reader_A": {"executor": "agent", "model_id": "claude-sonnet-5-5"}})
    assert P.verify(proto, {"code.py": code}, {"execution_manifest_sha256": "e1"}, tmp_path / "in", tmp_path / "snap.tar.gz") == []
    code.write_text("x = 2")
    (tmp_path / "in" / "m.md").write_text("edited")
    drift = P.verify(proto, {"code.py": code}, {"execution_manifest_sha256": "e2"}, tmp_path / "in", tmp_path / "snap.tar.gz")
    assert drift == ["input m.md changed", "file code.py changed", "manifest execution_manifest_sha256 changed"]
    assert all(r.get("model_id") for r in proto["models"].values())


# ---------- v4: agreement covers representation and linkage; rule references apart; every candidate disposed ----------

def test_a_task_against_a_checklist_entry_is_a_representation_disagreement():
    doc = P.union(_labels(_label(Q1, representation="item")), _labels(_label(Q1, representation="checklist", linked_to="Yard pilot")), seed=1)
    c = doc["candidates"][0]
    assert set(c["differ_on"]) == {"representation", "linked_to"} and doc["counts"]["both_agree"] == 0


def test_reminders_linked_to_different_work_are_a_linkage_disagreement_and_ids_beat_titles():
    a = _labels(_label(Q1, representation="reminder", linked_to="T012"))
    assert P.union(a, _labels(_label(Q1, representation="reminder", linked_to="T014")), seed=1)["candidates"][0]["differ_on"] == ["linked_to"]
    same = P.union(_labels(_label(Q1, representation="reminder", linked_to="Scanner logs from Opal ")),
                   _labels(_label(Q1, representation="reminder", linked_to="scanner logs from opal")), seed=1)
    assert same["candidates"][0]["differ_on"] == []


def test_an_extra_rule_reference_is_recorded_apart_and_order_does_not_matter():
    a = _labels(_label(Q1, rule_ids=["REMINDER-ATTACH", "DUE-RELATIVE"]))
    b = _labels(_label(Q1, rule_ids=["DUE-RELATIVE"]))
    c = P.union(a, b, seed=1)["candidates"][0]
    assert c["differ_on"] == [] and c["rule_refs_differ"] == {"only_in_some": ["REMINDER-ATTACH"]}
    same = P.union(_labels(_label(Q1, rule_ids=["B-RULE", "A-RULE"])), _labels(_label(Q1, rule_ids=["A-RULE", "B-RULE"])), seed=1)
    assert same["candidates"][0]["rule_refs_differ"] is None
    v = P.union(_labels(_label(Q1, rule_ids=["DUE-RELATIVE v1"])), _labels(_label(Q1, rule_ids=["DUE-RELATIVE v2"])), seed=1)
    assert v["candidates"][0]["rule_refs_differ"] == {"only_in_some": ["DUE-RELATIVE v1", "DUE-RELATIVE v2"]}


def test_every_candidate_needs_a_disposition_and_a_merge_needs_an_existing_target():
    union_doc = {"candidates": [{"id": "c001"}, {"id": "c002"}, {"id": "c003"}, {"id": "c004"}]}
    res = [{"id": "c001", "decision": "include"}, {"id": "c002", "decision": "exclude", "duplicate_of": "c009"},
           {"id": "c003", "decision": "policy_unresolved"}]
    d = P.dispositions(union_doc, res)
    assert d["by_candidate"] == {"c001": "included", "c002": "merged", "c003": "unresolved", "c004": None}
    assert d["problems"] == ["c002: merged into c009, which is not a candidate", "c004: no disposition"]


def test_deadline_words_without_a_date_need_a_reason(tmp_path):
    res = P.validate(_labels(_label(Q1, due=None, due_text="before Monday"), _label(Q2, due=None, due_text="before Monday",
                                                                                   due_unresolved="no approved convention for before <day>")), _inputs(tmp_path))
    assert res[0]["problems"] == ["due_text without a due needs due_unresolved"] and res[1]["problems"] == []


def _review_setup(tmp_path):
    union_doc = {"candidates": [{"id": "c001", "source": "both", "labels": [_label(Q1) | {"evidence": EV}]},
                                {"id": "c002", "source": "one", "labels": [_label(Q2) | {"evidence": EV}]}]}
    res = [{"id": "c001", "decision": "include", "final": _label(Q1)}, {"id": "c002", "decision": "exclude", "duplicate_of": None,
                                                                       "evidence": Q2, "rule": "not work: scheduling"}]
    prov = _labels(_label(Q1, candidate="c001"))
    return union_doc, res, prov, _inputs(tmp_path)


def test_a_claim_that_a_recorded_exclusion_is_missing_is_rejected_with_the_exclusion_cited(tmp_path):
    union_doc, res, prov, inputs = _review_setup(tmp_path)
    findings = [{"target": "c002", "type": "dropped_without_resolution", "quote": Q2, "status": "confirmed"}]
    out = P.check_claims(findings, union_doc, res, prov, inputs)
    f = out["findings"][0]
    assert f["factual"] == "rejected" and "c002 has a recorded exclusion (not work: scheduling)" in f["evidence"]
    assert f["semantic"] == "not checked by code: a recorded exclusion is not a justified one"


def test_claims_about_unknown_candidates_or_quotes_not_in_the_source_are_flagged(tmp_path):
    union_doc, res, prov, inputs = _review_setup(tmp_path)
    out = P.check_claims([{"target": "c009", "type": "due", "quote": Q1}, {"target": "c001", "type": "owner", "quote": "words nobody said in this meeting at all"},
                          {"target": "missing", "type": "missed_risk", "quote": Q3}], union_doc, res, prov, inputs)
    assert [f["factual"] for f in out["findings"]] == ["rejected", "rejected", "holds"]
    assert "c009 is not a candidate" in out["findings"][0]["evidence"] and "quote not in the transcript" in out["findings"][1]["evidence"]
    assert out["counts"] == {"included_resolutions": 1, "provisional_items": 1, "match": True}


def test_counts_that_disagree_with_the_records_are_reported(tmp_path):
    union_doc, res, prov, inputs = _review_setup(tmp_path)
    out = P.check_claims([], union_doc, res, _labels(_label(Q1, candidate="c001"), _label(Q2, candidate="c002")), inputs)
    assert out["counts"] == {"included_resolutions": 1, "provisional_items": 2, "match": False}


def test_the_check_command_fails_on_an_undisposed_candidate(tmp_path, capsys):
    union_doc, res, prov, inputs = _review_setup(tmp_path)
    files = {}
    for name, body in (("u", union_doc), ("r", res[:1]), ("p", prov)):
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(body))
    args = ["check", "--union", str(files["u"]), "--resolutions", str(files["r"]), "--provisional", str(files["p"]),
            "--inputs", str(inputs), "--out", str(tmp_path / "out.json")]
    assert P.main(args) == 1 and "c002: no disposition" in capsys.readouterr().out


# ---------- label quality in separate dimensions, every figure with its numerator and denominator ----------

def _assess(label, work="supported", policy="resolved", rep="compliant", **fields):
    f = {k: "correct" for k in ("owner", "commitment", "status", "due")} | fields
    by = [{"role": "skeptic", "stage": "review", "basis": "transcript + rules"}]
    prov = {"work": by, "representation": by} | {k: by for k, v in f.items() if v not in ("not_assessed", "not_applicable")}
    return {"label": label, "work": work, "fields": f, "policy": policy, "representation": rep, "provenance": prov}


def test_the_check_command_names_a_rejected_finding_by_id_and_targets(tmp_path, capsys):
    union_doc, res, prov, inputs = _review_setup(tmp_path)
    files = {}
    for name, body in (("u", union_doc), ("r", res), ("p", prov), ("f", {"findings": [{"id": "S1", "target": "c009", "type": "due"}]})):
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(body))
    P.main(["check", "--union", str(files["u"]), "--resolutions", str(files["r"]), "--provisional", str(files["p"]),
            "--findings", str(files["f"]), "--inputs", str(inputs), "--out", str(tmp_path / "out.json")])
    assert json.loads(capsys.readouterr().out)["claims_rejected"] == [{"id": "S1", "targets": ["c009"]}]


def test_supported_work_with_field_errors_is_not_an_invented_task():
    q = P.label_quality([_assess("c024", owner="incorrect", commitment="incorrect"), _assess("c001"),
                         _assess("c036", policy="policy_unresolved", commitment="not_assessed")], labels=["c001", "c024", "c036", "c040"])
    v = q["views"]["independent"]
    assert {k: v["work"][k] for k in ("supported", "unsupported", "unresolved", "not_assessed")} == \
        {"supported": 3, "unsupported": 0, "unresolved": 0, "not_assessed": 1}
    assert v["work"]["coverage"] == {"n": 3, "of": 4} and v["work"]["unsupported_rate"] == {"n": 0, "of": 3}
    assert v["fields"]["owner"]["incorrect_rate"] == {"n": 1, "of": 3} and v["fields"]["owner"]["not_assessed"] == 1
    assert v["fields"]["commitment"]["incorrect_rate"] == {"n": 1, "of": 2} and v["fields"]["commitment"]["not_assessed"] == 2
    assert v["fields"]["owner"]["incorrect_labels"] == ["c024"]
    assert q["policy_unresolved"] == ["c036"] and v["unassessed_labels"] == ["c040"]
    assert v["judgments_by_role"] == {"skeptic": 17} and q["views"]["initial"]["judgments_by_role"] == {}
    assert q["views"]["initial"]["work"]["unsupported_rate"] == "unknown (0 assessed)"


def test_an_unknown_outcome_value_is_refused():
    with pytest.raises(ValueError, match="work 'sound'"):
        P.label_quality([_assess("c1", work="sound")], labels=["c1"])


# ---------- losses and recoveries across the workflow, one identity per candidate ----------

def test_union_keeps_who_found_what_outside_the_blinded_file():
    doc = P.union(_labels(_label(Q1), _label(Q2)), _labels(_label(Q1)), seed=1)
    found = doc["found_by"]
    by_quote = {c["labels"][0]["quote"]: found[c["id"]] for c in doc["candidates"]}
    assert by_quote == {Q1: ["A", "B"], Q2: ["A"]}
    assert "found_by" not in json.dumps(doc["candidates"])


def test_omission_items_join_as_new_candidates_or_confirm_existing_ones():
    doc = P.union(_labels(_label(Q1)), _labels(_label(Q1)), seed=1)
    out = P.add_omissions(doc, _labels(_label(Q1), _label(Q3, kind="risk")))
    new = [c for c in out["candidates"] if c["source"] == "omission"]
    assert len(new) == 1 and new[0]["labels"][0]["quote"] == Q3 and out["found_by"][new[0]["id"]] == ["omission"]
    assert "omission" in out["found_by"][doc["candidates"][0]["id"]]


def _ledger_case():
    doc = P.union(_labels(_label(Q1), _label(Q2, kind="decision")), _labels(_label(Q1)), seed=1)
    doc = P.add_omissions(doc, _labels(_label(Q3, kind="risk")))
    ids = {c["labels"][0]["quote"]: c["id"] for c in doc["candidates"]}
    res = [{"id": ids[Q1], "decision": "include"}, {"id": ids[Q2], "decision": "exclude", "rule": "logistics"},
           {"id": ids[Q3], "decision": "include"}]
    corrections = [{"candidate": ids[Q2], "by": "removal_reviewer", "verdict": "restore_candidate", "evidence": "adopted"},
                   {"candidate": ids[Q2], "by": "skeptic", "verdict": "restore_candidate", "evidence": "same"},
                   {"candidate": ids[Q1], "by": "skeptic", "verdict": "keep_label", "evidence": "it is work"}]
    return doc, res, corrections, ids


def test_the_ledger_names_each_path_and_credits_a_recovery_once():
    doc, res, corrections, ids = _ledger_case()
    led = P.stage_ledger(doc, res, corrections)
    path = {r["id"]: r["path"] for r in led["candidates"]}
    assert path[ids[Q3]] == "first-pass miss, reviewer recovery (omission reader), no final miss"
    assert path[ids[Q2]] == "adjudication loss recovered (removal_reviewer)"
    assert path[ids[Q1]] == "found by readers, kept"
    assert led["by_role"]["removal_reviewer"] == {"restore_candidate": 1, "credited": 1}
    assert led["by_role"]["skeptic"] == {"restore_candidate": 1, "keep_label": 1, "credited": 0}   # a second confirmation is not a second recovery
    assert led["final"] == sorted([ids[Q1], ids[Q2], ids[Q3]])


def test_reference_misses_and_new_discoveries_are_reported_apart():
    doc, res, corrections, ids = _ledger_case()
    key = [{"cid": "k1", "evidence": EV, "quote": Q1}, {"cid": "k9", "evidence": EV, "quote": "Opal will repaint the north gate signs before the inspection next month"}]
    led = P.stage_ledger(doc, res, corrections, key)
    assert led["reference"] == {"key_items": 2, "final_holds": ["k1"], "final_misses": ["k9"],
                                "outside_key_final": sorted([ids[Q2], ids[Q3]]),
                                "unknown_omissions": "not measured: no independent reference beyond the key and the omission reader"}


# ---------- role telemetry from the subagent transcripts (trajectory's parser), calls classified ----------

def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def _turn(ts, tools, usage):
    return {"type": "assistant", "timestamp": ts, "message": {"model": "claude-sonnet-5-5", "usage": usage,
            "content": [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in tools]}}


def _result(ts, results):
    return {"type": "user", "timestamp": ts, "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": out, "is_error": err}
                                                                     for i, out, err in results]}}


def test_role_telemetry_reads_tokens_calls_time_and_the_resolved_model(tmp_path):
    u = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 10}
    f = _jsonl(tmp_path / "a.jsonl", [_turn("2026-11-04T10:00:00Z", [("t1", "Read", {"file_path": "x"})], u),
                                      _result("2026-11-04T10:00:05Z", [("t1", "ok", False)]),
                                      _turn("2026-11-04T10:01:00Z", [("t2", "Write", {"file_path": "y"})], u),
                                      _result("2026-11-04T10:01:02Z", [("t2", "ok", False)])])
    t = P.role_telemetry({"reader_A": f}, {"reader_A": 8364})["roles"]["reader_A"]
    assert t["model"] == "claude-sonnet-5-5" and t["model_calls"] == 2 and t["tool_calls"] == 2
    assert t["tokens"] == {"input": 200, "output": 100, "cache_read": 2000, "cache_write": 20}
    assert t["elapsed_s"] == 62 and t["meeting_words"] == 8364
    assert t["cost"] == {"actual_usd": None, "estimated_usd": None, "why": "no price table is recorded for these models: unknown, not zero"}


def test_failed_calls_are_classified_and_unclassified_ones_stay_visible(tmp_path):
    u = {"input_tokens": 1, "output_tokens": 1}
    f = _jsonl(tmp_path / "b.jsonl", [
        _turn("2026-11-04T10:00:00Z", [("t1", "Write", {"file_path": "y"})], u),
        _result("2026-11-04T10:00:01Z", [("t1", "InputValidationError: content is required", True)]),
        _turn("2026-11-04T10:00:02Z", [("t2", "Write", {"file_path": "y"})], u),
        _result("2026-11-04T10:00:03Z", [("t2", "InputValidationError: content is required", True)]),
        _turn("2026-11-04T10:00:04Z", [("t3", "Bash", {"command": "ls"})], u),
        _result("2026-11-04T10:00:05Z", [("t3", "Error: overloaded, please retry", True)]),
        _turn("2026-11-04T10:00:06Z", [("t4", "Read", {"file_path": "z"})], u),
        _result("2026-11-04T10:00:07Z", [("t4", "File does not exist.", True)])])
    t = P.role_telemetry({"skeptic": f}, {})["roles"]["skeptic"]
    assert t["calls"] == {"avoidable_agent_error": 1, "avoidable_repetition": 1, "recovery_overhead": 1, "unclassified": 1,
                          "succeeded": 0, "failed": 4}
    assert t["avoidable_call_rate"] == {"n": 2, "of": 4} and t["classification_coverage"] == {"n": 3, "of": 4}
    assert t["tool_error_rate"] == {"n": 4, "of": 4} and t["meeting_words"] is None


def test_a_missing_transcript_is_unknown_not_zero(tmp_path):
    t = P.role_telemetry({"adjudicator": tmp_path / "none.jsonl"}, {})
    assert t["roles"]["adjudicator"] == {"telemetry": "unknown: no transcript"} and t["wall_clock_s"] is None


def test_a_transcript_without_timestamps_has_unknown_time_not_zero(tmp_path):
    f = _jsonl(tmp_path / "c.jsonl", [{"type": "assistant", "message": {"model": "m", "usage": {}, "content": []}}])
    t = P.role_telemetry({"reader_B": f}, {})
    assert t["roles"]["reader_B"]["elapsed_s"] is None and t["wall_clock_s"] is None


def test_a_key_item_whose_span_was_merged_into_a_final_label_is_held():
    doc = P.union(_labels(_label(Q1), _label(Q2)), _labels(), seed=1)
    ids = {c["labels"][0]["quote"]: c["id"] for c in doc["candidates"]}
    res = [{"id": ids[Q1], "decision": "include"}, {"id": ids[Q2], "decision": "exclude", "duplicate_of": ids[Q1]}]
    led = P.stage_ledger(doc, res, [], [{"cid": "k2", "evidence": EV, "quote": Q2}])
    assert led["reference"]["final_holds"] == ["k2"]


def test_contributors_are_optional_but_must_be_a_list_of_names(tmp_path):
    res = P.validate(_labels(_label(Q1, owner=None, contributors=["Nora Quill", "Ravi Tamsin"]), _label(Q2, contributors="Nora"),
                             _label(Q3, contributors=[""])), _inputs(tmp_path))
    assert res[0]["problems"] == [] and res[1]["problems"] == ["contributors 'Nora'"] and res[2]["problems"] == ["contributors ['']"]


def test_a_link_naming_two_targets_is_refused_and_a_title_with_and_is_not(tmp_path):
    """Regression (v7 c018): 'billing work / RMS replacement requirements' joined two targets the client left open."""
    res = P.validate(_labels(_label(Q1, representation="reminder", linked_to="berth schedule / crane permits"),
                             _label(Q2, representation="checklist", linked_to="Site visits; gate access"),
                             _label(Q3, representation="checklist", linked_to="Yard audit with customers, agents and drivers")), _inputs(tmp_path))
    assert res[0]["problems"] == ["linked_to names more than one target: 'berth schedule / crane permits'"]
    assert res[1]["problems"] == ["linked_to names more than one target: 'Site visits; gate access'"]
    assert res[2]["problems"] == []


@pytest.mark.parametrize("title", ["CI/CD migration", "C++ migration", "Build or buy analysis", "Search + filter prototype", "T012"])
def test_a_single_target_title_with_punctuation_is_one_link(tmp_path, title):
    assert P.validate(_labels(_label(Q1, representation="checklist", linked_to=title)), _inputs(tmp_path))[0]["problems"] == []


def test_an_exact_known_title_is_one_link_before_punctuation_is_read(tmp_path):
    lab = _label(Q1, representation="checklist", linked_to="Ops / Dev handover")
    assert P.validate(_labels(lab), _inputs(tmp_path))[0]["problems"] != []
    assert P.validate(_labels(lab), _inputs(tmp_path), known_titles={"ops /  dev Handover"})[0]["problems"] == []
    sibling = _label(Q2, title="Ops / Dev handover")
    assert P.validate(_labels(lab, sibling), _inputs(tmp_path))[0]["problems"] == []
