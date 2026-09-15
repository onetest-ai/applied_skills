"""Tests for generate_promptfoo.py."""
import csv
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_promptfoo as GP


def _write_csv(tmp_path, rows):
    fieldnames = [
        "eval_id", "category", "scope", "question", "query_suffix",
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
        "query_suffix": "owners decisions commitments",
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


def test_query_suffix_forwarded_to_vars(tmp_path):
    # When a query_suffix is present in CSV, derived terms are appended to it.
    # _sample_row uses expected_answer_must_contain = "logging | monitoring",
    # so derived terms ("logging monitoring") are appended to the existing suffix.
    csv_path = _write_csv(tmp_path, [_sample_row(query_suffix="owners decisions commitments")])
    out = tmp_path / "config.yaml"
    js = tmp_path / "ctx.js"
    js.write_text("module.exports = async function() { return {output: 'x'}; }")
    GP.main(["--csv", csv_path, "--out", str(out),
             "--brain-url", "http://localhost:8002",
             "--context-js", str(js)])
    cfg = yaml.safe_load(out.read_text())
    for test in cfg["tests"]:
        suffix = test["vars"].get("query_suffix", "")
        assert suffix.startswith("owners decisions commitments"), (
            "existing suffix must be preserved as prefix, got: {!r}".format(suffix)
        )
        assert "logging" in suffix or "monitoring" in suffix, (
            "derived fact keywords must be appended, got: {!r}".format(suffix)
        )


def test_missing_query_suffix_derives_from_facts(tmp_path):
    # Row with empty query_suffix should derive terms from expected_answer_must_contain
    # _sample_row uses expected_answer_must_contain = "logging | monitoring"
    csv_path = _write_csv(tmp_path, [_sample_row(query_suffix="")])
    out = tmp_path / "config.yaml"
    js = tmp_path / "ctx.js"
    js.write_text("module.exports = async function() { return {output: 'x'}; }")
    GP.main(["--csv", csv_path, "--out", str(out),
             "--brain-url", "http://localhost:8002",
             "--context-js", str(js)])
    cfg = yaml.safe_load(out.read_text())
    for test in cfg["tests"]:
        assert "query_suffix" in test["vars"], "query_suffix must always be present"
        suffix = test["vars"]["query_suffix"]
        assert len(suffix) > 0, "derived query_suffix must be non-empty"
        assert "logging" in suffix.lower() or "monitoring" in suffix.lower(), (
            f"derived suffix should contain keywords from facts, got: {suffix!r}"
        )


# ---------------------------------------------------------------------------
# New tests for generic rubric threshold (Task 4)
# ---------------------------------------------------------------------------

def test_build_rubric_2_of_3_for_three_facts():
    # N-1 of N threshold: n=3 → AT LEAST 2 of 3
    row = {
        "eval_id": "E001", "category": "foo", "scope": "single-session",
        "question": "Q?",
        "expected_answer_must_contain": "fact A | fact B | fact C",
        "expected_answer_must_not_contain": "hallucinated",
        "ground_truth_source": "src", "notes": "", "min_items": "1",
    }
    rubric = GP.build_rubric(row)
    assert "AT LEAST 2 of 3" in rubric, "Expected threshold text, got: {}".format(rubric[:200])
    assert "EVERY" not in rubric, "Old ALL-required text must not appear"


def test_build_rubric_threshold_for_two_facts():
    # N-1 of N threshold: n=2 → AT LEAST 1 of 2
    row = {
        "eval_id": "E002", "category": "foo", "scope": "single-session",
        "question": "Q?",
        "expected_answer_must_contain": "fact A | fact B",
        "expected_answer_must_not_contain": "hallucinated",
        "ground_truth_source": "src", "notes": "", "min_items": "1",
    }
    rubric = GP.build_rubric(row)
    assert "AT LEAST 1 of 2" in rubric, (
        "Two-fact rubric should use N-1=1 threshold, got: {}".format(rubric[:200])
    )


def test_build_rubric_one_fact_requires_all():
    # N-1 of N threshold: n=1 → max(1, 0)=1 → ALL 1 (no AT LEAST needed)
    row = {
        "eval_id": "E003", "category": "foo", "scope": "single-session",
        "question": "Q?",
        "expected_answer_must_contain": "single fact only",
        "expected_answer_must_not_contain": "hallucinated",
        "ground_truth_source": "src", "notes": "", "min_items": "1",
    }
    rubric = GP.build_rubric(row)
    # n=1: threshold = max(1, 1-1)=max(1,0)=1, ALL 1 fact required
    assert "ALL 1" in rubric or "AT LEAST" not in rubric


def test_derive_query_terms_extracts_keywords():
    terms = GP._derive_query_terms("Technology stream led by Rafael | CX stream led by Bill")
    assert "Technology" in terms or "technology" in terms.lower()
    assert "Rafael" in terms or "rafael" in terms.lower()


def test_build_persona_block_empty_without_brain_context():
    # When brain_context is None, build_persona_block must return empty string.
    # This ensures backward-compat: no taxonomy → no injection.
    result = GP.build_persona_block(None)
    assert result == "", "build_persona_block(None) must return empty string, got: {!r}".format(result)


def test_build_persona_block_with_persona_and_stakeholders():
    # When brain_context has persona + stakeholders, block must contain both.
    brain_context = {
        "persona": "You are a Solution Architect I at EPAM.",
        "stakeholders": {
            "Sanjeev Patil": "Client-Side Coordinator",
            "Carola Albers": "DevSecOps Manager D2C",
        },
    }
    result = GP.build_persona_block(brain_context)
    assert "Solution Architect" in result, "persona text must appear in block"
    assert "Sanjeev Patil" in result, "stakeholder names must appear in block"
    assert "Carola Albers" in result, "all stakeholders must appear in block"
    assert "DevSecOps Manager" in result, "stakeholder roles must appear in block"


def test_prompt_template_without_taxonomy_is_unchanged(tmp_path):
    # Without --taxonomy, the emitted prompts list must equal PROMPT_TEMPLATE exactly.
    csv_path = _write_csv(tmp_path, [_sample_row()])
    out = tmp_path / "config.yaml"
    js = tmp_path / "ctx.js"
    js.write_text("module.exports = async function() { return {output: 'x'}; }")
    GP.main(["--csv", csv_path, "--out", str(out),
             "--brain-url", "http://localhost:8002",
             "--context-js", str(js)])
    cfg = yaml.safe_load(out.read_text())
    assert cfg["prompts"] == [GP.PROMPT_TEMPLATE], (
        "Without taxonomy, prompt template must be the canonical PROMPT_TEMPLATE string"
    )


def test_prompt_template_with_taxonomy_injects_project_context(tmp_path):
    # With --taxonomy containing persona+stakeholders, the emitted prompt must include
    # a <project_context> section before <question>.
    import json
    taxonomy = {
        "brain_context": {
            "goal": "Omniscient Project Assistant",
            "audience": "New EPAM engineers",
            "persona": "You are Solution Architect I at EPAM.",
            "stakeholders": {
                "Sanjeev Patil": "Client-Side Coordinator",
                "Carola Albers": "DevSecOps Manager D2C",
            },
        }
    }
    taxo_path = tmp_path / "taxonomy.json"
    taxo_path.write_text(json.dumps(taxonomy), encoding="utf-8")
    csv_path = _write_csv(tmp_path, [_sample_row()])
    out = tmp_path / "config.yaml"
    js = tmp_path / "ctx.js"
    js.write_text("module.exports = async function() { return {output: 'x'}; }")
    GP.main(["--csv", csv_path, "--out", str(out),
             "--brain-url", "http://localhost:8002",
             "--context-js", str(js),
             "--taxonomy", str(taxo_path)])
    cfg = yaml.safe_load(out.read_text())
    prompt = cfg["prompts"][0]
    assert "<project_context>" in prompt, "taxonomy with persona must inject <project_context> block"
    assert "Solution Architect" in prompt, "persona text must appear in the prompt"
    assert "Sanjeev Patil" in prompt, "stakeholder names must appear in the prompt"
    assert "<question>" in prompt, "<question> tag must still be present after injection"
    assert prompt.index("<project_context>") < prompt.index("<question>"), (
        "<project_context> must appear before <question> in the prompt"
    )


def test_generate_promptfoo_vars_include_derived_suffix(tmp_path):
    evals_csv = tmp_path / "evals.csv"
    context_js = tmp_path / "ctx.js"
    context_js.write_text("module.exports = async () => ({ output: '' });")
    rows = [{
        "eval_id": "E001", "category": "team_roster", "scope": "single-session",
        "question": "Who led the technology stream?",
        "query_suffix": "",
        "expected_answer_must_contain": "Technology stream led by Rafael | CX led by Bill Gastrock | Business process stream Claudia",
        "expected_answer_must_not_contain": "hallucinated",
        "ground_truth_source": "Sep09.vtt.md", "notes": "db-mode", "min_items": "1",
    }]
    with evals_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    out = str(tmp_path / "config.yaml")
    GP.main(["--csv", str(evals_csv), "--out", out,
             "--brain-url", "http://localhost:9100",
             "--context-js", str(context_js)])
    cfg = yaml.safe_load(open(out))
    test_vars = cfg["tests"][0]["vars"]
    assert "query_suffix" in test_vars
    suffix = test_vars["query_suffix"]
    assert len(suffix) > 0, "query_suffix must be derived from facts when empty in CSV"
