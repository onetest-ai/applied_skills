"""evals/smart.py: a development round picks the smallest set of bank tasks the change can affect, from the changed
files alone (no model, no git), and run.py --smart runs it through bank.py's own functions. No test starts a trial.
Invented data only."""
import json
import sys

import pytest

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402
import run as RUN  # noqa: E402
import smart as SM  # noqa: E402

T = "bundles/taska/"
MINUTES = {"S": 0.5, "M": 1.0, "L": 1.1, "XL": 3.0, "XL|one block": 2.5}


def _task(tid, size, caps, status="golden", difficulty="medium", structure="conversation", after=None, why=None):
    check = {"id": f"{tid}-1", "span": [0, 10], "quote": "invented", "caps": caps, "status": status,
             "difficulty": difficulty, "source": "invented"}
    t = {"task": tid, "evidence": f"2026-03-0{len(tid)}_{tid}", "class": {"size": size, "structure": structure},
         "must_create": [] if why else [check], "must_not_create": [check | {"why": why}] if why else [],
         "outcome": ["processed"]}
    return t | ({"after": [after]} if after else {})


@pytest.fixture(autouse=True)
def _invented_meetings(monkeypatch):
    """These banks are invented tasks that name no meeting: no project holds their transcripts."""
    monkeypatch.setattr(B, "check_transcripts", lambda project, tasks: None)


@pytest.fixture
def bank(tmp_path):
    tasks = [_task("s1", "S", ["committed"]),
             _task("s2", "S", ["committed"], difficulty="easy"),
             _task("m1", "M", ["decisions"]),
             _task("l1", "L", ["owners"]),
             _task("ob1", "XL", ["long_meeting", "one_block"], structure="one block"),
             _task("xl1", "XL", ["long_meeting"]),
             _task("c1", "S", ["continuity"], difficulty="easy"),
             _task("c2", "S", ["continuity"], after="c1"),
             _task("asr1", "S", ["asr"], status="draft"),
             _task("p1", "M", ["people"], difficulty="hard", why="people")]
    return B.write_bank(tmp_path / "bank", tasks, "v1", "2026-11-03", "unit")


@pytest.mark.parametrize("path", ["skills/run/taska_lib/evalscore.py", "evals/key_tools.py", "evals/relabel.py",
                                  "evals/quality.py", "evals/QUALITY_JUDGE.md", "evals/JUDGE.md",
                                  "skills/run/taska_lib/report.py", "evals/golden/bank.v2.jsonl"])
def test_scorer_key_labels_quality_judge_and_report_wording_rescore_with_no_model(bank, path):
    p = SM.plan([T + path], bank, MINUTES, None)
    assert p["round"] == "rescore" and p["tasks"] == [] and p["expected_minutes"] == 0.0 and p["refused"] is None
    assert p["files"][T + path] == "rescore"


def test_a_prompt_line_naming_a_capability_runs_that_capabilitys_golden_tasks(bank):
    f = T + "skills/run/EXTRACT.md"
    p = SM.plan([f], bank, MINUTES, None, lines={f: ["Name the owner when the meeting says one.", ""]})
    assert p["round"] == "capability" and p["capabilities"] == ["owners"] and p["tasks"] == ["l1"]
    assert p["expected_minutes"] == 1.1 and p["comparable"] is False


def test_a_capability_round_brings_a_chains_earlier_task_and_says_when_no_task_is_golden(bank):
    f = T + "skills/run/EXTRACT.md"
    p = SM.plan([f], bank, MINUTES, None, lines={f: ["Read the earlier meeting for continuity."]})
    assert p["capabilities"] == ["continuity"] and p["tasks"] == ["c1", "c2"]
    p = SM.plan([f], bank, MINUTES, None, lines={f: ["A speech-recognition transcript has no speakers."]})
    assert p["tasks"] == ["asr1"] and any("no golden task" in w and "asr" in w for w in p["why"])


def test_people_matter_code_is_the_people_capability(bank):
    p = SM.plan([T + "skills/run/taska_lib/sensitive.py"], bank, MINUTES, None)
    assert p["round"] == "capability" and p["capabilities"] == ["people"] and p["tasks"] == ["p1"]


