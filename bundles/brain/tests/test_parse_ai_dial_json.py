"""Tests for AI DIAL conversation JSON parsing in parse_corpus."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

AI_DIAL_JSON = {
    "version": 1,
    "history": [
        {
            "id": "conversations/abc123",
            "name": "ALBATROS_EMAILS",
            "messages": [
                {"role": "user",     "content": "Who is Alex?"},
                {"role": "assistant","content": "Alex Rivera is the Platform Lead for the Alpha portfolio."},
                {"role": "user",     "content": "What is the Beta stream?"},
                {"role": "assistant","content": "Beta stream handles platform integrations. Status: 100% Migrated."},
            ]
        }
    ],
    "folders": []
}


def test_ai_dial_json_parse_returns_markdown(tmp_path):
    import parse_corpus
    json_file = tmp_path / "ALBATROS_EMAILS.json"
    json_file.write_text(json.dumps(AI_DIAL_JSON), encoding="utf-8")

    result = parse_corpus._parse_ai_dial_json(str(json_file))

    assert result is not None
    assert "Alex" in result
    assert "Beta stream" in result


def test_ai_dial_json_only_includes_assistant_content(tmp_path):
    import parse_corpus
    json_file = tmp_path / "test_chat.json"
    json_file.write_text(json.dumps(AI_DIAL_JSON), encoding="utf-8")

    result = parse_corpus._parse_ai_dial_json(str(json_file))
    assert "Platform Lead for the Alpha portfolio" in result


def test_ai_dial_json_parse_corpus_accepts_json_format(tmp_path):
    import parse_corpus

    src_dir = tmp_path / "corpus"; src_dir.mkdir()
    out_dir = tmp_path / "parsed";  out_dir.mkdir()
    (src_dir / "albatros.json").write_text(json.dumps(AI_DIAL_JSON), encoding="utf-8")

    parse_corpus.main([
        "--corpus",  str(src_dir),
        "--out",     str(out_dir),
        "--formats", "json",
    ])

    md_files = list(out_dir.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "Alex" in content
