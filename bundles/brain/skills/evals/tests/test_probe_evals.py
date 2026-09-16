"""Tests for probe_evals loading from taxonomy (TDD: RED written before implementation)."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SAMPLE_TAXONOMY = {
    "brain_context": {
        "goal": "Project Knowledge Assistant for onboarding",
        "audience": "New engineers onboarding to platform projects",
    },
    "intent_taxonomy": {
        "l1": ["ActionItem", "ProcessObservation"],
        "eval_config": {}
    },
    "probe_evals": [
        {
            "eval_id": "PROBE_001",
            "category": "PeopleOwnership",
            "scope": "probe",
            "question": "Who is Alex and what is her role?",
            "query_suffix": "Alex Rivera Platform Lead",
            "expected_answer_must_contain": "Rivera Alex | Platform Lead",
            "expected_answer_must_not_contain": "not established,not found,no evidence",
            "ground_truth_source": "stakeholders.xlsx",
            "notes": "TDD probe — RED without xlsx GREEN after incremental index",
            "min_items": 1,
        }
    ]
}


def test_probe_evals_included_in_generate_evals_output(tmp_path):
    """generate_evals.main() appends probe_evals rows when taxonomy has probe_evals key."""
    import generate_evals

    taxo_path = tmp_path / "taxonomy.philips.json"
    taxo_path.write_text(json.dumps(SAMPLE_TAXONOMY), encoding="utf-8")

    ext_dir = tmp_path / "extractions"
    ext_dir.mkdir()

    out_csv = tmp_path / "evals.csv"
    generate_evals.main([
        "--extractions", str(ext_dir),
        "--taxonomy",    str(taxo_path),
        "--out",         str(out_csv),
    ])

    rows = list(csv.DictReader(out_csv.open()))
    probe_rows = [r for r in rows if r.get("eval_id", "").startswith("PROBE_")]
    assert len(probe_rows) >= 1, f"Expected probe eval rows, got: {rows}"
    assert probe_rows[0]["eval_id"] == "PROBE_001"
    assert "Alex" in probe_rows[0]["question"]


def test_probe_eval_row_has_required_fields(tmp_path):
    """Probe eval row has all required CSV fields."""
    import generate_evals

    taxo_path = tmp_path / "taxonomy.philips.json"
    taxo_path.write_text(json.dumps(SAMPLE_TAXONOMY), encoding="utf-8")
    ext_dir = tmp_path / "extractions"; ext_dir.mkdir()
    out_csv = tmp_path / "evals.csv"

    generate_evals.main([
        "--extractions", str(ext_dir),
        "--taxonomy",    str(taxo_path),
        "--out",         str(out_csv),
    ])

    rows = list(csv.DictReader(out_csv.open()))
    probe = next(r for r in rows if r.get("eval_id", "").startswith("PROBE_"))
    for field in ["eval_id", "category", "scope", "question", "query_suffix",
                  "expected_answer_must_contain", "ground_truth_source", "min_items"]:
        assert probe.get(field), f"Missing field: {field}"
