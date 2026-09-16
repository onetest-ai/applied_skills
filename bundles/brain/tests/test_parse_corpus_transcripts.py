"""TDD red-phase tests for VTT/SRT parsing in parse_corpus.parse_one().

SRT and VTT support does NOT exist yet in parse_one() — tests must fail (red).
Run: python -m pytest bundles/brain/tests/test_parse_corpus_transcripts.py -v
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
    path.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v Alex>Use Gatling for the baseline.\n", encoding="utf-8")
    content, _ = parse_one(str(path), 20, 8)
    assert "— Alex" in content
    assert "<!-- speaker: Alex -->" in content
    assert "<v Alex>" not in content


def test_srt_name_prefix_is_extracted_as_speaker(tmp_path):
    path = tmp_path / "meeting.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:03,000\nAlex: Use Gatling for the baseline.\n", encoding="utf-8")
    content, _ = parse_one(str(path), 20, 8)
    assert "— Alex" in content
    assert "<!-- speaker: Alex -->" in content


# ---------------------------------------------------------------------------
# merge_cues tests (Task 1)
# ---------------------------------------------------------------------------

VTT_SAME_SPEAKER = """\
WEBVTT

aaa-0
00:00:01.000 --> 00:00:02.000
<v Alice>First sentence.

aaa-1
00:00:02.500 --> 00:00:03.500
<v Alice>Second sentence.

aaa-2
00:00:04.000 --> 00:00:05.000
<v Alice>Third sentence.

bbb-0
00:00:06.000 --> 00:00:07.000
<v Bob>Bob speaks now.

bbb-1
00:00:07.500 --> 00:00:08.500
<v Bob>Bob continues.
"""


def test_merge_cues_3_groups_same_speaker_into_one_heading(tmp_path):
    """merge_cues=3: Alice's 3 cues -> 1 heading, Bob's 2 cues -> 1 heading = 2 headings total."""
    p = tmp_path / "merged.vtt"
    p.write_text(VTT_SAME_SPEAKER, encoding="utf-8")
    content, _ = parse_one(str(p), 20, 8, merge_cues=3)
    headings = [l for l in content.splitlines() if l.startswith("## ")]
    assert len(headings) == 2, "Expected 2 headings, got %d:\n%s" % (len(headings), content)


def test_merge_cues_default_1_preserves_per_cue_headings(tmp_path):
    """merge_cues=1 (default): same VTT produces one heading per UUID group (2 total after UUID merge)."""
    p = tmp_path / "unmerged.vtt"
    p.write_text(VTT_SAME_SPEAKER, encoding="utf-8")
    content, _ = parse_one(str(p), 20, 8, merge_cues=1)
    headings = [l for l in content.splitlines() if l.startswith("## ")]
    # aaa-0/1/2 all collapse to UUID base "aaa" -> 1 heading; bbb-0/1 -> 1 heading = 2 total
    assert len(headings) == 2, "Expected 2 headings (UUID-merged), got %d:\n%s" % (len(headings), content)


def test_merge_cues_speaker_change_flushes_buffer(tmp_path):
    """merge_cues=5: even with budget=5, Alice->Bob boundary forces a flush = 2 headings."""
    p = tmp_path / "flush.vtt"
    p.write_text(VTT_SAME_SPEAKER, encoding="utf-8")
    content, _ = parse_one(str(p), 20, 8, merge_cues=5)
    headings = [l for l in content.splitlines() if l.startswith("## ")]
    assert len(headings) == 2, "Expected 2 headings (speaker boundary flush), got %d" % len(headings)


def test_merge_cues_merged_heading_contains_all_text(tmp_path):
    """With merge_cues=3, Alice's merged heading must contain all three cue texts."""
    p = tmp_path / "text_check.vtt"
    p.write_text(VTT_SAME_SPEAKER, encoding="utf-8")
    content, _ = parse_one(str(p), 20, 8, merge_cues=3)
    assert "First sentence" in content
    assert "Second sentence" in content
    assert "Third sentence" in content


SRT_SAME_SPEAKER = """\
1
00:00:01,000 --> 00:00:02,000
Alice: Hello team.

2
00:00:03,000 --> 00:00:04,000
Alice: Let's start.

3
00:00:05,000 --> 00:00:06,000
Bob: Sounds good.
"""


def test_srt_merge_cues_groups_same_speaker(tmp_path):
    """SRT with merge_cues=2: Alice's 2 cues -> 1 heading, Bob's 1 cue -> 1 heading = 2 total."""
    p = tmp_path / "merged.srt"
    p.write_text(SRT_SAME_SPEAKER, encoding="utf-8")
    content, _ = parse_one(str(p), 20, 8, merge_cues=2)
    headings = [l for l in content.splitlines() if l.startswith("## ")]
    assert len(headings) == 2, "Expected 2 headings, got %d:\n%s" % (len(headings), content)


# ---------------------------------------------------------------------------
# Speaker prefix must NOT over-match common sentence-leading words (bug #3).
# "Note:", "Today:", "Warning:" etc. are not speakers — only real names are.
# ---------------------------------------------------------------------------
import pytest as _pytest


@_pytest.mark.parametrize("lead", ["Note", "Today", "Warning", "Update", "Summary",
                                   "Question", "Action", "Okay", "Actually"])
def test_srt_common_word_prefix_is_not_a_speaker(tmp_path, lead):
    path = tmp_path / "meeting.srt"
    path.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n%s: the migration is on track.\n" % lead,
        encoding="utf-8",
    )
    content, _ = parse_one(str(path), 20, 8)
    assert "<!-- speaker: %s -->" % lead not in content, "'%s:' misread as a speaker" % lead
    assert "— %s" % lead not in content
    # the full text (including the leading word) must be preserved in the body
    assert "%s: the migration is on track." % lead in content


def test_srt_real_name_prefix_still_extracted(tmp_path):
    """Guard against over-correction: genuine 'Name:' prefixes must still work."""
    path = tmp_path / "meeting.srt"
    path.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nMaria Gomez: Use Gatling for the baseline.\n",
        encoding="utf-8",
    )
    content, _ = parse_one(str(path), 20, 8)
    assert "<!-- speaker: Maria Gomez -->" in content
    assert "— Maria Gomez" in content