def test_extraction_runs_one_task_per_class_medium_difficulty_first_without_xl(bank):
    for files, lines in (([T + "skills/run/taska_lib/ledger.py"], None),
                         ([T + "skills/run/EXTRACT.md"], None),
                         ([T + "skills/run/EXTRACT.md"], {T + "skills/run/EXTRACT.md": ["Read every part.", "the owner"]})):
        p = SM.plan(files, bank, MINUTES, None, lines=lines)
        assert p["round"] == "smoke" and p["tasks"] == ["s1", "m1", "l1", "ob1"], files
        assert p["expected_minutes"] == 2.5 and p["comparable"] is False


def test_long_meeting_code_adds_the_xl_task(bank):
    p = SM.plan([T + "skills/run/taska_lib/coverage.py"], bank, MINUTES, None)
    assert p["round"] == "smoke" and p["tasks"] == ["s1", "m1", "l1", "ob1", "xl1"] and p["long_meeting"] is True


def test_extraction_with_a_capability_keeps_both(bank):
    f = T + "skills/run/EXTRACT.md"
    p = SM.plan([f, T + "skills/run/taska_lib/ledger.py"], bank, MINUTES, None, lines={f: ["the client owner"]})
    assert p["round"] == "smoke" and p["tasks"] == ["s1", "m1", "l1", "ob1"] and p["capabilities"] == ["owners"]


def test_the_second_read_only_falls_back_to_the_smoke_set_and_says_why(bank):
    f = T + "skills/run/SKILL.md"
    for files, lines in (([T + "skills/run/taska_lib/reads.py"], None), ([f], {f: ["second reads go by why"]})):
        p = SM.plan(files, bank, MINUTES, None, lines=lines)
        assert p["round"] == "second_read" and p["tasks"] == ["s1", "m1", "l1", "ob1"] and p["comparable"] is False
        assert any("after-extraction snapshot" in w for w in p["why"])


def test_a_release_is_the_full_tier_2_and_is_never_run_by_smart(bank):
    p = SM.plan([T + ".claude-plugin/plugin.json", T + "skills/run/taska_lib/ledger.py"], bank, MINUTES, None)
    assert p["round"] == "full" and p["tasks"] == [] and "--tier 2 --yes --full" in p["refused"]


def test_operations_and_tests_alone_change_nothing_a_round_measures(bank):
    p = SM.plan([T + "skills/run/workbench.html", T + "tests/test_x.py", "bundles/brain/x.py"], bank, MINUTES, None)
    assert p["round"] == "rescore" and p["tasks"] == [] and set(p["files"].values()) == {"ignored"}


def test_a_budget_refusal_names_what_to_drop(bank):
    p = SM.plan([T + "skills/run/taska_lib/ledger.py"], bank, MINUTES, 2.0)
    assert p["refused"] and "drop ob1" in p["refused"] and p["budget"]["drop"] == ["ob1"]
    assert SM.plan([T + "skills/run/taska_lib/ledger.py"], bank, MINUTES, 2.5)["refused"] is None


def test_expected_minutes_use_the_measured_minutes_per_class_and_the_measured_overhead(bank):
    f = [T + "skills/run/taska_lib/ledger.py"]
    assert SM.plan(f, bank, MINUTES | {"XL|one block": 4.0}, None)["expected_minutes"] == 4.0
    assert SM.plan(f, bank, MINUTES, None, overhead=(1.5, 3))["expected_minutes"] == 4.0
    assert SM.plan(f, bank, MINUTES, None, k=2, parallel=4)["expected_minutes"] == 2.6
    no_l = {k: v for k, v in MINUTES.items() if k != "L"}
    p = SM.plan(f, bank, no_l, None)
    assert p["expected_minutes"] is None and p["refused"] is None and "size L" in p["minutes_note"]
    p = SM.plan(f, bank, no_l, 10)
    assert p["refused"] and "size L" in p["refused"]


