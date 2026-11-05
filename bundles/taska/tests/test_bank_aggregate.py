"""bank.aggregate / compare / compare_models over run folders built by hand. Invented data only; no model calls."""
import json
import subprocess
import sys

import pytest

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402


def run_cli(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def T(task, n, ok, status="done", caps=("committed",), latency=100, cost=0.5, out=1000, errors=None, results=None):
    res = results or ["pass" if ok else "fail"]
    return {"task": task, "trial": n, "status": status, "pass": ok and status == "done",
            "checks": [{"id": f"{task}-{i}", "type": "must_create", "caps": list(caps), "result": r}
                       for i, r in enumerate(res)],
            "latency_s": latency, "cost_usd": cost,
            "tokens": {"input": 10, "output": out, "cache_read": 0, "cache_write": 0},
            "errors": errors or {"tool": 0, "refusals": 0, "blocked": 0, "acks_without_reread": 0}}


def run_dir(root, name, trials, tier="low", sha="abc", version="v1", code="c1", kind="trials", prompts=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"kind": kind, "bank_version": version, "bank_sha256": sha,
                                            "code_version": code, "tier": tier, "model": f"m-{tier}", "k": 3,
                                            "tasks": sorted({t["task"] for t in trials})} | ({"prompts_sha256": prompts} if prompts else {})))
    for t in trials:
        f = d / "trials" / f"{t['task']}-{t['trial']}"
        f.mkdir(parents=True)
        (f / "grade.json").write_text(json.dumps(t))
    return d


def test_pass_at_k_and_hat_k():
    a = B.aggregate([T("a", 1, True), T("a", 2, False), T("a", 3, True)])
    row = a["tasks"]["a"]
    assert (row["pass_at_k"], row["pass_hat_k"], row["trials"], row["valid"]) == (1, 0, 3, 3)


def test_invalid_trials_are_counted_not_missed():
    a = B.aggregate([T("a", 1, True), T("a", 2, False, status="timeout"), T("a", 3, False, status="error"),
                     T("b", 1, False, status="invalid")])
    row = a["tasks"]["a"]
    assert (row["valid"], row["trials"], row["pass_hat_k"], row["timeout"], row["error"]) == (1, 3, 1, 1, 1)
    assert a["tasks"]["b"]["pass_at_k"] is None and a["tasks"]["b"]["pass_hat_k"] is None
    assert a["tasks"]["b"]["invalid"] == 1
    assert a["overall"]["timeout"] == 1 and a["overall"]["error"] == 1 and a["overall"]["invalid"] == 1
    assert a["overall"]["pass_hat_k"] == {"ok": 1, "n": 1}


def test_capability_shares_skip_na_and_invalid():
    a = B.aggregate([T("a", 1, True, caps=("owners",), results=["pass", "fail", "n/a"]),
                     T("a", 2, False, status="timeout", caps=("owners",), results=["fail"])])
    assert a["caps"]["owners"] == {"ok": 1, "n": 2}


def test_stats_nearest_rank_and_error_rates():
    ts = [T("a", i + 1, True, latency=l, errors={"tool": e, "refusals": 0, "blocked": 0, "acks_without_reread": 0})
          for i, (l, e) in enumerate([(10, 0), (20, 2), (30, 0), (40, 2)])]
    row = B.aggregate(ts)["tasks"]["a"]
    assert row["latency_s"] == {"mean": 25, "median": 25, "p90": 40, "n": 4}
    assert row["error_rates"]["tool"] == 1.0


def test_budget_breaches():
    ts = [T("a", 1, True, latency=700, cost=0.1), T("a", 2, True, latency=60, cost=2.0), T("a", 3, True)]
    a = B.aggregate(ts, {"a": {"budget": {"minutes": 10, "cost_usd": 1.0}}})
    assert sorted((b["trial"], b["what"]) for b in a["breaches"]) == [(1, "minutes"), (2, "cost_usd")]
    assert B.aggregate(ts)["breaches"] == []


def test_load_run(tmp_path):
    d = run_dir(tmp_path, "r", [T("a", 1, True), T("a", 2, False)])
    head, trials = B.load_run(d)
    assert head["tier"] == "low" and len(trials) == 2


def test_compare_refuses_mismatch_naming_both(tmp_path):
    a = run_dir(tmp_path, "a", [T("t", 1, True)], sha="aaa")
    b = run_dir(tmp_path, "b", [T("t", 1, True)], sha="bbb")
    with pytest.raises(ValueError, match="aaa.*bbb"):
        B.compare(a, b)
    base = run_dir(tmp_path, "base", [T("t", 1, True)])
    c = run_dir(tmp_path, "c", [T("t", 1, True)], tier="med")
    with pytest.raises(ValueError, match="low.*med"):
        B.compare(base, c)
    r = run_dir(tmp_path, "r", [T("t", 1, True)], kind="replay")
    with pytest.raises(ValueError, match="trials.*replay"):
        B.compare(base, r)


