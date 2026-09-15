"""Tests for brain_context goal/persona injection in generate_promptfoo."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

BRAIN_CTX = {
    "goal": "Omniscient Project Assistant for Philips D2C/D2B onboarding",
    "audience": "New EPAM engineers onboarding to Philips D2C/D2B projects",
}

MINIMAL_ROW = {
    "eval_id": "PROBE_001",
    "category": "PeopleOwnership",
    "scope": "probe",
    "question": "Who is Carola and what is her role?",
    "query_suffix": "Carola DevSecOps",
    "expected_answer_must_contain": "Albers Carola | DevSecOps",
    "expected_answer_must_not_contain": "not established",
    "ground_truth_source": "philips_owners.xlsx",
    "notes": "TDD probe",
    "min_items": "1",
}

PHILIPS_TAXONOMY = {
    "brain_context": BRAIN_CTX,
    "intent_taxonomy": {"l1": ["ActionItem"], "eval_config": {}},
    "probe_evals": [],
}


def test_brain_context_goal_appears_in_rubric():
    import generate_promptfoo
    rubric = generate_promptfoo.build_rubric(MINIMAL_ROW, brain_context=BRAIN_CTX)
    assert "Philips D2C/D2B" in rubric
    assert "New EPAM engineers" in rubric


def test_brain_context_goal_in_prompt_template(tmp_path):
    import generate_promptfoo

    taxo_path = tmp_path / "taxonomy.philips.json"
    taxo_path.write_text(json.dumps(PHILIPS_TAXONOMY), encoding="utf-8")

    csv_path = tmp_path / "evals.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(MINIMAL_ROW.keys()))
        w.writeheader()
        w.writerow(MINIMAL_ROW)

    js_path = tmp_path / "dummy.js"
    js_path.write_text("module.exports = async () => ({output: 'x'})")
    out_yaml = tmp_path / "config.yaml"

    generate_promptfoo.main([
        "--csv",        str(csv_path),
        "--out",        str(out_yaml),
        "--brain-url",  "http://localhost:8002",
        "--context-js", str(js_path),
        "--taxonomy",   str(taxo_path),
    ])

    content = out_yaml.read_text()
    assert "D2C/D2B" in content or "Philips" in content
