"""evals/live.py: while an eval runs, a page answers the eval's questions from the copy's ledger, the key, the same-tier
baseline and this run's own session file: is it finding the work, where is it stuck, is the run healthy. Refreshed by
polling live.json (no page reload). Invented data only."""
import json
import sys
import time

from conftest import TASKA_ROOT, run_cli

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import live as LV  # noqa: E402
from taska_lib import config as C, ledger as L, store as S  # noqa: E402

FOUND = "I will reset the passwords today and send the new credentials to each of you by email"
MISSED = "Ravi will paint the loading dock markings before the safety inspection on Thursday morning"


def _setup(project):
    run_cli(project, "sync")
    day1, day2 = [i["id"] for i in run_cli(project, "pending")["items"]]
    store = C.load(project).store
    S.append(store, [
        {"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Onboarding"}},
        {"type": "create", "mid": "t1", "id": "T001", "kind": "task", "by": "agent", "evidence": day1, "date": "2026-03-02",
         "quote": FOUND, "fields": {"title": "Reset the passwords", "parent": "E01", "internal_owner": None,
                                    "client_owner": "Opal Brennick", "client_owed": True, "ac": [], "due": None, "plan": None}}])
    L.mark_processed(store, day1, "read")
    key = [{"cid": "k-001", "evidence": day1, "quote": FOUND, "title": "Reset passwords", "kind": "task", "commitment": "committed"},
           {"cid": "k-002", "evidence": day1, "quote": MISSED, "title": "Book the walkthrough", "kind": "task", "commitment": "committed"},
           {"cid": "k-003", "evidence": day2, "quote": "the warehouse walkthrough session happened yesterday and the team took a lot of photos",
            "title": "Walkthrough photos", "kind": "task", "commitment": "committed"}]
    return day1, day2, store, key


def _session(root, copy, store, day1, day2):
    (root / "p" / "s1" / "subagents").mkdir(parents=True)
    main = [{"cwd": str(copy), "type": "assistant", "timestamp": "2026-03-04T10:00:00Z",
             "message": {"model": "claude-test-1", "content": [{"type": "tool_use", "id": "a0", "name": "Agent", "input": {"description": "Extraction batch 1"}}]}}]
    (root / "p" / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in main) + "\n")
    sub = [
        {"cwd": str(copy), "type": "assistant", "timestamp": "2026-03-04T10:00:05Z", "message": {"model": "claude-test-1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": str(store / "evidence" / f"{day2}.md")}}]}},
        {"cwd": str(copy), "type": "user", "timestamp": "2026-03-04T10:00:06Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "1\tline one\n2\tline two"}]}},
        {"cwd": str(copy), "type": "assistant", "timestamp": "2026-03-04T10:01:00Z", "message": {"model": "claude-test-1", "content": [
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": f'{copy}/taska done --evidence {day2} --no-decisions "only the photos were discussed in this short part"'}}]}},
        {"cwd": str(copy), "type": "user", "timestamp": "2026-03-04T10:01:01Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": 'Exit code 1\n{"status": "decisions", "parts": [1], "hint": "read part 1"}'}]}}]
    (root / "p" / "s1" / "subagents" / "agent-b1.jsonl").write_text("\n".join(json.dumps(x) for x in sub) + "\n")
    (root / "p" / "s1" / "subagents" / "agent-b1.meta.json").write_text(json.dumps({"description": "Extraction batch 1"}))


def _snap(project, tmp_path, **kw):
    day1, day2, store, key = _setup(project)
    _session(tmp_path / "sessions", project, store, day1, day2)
    return day1, day2, LV.snapshot(project, key, started=0, now=time.time(), root=tmp_path / "sessions", **kw)


def test_the_scorecard_says_what_was_found_and_what_was_missed_per_meeting(project, tmp_path):
    day1, day2, snap = _snap(project, tmp_path)
    assert snap["schema"] == "eval-live/1" and snap["unit_label"] == "meeting"
    b7 = next(m for m in snap["metrics"] if m["code"] == "B7")
    assert (b7["ok"], b7["n"], b7["name"]) == (1, 3, "Committed work found")
    m1 = next(u for u in snap["units"] if u["id"] == day1)
    assert (m1["found"], m1["expected"], m1["state"]) == (1, 2, "done")
    assert [(x["id"], x["stage"]) for x in m1["missed"]] == [("k-002", "read, never proposed")]
    assert m1["missed"][0]["quote"] == MISSED


def test_a_meeting_waiting_at_a_gate_says_which_gate_and_what_the_agent_answered(project, tmp_path):
    day1, day2, snap = _snap(project, tmp_path)
    m2 = next(u for u in snap["units"] if u["id"] == day2)
    assert m2["state"] == "at the gate"
    assert m2["gate"] == [{"status": "decisions", "parts": [1], "answer": "only the photos were discussed in this short part"}]
    assert snap["stuck"]["reasons"] == {"decisions": 1}
    health = {h["name"]: h["value"] for h in snap["health"]}
    assert health["Gate refusals (by design)"] == 1 and health["Tool errors (not gates)"] == 0   # a refusal is not an error