def test_an_explicit_set_takes_the_same_path_and_budget(bank):
    p = SM.select(bank, MINUTES, None, tasks=["c2"])
    assert p["round"] == "explicit" and p["tasks"] == ["c1", "c2"] and p["expected_minutes"] == 1.0
    assert p["comparable"] is False
    assert SM.select(bank, MINUTES, None, meetings=["2026-03-02_m1", "2026-03-02_l1"])["tasks"] == ["m1", "l1"]
    assert "nope" in SM.select(bank, MINUTES, None, tasks=["nope"])["refused"]
    assert "2026-01-01_x" in SM.select(bank, MINUTES, None, meetings=["2026-01-01_x"])["refused"]
    assert "drop" in SM.select(bank, MINUTES, 0.6, tasks=["s1", "m1"])["refused"]


def test_diff_lines_are_the_added_and_removed_lines_per_file():
    diff = ("diff --git a/x/EXTRACT.md b/x/EXTRACT.md\n--- a/x/EXTRACT.md\n+++ b/x/EXTRACT.md\n@@ -1 +1 @@\n"
            "-old owner line\n+new owner line\ndiff --git a/y.py b/y.py\n--- /dev/null\n+++ b/y.py\n@@ -0,0 +1 @@\n+z = 1\n")
    assert SM.diff_lines(diff) == {"x/EXTRACT.md": ["old owner line", "new owner line"], "y.py": ["z = 1"]}


def _run_dir(path, **head):
    (path / "trials").mkdir(parents=True)
    (path / "run.json").write_text(json.dumps({"kind": "trials", "bank_version": "v1", "bank_sha256": "a", "tier": "low",
                                               "k": 1, "tasks": []} | head))
    return path


def test_a_smoke_run_is_never_compared_with_a_full_run(tmp_path):
    smoke = _run_dir(tmp_path / "smoke", round="smoke", comparable=False)
    full = _run_dir(tmp_path / "full")
    with pytest.raises(ValueError, match="smoke"):
        B.compare(smoke, full)
    with pytest.raises(ValueError, match="smoke"):
        B.load_baseline(smoke, full)
    assert B.compare(smoke, _run_dir(tmp_path / "smoke2", round="smoke", comparable=False))["paired"] == 0


def test_run_bank_writes_the_rounds_tags_and_the_page_carries_them(bank, tmp_path, monkeypatch):
    project = tmp_path / "p"
    project.mkdir()
    monkeypatch.setattr(B, "trial", lambda *a, **k: {"status": "done"})
    monkeypatch.setattr(B, "check_transcripts", lambda project, tasks: None)   # invented tasks name no meeting
    out = B.run_bank(project, bank, "low", 1, ["s1"], 1, ["true"], tmp_path / "out", minutes_by_class=MINUTES,
                     tags={"round": "smoke", "comparable": False})
    head = json.loads((out / "run.json").read_text())
    assert head["round"] == "smoke" and head["comparable"] is False
    doc = B.page_doc(out)
    assert doc["provenance"]["comparable"] is False and doc["provenance"]["round"] == "smoke"
    assert "never compared" in doc["label"]


def _cli(project, bank, *extra):
    return ["--project", str(project), "--smart", "--bank", str(bank), *extra]


def test_smart_rescore_replays_the_stored_records_and_calls_no_model(bank, tmp_path, monkeypatch, capsys):
    project = tmp_path / "p"
    rec = project / "reports" / "evals" / "20261101-100000-2"
    rec.mkdir(parents=True)
    (rec / "meta.json").write_text("{}")
    monkeypatch.setattr(B, "run_bank", lambda *a, **k: pytest.fail("rescore ran trials"))
    monkeypatch.setattr(RUN, "reachable", lambda probe: pytest.fail("rescore probed a model"))
    assert RUN.main(_cli(project, bank, "--changed", T + "skills/run/taska_lib/evalscore.py")) == 0
    out = capsys.readouterr().out
    assert '"round": "rescore"' in out
    made = list((project / "reports" / "evals" / "bank").glob("replay-v1-*/run.json"))
    assert len(made) == 1 and json.loads(made[0].read_text())["records"] == [rec.name]


def _fake_run_bank(calls, tmp_path):
    def fake(project, bank_path, tier, k, tasks=None, parallel=4, ai=None, out_root=None, invalid="",
             minutes_by_class=None, tags=None):
        calls.append({"tier": tier, "k": k, "tasks": tasks, "parallel": parallel, "tags": tags, "invalid": invalid})
        return _run_dir(tmp_path / f"run{len(calls)}", tasks=tasks, **(tags or {}))
    return fake