def test_compare_lost_gained(tmp_path):
    a = run_dir(tmp_path, "a", [T("x", 1, True), T("y", 1, False), T("z", 1, True)])
    b = run_dir(tmp_path, "b", [T("x", 1, False), T("y", 1, True), T("z", 1, True)])
    c = B.compare(a, b)
    assert c["lost"] == ["x"] and c["gained"] == ["y"] and c["p"] == 1.0


def test_compare_models(tmp_path):
    a = run_dir(tmp_path, "a", [T("x", 1, False, latency=100, cost=1.0, out=100, caps=("owners",)),
                                T("x", 2, True, latency=200, cost=1.0, out=100, caps=("owners",)),
                                T("y", 1, True, caps=("owners",))], tier="low")
    b = run_dir(tmp_path, "b", [T("x", 1, True, latency=50, cost=3.0, out=300, caps=("owners",)),
                                T("x", 2, True, latency=70, cost=3.0, out=300, caps=("owners",)),
                                T("y", 1, True, caps=("owners",))], tier="med")
    m = B.compare_models(a, b)
    assert m["a"] == {"tier": "low", "model": "m-low"} and m["b"]["tier"] == "med"
    x = next(t for t in m["tasks"] if t["task"] == "x")
    assert x["a"]["pass_hat_k"] == 0 and x["b"]["pass_hat_k"] == 1
    assert x["delta"]["pass_hat_k"] == 1 and x["delta"]["pass_at_k"] == 0
    assert x["delta"]["latency_median_s"] == 60 - 150 and x["delta"]["cost_median_usd"] == 2.0
    assert x["delta"]["tokens_median"] == 300 + 10 - (100 + 10)
    assert "tool" in x["delta"]["error_rates"]
    assert m["caps"]["owners"]["a"] == {"ok": 2, "n": 3} and m["caps"]["owners"]["b"] == {"ok": 3, "n": 3}
    assert m["caps"]["owners"]["delta"]["share"] == round(1 - 2 / 3, 4)
    assert x["a"]["valid"] == 2 and x["a"]["trials"] == 2 and x["a"]["latency_n"] == 2
    assert m["overall"]["a"]["pass_hat_k"] == {"ok": 1, "n": 2, "share": 0.5}
    assert m["overall"]["b"]["pass_at_k"]["share"] == 1.0
    assert m["overall"]["delta"] == {"pass_at_k": 0.0, "pass_hat_k": 0.5}
    assert m["paired"] == 2
    assert m["overall"]["lost"] == 0 and m["overall"]["gained"] == 1 and m["p"] == 1.0


def test_compare_models_refusals(tmp_path):
    a = run_dir(tmp_path, "a", [T("t", 1, True)], tier="low")
    for name, kw, pat in [("s", {"sha": "zzz", "tier": "med"}, "abc.*zzz"),
                          ("c", {"code": "c2", "tier": "med"}, "c1.*c2"),
                          ("t", {"tier": "low"}, "same tier"),
                          ("k", {"tier": "med", "kind": "replay"}, "trials")]:
        b = run_dir(tmp_path, name, [T("t", 1, True)], **kw)
        with pytest.raises(ValueError, match=pat):
            B.compare_models(a, b)


def test_cli(tmp_path):
    a = run_dir(tmp_path, "a", [T("t", 1, True)], tier="low")
    b = run_dir(tmp_path, "b", [T("t", 1, True)], tier="low", sha="other")
    r = run_cli(str(EVALS / "bank.py"), "aggregate", "--run", str(a))
    assert r.returncode == 0 and json.loads(r.stdout)["tasks"]["t"]["pass_hat_k"] == 1
    assert (a / "bank.json").exists()
    r = run_cli(str(EVALS / "bank.py"), "compare", "--a", str(a), "--b", str(b))
    assert r.returncode == 2 and "other" in (r.stdout + r.stderr)
    r = run_cli(str(EVALS / "bank.py"), "compare-models", "--a", str(a), "--b", str(b))
    assert r.returncode == 2


def test_nothing_silently_dropped(tmp_path):
    a = run_dir(tmp_path, "a", [T("x", 1, True), T("y", 1, True), T("only_a", 1, True), T("nv", 1, True)])
    b = run_dir(tmp_path, "b", [T("x", 1, False), T("y", 1, True), T("only_b", 1, True),
                                T("nv", 1, False, status="timeout")])
    c = B.compare(a, b)
    assert c["only_in_a"] == ["only_a"] and c["only_in_b"] == ["only_b"]
    assert c["no_valid_b"] == ["nv"] and c["no_valid_a"] == []
    assert c["paired"] == 2 and c["unchanged"] == 1 and c["lost"] == ["x"]
    a2 = run_dir(tmp_path, "a2", [T("x", 1, True), T("nv", 1, True), T("only_a", 1, True)], tier="low")
    b2 = run_dir(tmp_path, "b2", [T("x", 1, True), T("nv", 1, False, status="error"), T("only_b", 1, True)],
                 tier="med")
    m = B.compare_models(a2, b2)
    assert m["only_in_a"] == ["only_a"] and m["only_in_b"] == ["only_b"] and m["no_valid_b"] == ["nv"]
    assert m["paired"] == 1
    nv = next(t for t in m["tasks"] if t["task"] == "nv")
    assert nv["b"]["valid"] == 0 and nv["delta"]["pass_hat_k"] is None and nv["delta"]["latency_median_s"] is None


