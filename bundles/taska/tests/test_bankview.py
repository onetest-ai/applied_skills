"""The task bank results page: bank.page_doc (what the page needs), bankview.PAGE / write, the page and
compare-models CLI. Invented data only; no model calls."""
import json
import shutil
import subprocess
import sys

import pytest

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402
import bankview as BV  # noqa: E402
import evalview  # noqa: E402
from test_bank_aggregate import T, run_dir  # noqa: E402


def cli(*args):
    return subprocess.run([sys.executable, str(EVALS / "bank.py"), *args], capture_output=True, text=True)


def failing_run(root, kind="trials"):
    bad = T("t1", 1, False)
    bad["checks"][0] |= {"id": "t1-0", "node": "T9", "at": "2026-11-03T10:14:52Z", "events": ["e2"], "quote": "we ship friday"}
    bad["unscored"] = [{"node": "T4", "quote": "x"}]
    good = T("t2", 1, True)
    d = run_dir(root, "r1", [bad, good], kind=kind)
    f = d / "trials" / "t1-1"
    (f / "transcript").mkdir()
    (f / "trace.json").write_text(json.dumps({
        "agents": [{"id": "a0", "name": "main"}],
        "spans": [{"id": "s1", "agent": "a0", "tool": "Bash", "start": "2026-11-03T10:15:00Z", "end": "2026-11-03T10:15:02Z", "error": False, "step": "run done"}],
        "events": [{"id": "e1", "agent": "a0", "type": "read", "at": "2026-11-03T10:10:00Z", "evidence": "m1", "lines": [1, 40]},
                   {"id": "e3", "agent": "a0", "type": "gate", "at": "2026-11-03T10:16:00Z", "evidence": "m1", "status": "coverage", "parts": [2], "quote": ["we <script>alert(1)</script> ship"]},
                   {"id": "e2", "agent": "a0", "type": "apply", "at": "2026-11-03T10:14:52Z", "evidence": "m1",
                    "result": "rejected", "why": "no quote", "quote": "we ship friday"}]}))
    BV_write_transcripts(d)
    return d


def BV_write_transcripts(d):
    B.write_transcripts(d)


def test_page_doc_names_failed_checks_with_why_in_words_and_transcript_link(tmp_path):
    doc = B.page_doc(failing_run(tmp_path))
    assert doc["kind"] == "trials" and doc["provenance"]["tier"] == "low" and doc["provenance"]["k"] == 3
    assert doc["aggregate"]["tasks"]["t1"]["pass_at_k"] == 0 and doc["aggregate"]["overall"]["valid"] == 2
    t1 = next(t for t in doc["tasks"] if t["task"] == "t1")
    tr = t1["trials"][0]
    assert tr["unscored"] == 1
    f = tr["failed"][0]
    assert f["id"] == "t1-0" and f["node"] == "T9" and f["quote"] == "we ship friday"
    assert f["transcript"] == "trials/t1-1/transcript.html#t=2026-11-03T10:14:52Z"
    assert [w["step"] for w in f["why"]] == ["apply rejected"] and "no quote" in f["why"][0]["detail"]
    assert f["why"][0]["agent"] == "main"
    t2 = next(t for t in doc["tasks"] if t["task"] == "t2")
    assert t2["trials"][0]["failed"] == []


def test_replay_run_is_labelled_and_has_no_transcript_links(tmp_path):
    d = failing_run(tmp_path, kind="replay")
    doc = B.page_doc(d)
    assert doc["kind"] == "replay"
    assert doc["label"].startswith("replay") and "not comparable with trials" in doc["label"]
    t1 = next(t for t in doc["tasks"] if t["task"] == "t1")
    assert t1["trials"][0]["failed"] and all(f["transcript"] is None for f in t1["trials"][0]["failed"])
    assert not (d / "trials" / "t1-1" / "transcript.html").exists()


def test_missing_and_status_counts_are_in_the_doc(tmp_path):
    d = run_dir(tmp_path, "r2", [T("a", 1, True), T("a", 2, False, status="timeout")])
    doc = B.page_doc(d)
    assert doc["aggregate"]["tasks"]["a"]["timeout"] == 1 and doc["aggregate"]["overall"]["missing"] == 1


def test_page_shares_style_fetches_bank_json_and_names_n():
    assert evalview.STYLE in BV.PAGE
    assert "fetch('bank.json'" in BV.PAGE
    assert "Open the transcript" in BV.PAGE
    assert "not comparable with trials" in BV.PAGE or "doc.label" in BV.PAGE
    assert "doc.kind" in BV.PAGE and "'models'" in BV.PAGE
    assert "McNemar" in BV.PAGE and "only_in" in BV.PAGE and "no_valid" in BV.PAGE


