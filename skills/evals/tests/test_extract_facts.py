"""TDD tests for extract_facts.py — RED phase written before implementation."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


TAXONOMY_L1 = ["ActionItem", "KnowledgeGap", "QualityRisk"]

SAMPLE_MD = """# Meeting 2026-09-10

Karen: We need to assign the logging task to Rafael by Friday.
Rafael: There's a gap in our understanding of the retry logic.
Karen: The deployment quality risk is that we haven't tested under load.
"""

# A fake LLM that returns valid extraction JSON
def _fake_llm(prompt: str) -> str:
    return json.dumps([
        {"category": "ActionItem", "verbatim_quote": "assign the logging task to Rafael by Friday"},
        {"category": "KnowledgeGap", "verbatim_quote": "gap in our understanding of the retry logic"},
        {"category": "QualityRisk", "verbatim_quote": "haven't tested under load"},
    ])


def test_extract_facts_returns_list():
    """extract_facts_from_md returns a non-empty list."""
    import extract_facts
    result = extract_facts.extract_facts_from_md(SAMPLE_MD, TAXONOMY_L1, _fake_llm)
    assert isinstance(result, list)
    assert len(result) > 0


def test_extract_facts_schema_each_item():
    """Each item in the result has category and verbatim_quote keys."""
    import extract_facts
    result = extract_facts.extract_facts_from_md(SAMPLE_MD, TAXONOMY_L1, _fake_llm)
    for item in result:
        assert "category" in item, f"missing 'category' in {item}"
        assert "verbatim_quote" in item, f"missing 'verbatim_quote' in {item}"


def test_extract_facts_category_in_taxonomy():
    """All returned categories are from the supplied taxonomy list."""
    import extract_facts
    result = extract_facts.extract_facts_from_md(SAMPLE_MD, TAXONOMY_L1, _fake_llm)
    for item in result:
        assert item["category"] in TAXONOMY_L1, (
            f"category '{item['category']}' not in taxonomy"
        )


def test_extract_facts_verbatim_quote_nonempty():
    """No extraction has an empty verbatim_quote."""
    import extract_facts
    result = extract_facts.extract_facts_from_md(SAMPLE_MD, TAXONOMY_L1, _fake_llm)
    for item in result:
        assert item["verbatim_quote"].strip(), f"empty verbatim_quote in {item}"


def test_run_extractions_writes_files(tmp_path):
    """run_extractions writes one *_extraction.json per MD file."""
    import extract_facts

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    (parsed_dir / "meeting_sep10.md").write_text(SAMPLE_MD, encoding="utf-8")

    out_dir = tmp_path / "extractions"
    taxonomy = {"intent_taxonomy": {"l1": TAXONOMY_L1}}

    extract_facts.run_extractions(str(parsed_dir), taxonomy, str(out_dir), llm_fn=_fake_llm)

    output_files = list(out_dir.glob("*_extraction.json"))
    assert len(output_files) == 1, f"expected 1 file, got {output_files}"


def test_run_extractions_output_schema(tmp_path):
    """Output JSON has file_slug and extractions list."""
    import extract_facts

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    (parsed_dir / "sep10_standup.md").write_text(SAMPLE_MD, encoding="utf-8")

    out_dir = tmp_path / "extractions"
    taxonomy = {"intent_taxonomy": {"l1": TAXONOMY_L1}}

    extract_facts.run_extractions(str(parsed_dir), taxonomy, str(out_dir), llm_fn=_fake_llm)

    outfile = list(out_dir.glob("*_extraction.json"))[0]
    data = json.loads(outfile.read_text(encoding="utf-8"))
    assert "file_slug" in data
    assert "extractions" in data
    assert isinstance(data["extractions"], list)
    assert len(data["extractions"]) > 0


def test_run_extractions_slug_from_filename(tmp_path):
    """file_slug in output JSON matches the md filename stem."""
    import extract_facts

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    (parsed_dir / "q3_kickoff.md").write_text(SAMPLE_MD, encoding="utf-8")

    out_dir = tmp_path / "extractions"
    taxonomy = {"intent_taxonomy": {"l1": TAXONOMY_L1}}

    extract_facts.run_extractions(str(parsed_dir), taxonomy, str(out_dir), llm_fn=_fake_llm)

    outfile = list(out_dir.glob("*_extraction.json"))[0]
    data = json.loads(outfile.read_text(encoding="utf-8"))
    assert data["file_slug"] == "q3_kickoff"


def test_run_extractions_llm_returns_bad_json(tmp_path):
    """If LLM returns malformed JSON, the file is skipped (no crash)."""
    import extract_facts

    def _bad_llm(prompt):
        return "not valid json {"

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    (parsed_dir / "bad_meeting.md").write_text(SAMPLE_MD, encoding="utf-8")

    out_dir = tmp_path / "extractions"
    taxonomy = {"intent_taxonomy": {"l1": TAXONOMY_L1}}

    # Should not raise
    extract_facts.run_extractions(str(parsed_dir), taxonomy, str(out_dir), llm_fn=_bad_llm)

    # No output file written for the bad slug
    output_files = list(out_dir.glob("*_extraction.json"))
    assert len(output_files) == 0, "bad JSON should produce no output file"


def test_run_extractions_empty_parsed_dir(tmp_path):
    """Empty parsed dir produces no output and no error."""
    import extract_facts

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    out_dir = tmp_path / "extractions"
    taxonomy = {"intent_taxonomy": {"l1": TAXONOMY_L1}}

    extract_facts.run_extractions(str(parsed_dir), taxonomy, str(out_dir), llm_fn=_fake_llm)

    assert not list(out_dir.glob("*_extraction.json"))


def test_empty_fence_falls_back_to_full_response():
    """LLM returns empty fence block followed by actual JSON — must parse correctly."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from extract_facts import extract_facts_from_md

    def llm_fn(prompt):
        # Two consecutive fences with no content, then valid JSON after
        return '```\n```\n[{"category": "Decision", "verbatim_quote": "We chose Python."}]'

    taxonomy_l1 = ["Decision"]
    result = extract_facts_from_md("some md text", taxonomy_l1, llm_fn)
    assert len(result) == 1
    assert result[0]["verbatim_quote"] == "We chose Python."