def test_smart_smoke_runs_through_run_bank_tagged_never_comparable(bank, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(B, "run_bank", _fake_run_bank(calls, tmp_path))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: True)
    project = tmp_path / "p"
    args = _cli(project, bank, "--changed", T + "skills/run/taska_lib/ledger.py", "--budget", "10")
    assert RUN.main(args) == 2   # model calls need --yes
    assert "--yes" in capsys.readouterr().out and not calls
    assert RUN.main(args + ["--yes"]) == 0
    out = capsys.readouterr().out
    assert '"expected_minutes": 2.5' in out
    (call,) = calls
    assert call["tasks"] == ["s1", "m1", "l1", "ob1"] and call["tier"] == "med" and call["k"] == 1
    assert call["tags"] == {"round": "smoke", "comparable": False}
    run_dir = tmp_path / "run1"
    assert json.loads((run_dir / "plan.json").read_text())["comparable"] is False
    assert json.loads((run_dir / "bank.json").read_text())["provenance"]["comparable"] is False


def test_smart_capability_defaults_to_the_low_tier(bank, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(B, "run_bank", _fake_run_bank(calls, tmp_path))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: True)
    assert RUN.main(_cli(tmp_path / "p", bank, "--changed", T + "skills/run/taska_lib/sensitive.py", "--yes")) == 0
    assert calls[0]["tier"] == "low" and calls[0]["tasks"] == ["p1"]


def test_smart_over_budget_refuses_before_any_model_call(bank, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(B, "run_bank", lambda *a, **k: pytest.fail("ran over budget"))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: pytest.fail("probed over budget"))
    args = _cli(tmp_path / "p", bank, "--changed", T + "skills/run/taska_lib/ledger.py", "--budget", "2", "--yes")
    assert RUN.main(args) == 2
    assert "drop ob1" in capsys.readouterr().out