def test_write_is_atomic_and_replaces_a_changed_page(tmp_path):
    (tmp_path / "index.html").write_text("old")
    p = BV.write(tmp_path, {"kind": "trials"})
    assert p == tmp_path / "bank.json" and json.loads(p.read_text()) == {"kind": "trials"}
    assert (tmp_path / "index.html").read_text() == BV.PAGE
    assert not (tmp_path / "bank.json.tmp").exists()


def test_cli_page_and_aggregate_write_the_page(tmp_path):
    d = failing_run(tmp_path)
    assert cli("page", "--run", str(d)).returncode == 0
    assert (d / "index.html").exists() and json.loads((d / "bank.json").read_text())["tasks"]
    (d / "index.html").unlink()
    assert cli("aggregate", "--run", str(d)).returncode == 0
    assert (d / "index.html").exists()
    assert "aggregate" in json.loads((d / "bank.json").read_text())


def test_compare_models_writes_a_models_page(tmp_path):
    a = run_dir(tmp_path, "lo", [T("a", 1, True), T("a", 2, False)], tier="low")
    b = run_dir(tmp_path, "me", [T("a", 1, True), T("a", 2, True)], tier="med")
    out = tmp_path / "cmp"
    r = cli("compare-models", "--a", str(a), "--b", str(b), "--out", str(out))
    assert r.returncode == 0, r.stderr
    doc = json.loads((out / "bank.json").read_text())
    assert doc["kind"] == "models" and doc["tasks"][0]["a"]["valid"] == 2 and "p" in doc and "only_in_a" in doc
    assert (out / "index.html").exists()


def trace_of(d):
    return json.loads((d / "trials" / "t1-1" / "trace.json").read_text())


def test_transcript_page_lists_steps_reasons_gate_escapes_and_anchors(tmp_path):
    d = failing_run(tmp_path)
    html = (d / "trials" / "t1-1" / "transcript.html").read_text()
    assert evalview.STYLE in html
    assert "apply rejected" in html and "no quote" in html and "done refused" in html and "coverage" in html
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert 'id="t=2026-11-03T10:14:52Z"' in html
    link = B.page_doc(d)["tasks"][0]["trials"][0]["failed"][0]["transcript"]
    assert link.split("#", 1)[1] == "t=2026-11-03T10:14:52Z"
    assert html.index("read lines") < html.index("apply rejected") < html.index("done refused")


def test_transcript_page_function_is_pure():
    out = BV.transcript_page({"agents": [], "spans": [], "events": []}, "a <b>")
    assert "a &lt;b&gt;" in out and "<b>" not in out.split("<body>")[1].split("</h1>")[0].replace("<h1>", "")


def test_page_cli_writes_transcript_for_existing_trials(tmp_path):
    d = failing_run(tmp_path)
    (d / "trials" / "t1-1" / "transcript.html").unlink()
    assert cli("page", "--run", str(d)).returncode == 0
    assert (d / "trials" / "t1-1" / "transcript.html").exists()


def test_links_encode_task_and_trial(tmp_path):
    bad = T("a b#c", 1, False)
    d = run_dir(tmp_path, "r9", [bad])
    f = d / "trials" / "a b#c-1"
    (f / "trace.json").write_text(json.dumps({"agents": [], "spans": [], "events": []}))
    B.write_transcripts(d)
    link = B.page_doc(d)["tasks"][0]["trials"][0]["failed"][0]["transcript"]
    assert link.startswith("trials/a%20b%23c-1/transcript.html")


def test_page_guards_tasks_without_valid_trials_and_formats_p():
    assert "valid of" in BV.PAGE and "valid: 0, trials: 0" in BV.PAGE
    assert "num(doc.p, 3)" in BV.PAGE and "esc(num(doc.paired, 0))" in BV.PAGE


def test_a_failed_checks_link_anchors_to_a_row_that_exists_in_the_transcript(tmp_path):
    import evaltrace as TR
    bad = T("t1", 1, False)
    bad["checks"] = [{"id": "n-1", "type": "must_not_create", "caps": ["not_work"], "result": "fail", "node": "T1", "at": None}]
    d = run_dir(tmp_path, "rz", [bad, T("t2", 1, True)])
    f = d / "trials" / "t1-1"
    trace = {"agents": [{"id": "a0", "name": "main"}],
             "spans": [{"id": "s1", "agent": "a0", "tool": "Bash", "start": "2026-11-03T10:15:00Z", "end": None, "error": False, "step": "apply"}],
             "events": [{"id": "e1", "agent": "a0", "type": "apply", "at": "2026-11-03T10:15:00Z", "evidence": "m1",
                         "result": "applied", "why": None, "quote": "ship", "qspan": [2, 8], "index": 0}]}
    task = {"evidence": "m1", "must_not_create": [{"id": "n-1", "span": [0, 10]}]}
    (f / "grade.json").write_text(json.dumps(dict(bad, **TR.link({"checks": bad["checks"]}, task, trace))))
    (f / "trace.json").write_text(json.dumps(trace))
    B.write_transcripts(d)
    link = B.page_doc(d)["tasks"][0]["trials"][0]["failed"][0]["transcript"]
    anchor = link.split("#", 1)[1]
    assert anchor == "t=2026-11-03T10:15:00Z"
    assert f'id="{anchor}"' in (f / "transcript.html").read_text()