def test_k_must_match(tmp_path):
    a = run_dir(tmp_path, "a", [T("t", 1, True)], tier="low")
    b = run_dir(tmp_path, "b", [T("t", 1, True)], tier="med")
    h = json.loads((b / "run.json").read_text())
    (b / "run.json").write_text(json.dumps(h | {"k": 5}))
    with pytest.raises(ValueError, match="k.*3.*5"):
        B.compare_models(a, b)
    b2 = run_dir(tmp_path, "b2", [T("t", 1, True)], tier="low")
    (b2 / "run.json").write_text(json.dumps(h | {"k": 5, "tier": "low"}))
    with pytest.raises(ValueError, match="k.*3.*5"):
        B.compare(a, b2)


def test_missing_trials_reported(tmp_path):
    d = run_dir(tmp_path, "r", [T("a", 1, True), T("a", 3, True)])
    head, trials = B.load_run(d)
    assert head["missing"] == ["a-2"] and len(trials) == 2
    assert B.aggregate(trials, missing=head["missing"])["overall"]["missing"] == 1
    assert B.aggregate(trials)["overall"]["missing"] == 0


def test_compare_models_refuses_runs_made_with_different_prompts(tmp_path):
    a = run_dir(tmp_path, "a", [T("t", 1, True)], tier="low", prompts="p1")
    b = run_dir(tmp_path, "b", [T("t", 1, True)], tier="med", prompts="p2")
    with pytest.raises(ValueError, match="prompts_sha256.*p1.*p2"):
        B.compare_models(a, b)
    ok = run_dir(tmp_path, "ok", [T("t", 1, True)], tier="med", prompts="p1")
    assert B.compare_models(a, ok)["paired"] == 1


def C(cid, result, status="draft", typ="must_create", **extra):
    return {"id": cid, "type": typ, "caps": ["committed"], "result": result, "status": status, **extra}


def TC(task, n, checks, **kw):
    t = T(task, n, True, **kw)
    t["checks"] = checks
    t["pass"] = all(c["result"] != "fail" for c in checks)
    return t


def test_pending_ruling_checks_are_na_in_pass_and_counted_apart():
    t = TC("a", 1, [C("a-0", "pass"), C("a-1", "n/a", pending_ruling=True, ruled_result="pass"),
                    C("a-2", "n/a", pending_ruling=True, ruled_result="fail")])
    assert t["pass"] is True
    a = B.aggregate([t])
    assert a["pending_ruling"] == {"ok": 1, "n": 2}
    assert a["caps"]["committed"] == {"ok": 1, "n": 1}   # never in shares
    assert a["overall"]["pass_hat_k"] == {"ok": 1, "n": 1}


def test_only_golden_checks_decide_the_gate():
    t1 = TC("a", 1, [C("a-0", "pass", "golden"), C("a-1", "fail", "draft")])
    t2 = TC("a", 2, [C("a-0", "pass", "golden"), C("a-1", "pass", "draft")])
    t3 = TC("b", 1, [C("b-0", "fail", "reviewed")])
    a = B.aggregate([t1, t2, t3])
    assert a["tasks"]["a"]["pass_hat_k"] == 0 and a["tasks"]["a"]["gate"]["pass_hat_k"] == 1
    assert a["tasks"]["b"]["gate"] == "no gate"
    assert a["gate"]["pass_hat_k"] == {"ok": 1, "n": 1} and a["gate"]["no_gate"] == 1
    assert a["overall"]["pass_hat_k"] == {"ok": 0, "n": 2}


def test_an_outcome_check_counts_as_golden_and_its_failure_fails_the_gate():
    good = TC("a", 1, [C("a-0", "pass", "golden"), C("processed", "pass", typ="outcome", status=None)])
    bad = TC("a", 2, [C("a-0", "pass", "golden"), C("processed", "fail", typ="outcome", status=None)])
    only = TC("b", 1, [C("b-0", "pass", "draft"), C("processed", "fail", typ="outcome", status=None)])
    na = TC("c", 1, [C("c-0", "pass", "draft"), C("decisions_read", "n/a", typ="outcome", status=None)])
    a = B.aggregate([good, bad, only, na])
    assert a["tasks"]["a"]["gate"]["pass_hat_k"] == 0 and a["tasks"]["a"]["gate"]["pass_at_k"] == 1
    assert a["tasks"]["b"]["gate"]["pass_hat_k"] == 0   # an outcome alone is enough to gate
    assert a["tasks"]["c"]["gate"] == "no gate"


