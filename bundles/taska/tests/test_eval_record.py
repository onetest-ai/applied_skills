"""evals/record.py and evals/compare.py: every eval run leaves a record that says which code, prompts, key and model
it measured, and two records are compared item by item, like for like, or not at all. Invented data only."""
import json
import re
import sys
from pathlib import Path

from conftest import RUN_DIR, TASKA_ROOT, run_cli

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import compare as CR  # noqa: E402
import record as ER  # noqa: E402
from key_tools import write_version  # noqa: E402


def test_mcnemar_reference():
    assert abs(CR.mcnemar_exact(2, 9) - 0.0654) < 0.0005
    assert CR.mcnemar_exact(0, 0) == 1.0


def test_compare_refuses_mismatched_keys():
    a, b = {"key_sha": "aaa", "snapshot_of": "x"}, {"key_sha": "bbb", "snapshot_of": "x"}
    assert "key" in CR.incompatible(a, b)
    assert CR.incompatible(a, dict(a)) is None


def test_different_snapshots_need_the_scope_flag():
    a, b = {"key_sha": "k", "snapshot_of": "x"}, {"key_sha": "k", "snapshot_of": "y"}
    assert "snapshot" in CR.incompatible(a, b)
    assert CR.incompatible(a, b, allow_scope_change=True) is None


def test_paired_diff_and_like_for_like():
    key = {"c1": {"evidence": "m1", "commitment": "committed", "kind": "task"},
           "c2": {"evidence": "m1", "commitment": "committed", "kind": "task"},
           "c3": {"evidence": "m2", "commitment": "committed", "kind": "task"}}
    A = [{"cid": "c1", "match": "node:T1"}, {"cid": "c2", "match": None}, {"cid": "c3", "match": None}]
    B = [{"cid": "c1", "match": None}, {"cid": "c2", "match": "node:T2"}, {"cid": "c3", "match": "node:T3"}]
    full = CR.paired(A, B, key)
    assert (full["gained"], full["lost"], full["still_missed"], full["same_hit"]) == (["c2", "c3"], ["c1"], [], [])
    like = CR.paired(A, B, key, meetings={"m1"})
    assert like["gained"] == ["c2"] and like["n"] == 2


def test_an_incomplete_run_is_compared_only_on_meetings_both_processed():
    key = {"c1": {"evidence": "m1", "commitment": "committed"}, "c2": {"evidence": "m2", "commitment": "committed"}}
    A = [{"cid": "c1", "match": "node:T1"}, {"cid": "c2", "match": "node:T2"}]
    B = [{"cid": "c1", "match": "node:T1"}, {"cid": "c2", "match": None}]
    ma = {"key_sha": "k", "snapshot_of": "s", "processed": ["m1", "m2"]}
    mb = {"key_sha": "k", "snapshot_of": "s", "processed": ["m1"], "incomplete": True}
    r = CR.compare(ma, {"items": A}, mb, {"items": B, "incomplete": True}, key)
    assert r["all"] is None and r["like_for_like"]["n"] == 1 and r["like_for_like"]["lost"] == []


def test_two_records_without_a_key_are_refused():
    assert "key" in CR.incompatible({"key_sha": None}, {"key_sha": None})


def test_records_scored_with_and_without_covered_by_are_not_compared():
    a = {"key_sha": "k", "snapshot_of": "s", "covered_by": True}
    b = {"key_sha": "k", "snapshot_of": "s", "covered_by": False}
    assert "covered_by" in CR.incompatible(a, b)


def test_neutral_key_drops_covered_by():
    assert ER.neutral([{"cid": "c1", "covered_by": "T1"}]) == [{"cid": "c1", "covered_by": None}]


def test_record_writes_meta_and_progress_line(tmp_path):
    score = {"B7": {"n": 4, "ok": 3, "rate": 0.75, "lb95": 0.3, "distinct_nodes": 3}, "items": [{"cid": "c1", "match": "node:T1"}]}
    rec = ER.write_record(tmp_path / "runs", tmp_path / "progress.jsonl", tier="0", label="unit",
                          meta={"key_sha": "k", "code_version": "v", "snapshot_of": None}, score=score)
    m = json.loads((rec / "meta.json").read_text())
    assert m["tier"] == "0" and m["key_sha"] == "k" and (rec / "score.json").exists()
    line = json.loads((tmp_path / "progress.jsonl").read_text().splitlines()[-1])
    assert line["B7"] == {"ok": 3, "n": 4, "lb95": 0.3, "distinct": 3} and line["record"] == rec.name


def test_incomplete_run_reports_no_rates(tmp_path):
    score = {"B7": {"n": 4, "ok": 1, "rate": 0.25, "lb95": 0.05, "distinct_nodes": 1}, "items": [], "incomplete": True,
             "missing": ["m3"]}
    ER.write_record(tmp_path / "runs", tmp_path / "progress.jsonl", tier="2", label="cut short", meta={}, score=score)
    line = json.loads((tmp_path / "progress.jsonl").read_text().splitlines()[-1])
    assert line["incomplete"] is True and "B7" not in line and line["missing"] == ["m3"]


