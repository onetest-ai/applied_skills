"""evals/verdict.py: the slice of a meeting (size by structure), the causes of a run's misses and the per-slice table
against a baseline. Invented data only."""
import json
import re
import sys

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import verdict  # noqa: E402


def test_a_meeting_is_sliced_by_size_and_structure():
    turns = [{"speaker": "Nora Quill", "text": "a" * 6000}, {"speaker": "Ravi Tamsin", "text": "b" * 6000}]
    assert verdict.slice_of({"turns": turns}) == "M turns"
    assert verdict.slice_of({"turns": [{"speaker": "?", "text": "c" * 31000}]}) == "L one block"
    assert verdict.slice_of(None) == "unknown"


def test_size_bands_break_at_ten_and_thirty_thousand():
    def one(n):
        return {"turns": [{"speaker": "Nora Quill", "text": "a" * n}]}
    assert verdict.slice_of(one(9999)) == "S one block"
    assert verdict.slice_of(one(10000)) == "M one block"
    assert verdict.slice_of(one(30000)) == "M one block"
    assert verdict.slice_of(one(30001)) == "L one block"


def test_two_turns_by_one_named_speaker_are_one_block():
    turns = [{"speaker": "Nora Quill", "text": "a"}, {"speaker": "Nora Quill", "text": "b"}, {"speaker": "?", "text": "c"}]
    assert verdict.slice_of({"turns": turns}) == "S one block"


def test_a_meeting_without_turns_is_sized_by_its_text():
    assert verdict.slice_of({"text": "t" * 12000}) == "M one block"


def test_causes_rank_misses_by_slice_kind_and_class():
    key = {"a": {"kind": "task", "evidence": "m1"}, "b": {"kind": "task", "evidence": "m1"}, "c": {"kind": "decision", "evidence": "m2"}}
    rows = verdict.causes(["a", "b", "c"], key, {"m1": "L one block", "m2": "S turns"}, {"a": "blind spot", "b": "blind spot"})
    assert rows[0] == {"slice": "L one block", "kind": "task", "class": "blind spot", "misses": 2, "items": ["a", "b"]}
    assert rows[1]["class"] == "n/a"


def test_causes_break_ties_by_slice_then_kind_and_know_no_meeting():
    key = {"a": {"kind": "task", "evidence": "m1"}, "b": {"kind": "risk", "evidence": "m1"}, "c": {"kind": "task", "evidence": "gone"}}
    rows = verdict.causes(["a", "b", "c"], key, {"m1": "S turns"}, {})
    assert [(r["slice"], r["kind"]) for r in rows] == [("S turns", "risk"), ("S turns", "task"), ("unknown", "task")]


def test_a_slice_table_counts_found_items_per_run():
    key = {"a": {"evidence": "m1"}, "b": {"evidence": "m1"}}
    t = verdict.slice_table({"a", "b"}, {"a"}, key, {"m1": "S turns"})
    assert t == [{"slice": "S turns", "items": 2, "baseline_found": 2, "candidate_found": 1, "delta": -1}]


def test_a_missing_run_copy_gives_unknown_slices(tmp_path):
    assert verdict.meeting_slices(tmp_path / "gone", {"m1"}) == {"m1": "unknown"}


def test_meeting_slices_read_the_run_copy_evidence(tmp_path):
    ev = tmp_path / ".taska" / "evidence"
    ev.mkdir(parents=True)
    turns = [{"speaker": "Nora Quill", "text": "a" * 6000}, {"speaker": "Ravi Tamsin", "text": "b" * 6000}]
    (ev / "m1.json").write_text(json.dumps({"id": "m1", "kind": "meeting", "turns": turns}), encoding="utf-8")
    assert verdict.meeting_slices(tmp_path, {"m1", "m2"}) == {"m1": "M turns", "m2": "unknown"}


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _project(tmp_path, key_items, meetings=None):
    proj = tmp_path / "proj"
    turns = [{"speaker": "Nora Quill", "text": "a" * 100}, {"speaker": "Ravi Tamsin", "text": "b" * 100}]
    blob = [{"speaker": "?", "text": "c" * 100}]
    for eid, t in (meetings or {"m1": turns, "m2": blob}).items():
        _write(proj / ".taska" / "evidence" / f"{eid}.json", {"id": eid, "turns": t})
    key = tmp_path / "key.jsonl"
    key.write_text("\n".join(json.dumps(i) for i in key_items) + "\n", encoding="utf-8")
    return proj, key