def test_by_class_reports_p50_p95_and_usd_per_correct_and_names_empty_classes():
    cls = lambda size, st="conversation": {"size": size, "structure": st, "owners": "2–3", "audience": "internal"}
    tasks = {"a": {"class": cls("S")}, "b": {"class": cls("S")}, "c": {"class": cls("M", "one block")}, "old": {}}
    trials = [dict(TC("a", 1, [C("a-0", "pass")], cost=1.0), turns=10, latency_s=60),
              dict(TC("a", 2, [C("a-0", "pass")], cost=3.0), turns=30, latency_s=180),
              dict(TC("b", 1, [C("b-0", "fail")], cost=2.0), turns=20, latency_s=120),
              TC("c", 1, [C("c-0", "fail")], cost=4.0), TC("old", 1, [C("o", "pass")])]
    a = B.aggregate(trials, tasks)
    s = a["by_class"]["size"]["S"]
    assert s["tasks"] == 2 and s["valid"] == 3
    assert s["turns"] == {"p50": 20, "p95": 30, "n": 3} and s["minutes"]["p50"] == 2.0 and s["minutes"]["n"] == 3
    assert s["tokens"]["n"] == 3 and s["tokens"]["p95"] == 1010
    assert s["usd_per_passed_must_create"] == {"value": 3.0, "cost_usd": 6.0, "passed": 2, "n": 3}
    assert s["pass_hat_k"] == {"ok": 1, "n": 2}
    assert a["by_class"]["structure"]["one block"]["tasks"] == 1
    assert a["by_class"]["size"]["M"]["usd_per_passed_must_create"]["value"] is None
    assert a["unclassed"] == {"tasks": 1, "ids": ["old"]} and "unclassed" not in a["by_class"]
    assert a["empty_classes"]["size"] == ["L", "XL"] and a["empty_classes"]["audience"] == ["client"]


def test_a_one_block_failure_does_not_move_overall():
    one = {"size": "S", "structure": "one block", "owners": "0–1", "audience": "internal"}
    tasks = {"a": {"class": dict(one, structure="conversation")}, "c": {"class": one}}
    base = [TC("a", 1, [C("a-0", "pass")])]
    ok = B.aggregate(base + [TC("c", 1, [C("c-0", "pass")])], tasks)
    bad = B.aggregate(base + [TC("c", 1, [C("c-0", "fail")])], tasks)
    for k in ("pass_at_k", "pass_hat_k"):
        assert ok["overall"][k] == bad["overall"][k] == {"ok": 1, "n": 1}
    assert bad["by_class"]["structure"]["one block"]["pass_hat_k"] == {"ok": 0, "n": 1}


def test_an_old_bank_aggregates_with_its_previous_fields_unchanged():
    old = [T("a", 1, True), T("a", 2, False), T("b", 1, True)]
    for t in old:
        for c in t["checks"]:
            c.pop("status", None)
    a = B.aggregate(old, {"a": {"budget": {}}, "b": {}})
    assert a["gate"] is None and a["unclassed"] == {"tasks": 2, "ids": ["a", "b"]}
    assert set(a["tasks"]["a"]) == {"trials", "valid", "timeout", "error", "invalid", "pass_at_k", "pass_hat_k",
                                    "latency_s", "cost_usd", "tokens", "error_rates"}
    assert (a["tasks"]["a"]["pass_at_k"], a["tasks"]["a"]["pass_hat_k"]) == (1, 0)
    assert a["overall"] == {"tasks": 2, "trials": 3, "missing": 0, "valid": 3, "timeout": 0, "error": 0, "invalid": 0,
                            "pass_at_k": {"ok": 2, "n": 2}, "pass_hat_k": {"ok": 1, "n": 2}}
    assert a["caps"] == {"committed": {"ok": 2, "n": 3}} and a["breaches"] == []


def test_write_bank_rejects_bad_class_status_difficulty_and_several_afters(tmp_path):
    good = {"task": "a", "evidence": "e", "transcript_sha": "x", "class": {"size": "S", "audience": "client"},
            "must_create": [{"id": "k", "span": [0, 5], "status": "golden", "difficulty": "hard", "pending_ruling": True}]}
    B.write_bank(tmp_path / "b", [good, {"task": "b", "after": ["a"]}], "v1", "2026-11-03", "n")
    for bad in ({"class": {"size": "XXL"}}, {"class": {"mood": "S"}}, {"after": ["nope"]}, {"after": ["a", "a"]},
                {"must_create": [{"id": "k", "status": "gold"}]},
                {"must_create": [{"id": "k", "difficulty": "trivial"}]},
                {"must_create": [{"id": "k", "pending_ruling": "yes"}]}):
        with pytest.raises(ValueError):
            B.write_bank(tmp_path / "c", [{"task": "a", **bad}], "v1", "2026-11-03", "n")
    with pytest.raises(ValueError, match="one task"):
        B.write_bank(tmp_path / "d", [{"task": "a"}, {"task": "b"}, {"task": "c", "after": ["a", "b"]}], "v1", "d", "n")


SIZE = lambda size, st="conversation": {"size": size, "structure": st, "owners": "2–3", "audience": "internal"}


def U(task, n, turns=None, latency=60, cost=0.5, agent_turns=None, tool_calls=None, tool_errors=0, ok=True, status="done"):
    t = TC(task, n, [C(f"{task}-0", "pass" if ok else "fail", "golden")], latency=latency, cost=cost, status=status,
           errors={"tool": tool_errors, "refusals": 0, "blocked": 0, "acks_without_reread": 0})
    t["tokens"] = {"input": 100 * n, "output": 10 * n, "cache_read": 1000 * n, "cache_write": 50 * n}
    for key, v in (("turns", turns), ("agent_turns_max", agent_turns), ("tool_calls", tool_calls)):
        if v is not None:
            t[key] = v
    return t