def test_a_full_round_says_to_run_the_full_tier_2(bank, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(B, "run_bank", lambda *a, **k: pytest.fail("smart ran a full round"))
    assert RUN.main(_cli(tmp_path / "p", bank, "--changed", T + ".claude-plugin/plugin.json", "--yes")) == 2
    assert "--full" in capsys.readouterr().out


def test_explicit_meetings_run_through_the_same_path(bank, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(B, "run_bank", _fake_run_bank(calls, tmp_path))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: True)
    args = ["--project", str(tmp_path / "p"), "--bank", str(bank), "--meetings", "2026-03-02_m1", "--yes"]
    assert RUN.main(args) == 0
    assert calls[0]["tasks"] == ["m1"] and calls[0]["tags"] == {"round": "explicit", "comparable": False}
    assert RUN.main(["--project", str(tmp_path / "p"), "--bank", str(bank), "--tasks", "nope", "--yes"]) == 2


def test_tier_2_without_full_still_names_smart(tmp_path, capsys):
    assert RUN.main(["--project", str(tmp_path), "--key", "k", "--before", "b", "--tier", "2", "--yes"]) == 2
    assert capsys.readouterr().out.strip() == "a full tier 2 takes 13–35 min: add --full, or run --smart"


@pytest.mark.parametrize("path,rnd", [
    ("skills/run/taska_lib/evalscore.py", "rescore"), ("evals/verdict.py", "rescore"), ("evals/compare.py", "rescore"),
    ("evals/crossrun.py", "rescore"), ("evals/items_csv.py", "rescore"), ("evals/quality.py", "rescore"),
    ("evals/relabel.py", "rescore"), ("evals/key_tools.py", "rescore"), ("evals/gold_from_judged.py", "rescore"),
    ("evals/check_judge.py", "rescore"), ("evals/check_reader.py", "rescore"), ("evals/pool.py", "rescore"),
    ("evals/echo.py", "rescore"), ("evals/JUDGE.md", "rescore"), ("evals/QUALITY_JUDGE.md", "rescore"),
    ("evals/READER.md", "rescore"), ("evals/key/items.v3.jsonl", "rescore"), ("evals/golden/bank.v2.jsonl", "rescore"),
    ("evals/trajectory.py", "rescore"), ("evals/evaltrace.py", "rescore"),
    ("evals/evalview.py", "ignored"), ("evals/bankview.py", "ignored"), ("evals/live.py", "ignored"),
    ("evals/smart.py", "ignored"),
    ("skills/run/taska_lib/ai.py", "smoke"), ("evals/run.py", "smoke"), ("evals/record.py", "smoke"),
    ("evals/bank.py", "smoke"), ("evals/tiers.json", "smoke"), ("evals/golden/record.py", "smoke"),
    ("evals/meetings.py", "regenerate"), ("evals/golden/generate.py", "regenerate"),
    ("evals/golden/templates.json", "regenerate"), ("evals/planted/generate.py", "regenerate"),
    ("evals/planted/templates.json", "regenerate"),
    ("skills/run/taska_lib/sources/msgraph.py", "ignored"), ("skills/run/taska_lib/transcripts.py", "ignored"),
    ("skills/run/taska_lib/media.py", "ignored")])
def test_the_file_to_round_table(bank, path, rnd):
    p = SM.plan([T + path], bank, MINUTES, None)
    assert p["files"][T + path] == rnd
    assert p["round"] == ("rescore" if rnd in ("ignored", "regenerate") else rnd)


def test_tiers_regenerators_and_sync_code_say_why(bank):
    p = SM.plan([T + "evals/tiers.json"], bank, MINUTES, None)
    assert any("baseline" in w and "tiers.json" in w for w in p["why"])
    p = SM.plan([T + "evals/golden/templates.json"], bank, MINUTES, None)
    assert p["tasks"] == [] and any("regenerate the bank" in w and "explicit set" in w for w in p["why"])
    p = SM.plan([T + "skills/run/taska_lib/media.py"], bank, MINUTES, None)
    assert any("never sync" in w for w in p["why"])


def test_rescore_names_every_change_no_free_command_exercises(bank):
    files = [T + "skills/run/taska_lib/report.py", T + "skills/run/taska_lib/evalscore.py", T + "evals/verdict.py"]
    p = SM.plan(files, bank, MINUTES, None)
    assert p["unexercised"] == [files[0], files[2]] and p["judge"] is False
    assert any(files[0] in w and files[2] in w and "no free command" in w for w in p["why"])
    p = SM.plan([T + "evals/quality.py", T + "evals/QUALITY_JUDGE.md"], bank, MINUTES, None)
    assert p["judge"] is True and p["unexercised"] == [T + "evals/QUALITY_JUDGE.md"]


def test_a_changed_prompt_with_no_lines_says_so(bank):
    f = T + "skills/run/EXTRACT.md"
    p = SM.plan([f], bank, MINUTES, None)
    assert any(f"no changed lines for {f}" in w and "--tasks" in w for w in p["why"])


def test_the_matched_alias_is_recorded_per_line(bank):
    f = T + "skills/run/EXTRACT.md"
    p = SM.plan([f], bank, MINUTES, None, lines={f: ["Name the owner when the meeting says one."]})
    assert p["matches"][f] == [{"line": "Name the owner when the meeting says one.", "caps": {"owners": "owner"}}]
    assert any("owners" in w and "'owner'" in w for w in p["why"])


def test_an_explicit_round_is_labelled_chosen_by_hand(tmp_path):
    doc = B.page_doc(_run_dir(tmp_path / "x", round="explicit", comparable=False))
    assert "chosen by hand" in doc["label"]


def test_smart_refuses_an_empty_set_and_conflicting_flags(tmp_path, monkeypatch, capsys):
    bank = B.write_bank(tmp_path / "bank", [_task("xl1", "XL", ["long_meeting"])], "v1", "2026-11-03", "unit")
    monkeypatch.setattr(B, "run_bank", lambda *a, **k: pytest.fail("ran an empty round"))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: True)
    assert RUN.main(_cli(tmp_path / "p", bank, "--changed", T + "evals/bank.py", "--yes")) == 2
    assert "no task" in capsys.readouterr().out
    assert RUN.main(_cli(tmp_path / "p", bank, "--changed", "x", "--base", "HEAD")) == 2
    assert "--base" in capsys.readouterr().out
    assert RUN.main(_cli(tmp_path / "p", bank, "--tasks", "xl1")) == 2
    assert "--tasks" in capsys.readouterr().out


