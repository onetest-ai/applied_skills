"""--pack-turns N: group consecutive transcript turns (any speaker) into one section (one
chunk) while the rendered size stays within budget, so a short answer stays next to its
question without becoming its own chunk via --fold-interjections."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "corpus-taxonomy-extraction"))
from parse_corpus import _pack_turns, _render_turn_sections, parse_one  # noqa: E402


def turn(ts, speaker, text):
    return {"ts": ts, "speaker": speaker, "text": text}


VTT = """WEBVTT

a-0
00:00:01.000 --> 00:00:04.000
<v Ann>Which environment do we run the baseline in?</v>

b-0
00:00:05.000 --> 00:00:05.500
<v Bob>Yeah.</v>

c-0
00:00:06.000 --> 00:00:09.000
<v Ann>Then we compare the 90th percentile.</v>
"""

SRT = (
    "1\n00:00:01,000 --> 00:00:04,000\nAnn: We run the baseline on Friday.\n\n"
    "2\n00:00:05,000 --> 00:00:05,500\nBob: Sounds good, let's do it then.\n"
)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --- _pack_turns -----------------------------------------------------------

def test_pack_groups_until_budget():
    turns = [turn("00:01", "Ann", "a" * 300), turn("00:02", "Bob", "b" * 300), turn("00:03", "Ann", "c" * 300)]
    assert [len(p) for p in _pack_turns(turns, 700)] == [2, 1]


def test_turn_longer_than_budget_is_its_own_pack():
    turns = [turn("00:01", "Ann", "a" * 1500), turn("00:02", "Bob", "short")]
    assert [len(p) for p in _pack_turns(turns, 1000)] == [1, 1]


def test_max_chars_zero_is_one_pack_per_turn():
    turns = [turn("00:01", "Ann", "Hi."), turn("00:02", "Bob", "Hey.")]
    assert [len(p) for p in _pack_turns(turns, 0)] == [1, 1]


# --- _render_turn_sections ---------------------------------------------------

def test_render_single_turn_keeps_todays_format_with_speaker():
    md = _render_turn_sections([[turn("00:04", "Ann", "Hello there.")]], lambda ts: ts)
    assert md == "\n## 00:04 — Ann (cue 1)\n\n<!-- speaker: Ann -->\n\nHello there.\n"


def test_render_single_turn_keeps_todays_format_without_speaker():
    md = _render_turn_sections([[turn("00:04", "", "Hello there.")]], lambda ts: ts)
    assert md == "\n## 00:04 (cue 1)\n\nHello there.\n"


def test_render_multi_turn_has_range_heading_and_inline_speakers():
    md = _render_turn_sections([[turn("00:04", "Ann", "Hi."), turn("00:09", "", "Noise.")]], lambda ts: ts)
    assert md == "\n## 00:04–00:09 (cues 1–2)\n\n00:04 Ann: Hi.\n\n00:09 Noise.\n"
    assert "<!-- speaker" not in md


def test_seq_counts_turns_across_packs():
    packs = [[turn("00:01", "Ann", "Hi."), turn("00:02", "Bob", "Hey.")], [turn("00:03", "Ann", "Bye.")]]
    md = _render_turn_sections(packs, lambda ts: ts)
    assert "(cues 1–2)" in md
    assert "(cue 3)" in md


# --- integration via parse_one ----------------------------------------------

def test_vtt_pack_merges_all_turns_into_one_section(tmp_path):
    md, method = parse_one(write(tmp_path, "m.vtt", VTT), 20, 8, merge_cues=10, pack=1000)
    assert method == "transcript-etl"
    assert md.count("\n## ") == 1
    assert "Which environment do we run the baseline in?" in md
    assert "Then we compare the 90th percentile." in md


def test_vtt_fold_then_pack_keeps_interjection_inside_host_paragraph(tmp_path):
    md, _ = parse_one(write(tmp_path, "m.vtt", VTT), 20, 8, merge_cues=10, fold=20, pack=1000)
    assert md.count("\n## ") == 1
    assert "[Bob: Yeah.]" in md
    assert "Which environment do we run the baseline in? [Bob: Yeah.]" in md


def test_srt_gets_the_same_pack_semantics(tmp_path):
    md, _ = parse_one(write(tmp_path, "m.srt", SRT), 20, 8, merge_cues=10, pack=1000)
    assert md.count("\n## ") == 1
    assert "Ann: We run the baseline on Friday." in md
    assert "Bob: Sounds good, let's do it then." in md


def test_vtt_default_pack_is_off_and_section_count_unchanged(tmp_path):
    md, _ = parse_one(write(tmp_path, "m.vtt", VTT), 20, 8, merge_cues=10)
    assert md.count("\n## ") == 3
