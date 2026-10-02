"""Tests for behaviors not covered by the existing unit tests.

Gaps addressed (each test documents WHY it was added):
  1. baseline.sh sensitive-data guard (exit 2 when EVALS_OUT resolves inside the repo)
  2. baseline.sh missing-config guard (exit 2 with clear message)
  3. baseline.sh missing-python guard (exit 2 with clear message)
  4. baseline.sh stage order (brain_inventory -> baseline_reconcile -> coverage_report)
  5. (the summary block and every stage end to end: test_evals_remote_e2e.py)
  6. brain_inventory.default_terms: both MCP calls fail -> configured seeds only, no exception
  7. brain_inventory.default_terms: taxonomy OK / metrics fail -> merges gracefully
  8. brain_inventory.default_terms: filters intent_l1 nodes only (not other kinds)
  9. brain_mcp_client: structured_content=None falls back to .data
 10. brain_mcp_client: structured_content present takes priority over .data
 11. load_brain_context.js: module loads and exports a function
 12. load_brain_context.js: missing question returns {error: ...}
 (the provider's behaviour is tested against the real module in test_evals_remote_context_js.py)
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SKILL_DIR = REPO / "skills" / "evals-remote"
BASELINE_SH = SKILL_DIR / "baseline.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path, url="http://fake/mcp"):
    p = tmp_path / "evals.config.json"
    p.write_text(json.dumps({"brain": {"url": url, "key_env": "BRAIN_API_KEY"}}))
    return str(p)


def _run_baseline(env_overrides, cwd=None, timeout=10):
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        ["bash", str(BASELINE_SH)],
        env=env, capture_output=True, text=True,
        cwd=str(cwd or REPO), timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 1-3: baseline.sh guards (no MCP connection needed)
# ---------------------------------------------------------------------------

def test_baseline_sensitive_data_guard_rejects_inside_repo(tmp_path):
    """EVALS_OUT pointing inside the git tree must exit 2 with 'REFUSING' in stderr."""
    result = _run_baseline({
        "EVALS_CONFIG": _cfg(tmp_path),
        "BRAIN_API_KEY": "fake",
        "EVALS_OUT": str(REPO),          # inside the repo — must be refused
        "EVALS_PY": sys.executable,
    })
    assert result.returncode == 2
    assert "REFUSING" in result.stderr


def test_baseline_sensitive_data_guard_rejects_dot(tmp_path):
    """EVALS_OUT=. (current dir = repo root) must also exit 2."""
    result = _run_baseline({
        "EVALS_CONFIG": _cfg(tmp_path),
        "BRAIN_API_KEY": "fake",
        "EVALS_OUT": ".",
        "EVALS_PY": sys.executable,
    })
    assert result.returncode == 2
    assert "REFUSING" in result.stderr


def test_baseline_missing_config_exits_nonzero(tmp_path):
    """A config path that does not exist must produce a clear error, not a traceback."""
    result = _run_baseline({
        "EVALS_CONFIG": str(tmp_path / "nonexistent.json"),
        "BRAIN_API_KEY": "fake",
        "EVALS_OUT": str(tmp_path / "out"),
        "EVALS_PY": sys.executable,
    })
    assert result.returncode == 2
    assert "not found" in result.stderr.lower() or "nonexistent" in result.stderr


def test_baseline_missing_python_exits_nonzero(tmp_path):
    """A non-existent EVALS_PY is a usage error (exit 2, never 1 = gate FAIL) with a helpful message."""
    result = _run_baseline({
        "EVALS_CONFIG": _cfg(tmp_path),
        "BRAIN_API_KEY": "fake",
        "EVALS_OUT": str(tmp_path / "out"),
        "EVALS_PY": str(tmp_path / "no_such_python"),
    })
    assert result.returncode == 2
    assert "python not found" in result.stderr.lower() or "not found" in result.stderr.lower()


# ---------------------------------------------------------------------------
# 4: stage ordering — verified via bash -x trace
# ---------------------------------------------------------------------------

def test_baseline_stage_order():
    """brain_inventory runs before baseline_reconcile, which runs before coverage_report.

    Verified statically from source line numbers: set -euo pipefail means the health
    call aborts the script before any of the three stages if MCP is unreachable, so
    a runtime trace cannot observe ordering. The contract is in the source text.
    """
    src = BASELINE_SH.read_text()
    lines = src.splitlines()

    def first_line(keyword):
        for i, ln in enumerate(lines):
            if keyword in ln:
                return i
        return None

    inv = first_line("brain_inventory.py")
    rec = first_line("baseline_reconcile.py")
    cov = first_line("coverage_report.py")

    assert inv is not None, "brain_inventory.py not in baseline.sh"
    assert rec is not None, "baseline_reconcile.py not in baseline.sh"
    assert cov is not None, "coverage_report.py not in baseline.sh"
    assert inv < rec, f"brain_inventory (line {inv}) must precede baseline_reconcile (line {rec})"
    assert rec < cov, f"baseline_reconcile (line {rec}) must precede coverage_report (line {cov})"


# ---------------------------------------------------------------------------
# 6-8: brain_inventory.default_terms silent degradation
# ---------------------------------------------------------------------------

import brain_inventory as bi
from brain_mcp_client import BrainClientError


def test_default_terms_both_fail_returns_config_seeds_only():
    """When both get_taxonomy and list_metrics throw, only the configured seeds remain — no exception."""
    calls = []

    def fail_all(name, args):
        calls.append(name)
        raise BrainClientError("network down")

    assert bi.default_terms(call=fail_all, seeds=["alpha"]) == ["alpha"]
    assert calls == ["get_taxonomy", "list_metrics"]


def test_default_terms_taxonomy_ok_metrics_fail():
    """get_taxonomy succeeds, list_metrics fails — taxonomy labels merged, no crash."""
    def partial(name, args):
        if name == "get_taxonomy":
            return {"nodes": [
                {"label": "CustomerExperience", "kind": "intent_l1"},
                {"label": "child", "kind": "other"},
            ]}
        raise BrainClientError("metrics unreachable")

    terms = bi.default_terms(call=partial, seeds=["alpha"])
    assert terms == ["alpha", "CustomerExperience"]


def test_default_terms_filters_intent_l1_only():
    """Only nodes with kind=='intent_l1' are added; all other kinds are ignored."""
    def only_taxonomy(name, args):
        if name == "get_taxonomy":
            return {"nodes": [
                {"label": "L1Node", "kind": "intent_l1"},
                {"label": "L2Node", "kind": "intent_l2"},
                {"label": "EntityNode", "kind": "entity"},
                {"label": "", "kind": "intent_l1"},      # empty label - skipped
            ]}
        return {"metrics": []}

    terms = bi.default_terms(call=only_taxonomy)
    assert "L1Node" in terms
    assert "L2Node" not in terms
    assert "EntityNode" not in terms
    assert "" not in terms


# ---------------------------------------------------------------------------
# 9-10: brain_mcp_client structured_content fallback
# ---------------------------------------------------------------------------

import brain_mcp_client as bmc


def test_client_falls_back_to_data_when_structured_content_is_none(monkeypatch):
    """When structured_content is None, the result must come from .data."""
    class _Result:
        structured_content = None
        data = {"hits": [{"source": "a.md"}]}

    async def _fake_call_tool(name, args):
        return _Result()

    class _FakeClient:
        def __init__(self, transport): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def call_tool(self, name, args): return _Result()

    monkeypatch.setattr(bmc, "Client", _FakeClient)
    monkeypatch.setattr(bmc, "StreamableHttpTransport", lambda url, headers=None: None)
    out = bmc.call_tool("search_knowledge", {}, url="http://x/mcp", key="k")
    assert out == {"hits": [{"source": "a.md"}]}


def test_client_structured_content_takes_priority_over_data(monkeypatch):
    """When both structured_content and data are present, structured_content wins."""
    class _Result:
        structured_content = {"from": "structured"}
        data = {"from": "data"}

    class _FakeClient:
        def __init__(self, transport): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def call_tool(self, name, args): return _Result()

    monkeypatch.setattr(bmc, "Client", _FakeClient)
    monkeypatch.setattr(bmc, "StreamableHttpTransport", lambda url, headers=None: None)
    out = bmc.call_tool("health", {}, url="http://x/mcp", key="k")
    assert out == {"from": "structured"}


# ---------------------------------------------------------------------------
# 11-16: load_brain_context.js
# ---------------------------------------------------------------------------

def _node(script, timeout=10):
    return subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )


_JS_DIR = str(SKILL_DIR)


def test_load_brain_context_exports_function():
    """The JS module must export a single async function (the promptfoo context provider)."""
    result = _node(
        f"const m = require('{_JS_DIR}/load_brain_context.js');"
        "console.log(typeof m);"
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "function"


def test_load_brain_context_missing_question_returns_error():
    """Calling the provider with no question must return {{error: '...'}} immediately."""
    result = _node(
        f"const ctx = require('{_JS_DIR}/load_brain_context.js');"
        "ctx('brain_context', '', {}).then(r => console.log(JSON.stringify(r)));"
    )
    assert result.returncode == 0
    out = json.loads(result.stdout.strip())
    assert "error" in out
    assert "question" in out["error"].lower()