def test_smart_rescore_reuses_cached_judge_answers_when_judge_files_changed(bank, tmp_path, monkeypatch, capsys):
    project = tmp_path / "p"
    recs = [project / "reports" / "evals" / n for n in ("20261101-100000-2", "20261102-100000-2")]
    for r in recs:
        r.mkdir(parents=True)
        (r / "meta.json").write_text("{}")
    judged = []
    monkeypatch.setattr(RUN, "judge_cache", lambda rec, out: judged.append(rec) or {"batches": 0})
    assert RUN.main(_cli(project, bank, "--changed", T + "skills/run/taska_lib/evalscore.py")) == 0
    assert judged == []
    assert RUN.main(_cli(project, bank, "--changed", T + "evals/QUALITY_JUDGE.md")) == 0
    assert judged == [recs[-1]]


def _git(repo, *args):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo, check=True,
                   capture_output=True)


def test_changed_files_reads_tracked_untracked_and_deleted_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "a.md").write_text("one\n")
    (repo / "gone.md").write_text("bye\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "x")
    (repo / "a.md").write_text("two\n")
    (repo / "gone.md").unlink()
    (repo / "new.md").write_text("fresh\n")
    files, lines = RUN.changed_files(None, repo)
    assert sorted(files) == ["a.md", "gone.md", "new.md"]
    assert lines == {"a.md": ["one", "two"], "gone.md": ["bye"], "new.md": ["fresh"]}
    _git(repo, "branch", "-m", "main", "trunk")
    with pytest.raises(ValueError, match="--base"):
        RUN.changed_files(None, repo)


def _bars(project, body):
    (project / "evals").mkdir(parents=True, exist_ok=True)
    (project / "evals" / "bars.json").write_text(json.dumps(body))


def test_a_smart_round_is_judged_against_the_projects_bars_and_exits_3_when_they_fail(bank, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(B, "run_bank", _fake_run_bank(calls, tmp_path))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: True)
    project = tmp_path / "p"
    _bars(project, {"turn_cap": 0, "complete_required": True})
    assert RUN.main(_cli(project, bank, "--changed", T + "skills/run/taska_lib/ledger.py", "--yes")) == 3
    out = capsys.readouterr().out
    assert "hard bars" in out and '"bars": "' in out
    assert json.loads((tmp_path / "run1" / "bank.json").read_text())["aggregate"]["bars"]["source"]


def test_a_smart_round_refuses_bad_bars_before_any_model_call(bank, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(B, "run_bank", lambda *a, **k: pytest.fail("ran with bad bars"))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: pytest.fail("probed with bad bars"))
    project = tmp_path / "p"
    _bars(project, {"turn_cap": "80"})
    assert RUN.main(_cli(project, bank, "--changed", T + "skills/run/taska_lib/ledger.py", "--yes")) == 2
    assert "turn_cap" in capsys.readouterr().out


def test_a_smart_round_refuses_a_full_baseline_before_any_model_call(bank, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(B, "run_bank", lambda *a, **k: pytest.fail("ran against a full baseline"))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: pytest.fail("probed against a full baseline"))
    sha = B.load_bank(bank)[0]["sha256"]
    full = _run_dir(tmp_path / "full", bank_sha256=sha, tier="med")
    args = _cli(tmp_path / "p", bank, "--changed", T + "skills/run/taska_lib/ledger.py", "--baseline", str(full), "--yes")
    assert RUN.main(args) == 2
    assert "never compared with a full run" in capsys.readouterr().out


def test_a_smart_round_checks_regression_against_a_baseline_of_the_same_round(bank, tmp_path, monkeypatch):
    sha = B.load_bank(bank)[0]["sha256"]
    calls, docs = [], []

    def fake(project, bank_path, tier, k, tasks=None, parallel=4, ai=None, out_root=None, invalid="",
             minutes_by_class=None, tags=None):
        calls.append(tasks)
        return _run_dir(tmp_path / "run", tasks=tasks, bank_sha256=sha, tier=tier, **(tags or {}))
    page_doc = B.page_doc
    monkeypatch.setattr(B, "run_bank", fake)
    monkeypatch.setattr(B, "page_doc", lambda *a, **k: docs.append(k) or page_doc(*a, **k))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: True)
    base = _run_dir(tmp_path / "base", bank_sha256=sha, tier="med", round="smoke", comparable=False)
    args = _cli(tmp_path / "p", bank, "--changed", T + "skills/run/taska_lib/ledger.py", "--baseline", str(base), "--yes")
    assert RUN.main(args) == 0
    assert calls and docs and docs[0]["baseline"] == []


