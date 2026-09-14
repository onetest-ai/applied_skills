"""TDD red-phase tests for VTT/SRT parsing in parse_corpus.parse_one().

SRT and VTT support does NOT exist yet in parse_one() — tests must fail (red).
Run: python -m pytest skills/corpus-taxonomy-extraction/tests/test_parse_corpus_transcripts.py -v
"""
import os
import sys
import pathlib
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from parse_corpus import parse_one  # noqa: E402


SRT_CONTENT = """\
1
00:00:00,310 --> 00:00:01,950
We're doing some testing in UAT.

2
00:00:03,310 --> 00:00:09,710
Performance testing is a priority.

3
00:00:10,190 --> 00:00:12,030
We need to establish a baseline.
"""

VTT_CONTENT = """\
WEBVTT

abc123-0
00:00:04.560 --> 00:00:05.520
I am in touch with the team.

def456-0
00:00:05.560 --> 00:00:10.000
We need to review the infrastructure.

ghi789-0
00:00:11.000 --> 00:00:15.000
The deployment process needs documentation.
"""


@pytest.fixture()
def srt_path(tmp_path):
    p = tmp_path / "test.srt"
    p.write_text(SRT_CONTENT, encoding="utf-8")
    return p


@pytest.fixture()
def vtt_path(tmp_path):
    p = tmp_path / "test.vtt"
    p.write_text(VTT_CONTENT, encoding="utf-8")
    return p


def test_srt_parse_returns_markdown(srt_path):
    """parse_one() on an SRT file must return (markdown_content, 'transcript-etl')
    where the content contains ## headings."""
    content, method = parse_one(str(srt_path), 20, 8)
    assert method == "transcript-etl", f"Expected method 'transcript-etl', got '{method}'"
    assert content is not None, "Content must not be None"
    assert "## " in content, f"Expected ## headings in output. Got:\n{content[:500]}"


def test_srt_heading_count_equals_cue_count(srt_path):
    """SRT with 3 cues must produce exactly 3 ## headings in the output Markdown."""
    content, _ = parse_one(str(srt_path), 20, 8)
    headings = [line for line in content.splitlines() if line.startswith("## ")]
    assert len(headings) == 3, (
        f"Expected 3 headings (one per cue), got {len(headings)}.\nContent:\n{content}"
    )


def test_vtt_parse_returns_markdown(vtt_path):
    """parse_one() on a VTT file must return (markdown_content, 'transcript-etl')
    where the content contains ## headings."""
    content, method = parse_one(str(vtt_path), 20, 8)
    assert method == "transcript-etl", f"Expected method 'transcript-etl', got '{method}'"
    assert content is not None, "Content must not be None"
    assert "## " in content, f"Expected ## headings in output. Got:\n{content[:500]}"


def test_vtt_heading_count_equals_cue_count(vtt_path):
    """VTT with 3 cues must produce exactly 3 ## headings (multi-line cues merged per UUID)."""
    content, _ = parse_one(str(vtt_path), 20, 8)
    headings = [line for line in content.splitlines() if line.startswith("## ")]
    assert len(headings) == 3, (
        f"Expected 3 headings (one per VTT cue UUID), got {len(headings)}.\nContent:\n{content}"
    )


def test_vtt_voice_tag_is_extracted_as_speaker(tmp_path):
    path = tmp_path / "2025-09-21-meeting.vtt"
    path.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v Karen>Use Gatling for the baseline.\n", encoding="utf-8")
    content, _ = parse_one(str(path), 20, 8)
    assert "— Karen" in content
    assert "<!-- speaker: Karen -->" in content
    assert "<v Karen>" not in content


def test_srt_name_prefix_is_extracted_as_speaker(tmp_path):
    path = tmp_path / "meeting.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:03,000\nKaren: Use Gatling for the baseline.\n", encoding="utf-8")
    content, _ = parse_one(str(path), 20, 8)
    assert "— Karen" in content
    assert "<!-- speaker: Karen -->" in content