def test_usage_comes_first_per_task_and_class_and_dollars_are_a_secondary_block():
    tasks = {"a": {"class": SIZE("S")}, "b": {"class": SIZE("M")}}
    a = B.aggregate([U("a", 1, turns=10, latency=60), U("a", 2, turns=30, latency=180), U("b", 1, latency=120)], tasks)
    assert list(a)[0] == "usage"
    ua = a["usage"]["tasks"]["a"]
    assert set(ua["tokens"]) == {"input", "output", "cache_read", "cache_write"}
    assert ua["tokens"]["cache_read"] == {"p50": 1500.0, "p95": 2000, "n": 2}
    assert ua["turns"] == {"p50": 20.0, "p95": 30, "n": 2} and ua["minutes"] == {"p50": 2.0, "p95": 3.0, "n": 2}
    assert a["usage"]["tasks"]["b"]["turns"] == {"p50": None, "p95": None, "n": 0}   # not measured, never zero
    assert a["usage"]["by_class"]["size"]["S"]["minutes"]["n"] == 2
    assert a["usage"]["round"]["tokens"] == {"input": 400, "output": 40, "cache_read": 4000, "cache_write": 200}
    assert a["usage"]["round"]["tokens_n"] == {"input": 3, "output": 3, "cache_read": 3, "cache_write": 3}
    assert a["usage"]["round"]["minutes"] == {"value": None, "n": 0}
    assert list(a).index("api_equivalent_usd") > list(a).index("usage")
    assert a["api_equivalent_usd"]["total"] == 1.5 and a["api_equivalent_usd"]["per_meeting"]["n"] == 3
    timed = B.aggregate([U("a", 1)], tasks, round_minutes=4.5)
    assert timed["usage"]["round"]["minutes"] == {"value": 4.5, "n": 1}


def test_a_one_block_task_is_its_own_class_outside_the_main_bar():
    tasks = {"a": {"class": SIZE("S")}, "c": {"class": SIZE("S", "one block")}}
    ok = B.aggregate([U("a", 1), U("c", 1)], tasks)
    bad = B.aggregate([U("a", 1), U("c", 1, ok=False)], tasks)
    for k in ("pass_at_k", "pass_hat_k"):
        assert ok["overall"]["main"][k] == bad["overall"]["main"][k] == {"ok": 1, "n": 1}
    assert bad["overall"]["one_block"]["pass_hat_k"] == {"ok": 0, "n": 1}
    assert ok["overall"]["one_block"]["pass_hat_k"] == {"ok": 1, "n": 1}
    assert "one block" in bad["usage"]["by_class"]["structure"]


BARS = {"turns_p95_max": {"S": 25, "M": 40}, "minutes_p95_max": {"S": 1.0}, "turn_cap": 15, "complete_required": True,
        "minutes_per_meeting_avg_max": 3.0, "tool_error_rate_max": 0.10,
        "regression": {"median_rise_max": 0.20, "recall_drop_max": 0}}


def _rows(a, key):
    return [r for r in a["bars"]["rows"] if r["key"] == key]


def test_pass_rows_read_not_set_without_keys():
    tasks = {"a": {"class": SIZE("S")}}
    a = B.aggregate([U("a", 1, turns=10, agent_turns=8, tool_calls=20)], tasks)
    keys = {r["key"] for r in a["bars"]["rows"]}
    assert set(B.BAR_KEYS) - set(B.VERDICT_BARS) <= keys
    assert {r["result"] for r in a["bars"]["rows"] if r["kind"] != "value"} == {"not set"}
    assert all(r["bar"] is None for r in a["bars"]["rows"] if r["kind"] != "value")
    assert a["bars"]["run"] == "not set"


def test_bars_give_pass_rows_and_a_hard_bar_fails_the_run():
    tasks = {"a": {"class": SIZE("S")}, "b": {"class": SIZE("M")}, "c": {"class": SIZE("S", "one block")}}
    trials = [U("a", 1, turns=10, latency=30, agent_turns=8, tool_calls=20, tool_errors=1),
              U("a", 2, turns=20, latency=90, agent_turns=9, tool_calls=20, tool_errors=1),
              U("b", 1, turns=30, latency=100, agent_turns=12, tool_calls=10),
              U("c", 1, turns=99, latency=300, agent_turns=99, tool_calls=10)]
    a = B.aggregate(trials, tasks, bars=BARS)
    turns = {r["scope"]: r for r in _rows(a, "turns_p95_max")}
    assert turns["S"]["result"] == "pass" and turns["S"]["value"] == 20 and turns["S"]["n"] == 2   # one block is outside
    assert turns["M"]["result"] == "pass"
    minutes = {r["scope"]: r for r in _rows(a, "minutes_p95_max")}
    assert minutes["S"]["result"] == "fail" and minutes["S"]["value"] == 1.5
    assert minutes["M"]["result"] == "not set"
    assert {r["result"] for r in _rows(a, "tokens_p95_max")} == {"not set"}
    cap = _rows(a, "turn_cap")[0]
    assert cap["result"] == "fail" and cap["hard"] and cap["value"] == 99
    assert _rows(a, "complete_required")[0]["result"] == "pass"
    assert _rows(a, "tool_error_rate_max")[0]["value"] == 0.0333 and _rows(a, "tool_error_rate_max")[0]["result"] == "pass"
    assert _rows(a, "minutes_per_meeting_avg_max")[0]["result"] == "pass"
    assert a["bars"]["run"] == "fail"
    calm = B.aggregate(trials[:3], tasks, bars=BARS)
    assert calm["bars"]["run"] == "pass"
    broken = B.aggregate(trials[:3] + [U("b", 2, status="error")], tasks, bars=BARS)
    assert _rows(broken, "complete_required")[0]["result"] == "fail" and broken["bars"]["run"] == "fail"