def test_provenance_names_the_commit_the_diff_and_the_prompts():
    p = ER.provenance(RUN_DIR)
    assert re.fullmatch(r"[0-9a-f]{40}", p["git_commit"])
    assert isinstance(p["git_dirty"], bool) and re.fullmatch(r"[0-9a-f]{64}", p["git_diff_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", p["prompts_sha256"]) and re.fullmatch(r"[0-9a-f]{10}", p["code_version"])


def test_a_prompt_edit_changes_the_prompts_fingerprint(tmp_path):
    (tmp_path / "EXTRACT.md").write_text("rule one\n")
    (tmp_path / "taska_lib").mkdir()
    (tmp_path / "taska_lib" / "x.py").write_text("x = 1\n")
    before = ER.prompts_sha(tmp_path)
    (tmp_path / "EXTRACT.md").write_text("rule two\n")
    assert ER.prompts_sha(tmp_path) != before


def test_models_are_read_from_the_run_session(tmp_path):
    s = tmp_path / "run.jsonl"
    s.write_text("\n".join(json.dumps(r) for r in [
        {"type": "assistant", "message": {"model": "claude-test-1", "content": []}},
        {"type": "assistant", "message": {"model": "claude-test-1", "content": []}}]) + "\n")
    (tmp_path / "run" / "subagents").mkdir(parents=True)
    (tmp_path / "run" / "subagents" / "agent-a1.jsonl").write_text(
        json.dumps({"type": "assistant", "message": {"model": "claude-test-2", "content": []}}) + "\n")
    assert ER.models(s) == ["claude-test-1", "claude-test-2"]


def test_main_scores_a_project_against_a_key_version(project, tmp_path):
    run_cli(project, "sync")
    evid = run_cli(project, "pending")["items"][0]["id"]
    item = {"cid": "x-001", "evidence": evid, "quote": "I will reset the passwords today and send the new credentials",
            "kind": "task", "commitment": "committed", "owner": "Opal Brennick", "due": None, "status": "open",
            "client_owed": True, "covered_by": None, "title": "Reset passwords"}
    key = write_version(tmp_path, [item], "v1", "2026-03-04", "unit")
    out = tmp_path / "records"
    rec = ER.main(["--project", str(project), "--key", str(key), "--tier", "0", "--label", "unit", "--out-root", str(out)])
    meta = json.loads((rec / "meta.json").read_text())
    assert meta["key_version"] == "v1" and meta["key_path"] == str(key) and meta["git_commit"]
    assert (rec / "items.csv").exists() and (out / "progress.jsonl").exists()
    assert json.loads((rec / "score.json").read_text())["B7"]["n"] == 1


def test_a_rescore_keeps_the_runs_own_provenance_from_the_first_record_of_its_copy(tmp_path):
    for name, commit in (("20260301-100000-2", "a" * 40), ("20260302-100000-2", "b" * 40)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text(json.dumps({"project": "/runs/x-tier2-med", "git_commit": commit,
                                                               "git_dirty": False, "prompts_sha256": commit[:1] * 64, "models": ["m"]}))
    (tmp_path / "20260303-100000-2").mkdir()
    (tmp_path / "20260303-100000-2" / "meta.json").write_text(json.dumps({"project": "/runs/other", "git_commit": "c" * 40}))
    p = ER.run_provenance(tmp_path, "/runs/x-tier2-med")
    assert p["git_commit"] == "a" * 40 and p["prompts_sha256"] == "a" * 64   # the first record: written right after the run
    assert ER.run_provenance(tmp_path, "/runs/none") == {}


def _tree(root):
    (root / "skills" / "run" / "taska_lib").mkdir(parents=True)
    (root / "skills" / "run" / "EXTRACT.md").write_text("rule\n")
    (root / "skills" / "run" / "taska_lib" / "x.py").write_text("x = 1\n")
    (root / "evals").mkdir()
    (root / "evals" / "verdict.py").write_text("v = 1\n")
    return root


def test_the_manifests_hash_every_runtime_and_grading_file_tracked_or_not(tmp_path):
    root = _tree(tmp_path / "bundle")
    m = ER.manifests(root)
    assert set(m) == {"execution_manifest_sha256", "grading_manifest_sha256", "manifest_files"}
    (root / "skills" / "run" / "taska_lib" / "new.py").write_text("y = 2\n")          # untracked runtime file
    m2 = ER.manifests(root)
    assert m2["execution_manifest_sha256"] != m["execution_manifest_sha256"]
    assert m2["grading_manifest_sha256"] == m["grading_manifest_sha256"]
    (root / "evals" / "verdict.py").write_text("v = 2\n")
    assert ER.manifests(root)["grading_manifest_sha256"] != m["grading_manifest_sha256"]
    (root / "skills" / "run" / "taska_lib" / "__pycache__").mkdir()
    (root / "skills" / "run" / "taska_lib" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert ER.manifests(root) == ER.manifests(root) and ER.manifests(root)["execution_manifest_sha256"] == m2["execution_manifest_sha256"]


def test_code_changed_during_a_run_is_named_on_the_record(tmp_path):
    root = _tree(tmp_path / "bundle")
    start = ER.manifests(root)
    assert ER.changed_since(start, ER.manifests(root)) == []
    (root / "skills" / "run" / "EXTRACT.md").write_text("rule edited mid-run\n")
    assert ER.changed_since(start, ER.manifests(root)) == ["execution"]