RENDER = """
const fs = require('fs');
const els = {};
global.document = {getElementById: id => (els[id] ||= {innerHTML: '', textContent: ''})};
global.fetch = () => new Promise(() => {});
const page = fs.readFileSync(process.argv[2], 'utf8');
eval(page.slice(page.indexOf('<script>') + 8, page.indexOf('</script>')));
run(JSON.parse(fs.readFileSync(process.argv[3], 'utf8')));
process.stdout.write(els.body.innerHTML);
"""


def rendered(tmp_path, doc):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the page cannot be rendered")
    (tmp_path / "render.js").write_text(RENDER)
    (tmp_path / "page.html").write_text(BV.PAGE)
    (tmp_path / "doc.json").write_text(json.dumps(doc))
    r = subprocess.run([node, str(tmp_path / "render.js"), str(tmp_path / "page.html"), str(tmp_path / "doc.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_rendered_page_shows_usage_before_the_api_equivalent_line_and_the_bars(tmp_path):
    from test_bank_aggregate import U, SIZE, BARS
    tasks = {"a": {"class": SIZE("S")}, "c": {"class": SIZE("S", "one block")}}
    d = run_dir(tmp_path, "r", [U("a", 1, turns=10, agent_turns=99), U("c", 1)])
    h = rendered(tmp_path, B.page_doc(d, tasks, bars=BARS, bars_source="x/bars.json"))
    usage, api, passed, bars = (h.index(x) for x in ("<h2>Usage", "API-equivalent $ (not a bill)", "Tasks passed at least once", "<h2>Bars"))
    assert usage < api < passed < bars
    assert "One block" in h and "n&lt;20: p95 = max" in h and "This run has no class data" not in h
    assert 'class="box warn"><b>Hard bars:</b> fail' in h


def test_the_page_marks_unverified_like_fail_shows_not_set_and_says_when_there_is_no_class_data(tmp_path):
    from test_bank_aggregate import U, BARS
    d = run_dir(tmp_path, "r", [U("a", n) for n in (1, 2, 3)])   # k = 3, complete; no trial carries agent turns
    h = rendered(tmp_path, B.page_doc(d, bars=BARS, bars_source="x/bars.json"))
    assert 'class="box warn"><b>Hard bars:</b> unverified' in h
    assert "This run has no class data" in h
    bare = rendered(tmp_path, B.page_doc(d))
    assert "not set" in bare and 'class="box "><b>Hard bars:</b> not set' in bare


def test_turn_cap_shows_trials_with_data_out_of_all_trials(tmp_path):
    from test_bank_aggregate import U, BARS
    d = run_dir(tmp_path, "r", [U("a", 1, agent_turns=3), U("a", 2)])
    h = rendered(tmp_path, B.page_doc(d, bars=BARS))
    assert "1/2 trials" in h


def test_page_doc_reads_round_minutes_and_the_projects_bars(tmp_path):
    d = run_dir(tmp_path, "r3", [T("a", 1, True)])
    head = json.loads((d / "run.json").read_text())
    (d / "run.json").write_text(json.dumps(head | {"started": "2026-11-03T10:00:00", "finished": "2026-11-03T10:07:30"}))
    doc = B.page_doc(d)
    assert doc["aggregate"]["usage"]["round"]["minutes"] == {"value": 7.5, "n": 1}
    assert {r["result"] for r in doc["aggregate"]["bars"]["rows"] if r["kind"] != "value"} == {"not set"}
    proj = tmp_path / "proj"
    (proj / "evals").mkdir(parents=True)
    (proj / "evals" / "bars.json").write_text(json.dumps({"turn_cap": 15, "minutes_per_round_max": 5}))
    assert cli("page", "--run", str(d), "--project", str(proj)).returncode == 0
    rows = json.loads((d / "bank.json").read_text())["aggregate"]["bars"]
    by = {r["key"]: r for r in rows["rows"]}
    assert by["turn_cap"]["bar"] == 15 and by["turn_cap"]["result"] == "no data"
    assert by["minutes_per_round_max"]["result"] == "fail" and by["minutes_per_round_max"]["value"] == 7.5
    assert rows["source"].endswith("bars.json")