def test_a_field_the_trace_does_not_emit_is_no_data_never_zero():
    a = B.aggregate([U("a", 1)], {"a": {"class": SIZE("S")}}, bars=BARS)
    cap = _rows(a, "turn_cap")[0]
    assert cap["result"] == "no data" and cap["n"] == 0 and cap["value"] is None
    assert _rows(a, "tool_error_rate_max")[0]["result"] == "no data"
    assert a["bars"]["run"] == "unverified"   # a hard bar without data neither fails nor passes the run


def test_regression_rows_compare_the_same_meetings_with_the_baseline():
    tasks = {"a": {"class": SIZE("S")}, "b": {"class": SIZE("S")}, "new": {"class": SIZE("S")}}
    base = [U("a", 1, turns=10, latency=60), U("b", 1, turns=10, latency=60)]
    cur = [U("a", 1, turns=13, latency=60), U("b", 1, turns=13, latency=60, ok=False), U("new", 1, turns=90, latency=900)]
    a = B.aggregate(cur, tasks, bars=BARS, baseline=base)
    rise = {(r["scope"], r["measure"]): r for r in _rows(a, "regression.median_rise_max")}
    assert rise[("S", "turns")]["value"] == 0.3 and rise[("S", "turns")]["result"] == "fail"
    assert rise[("S", "turns")]["n"] == 2   # the new meeting is not in the baseline
    assert rise[("S", "minutes")]["value"] == 0.0 and rise[("S", "minutes")]["result"] == "pass"
    drop = _rows(a, "regression.recall_drop_max")[0]
    assert drop["value"] == 0.5 and drop["result"] == "fail"
    none = B.aggregate(cur, tasks, bars=BARS)
    assert {r["result"] for r in _rows(none, "regression.median_rise_max")} == {"no baseline"}


def test_value_rows_are_information_only():
    a = B.aggregate([U("a", 1, turns=20, cost=1.0)], {"a": {"class": SIZE("S")}}, bars=BARS)
    vals = {r["key"]: r for r in a["bars"]["rows"] if r["kind"] == "value"}
    assert vals["correct_per_10_turns"]["value"] == 0.5 and vals["correct_per_10_turns"]["result"] == "info"
    assert vals["api_equivalent_usd_per_correct_item"]["value"] == 1.0


def test_expected_minutes_runs_chains_in_order_and_trials_in_parallel():
    m = {"S": 0.5, "M": 1.0, "L": 2.0}
    ts = [{"task": "a", "class": SIZE("S")}, {"task": "b", "class": SIZE("M")}, {"task": "c", "class": SIZE("L")},
          {"task": "d", "class": SIZE("S"), "after": ["c"]}]
    assert B.expected_minutes(ts, 1, 1, m) == 4.0
    assert B.expected_minutes(ts, 2, 1, m) == 8.0
    assert B.expected_minutes(ts, 1, 4, m) == 2.5   # the chain c → d is the longest job
    assert B.expected_minutes(ts, 2, 2, m) == 4.0
    with pytest.raises(ValueError, match="XL"):
        B.expected_minutes([{"task": "x", "class": SIZE("XL")}], 1, 1, m)
    with pytest.raises(ValueError, match="no size"):
        B.expected_minutes([{"task": "y"}], 1, 1, m)


def test_round_tokens_count_per_kind_and_a_missing_kind_is_no_data():
    t1, t2 = U("a", 1), U("a", 2)
    del t1["tokens"]["cache_write"]
    del t2["tokens"]["cache_write"]
    t2["tokens"].pop("cache_read")
    r = B.aggregate([t1, t2])["usage"]["round"]
    assert r["tokens"]["cache_write"] is None and r["tokens_n"]["cache_write"] == 0
    assert r["tokens"]["cache_read"] == 1000 and r["tokens_n"]["cache_read"] == 1


