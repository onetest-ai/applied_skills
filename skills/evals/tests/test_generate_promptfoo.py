"""Tests for generate_promptfoo.py."""
import csv
import sys
import yaml
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_promptfoo as GP


def _write_csv(tmp_path, rows):
    fieldnames = [
        "eval_id", "category", "scope", "question",
        "expected_answer_must_contain", "expected_answer_must_not_contain",
        "ground_truth_source", "notes", "min_items",
    ]
    p = tmp_path / "evals.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return str(p)


def _sample_row(**kw):
    base = {
        "eval_id": "E001", "category": "recall", "scope": "single-session",
        "question": "What action items were identified?",
        "expected_answer_must_contain": "logging | monitoring",
        "expected_answer_must_not_contain": "hallucinated",
        "ground_truth_source": "abc123", "notes": "ActionItem", "min_items": "1",
    }
    base.update(kw)
    return base


def test_generates_three_providers(tmp_path):
    csv_path = _write_csv(tmp_path, [_sample_row()])
    out = tmp_path / "config.yaml"
    js = tmp_path / "ctx.js"
    js.write_text("module.exports = async function() { return {output: 'x'}; }")
    GP.main(["--csv", csv_path, "--out", str(out),
             "--brain-url", "http://localhost:8002",
             "--context-js", str(js)])
    cfg = yaml.safe_load(out.read_text())
    provider_ids = [p["id"] if isinstance(p, dict) else p for p in cfg["providers"]]
    assert any("haiku" in p for p in provider_ids)
    assert any("sonnet" in p for p in provider_ids)
    assert any("opus" in p for p in provider_ids)


def test_each_test_has_context_var(tmp_path):
    csv_path = _write_csv(tmp_path, [_sample_row(), _sample_row(eval_id="E002")])
    out = tmp_path / "config.yaml"
    js = tmp_path / "ctx.js"
    js.write_text("module.exports = async function() { return {output: 'x'}; }")
    GP.main(["--csv", csv_path, "--out", str(out),
             "--brain-url", "http://localhost:8002",
             "--context-js", str(js)])
    cfg = yaml.safe_load(out.read_text())
    for test in cfg["tests"]:
        assert "context" in test["vars"], "every test must have context var"
        assert test["vars"]["context"].startswith("file://")


def test_llm_rubric_assertion_present(tmp_path):
    csv_path = _write_csv(tmp_path, [_sample_row()])
    out = tmp_path / "config.yaml"
    js = tmp_path / "ctx.js"
    js.write_text("module.exports = async function() { return {output: 'x'}; }")
    GP.main(["--csv", csv_path, "--out", str(out),
             "--brain-url", "http://localhost:8002",
             "--context-js", str(js)])
    cfg = yaml.safe_load(out.read_text())
    for test in cfg["tests"]:
        assert any(a["type"] == "llm-rubric" for a in test["assert"])
