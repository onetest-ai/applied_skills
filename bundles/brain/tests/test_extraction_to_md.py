"""TDD red-phase tests for extraction_to_md.serialize_one().

The module extraction_to_md does NOT exist yet — import will fail (red).
Run: python -m pytest bundles/brain/tests/test_extraction_to_md.py -v
"""
import subprocess
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from extraction_to_md import serialize_one  # noqa: E402 — intentional: module doesn't exist yet


FIXTURE = {
    "session_date": "2026-09-01",
    "title": "Demo Session",
    "file_slug": "demo-session",
    "session_axes": {"products": ["Salesforce"], "sdlc_phases": ["Testing"]},
    "extractions": [
        {
            "category": "QualityRisk",
            "product": "Salesforce",
            "verbatim_quote": "The queue pickup is hard to mimic.",
            "context": "Describes automation challenge.",
            "sdlc_phase": "Testing",
            "priority": "high",
        },
        {
            "category": "ActionItem",
            "product": "UNRESOLVED",
            "verbatim_quote": "Notify us before the test run.",
            "context": "",
            "sdlc_phase": "Testing",
            "priority": "",
        },
        {
            "category": "KnowledgeGap",
            "product": "Salesforce",
            "verbatim_quote": "No baseline exists from any prior vendor.",
            "context": "Context here.",
            "sdlc_phase": "Testing",
            "priority": "high",
        },
    ],
}


def test_heading_count_equals_record_count():
    """Output must have exactly one ## heading per extraction record."""
    output = serialize_one(FIXTURE)
    headings = [line for line in output.splitlines() if line.startswith("## ")]
    assert len(headings) == 3


def test_no_raw_json_brace_in_output():
    """serialize_one must not return a raw JSON object (first char must not be '{')."""
    output = serialize_one(FIXTURE)
    assert not output.strip().startswith("{"), (
        "Output looks like raw JSON — expected formatted Markdown"
    )


def test_verbatim_wrapped_in_blockquote():
    """Each verbatim_quote must appear as a Markdown blockquote line '> <text>'."""
    output = serialize_one(FIXTURE)
    assert "> The queue pickup is hard to mimic." in output, (
        "Expected verbatim quote wrapped as '> The queue pickup is hard to mimic.'"
    )


def test_empty_priority_omitted():
    """A record with priority='' must NOT produce a 'priority: ' line in the output."""
    output = serialize_one(FIXTURE)
    lines = output.splitlines()
    for line in lines:
        assert not line.strip().startswith("priority: ") or line.strip() != "priority: ", (
            "Empty priority field must be omitted from output, not rendered as 'priority: '"
        )
    # Stricter: the string 'priority: \n' (empty value) must not appear
    assert "priority: \n" not in output
    assert "**priority:** \n" not in output


def test_missing_fields_use_defaults():
    """Minimal extraction with only category and verbatim_quote must still render
    and heading must contain 'UNRESOLVED' for unresolved product field."""
    minimal = {
        "extractions": [
            {"category": "X", "verbatim_quote": "y"},
        ]
    }
    output = serialize_one(minimal)
    headings = [line for line in output.splitlines() if line.startswith("## ")]
    assert len(headings) == 1
    assert "UNRESOLVED" in headings[0], (
        f"Heading '{headings[0]}' must contain 'UNRESOLVED' when product is missing"
    )


def test_cli_two_dir_interface(tmp_path):
    """CLI: python extraction_to_md.py --input <dir> --out <dir> must produce a .md
    file containing ## headings for each extraction record."""
    # Write a valid extraction JSON fixture
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    import json

    fixture_file = input_dir / "test_abc12345_extraction.json"
    fixture_file.write_text(json.dumps(FIXTURE), encoding="utf-8")

    script = str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index" / "extraction_to_md.py")
    result = subprocess.run(
        [sys.executable, script, "--input", str(input_dir), "--out", str(out_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"CLI exited with {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    md_files = list(out_dir.glob("*.md"))
    assert len(md_files) >= 1, "Expected at least one .md file written to --out dir"

    content = md_files[0].read_text(encoding="utf-8")
    headings = [line for line in content.splitlines() if line.startswith("## ")]
    assert len(headings) >= 1, (
        f"Output .md file has no ## headings:\n{content[:500]}"
    )