def test_p95_rows_under_20_trials_are_provisional_and_rows_name_their_unit():
    tasks = {"a": {"class": SIZE("S")}}
    a = B.aggregate([U("a", 1, turns=10, agent_turns=3), U("a", 2, turns=12)], tasks, bars=BARS, baseline=[U("a", 1, turns=10)])
    turns = _rows(a, "turns_p95_max")[0]
    assert turns["provisional"] is True and turns["unit"] == "trials"
    many = B.aggregate([U("a", i, turns=10) for i in range(1, 21)], tasks, bars=BARS)
    assert "provisional" not in _rows(many, "turns_p95_max")[0]
    cap = _rows(a, "turn_cap")[0]
    assert (cap["n"], cap["of"]) == (1, 2)
    assert {r["unit"] for r in a["bars"]["rows"] if r["kind"] == "regression"} == {"tasks"}


def test_load_bars_refuses_unknown_keys_and_wrong_types(tmp_path):
    (tmp_path / "evals").mkdir()
    f = tmp_path / "evals" / "bars.json"
    f.write_text(json.dumps(BARS))
    assert B.load_bars(tmp_path)[0] == BARS
    for bad, name in (({"turn_capp": 80}, "turn_capp"), ({"turn_cap": "80"}, "turn_cap"),
                      ({"turns_p95_max": {"XXL": 3}}, "XXL"), ({"complete_required": 1}, "complete_required"),
                      ({"regression": {"median_rise": 0.2}}, "median_rise"), ({"tool_error_rate_max": True}, "tool_error_rate_max")):
        f.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match=name):
            B.load_bars(tmp_path)
    f.write_text(json.dumps({"turn_cap": "80"}))
    d = run_dir(tmp_path, "r", [T("a", 1, True)])
    for cmd in ("aggregate", "page"):
        r = run_cli(str(EVALS / "bank.py"), cmd, "--run", str(d), "--project", str(tmp_path))
        assert r.returncode == 2 and "turn_cap" in r.stderr


def test_a_baseline_from_another_tier_or_bank_is_refused(tmp_path):
    d = run_dir(tmp_path, "r", [T("a", 1, True)])
    base = run_dir(tmp_path, "b", [T("a", 1, True)], tier="high")
    with pytest.raises(ValueError, match="tier"):
        B.load_baseline(d, base)
    other = run_dir(tmp_path, "c", [T("a", 1, True)], sha="zzz")
    with pytest.raises(ValueError, match="bank_sha256"):
        B.load_baseline(d, other)
    same = run_dir(tmp_path, "s", [T("a", 1, False)])
    assert B.load_baseline(d, same)[0]["pass"] is False
    r = run_cli(str(EVALS / "bank.py"), "aggregate", "--run", str(d), "--baseline", str(base))
    assert r.returncode == 2 and "tier" in r.stderr


def test_expected_minutes_matches_the_runs_longest_first_order():
    m = {"S": 0.5, "M": 1.0, "L": 2.0}
    ts = [{"task": "a", "class": SIZE("S")}, {"task": "b", "class": SIZE("M")}, {"task": "c", "class": SIZE("L")},
          {"task": "d", "class": SIZE("S"), "after": ["c"]}]
    assert B.expected_minutes(ts, 1, 2, m) == 2.5   # FIFO (a, b, c→d) would take 3.0
    assert [(g[0]["task"], n) for g, n in B.job_order(ts, 2, m)] == [("c", 1), ("c", 2), ("b", 1), ("b", 2), ("a", 1), ("a", 2)]


def test_one_block_minutes_key_on_size_and_structure_once_measured():
    ts = [{"task": "o", "class": SIZE("S", "one block")}]
    assert B.expected_minutes(ts, 1, 1, {"S": 0.5, "S|one block": 2.0}) == 2.0
    plan = B.budget_plan(ts, 1, 1, {"S": 0.5}, 10)
    assert plan["expected_minutes"] == 0.5 and plan["size_fallback"] == ["o"]
    assert plan["overhead"] == "overhead not measured" and plan["overhead_minutes"] is None
    timed = B.budget_plan(ts, 1, 1, {"S": 0.5}, 10, overhead=(0.75, 2))
    assert timed["expected_minutes"] == 1.25 and timed["overhead_minutes"] == 0.75 and "2 runs" in timed["overhead"]
    assert B.budget_plan(ts, 1, 1, {"S": 0.5}, 10, overhead=(-0.25, 1))["expected_minutes"] == 0.25


def _measured_run(root, name, tier, latencies, model=None, started=None, finished=None, parallel=None, tasks=("a",),
                  order="submitted"):
    d = root / name
    for (tid, n), lat in latencies.items():
        (d / "trials" / f"{tid}-{n}").mkdir(parents=True)
        (d / "trials" / f"{tid}-{n}" / "grade.json").write_text(json.dumps({"task": tid, "trial": n, "status": "done", "latency_s": lat}))
    head = {"kind": "trials", "tier": tier, "tasks": list(tasks), "k": max(n for _, n in latencies)}
    if order == "submitted":
        order = [[tid, n] for tid, n in latencies]
    for k, v in (("model", model), ("started", started), ("finished", finished), ("parallel", parallel), ("order", order)):
        if v is not None:
            head[k] = v
    (d / "run.json").write_text(json.dumps(head))
    return d