def test_agents_are_named_by_their_batch(project, tmp_path):
    _, day2, snap = _snap(project, tmp_path)
    assert {a["name"] for a in snap["agents"]} == {"main", "Extraction batch 1"}
    batch = next(a for a in snap["agents"] if a["name"] == "Extraction batch 1")
    assert batch["last"].startswith("taska done") and day2[:10] in batch["last"]


def test_against_a_same_tier_baseline_lost_and_gained_items_are_named(project, tmp_path):
    baseline = {"record": "base-2", "duration_s": 600, "cost_usd": 5.0,
                "rows": [{"bucket": "B7", "key_id": "k-001", "verdict": "miss"}, {"bucket": "B7", "key_id": "k-002", "verdict": "hit"},
                         {"bucket": "B7", "key_id": "k-003", "verdict": "miss"}]}
    day1, _, snap = _snap(project, tmp_path, baseline=baseline)
    b = snap["baseline"]
    c7 = next(c for c in b["compared"] if c["code"] == "B7")
    assert b["record"] == "base-2" and next(m for m in snap["metrics"] if m["code"] == "B7")["baseline_ok"] == 1
    assert [x["id"] for x in c7["lost"]] == ["k-002"] and [x["id"] for x in c7["gained"]] == ["k-001"] and c7["p"] == 1.0
    assert next(u for u in snap["units"] if u["id"] == day1)["baseline_found"] == 1


def test_the_verdict_is_relative_to_the_baseline_and_says_no_bar_is_adopted(project, tmp_path):
    baseline = {"record": "base-2", "rows": [{"bucket": "B7", "key_id": "k-001", "verdict": "hit"}]}
    _, _, snap = _snap(project, tmp_path, baseline=baseline, exit_code=0, record="r1-2")
    assert snap["status"] == "finished" and snap["verdict"]["by_metric"]["B7"] == "within noise"
    assert "no pass bar is adopted" in snap["verdict"]["note"]


def test_while_running_there_is_no_verdict_and_without_a_baseline_the_page_says_so(project, tmp_path):
    _, _, snap = _snap(project, tmp_path)
    assert snap["verdict"] is None and snap["baseline"] is None


def test_the_page_polls_live_json_without_reloading_and_asks_the_three_questions(tmp_path):
    LV.write(tmp_path, {"status": "running"})
    page = (tmp_path / "index.html").read_text()
    assert "fetch('live.json'" in page and "15000" in page and "http-equiv" not in page
    assert page == LV.V.PAGE   # the generic page (evalview.py)
    assert json.loads((tmp_path / "live.json").read_text()) == {"status": "running"}


def test_a_session_that_ended_reads_as_finished_with_its_own_span(project, tmp_path):
    day1, day2, store, key = _setup(project)
    _session(tmp_path / "sessions", project, store, day1, day2)
    with open(tmp_path / "sessions" / "p" / "s1.jsonl", "a") as f:
        f.write(json.dumps({"cwd": str(project), "type": "cost-state", "totalCostUSD": 1.5, "timestamp": "2026-03-04T10:02:00Z"}) + "\n")
    snap = LV.snapshot(project, key, started=0, now=time.time(), root=tmp_path / "sessions")
    assert snap["status"] == "finished" and snap["elapsed_s"] == 120
    assert {h["name"]: h["value"] for h in snap["health"]}["Cost"] == "$1.50"


def test_the_watcher_stops_once_the_run_has_ended(project, tmp_path, monkeypatch):
    day1, day2, store, key = _setup(project)
    _session(tmp_path / "sessions", project, store, day1, day2)
    with open(tmp_path / "sessions" / "p" / "s1.jsonl", "a") as f:
        f.write(json.dumps({"cwd": str(project), "type": "cost-state", "totalCostUSD": 1.5}) + "\n")
    monkeypatch.setattr(LV, "SESSIONS", tmp_path / "sessions")
    (tmp_path / "key").mkdir()
    from key_tools import write_version
    kpath = write_version(tmp_path / "key", key, "v1", "2026-03-04", "t")
    assert LV.main(["--copy", str(project), "--key", str(kpath), "--out", str(tmp_path / "live"), "--every", "1"]) == 0
    assert json.loads((tmp_path / "live" / "live.json").read_text())["status"] == "finished"