def _record(tmp_path, proj, key, name, hits, *, tier="low", key_sha="k1", incomplete=False, snapshot="s1",
            processed=("m1", "m2"), trajectory=None, b7_lb=0.5, b13_lb=0.5, quality=None):
    base = tmp_path / "evals" / name
    items = [json.loads(l) for l in key.read_text().splitlines()]
    _write(base / "meta.json", {"key_version": "v1", "key_sha": key_sha, "key_path": str(key), "project": str(proj),
                                "model_tier": tier, "code_version": f"code-{name}", "snapshot_of": snapshot,
                                "processed": list(processed), "covered_by": True})
    _write(base / "score.json", {"incomplete": incomplete,
                                 "items": [{"cid": i["cid"], "evidence": i["evidence"], "match": i["cid"] in hits} for i in items],
                                 "B7": {"n": 4, "ok": 2, "rate": 0.5, "lb95": b7_lb}, "B13": {"n": 2, "ok": 1, "rate": 0.5, "lb95": b13_lb}})
    if trajectory is not None:
        _write(base / "trajectory.json", {"summary": trajectory})
    if quality is not None:
        _write(base / "quality" / "quality.json", {"summary": {"precision": quality}})
    return base


def _four():
    return [{"cid": f"c{n}", "evidence": "m1" if n < 3 else "m2", "kind": "task", "commitment": "committed"} for n in range(1, 5)]


def _pair(tmp_path, key_items, base_hits, cand_hits, **cand):
    proj, key = _project(tmp_path, key_items)
    _record(tmp_path, proj, key, "20261101-000000-1", base_hits)
    _record(tmp_path, proj, key, "20261102-000000-1", cand_hits, **cand)
    return tmp_path / "evals"


def test_lost_committed_items_with_a_small_p_hold(tmp_path):
    key = [{"cid": f"c{n}", "evidence": "m1", "kind": "task", "commitment": "committed"} for n in range(8)]
    evals = _pair(tmp_path, key, {f"c{n}" for n in range(8)}, set())
    doc = verdict.build(evals, "low", {}, "20261101-000000-1")
    assert doc["verdict"] == "hold"
    assert any("McNemar" in w for w in doc["why"])
    assert doc["diff"]["mcnemar_committed"]["lost"] == 8