def test_measured_minutes_take_only_runs_of_the_same_tier_and_model(tmp_path):
    tasks = {"a": {"task": "a", "class": SIZE("S")}, "o": {"task": "o", "class": SIZE("S", "one block")}}
    _measured_run(tmp_path, "low", "low", {("a", 1): 240, ("o", 1): 60}, tasks=("a", "o"))
    _measured_run(tmp_path, "high", "high", {("a", 1): 600})
    _measured_run(tmp_path, "low-other-model", "low", {("a", 1): 900}, model="m-other")
    assert B.measured_minutes([tmp_path], tasks, "low", "m-low") == {"S": 4.0, "S|one block": 1.0}
    assert B.measured_minutes([tmp_path / "high"], tasks, "low", "m-low") == {}


def test_overhead_is_the_runs_wall_time_beyond_its_simulated_schedule(tmp_path):
    tasks = {"a": {"task": "a", "class": SIZE("S")}, "b": {"task": "b", "class": SIZE("S")}}
    _measured_run(tmp_path, "r1", "low", {("a", 1): 60, ("b", 1): 120}, tasks=("a", "b"), parallel=2,
                  started="2026-11-03T10:00:00", finished="2026-11-03T10:03:00")
    _measured_run(tmp_path, "r2", "low", {("a", 1): 60, ("b", 1): 60}, tasks=("a", "b"), parallel=1,
                  started="2026-11-03T11:00:00", finished="2026-11-03T11:03:00")
    _measured_run(tmp_path, "r3", "low", {("a", 1): 60}, started="2026-11-03T12:00:00", finished="2026-11-03T12:09:00")
    _measured_run(tmp_path, "r4", "high", {("a", 1): 60}, parallel=1, started="2026-11-03T12:00:00", finished="2026-11-03T12:09:00")
    assert B.measured_overhead([tmp_path], tasks, "low", None) == (1.0, 2)   # r3 has no parallel, r4 another tier
    assert B.measured_overhead([tmp_path / "r3"], tasks, "low", None) == (None, 0)


def test_overhead_replays_the_recorded_submission_order_and_skips_a_run_without_one(tmp_path):
    tasks = {t: {"task": t, "class": SIZE("S")} for t in "abc"}
    lat = {("a", 1): 60, ("b", 1): 60, ("c", 1): 180}   # uneven: c was expected short but took 3 minutes
    _measured_run(tmp_path, "r1", "low", lat, tasks=("a", "b", "c"), parallel=2, order=[["a", 1], ["b", 1], ["c", 1]],
                  started="2026-11-03T10:00:00", finished="2026-11-03T10:05:00")
    assert B.measured_overhead([tmp_path], tasks, "low", None) == (1.0, 1)   # longest first would say 2.0
    _measured_run(tmp_path, "r2", "low", lat, tasks=("a", "b", "c"), parallel=2, order=None,
                  started="2026-11-03T11:00:00", finished="2026-11-03T11:09:00")
    assert B.measured_overhead([tmp_path], tasks, "low", None) == (1.0, 1)


def test_tokens_per_round_count_only_trials_with_all_four_kinds():
    t1, t2, t3 = U("a", 1), U("a", 2), U("a", 3)
    del t2["tokens"]["cache_write"]
    t3["tokens"] = None
    a = B.aggregate([t1, t2, t3], bars={"tokens_per_round_max": 10 ** 6})
    row = _rows(a, "tokens_per_round_max")[0]
    assert (row["value"], row["n"], row["of"]) == (1160, 1, 3)
    none = B.aggregate([t2, t3], bars={"tokens_per_round_max": 10 ** 6})
    assert _rows(none, "tokens_per_round_max")[0]["result"] == "no data"


def test_the_plan_prints_overhead_with_its_sign():
    ts = [{"task": "a", "class": SIZE("S")}]
    assert B.budget_plan(ts, 1, 1, {"S": 1.0}, 10, overhead=(-0.4, 2))["overhead"] == "−0.4 min, measured over 2 runs"
    assert B.budget_plan(ts, 1, 1, {"S": 1.0}, 10, overhead=(0.75, 2))["overhead"] == "+0.75 min, measured over 2 runs"


VERDICT = {"committed_lb_min": 0.8, "decisions_lb_min": 0.5, "precision_lb_min": 0.9, "wasted_rate_max": 0.1}


def test_the_verdict_pages_bars_load_but_the_bank_never_judges_or_shows_them(tmp_path):
    (tmp_path / "evals").mkdir()
    f = tmp_path / "evals" / "bars.json"
    f.write_text(json.dumps(BARS | VERDICT))
    bars = B.load_bars(tmp_path)[0]
    assert bars == BARS | VERDICT and set(VERDICT) <= set(B.BAR_KEYS)
    a = B.aggregate([U("a", 1, turns=10)], {"a": {"class": SIZE("S")}}, bars=bars)
    assert not {r["key"] for r in a["bars"]["rows"]} & set(VERDICT)
    for bad in ({"precision_lb_min": 1.5}, {"wasted_rate_max": -0.1}, {"committed_lb_min": "0.8"},
                {"decisions_lb_min": True}):
        f.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match=next(iter(bad))):
            B.load_bars(tmp_path)
