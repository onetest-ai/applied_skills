"""load_brain_context.js, exercised through the REAL module with an injected fake MCP.

Each test loads the shipped file (never a pasted copy of a function), so a change to the
provider is what the test sees.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = str(Path(__file__).resolve().parent.parent / "skills" / "evals-remote" / "load_brain_context.js")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

FAKE = """
const ctx = require(%(js)s);
const calls = [];
const catalog = {metrics: [
  {name: 'handle_seconds', unit: 's', description: 'Average handle time per contact', last_month: '2026-08'},
  {name: 'survey_score', unit: 'pts', description: 'Customer survey score', last_month: '2026-08'},
]};
async function fakeCall(tool, args) {
  calls.push({tool, args});
  if (tool === 'list_metrics') return catalog;
  if (tool === 'get_metric') return {rows: [
    {grain: 'overall', entity: 'all', month: '2026-07', value: 301, source_file: 'jul.xlsx'},
    {grain: 'overall', entity: 'all', month: '2026-08', value: 295, source_file: 'aug.xlsx',
     restated: true, other_reported_values: [{value: 290, source_file: 'aug_v1.xlsx'}]},
  ]};
  if (tool === 'search_knowledge') return {hits: [{source: 'a.md', text: 'A'}, {source: 'b.md', text: 'B'}]};
  return {};
}
(async () => {
  const out = await ctx._internal.buildContext(%(q)s, {synonyms: %(syn)s}, fakeCall);
  console.log(JSON.stringify({out, calls}));
})();
"""


def _run(question, synonyms=None):
    script = FAKE % {"js": json.dumps(JS), "q": json.dumps(question), "syn": json.dumps(synonyms or {})}
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_module_exports_the_provider_function():
    r = subprocess.run(["node", "-e", f"console.log(typeof require({json.dumps(JS)}))"],
                       capture_output=True, text=True, timeout=10)
    assert r.stdout.strip() == "function"


def test_searches_pin_latest_only_true_explicitly():
    res = _run("What drives repeat contacts?")
    searches = [c["args"] for c in res["calls"] if c["tool"] == "search_knowledge"]
    assert len(searches) == 2
    assert all(a["latest_only"] is True for a in searches)


def test_hits_from_both_searches_are_merged_once():
    out = _run("What drives repeat contacts?")["out"]
    assert out.count("[b.md]") == 1


def test_metric_rows_are_the_most_recent_window():
    res = _run("What was the average handle time?")
    gm = [c["args"] for c in res["calls"] if c["tool"] == "get_metric"]
    assert gm and gm[0]["name"] == "handle_seconds"
    # anchored on the catalog's last_month, not on the oldest rows of the table
    assert gm[0]["start_month"] == "2026-03"
    assert "2026-08 = 295" in res["out"]


def test_restated_values_are_shown_not_hidden():
    out = _run("What was the average handle time?")["out"]
    assert "restated" in out and "290" in out and "aug_v1.xlsx" in out


def test_no_domain_synonyms_unless_configured():
    plain = _run("What is our HT?")
    assert not any(c["tool"] == "get_metric" for c in plain["calls"])
    configured = _run("What is our HT?", synonyms={"ht": "handle"})
    assert any(c["args"].get("name") == "handle_seconds" for c in configured["calls"] if c["tool"] == "get_metric")


def test_at_most_three_metrics_are_attached():
    res = _run("handle survey contact customer score time average")
    assert len([c for c in res["calls"] if c["tool"] == "get_metric"]) <= 3


def test_api_key_travels_in_the_environment_never_argv():
    script = (f"const m = require({json.dumps(JS)})._internal;"
              "const s = m.clientSpawn('search_knowledge', {query: 'q'}, 'http://x/mcp', 'SECRET-KEY');"
              "console.log(JSON.stringify({argv: s.argv, env: s.env.BRAIN_API_KEY}));")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    spawn = json.loads(r.stdout)
    assert "SECRET-KEY" not in " ".join(spawn["argv"])
    assert spawn["env"] == "SECRET-KEY"