def test_a_slice_losing_two_items_holds_even_when_the_total_rises(tmp_path):
    key = [{"cid": f"a{n}", "evidence": "m1", "kind": "task", "commitment": "soft"} for n in range(4)]
    key += [{"cid": f"b{n}", "evidence": "m2", "kind": "task", "commitment": "soft"} for n in range(6)]
    base = {"a0", "a1", "a2", "a3", "b0"}
    cand = {"a0", "a1"} | {f"b{n}" for n in range(6)}
    doc = verdict.build(_pair(tmp_path, key, base, cand), "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    assert doc["verdict"] == "hold"
    assert any("S turns" in w for w in doc["why"])
    assert {r["slice"]: r["delta"] for r in doc["slices"]} == {"S turns": -2, "S one block": 5}


def test_no_baseline_is_inconclusive(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"})
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, None)
    assert doc["verdict"] == "inconclusive"
    assert any("verdict.py adopt" in w for w in doc["why"])
    assert doc["baseline"] is None and doc["diff"] is None


def test_a_baseline_on_another_key_is_inconclusive_without_a_diff(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261101-000000-1", {"c1"}, key_sha="other")
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"})
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    assert doc["verdict"] == "inconclusive"
    assert doc["diff"] is None
    assert any("different answer keys" in w for w in doc["why"])


def test_unset_bars_never_fail_and_unjudged_precision_is_unknown(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"}, trajectory={"tool_calls": 100, "tool_errors": 2, "refusals": 0})
    doc = verdict.build(evals, "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    status = {g["name"]: g["status"] for g in doc["gates"]}
    assert status == {"committed": "pass", "decisions": "not set", "committed strict": "not set", "decisions strict": "not set",
                      "wasted": "not set", "precision": "unknown"}
    assert doc["verdict"] == "ship"
    assert doc["schema"] == "eval-verdict/1"


def test_no_bars_at_all_is_inconclusive_not_ship(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    doc = verdict.build(evals, "low", {}, "20261101-000000-1")
    assert doc["verdict"] == "inconclusive"
    assert doc["why"] == ["no bars set (evals/bars.json)"]


def test_a_failed_gate_holds_without_a_baseline(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"}, b7_lb=0.2)
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.5}, None)
    assert doc["verdict"] == "hold"
    assert any("committed" in w for w in doc["why"])


def test_the_wasted_gate_and_validated_precision_are_judged(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1"}, {"c1"}, trajectory={"tool_calls": 100, "tool_errors": 30, "refusals": 0},
                  quality={"validated": True, "lb95": 0.9, "rate": 0.95, "n": 20})
    doc = verdict.build(evals, "low", {"wasted_rate_max": 0.1, "precision_lb_min": 0.8}, "20261101-000000-1")
    status = {g["name"]: g["status"] for g in doc["gates"]}
    assert status["wasted"] == "fail" and status["precision"] == "pass"
    assert doc["verdict"] == "hold"


def test_different_snapshots_show_a_like_for_like_diff_but_no_verdict(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261101-000000-1", {"c1", "c3"}, snapshot="s1", processed=("m1",))
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1", "c3"}, snapshot="s2")
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    assert doc["verdict"] == "inconclusive"
    assert doc["diff"]["n"] == 2
    assert any("snapshots" in w for w in doc["why"])


def test_causes_use_the_cross_run_class_when_built(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1"}, {"c1"})
    _write(evals / "crossrun" / "v1" / "crossrun.json", {"items": [{"id": "c2", "class": "blind spot"}]})
    doc = verdict.build(evals, "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    assert {(c["slice"], c["class"]) for c in doc["causes"]} == {("S turns", "blind spot"), ("S one block", "n/a")}
    assert doc["links"]["quality"] == "../../20261102-000000-1/quality/index.html"
    assert doc["links"]["crossrun"] == "../../crossrun/v1/index.html"
    assert doc["links"]["trajectory"] == "../../20261102-000000-1/trajectory.html"


def test_wasted_calls_exclude_gate_refusals():
    assert verdict.efficiency({"summary": {"tool_calls": 200, "tool_errors": 20, "refusals": 12}})["wasted"] == 8
    assert verdict.efficiency({"summary": {"tool_calls": 10, "tool_errors": 1, "refusals": 3}})["wasted"] == 0
    assert verdict.efficiency({})["wasted_rate"] is None


def test_the_candidate_is_the_newest_complete_record_of_the_tier_on_the_current_key(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261030-000000-1", {"c1"}, key_sha="old")
    _record(tmp_path, proj, key, "20261101-000000-1", {"c1"})
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"})
    _record(tmp_path, proj, key, "20261102-120000-1", {"c1"}, incomplete=True)
    _record(tmp_path, proj, key, "20261103-000000-1", {"c1"}, tier="med")
    assert verdict.candidate(tmp_path / "evals", "low").name == "20261102-000000-1"
    assert verdict.candidate(tmp_path / "evals", "high") is None


def _lost_pair(tmp_path):
    key = [{"cid": f"c{n}", "evidence": "m1", "kind": "task", "commitment": "committed"} for n in range(8)]
    return _pair(tmp_path, key, {f"c{n}" for n in range(8)}, set())


def test_the_ship_reason_does_not_claim_no_lost_items(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    doc = verdict.build(evals, "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    assert doc["verdict"] == "ship"
    assert doc["why"] == ["every gate with a bar passes, no significant loss of committed items (McNemar), no slice at net -2 or worse"]


def test_a_record_without_its_key_path_or_project_is_inconclusive(tmp_path):
    proj, key = _project(tmp_path, _four())
    base = _record(tmp_path, proj, key, "20261102-000000-1", {"c1"})
    meta = json.loads((base / "meta.json").read_text())
    del meta["key_path"]
    _write(base / "meta.json", meta)
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, None)
    assert doc["verdict"] == "inconclusive"
    assert any("key_path" in w for w in doc["why"])
    del meta["project"]
    meta["key_path"] = str(key)
    _write(base / "meta.json", meta)
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, None)
    assert doc["verdict"] == "inconclusive" and any("project" in w for w in doc["why"])


def test_an_unreadable_key_file_is_inconclusive(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"})
    key.unlink()
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, None)
    assert doc["verdict"] == "inconclusive"
    assert any("key" in w for w in doc["why"])


def test_an_incomplete_baseline_is_inconclusive_with_the_like_for_like_diff(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261101-000000-1", {"c1", "c3"}, incomplete=True, processed=("m1",))
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1", "c3"})
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    assert doc["verdict"] == "inconclusive"
    assert doc["diff"]["n"] == 2
    assert any("incomplete" in w for w in doc["why"])


def test_a_significant_loss_on_different_snapshots_is_not_a_hold(tmp_path):
    proj, key_path = _project(tmp_path, [{"cid": f"c{n}", "evidence": "m1", "kind": "task", "commitment": "committed"} for n in range(8)])
    _record(tmp_path, proj, key_path, "20261101-000000-1", {f"c{n}" for n in range(8)}, snapshot="s1")
    _record(tmp_path, proj, key_path, "20261102-000000-1", set(), snapshot="s2")
    doc = verdict.build(tmp_path / "evals", "low", {}, "20261101-000000-1")
    assert doc["diff"]["mcnemar_committed"]["p"] < 0.05
    assert doc["verdict"] == "inconclusive"


def _page_doc(tmp_path):
    doc = verdict.build(_lost_pair(tmp_path), "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    out = verdict.write(tmp_path / "out", doc)
    return doc, out


def test_the_page_leads_with_the_verdict_and_lists_lost_items(tmp_path):
    doc, out = _page_doc(tmp_path)
    assert out.name == "verdict.json" and (out.parent / "index.html").is_file()
    assert json.loads(out.read_text())["verdict"] == "hold"
    page = (out.parent / "index.html").read_text()
    assert "verdict.json" in page
    assert page.index("What the verdict says") < page.index("<h2>Gates")
    heads = [page.index(f"<h2>{h}") for h in ("Gates", "Lost", "Gained", "Causes", "Slices", "Efficiency")]
    assert heads == sorted(heads)
    assert page.index("id=verdict") < page.index("<h2>What the verdict says")
    assert "(d.links || {}).quality" in page and "not recorded" in page


def test_the_page_has_no_project_words():
    assert not re.search(r"(?i)meeting|taska|echo", verdict.PAGE)


def _adopt_setup(tmp_path, **record):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261101-000000-1", {"c1"}, **record)
    return proj, tmp_path / "evals"


def test_adopt_writes_the_baseline_and_names_the_one_it_replaces(tmp_path):
    proj, evals = _adopt_setup(tmp_path)
    _write(proj / "evals" / "baseline.json", {"low": "20261030-000000-1", "high": "20261029-000000-1"})
    result = verdict.adopt(proj, evals, "20261101-000000-1")
    assert result == {"tier": "low", "record": "20261101-000000-1", "replaced": "20261030-000000-1"}
    assert json.loads((proj / "evals" / "baseline.json").read_text()) == {"low": "20261101-000000-1", "high": "20261029-000000-1"}
    assert not list((proj / "evals").glob("*.tmp"))


def test_adopt_with_no_earlier_baseline_replaces_nothing(tmp_path):
    proj, evals = _adopt_setup(tmp_path)
    assert verdict.adopt(proj, evals, "20261101-000000-1")["replaced"] is None


def test_adopt_refuses_an_incomplete_record_and_leaves_the_file(tmp_path):
    proj, evals = _adopt_setup(tmp_path, incomplete=True)
    _write(proj / "evals" / "baseline.json", {"low": "old"})
    before = (proj / "evals" / "baseline.json").read_text()
    try:
        verdict.adopt(proj, evals, "20261101-000000-1")
    except ValueError as e:
        assert "incomplete" in str(e)
    else:
        raise AssertionError("adopt accepted an incomplete record")
    assert (proj / "evals" / "baseline.json").read_text() == before


def test_adopt_refuses_a_record_without_a_pinned_tier(tmp_path):
    proj, evals = _adopt_setup(tmp_path)
    meta = json.loads((evals / "20261101-000000-1" / "meta.json").read_text())
    del meta["model_tier"]
    _write(evals / "20261101-000000-1" / "meta.json", meta)
    try:
        verdict.adopt(proj, evals, "20261101-000000-1")
    except ValueError as e:
        assert "tier" in str(e)
    else:
        raise AssertionError("adopt accepted a record with no tier")
    assert not (proj / "evals" / "baseline.json").exists()


def test_adopt_refuses_an_unknown_record(tmp_path):
    proj, evals = _adopt_setup(tmp_path)
    try:
        verdict.adopt(proj, evals, "nope")
    except ValueError as e:
        assert "nope" in str(e)
    else:
        raise AssertionError("adopt accepted an unknown record")


def test_the_cli_builds_a_page_per_tier_that_has_a_candidate(tmp_path, capsys):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261101-000000-1", {"c1"})
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"})
    _write(proj / "evals" / "baseline.json", {"low": "20261101-000000-1"})
    _write(proj / "evals" / "bars.json", {"committed_lb_min": 0.1})
    assert verdict.main(["build", "--project", str(proj), "--evals-dir", str(tmp_path / "evals")]) == 0
    out = tmp_path / "evals" / "verdict" / "low"
    assert not (tmp_path / "evals" / "verdict" / "med").exists()
    assert json.loads((out / "verdict.json").read_text())["verdict"] == "ship"
    assert (out / "index.html").is_file()
    assert "low" in capsys.readouterr().out


def test_a_bar_on_a_gate_without_a_value_is_inconclusive_not_ship(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    doc = verdict.build(evals, "low", {"precision_lb_min": 0.8}, "20261101-000000-1")
    assert doc["verdict"] == "inconclusive"
    assert doc["why"] == ["gate precision has a bar but no value"]
    doc = verdict.build(evals, "low", {"wasted_rate_max": 0.1}, "20261101-000000-1")
    assert doc["verdict"] == "inconclusive"
    assert doc["why"] == ["gate wasted has a bar but no value"]


def test_a_passing_gate_never_ships_past_an_unknown_required_gate(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    doc = verdict.build(evals, "low", {"committed_lb_min": 0.1, "precision_lb_min": 0.9}, "20261101-000000-1")
    assert doc["verdict"] == "inconclusive"
    assert doc["why"] == ["gate precision has a bar but no value"]


def test_the_ship_reason_names_gates_with_a_bar(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    doc = verdict.build(evals, "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    assert doc["verdict"] == "ship" and doc["why"][0].startswith("every gate with a bar passes")


def test_the_candidate_as_its_own_baseline_is_inconclusive(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1"}, {"c1"})
    doc = verdict.build(evals, "low", {"committed_lb_min": 0.1}, "20261102-000000-1")
    assert doc["verdict"] == "inconclusive"
    assert doc["why"] == ["the candidate is the adopted baseline: no change to judge"]
    assert doc["diff"] is None


def test_a_hold_keeps_the_inconclusive_reasons_after_its_own(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"}, b7_lb=0.2)
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.5}, None)
    assert doc["verdict"] == "hold"
    assert doc["why"][0].startswith("gate committed failed")
    assert doc["why"][-1].startswith("no adopted baseline")


def test_a_failed_gate_has_a_bad_pill():
    assert re.search(r"fail: 'p-fail'", verdict.PAGE) and ".p-fail{" in verdict.PAGE


def test_out_with_several_tiers_is_refused(tmp_path, capsys):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"})
    code = verdict.main(["build", "--project", str(proj), "--evals-dir", str(tmp_path / "evals"),
                         "--tier", "low", "--tier", "med", "--out", str(tmp_path / "o")])
    assert code == 2
    assert "--out" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_a_requested_record_is_judged_itself_never_an_older_complete_one(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261101-000000-1", {"c1"})
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"}, incomplete=True)
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, None, record="20261102-000000-1")
    assert doc["verdict"] == "inconclusive" and doc["candidate"] is None
    assert doc["why"] == ["record 20261102-000000-1 is incomplete: it has no verdict"]
    assert doc["execution_status"] == "incomplete" and doc["release_status"] == "inconclusive"
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, None, record="20261109-000000-1")
    assert doc["why"] == ["no record 20261109-000000-1 of tier low"] and doc["execution_status"] == "missing"


def test_a_scored_record_reports_its_execution_and_release_status(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    doc = verdict.build(evals, "low", {"committed_lb_min": 0.1}, "20261101-000000-1")
    assert (doc["execution_status"], doc["evaluation_status"], doc["release_status"]) == ("complete", "scored", "ship")


def test_the_cli_exit_code_is_the_worst_verdict(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"}, b7_lb=0.2)
    args = ["build", "--project", str(proj), "--evals-dir", str(tmp_path / "evals")]
    _write(proj / "evals" / "bars.json", {"committed_lb_min": 0.5})
    assert verdict.main(args) == 5
    _write(proj / "evals" / "bars.json", {"committed_lb_min": 0.1, "precision_lb_min": 0.9})
    assert verdict.main(args) == 3
    assert verdict.main(args + ["--tier", "low", "--record", "20261109-000000-1"]) == 3


def test_a_record_whose_code_changed_during_its_run_is_invalid(tmp_path):
    proj, key = _project(tmp_path, _four())
    _record(tmp_path, proj, key, "20261102-000000-1", {"c1"})
    meta = tmp_path / "evals" / "20261102-000000-1" / "meta.json"
    meta.write_text(json.dumps(json.loads(meta.read_text()) | {"manifest_changed": ["execution"]}))
    doc = verdict.build(tmp_path / "evals", "low", {"committed_lb_min": 0.1}, None, record="20261102-000000-1")
    assert doc["verdict"] == "invalid" and doc["why"] == ["the execution code changed while record 20261102-000000-1 ran: it measures no one version"]
    assert verdict.EXIT["invalid"] == 4


def _strict_quality(tmp_path, name, committed, decisions=None):
    q = tmp_path / "evals" / name / "quality" / "quality.json"
    _write(q, {"summary": {"strict": {"committed": committed, **({"decisions": decisions} if decisions else {})}}})


def test_strict_gates_take_a_value_only_when_every_item_is_judged(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    bars = {"committed_strict_lb_min": 0.5}
    _strict_quality(tmp_path, "20261102-000000-1", {"n": 4, "accepted": 4, "unresolved": 1, "lb95": 0.51, "certified": False})
    doc = verdict.build(evals, "low", bars, "20261101-000000-1")
    row = next(g for g in doc["gates"] if g["name"] == "committed strict")
    assert row["status"] == "unknown" and doc["verdict"] == "inconclusive"
    assert doc["why"] == ["gate committed strict has a bar but no value"]
    _strict_quality(tmp_path, "20261102-000000-1", {"n": 4, "accepted": 3, "unresolved": 0, "lb95": 0.3, "certified": True,
                                                   "validated": True})
    doc = verdict.build(evals, "low", bars, "20261101-000000-1")
    assert next(g for g in doc["gates"] if g["name"] == "committed strict")["status"] == "fail" and doc["verdict"] == "hold"


def test_a_record_with_no_judged_quality_has_unknown_strict_gates(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    doc = verdict.build(evals, "low", {"decisions_strict_lb_min": 0.5}, "20261101-000000-1")
    assert next(g for g in doc["gates"] if g["name"] == "decisions strict")["status"] == "unknown"


def test_a_completely_judged_strict_figure_from_an_unvalidated_judge_has_no_value(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    _strict_quality(tmp_path, "20261102-000000-1", {"n": 4, "accepted": 4, "unresolved": 0, "lb95": 0.51, "certified": True,
                                                   "validated": False, "flag": "not validated"})
    doc = verdict.build(evals, "low", {"committed_strict_lb_min": 0.5}, "20261101-000000-1")
    row = next(g for g in doc["gates"] if g["name"] == "committed strict")
    assert row["status"] == "unknown" and row["why"] == "judge not validated (not validated)" and doc["verdict"] == "inconclusive"


def test_a_strict_figure_with_no_validation_recorded_has_no_value(tmp_path):
    evals = _pair(tmp_path, _four(), {"c1", "c2"}, {"c1", "c2"})
    _strict_quality(tmp_path, "20261102-000000-1", {"n": 4, "accepted": 4, "unresolved": 0, "lb95": 0.51, "certified": True})
    doc = verdict.build(evals, "low", {"committed_strict_lb_min": 0.5}, "20261101-000000-1")
    assert next(g for g in doc["gates"] if g["name"] == "committed strict")["status"] == "unknown"