def test_smart_brings_chains_through_banks_one_closure(bank, monkeypatch):
    seen = []
    closure = B.chain_closure
    monkeypatch.setattr(B, "chain_closure", lambda ids, tasks: seen.append(list(ids)) or closure(ids, tasks))
    assert SM.select(bank, MINUTES, None, ["c2"])["tasks"] == ["c1", "c2"]
    assert seen == [["c2"]]


def test_a_smart_round_refuses_a_project_without_the_banks_meetings_before_any_model_call(bank, tmp_path, monkeypatch,
                                                                                          capsys):
    def refuse(project, tasks):
        raise ValueError("transcript of x is missing or changed since the bank was made")
    monkeypatch.setattr(B, "check_transcripts", refuse)
    monkeypatch.setattr(B, "run_bank", lambda *a, **k: pytest.fail("ran on the wrong project"))
    monkeypatch.setattr(B, "measured_minutes", lambda *a, **k: MINUTES)
    monkeypatch.setattr(RUN, "reachable", lambda probe: pytest.fail("probed on the wrong project"))
    assert RUN.main(_cli(tmp_path / "p", bank, "--changed", T + "skills/run/taska_lib/ledger.py", "--yes")) == 2
    assert "transcript" in capsys.readouterr().out


def _judged_record(tmp_path, tier="med"):
    rec = tmp_path / "evals" / "20261102-100000-2"
    (rec / "quality" / "judge").mkdir(parents=True)
    (rec / "meta.json").write_text(json.dumps({"key_sha": "k1", "model_tier": tier}))
    (rec / "quality" / "judge" / "judge_m1.json").write_text("[]")
    return rec


def test_the_free_judge_uses_a_cached_tier_other_than_the_readers_and_never_writes_into_the_record(tmp_path, monkeypatch):
    import quality as Q
    rec = _judged_record(tmp_path)
    (rec.parent / Q.CACHE).mkdir()
    (rec.parent / Q.CACHE / f"k1-low.v{Q.CACHE_SCHEMA}.jsonl").write_text("")
    calls = []
    monkeypatch.setattr(Q, "judge_run", lambda record, out, model, ai, cache_only=False, **k:
                        calls.append((record, out, model, cache_only)) or (out / "judge" / "answers_m1.json").write_text("[]") or 1)
    before = sorted(p.name for p in (rec / "quality" / "judge").iterdir())
    out = tmp_path / "replay"
    got = RUN.judge_cache(rec, out)
    assert calls == [(rec, out / rec.name / "quality", "low", True)] and got["tier"] == "low"
    assert sorted(p.name for p in (rec / "quality" / "judge").iterdir()) == before
    assert (out / rec.name / "quality" / "judge" / "answers_m1.json").is_file()


def test_the_free_judge_skips_when_no_other_tier_has_a_cache(tmp_path, monkeypatch):
    import quality as Q
    rec = _judged_record(tmp_path)
    (rec.parent / Q.CACHE).mkdir()
    (rec.parent / Q.CACHE / f"k1-med.v{Q.CACHE_SCHEMA}.jsonl").write_text("")   # the reader's own tier
    monkeypatch.setattr(Q, "judge_run", lambda *a, **k: pytest.fail("judged on the reader's tier"))
    assert RUN.judge_cache(rec, tmp_path / "replay") == {"skipped": "no judge cache on this key for a tier other than the reader's (med)"}