def test_without_a_start_time_the_session_dates_the_run_and_its_models_name_the_tier(project, tmp_path, monkeypatch):
    day1, day2, store, key = _setup(project)
    _session(tmp_path / "sessions", project, store, day1, day2)
    monkeypatch.setattr(LV, "TIERS", {"low": "claude-x", "med": "claude-test-1", "high": "claude-y"})
    import datetime as dt
    now = dt.datetime(2026, 3, 4, 10, 5, tzinfo=dt.timezone.utc).timestamp()
    snap = LV.snapshot(project, key, started=0, now=now, root=tmp_path / "sessions")
    assert snap["elapsed_s"] == 300 and snap["model_tier"] == "med"


def test_a_page_written_by_older_code_is_replaced(tmp_path):
    (tmp_path / "index.html").write_text("<html>an older page</html>")
    LV.write(tmp_path, {"status": "running"})
    assert (tmp_path / "index.html").read_text() == LV.V.PAGE


def test_a_best_run_baseline_is_named_as_the_best_of_n(project, tmp_path):
    baseline = {"record": "base-2", "of": 4, "rows": [{"bucket": "B7", "key_id": "k-001", "verdict": "hit"}]}
    _, _, snap = _snap(project, tmp_path, baseline=baseline, exit_code=0, record="r1-2")
    assert snap["baseline"]["of"] == 4 and "the best of 4 runs so far" in snap["verdict"]["note"]


def test_provenance_compares_the_runs_own_code_not_the_code_that_scored_them(project, tmp_path):
    out = tmp_path / "records"
    for name, prj, commit in (("20260301-100000-2", "/runs/base-tier2-med", "a" * 40), ("20260302-100000-2", "/runs/base-tier2-med", "z" * 40),
                              ("20260303-100000-2", str(project), "b" * 40)):
        (out / name).mkdir(parents=True)
        (out / name / "meta.json").write_text(json.dumps({"project": prj, "git_commit": commit, "models": ["m"]}))
        (out / name / "score.json").write_text(json.dumps({"rows": []}))
    base = LV.load_baseline(out / "20260302-100000-2")   # a rescore of the base run
    _, _, snap = _snap(project, tmp_path, baseline=dict(base, of=1), record="20260303-100000-2", out_root=out)
    assert snap["provenance"]["baseline"]["code"].startswith("a" * 10)
    assert snap["provenance"]["this"]["code"].startswith("b" * 10)


def test_a_quality_check_becomes_caveats_that_say_what_the_counts_do_not():
    q = {"summary": {"found": [39, 51], "judged_items": [0, 44], "not_in_key_unjudged": 48, "independent_coverage": [3, 10]},
         "units": [{"items": [{"produced": {"matched_by": "position"}}, {"produced": {"matched_by": "quote"}}]}]}
    cs = LV.caveats(q, "../quality/index.html")
    assert cs[0]["href"] == "../quality/index.html"
    assert "39/51" in cs[0]["text"] and "1 only by position" in cs[0]["text"] and "0 of 44" in cs[0]["text"]
    assert "48" in cs[1]["text"] and "3 of 10" in cs[2]["text"]
    done = {"summary": {"found": [39, 51], "found_correct": [33, 51], "judged_items": [44, 44], "not_in_key_unjudged": 0,
                        "independent_coverage": [10, 10]}, "units": []}
    assert [c["text"] for c in LV.caveats(done, "q")] == ["Judged: 33 of 51 found correctly (a plain count says 39/51)."]


def test_the_precision_tile_is_judged_precision_once_validated_and_key_match_precision_with_its_caveat_before():
    metrics = [{"code": "B7", "name": "Committed", "ok": 25, "n": 31}, {"code": "B15", "name": "Key-match precision (lower bound)", "ok": 27, "n": 73}]
    before = {"summary": {"precision": {"validated": False, "flag": "not judged yet", "n": 0, "correct": 0}}}
    ms, cs = LV.precision_tile(metrics, before, "q")
    assert [m["code"] for m in ms] == ["B7", "B15"] and ms[1]["name"] == "Key-match precision (lower bound)"
    assert cs == [{"text": "Key-match precision is a lower bound: not judged yet.", "href": "q"}]
    after = {"summary": {"precision": {"validated": True, "flag": None, "n": 60, "correct": 47, "rate": 0.783, "lb95": 0.66,
                                       "meetings": 10, "per_meeting_to_delete": 1.3}}}
    ms, cs = LV.precision_tile(metrics, after, "q")
    assert [m["code"] for m in ms] == ["B7", "precision"] and (ms[1]["ok"], ms[1]["n"]) == (47, 60)   # one tile, never two
    assert cs == [{"text": "Judged precision 78% (at least 66%), 47 of 60 judged; 1.3 items to delete per meeting (10 meetings).", "href": "q"}]
    none = {"summary": {"precision": {"validated": True, "flag": None, "n": 0, "correct": 0, "rate": None, "lb95": None,
                                      "meetings": 10, "per_meeting_to_delete": None}}}
    assert "— items to delete" in LV.precision_tile(metrics, none, "q")[1][0]["text"]
    assert LV.precision_tile(metrics, None, "q") == (metrics, [])
