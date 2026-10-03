"""End to end: baseline.sh and run_eval.sh, unmodified, against a fake brain over real MCP/HTTP.

The fake brain is a real FastMCP server on a local port with canned answers, so these tests
cross every real boundary: shell script -> python client -> streamable HTTP + auth header ->
server, and promptfoo's place is taken by a fake `npx` that loads the real context provider
(load_brain_context.js) the way promptfoo would. Each request the server sees is logged, so a
test can assert what actually crossed the wire (the auth header, latest_only), and every file
the scripts write is scanned for the API key.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import openpyxl
import pytest

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent / "skills" / "evals-remote"
KEY = "test-key-7f3a9c"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

FAKE_BRAIN = textwrap.dedent('''
    import json, os, sys
    from fastmcp import FastMCP
    from fastmcp.server.dependencies import get_http_headers

    PORT, LOG = int(sys.argv[1]), sys.argv[2]
    CATALOG = ["Docs__Vision.pdf.md", "Ops Reporting__Monthly Ops January 2026.xlsm.md"]
    SUPERSEDED = "Client Docs__Prior engagement 2021 - 2023__Archive__Old plan.pdf.md"
    mcp = FastMCP("fake-brain")

    def log(tool, args):
        headers = get_http_headers(include_all=True) or {}
        with open(LOG, "a") as f:
            f.write(json.dumps({"tool": tool, "args": args, "key": headers.get("x-api-key")}) + "\\n")

    @mcp.tool
    def health() -> dict:
        log("health", {})
        return {"status": "ok", "knowledge_version": "kv-test"}

    @mcp.tool
    def list_sources(contains: str | None = None, offset: int = 0) -> dict:
        log("list_sources", {"offset": offset})
        return {"sources": [{"source": s} for s in CATALOG], "next_offset": None}

    @mcp.tool
    def search_knowledge(query: str, limit: int = 5, latest_only: bool = True) -> dict:
        log("search_knowledge", {"query": query, "limit": limit, "latest_only": latest_only})
        hits = [{"source": s, "text": "current text", "status": "ACTIVE"} for s in CATALOG]
        if not latest_only:
            hits.append({"source": SUPERSEDED, "text": "old text", "status": "SUPERSEDED"})
        return {"hits": hits}

    @mcp.tool
    def get_taxonomy(limit: int = 100) -> dict:
        log("get_taxonomy", {})
        return {"nodes": [{"label": "Operations", "kind": "intent_l1"}]}

    @mcp.tool
    def list_metrics() -> dict:
        log("list_metrics", {})
        if os.path.exists(LOG + ".no_metrics"):
            raise ValueError("no marts in this brain")
        return {"metrics": [{"name": "handle_seconds", "unit": "s",
                             "description": "Average handle time", "last_month": "2026-08"}]}

    @mcp.tool
    def get_metric(name: str, start_month: str | None = None, limit: int = 50) -> dict:
        log("get_metric", {"name": name, "start_month": start_month})
        return {"rows": [{"grain": "overall", "entity": "all", "month": "2026-08", "value": 295,
                          "source_file": "aug.xlsx"}]}

    mcp.run(transport="http", host="127.0.0.1", port=PORT, path="/mcp", show_banner=False)
''')

# Stands in for `npx promptfoo@... eval --config C --output O`: reads the generated config,
# runs the REAL context provider for each test (as promptfoo would), writes a results file in
# promptfoo's shape (vars + testCase.metadata copied from the config, so a key that leaked into
# the config would leak into the results too).
FAKE_NPX = textwrap.dedent('''
    import json, os, subprocess, sys, yaml
    argv = sys.argv[1:]
    cfg = yaml.safe_load(open(argv[argv.index("--config") + 1]))
    out = argv[argv.index("--output") + 1]
    repeat = int(argv[argv.index("--repeat") + 1]) if "--repeat" in argv else 1
    rows = []
    for t in cfg["tests"]:
        js = t["vars"]["context"].replace("file://", "")
        script = ("require(%s)('context', '', %s).then(r => console.log(JSON.stringify(r)))"
                  % (json.dumps(js), json.dumps(t["vars"])))
        ctx = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True,
                                        check=True).stdout)
        for p in cfg["providers"]:
            for _ in range(repeat):
                rows.append({"vars": t["vars"], "testCase": {"vars": t["vars"], "metadata": t["metadata"]},
                             "provider": {"id": p["id"]}, "success": "error" not in ctx,
                             "response": {"output": ctx.get("output", "")}})
    if os.environ.get("FAKE_FAIL_FIRST"):
        for r in rows:
            if r["testCase"]["metadata"]["eval_id"] == "E001":
                r["success"] = False
    json.dump({"results": {"results": rows}}, open(out, "w"))
    sys.exit(100 if any(not r["success"] for r in rows) else 0)
''')

CSV = (
    "eval_id,category,scope,question,query_suffix,expected_answer_must_contain,"
    "expected_answer_must_not_contain,ground_truth_source,notes,min_items\n"
    "E001,Strategy,single-session,What does the vision say?,,a vision,none,Vision.pdf,n,1\n"
    "E002,MetricOrKPI,single-session,What was the average handle time?,,295 seconds,none,"
    "Monthly Ops January 2026.xlsm,n,1\n"
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def brain(tmp_path_factory):
    d = tmp_path_factory.mktemp("brain")
    (d / "server.py").write_text(FAKE_BRAIN)
    log = d / "requests.jsonl"
    log.write_text("")
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(d / "server.py"), str(port), str(log)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("fake brain did not start")
    yield {"url": f"http://127.0.0.1:{port}/mcp", "log": log}
    proc.kill()


@pytest.fixture(autouse=True)
def _fresh_request_log(request):
    # The fake Brain is module-scoped; clear its log so each test asserts only on its own calls
    # (under random order, another test's default searches would otherwise leak in).
    if "brain" in request.fixturenames:
        request.getfixturevalue("brain")["log"].write_text("")


def _requests(brain):
    return [json.loads(line) for line in brain["log"].read_text().splitlines() if line]


def _workspace(tmp_path, brain, exclude=("prior .*engagement",), inventory=True):
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = {"brain": {"url": brain["url"], "key_env": "BRAIN_API_KEY", "key_header": "X-API-Key"},
           "scope": {"exclude_patterns": list(exclude)}}
    if inventory:
        wb = openpyxl.Workbook()
        ws_ = wb.active
        ws_.title = "Query"
        ws_.append(["Name", "Extension", "Date modified", "Date created", "Folder Path"])
        ws_.append(["Vision", ".pdf", "2026-08-01", "2026-08-01", "https://files.example.com/Docs/"])
        ws_.append(["Charter", ".docx", "2026-08-01", "2026-08-01", "https://files.example.com/Docs/"])
        wb.save(ws / "inventory.xlsx")
        cfg["corpus"] = {"inventory_xlsx": str(ws / "inventory.xlsx")}
    (ws / "config.json").write_text(json.dumps(cfg))
    (ws / "evals.csv").write_text(CSV)
    bindir = ws / "bin"
    bindir.mkdir()
    npx = bindir / "npx"
    npx.write_text(f"#!{sys.executable}\n" + FAKE_NPX)
    npx.chmod(0o755)
    return ws


def _env(ws, key=KEY, **extra):
    env = {**os.environ, "EVALS_CONFIG": str(ws / "config.json"), "EVALS_PY": sys.executable,
           "EVALS_OUT": str(ws / "out"), "EVALS_CSV": str(ws / "evals.csv"), "PATIENCE": "2",
           "REPEAT": "2", "PATH": f"{ws / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    env.pop("BRAIN_API_KEY", None)
    if key:
        env["BRAIN_API_KEY"] = key
    env.update(extra)
    return env


def _sh(script, env, timeout=180):
    return subprocess.run(["bash", str(SKILL_DIR / script)], env=env, capture_output=True,
                          text=True, timeout=timeout)


def _files_containing(root, needle):
    return [p for p in Path(root).rglob("*") if p.is_file() and needle.encode() in p.read_bytes()]


def test_baseline_fails_the_gate_on_a_nested_superseded_prior_engagement_document(tmp_path, brain):
    ws = _workspace(tmp_path, brain)
    r = _sh("baseline.sh", _env(ws))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "scope gate        : FAIL" in r.stdout
    report = json.loads((ws / "out" / "baseline_report.json").read_text())
    assert any("Prior engagement" in s["source"] for s in report["fail_sources"])
    reqs = _requests(brain)
    assert all(q["key"] == KEY for q in reqs)
    assert all(q["args"]["latest_only"] is False for q in reqs if q["tool"] == "search_knowledge")
    assert any(q["tool"] == "list_sources" for q in reqs)


def test_baseline_passes_a_clean_brain(tmp_path, brain):
    ws = _workspace(tmp_path, brain, exclude=())
    r = _sh("baseline.sh", _env(ws))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "scope gate        : PASS" in r.stdout


def test_baseline_crash_is_not_reported_as_a_gate_result_and_stale_reports_are_not_read(tmp_path, brain):
    ws = _workspace(tmp_path, brain, inventory=False)   # reconcile cannot run without an inventory
    out = ws / "out"
    out.mkdir()
    (out / "baseline_report.json").write_text(json.dumps({"gate": "PASS", "counts": {"fail": 0, "review": 0}}))
    r = _sh("baseline.sh", _env(ws))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "PASS" not in r.stdout
    assert "baseline_reconcile" in r.stderr


def test_baseline_works_against_a_keyless_local_brain(tmp_path, brain):
    ws = _workspace(tmp_path, brain, exclude=())
    before = len(_requests(brain))
    r = _sh("baseline.sh", _env(ws, key=None))
    assert r.returncode == 0, r.stdout + r.stderr
    assert all(q["key"] is None for q in _requests(brain)[before:])


def test_run_eval_end_to_end_keeps_the_key_out_of_every_file_and_the_repo(tmp_path, brain):
    ws = _workspace(tmp_path, brain)
    snaps_before = set((SKILL_DIR / "snapshots").glob("*")) if (SKILL_DIR / "snapshots").exists() else set()
    before = len(_requests(brain))
    r = _sh("run_eval.sh", _env(ws))
    assert r.returncode == 0, r.stdout + r.stderr
    out = ws / "out"
    assert (out / "promptfooconfig.yaml").exists()
    assert _files_containing(out, KEY) == []
    snaps_after = set((SKILL_DIR / "snapshots").glob("*")) if (SKILL_DIR / "snapshots").exists() else set()
    assert snaps_after == snaps_before, "run_eval.sh wrote eval output inside the repo"
    report = (out / "eval_report.md").read_text()
    assert "accuracy: 100.00% over 2 questions" in report
    # the provider ran for real: its searches pinned latest_only=true and carried the key
    reqs = _requests(brain)[before:]
    provider_searches = [q for q in reqs if q["tool"] == "search_knowledge" and q["args"]["latest_only"] is True]
    assert provider_searches and all(q["key"] == KEY for q in provider_searches)
    results = json.loads((out / "results.json").read_text())
    assert "[Docs__Vision.pdf.md]" in results["results"]["results"][0]["response"]["output"]
    assert "GOVERNED METRICS" in json.dumps(results)


def test_run_eval_second_run_diffs_against_the_first(tmp_path, brain):
    ws = _workspace(tmp_path, brain)
    env = _env(ws)
    assert _sh("run_eval.sh", env).returncode == 0
    r = _sh("run_eval.sh", env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(list((ws / "out" / "snapshots").glob("results_*.json"))) == 2
    report = (ws / "out" / "eval_report.md").read_text()
    assert "## Regressions" in report
    assert "share a knowledge_version" in report


def test_run_eval_refuses_an_output_dir_inside_the_repo(tmp_path, brain):
    ws = _workspace(tmp_path, brain)
    r = _sh("run_eval.sh", _env(ws, EVALS_OUT=str(SKILL_DIR / "out")))
    assert r.returncode == 2
    assert "REFUSING" in r.stderr
    assert not (SKILL_DIR / "out").exists()


def test_run_eval_reports_failing_tests_instead_of_aborting(tmp_path, brain):
    """promptfoo exits 100 when a test fails; that is a result, so the diff must still run."""
    ws = _workspace(tmp_path, brain)
    r = _sh("run_eval.sh", _env(ws, FAKE_FAIL_FIRST="1"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "accuracy: 50.00% over 2 questions" in (ws / "out" / "eval_report.md").read_text()


@pytest.fixture
def brain_without_marts(brain):
    flag = Path(str(brain["log"]) + ".no_metrics")
    flag.write_text("")
    yield brain
    flag.unlink()


def test_a_brain_without_marts_still_gets_a_baseline_and_an_eval(tmp_path, brain_without_marts):
    ws = _workspace(tmp_path, brain_without_marts, exclude=())
    r = _sh("baseline.sh", _env(ws))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "metric lens skipped" in r.stderr
    r = _sh("run_eval.sh", _env(ws))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "accuracy:" in (ws / "out" / "eval_report.md").read_text()
