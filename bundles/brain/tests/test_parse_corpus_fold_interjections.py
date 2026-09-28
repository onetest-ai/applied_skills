"""--fold-interjections N: a transcript turn shorter than N chars is folded into the previous
turn, inline as "[Speaker: text]", instead of becoming its own section (and so its own chunk)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "corpus-taxonomy-extraction"))
from parse_corpus import _fold_interjections, parse_one  # noqa: E402


def turn(ts, speaker, text):
    return {"ts": ts, "speaker": speaker, "text": text}


VTT = """WEBVTT

a-0
00:00:01.000 --> 00:00:04.000
<v Ann>Which environment do we run the baseline in?</v>

b-0
00:00:05.000 --> 00:00:05.500
<v Bob>Three.</v>

c-0
00:00:06.000 --> 00:00:09.000
<v Ann>Then we compare the 90th percentile.</v>
"""


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_short_turn_folds_into_previous_with_speaker():
    out = _fold_interjections([turn("00:01", "Ann", "Which environment do we use?"), turn("00:05", "Bob", "Three."),
                               turn("00:06", "Ann", "Then we compare the reports.")], 20)
    assert out == [turn("00:01", "Ann", "Which environment do we use? [Bob: Three.]"), turn("00:06", "Ann", "Then we compare the reports.")]


def test_first_turn_has_no_predecessor_and_is_kept():
    out = _fold_interjections([turn("00:00", "Bob", "Yeah."), turn("00:02", "Ann", "Let us start the review.")], 20)
    assert [t["text"] for t in out] == ["Yeah.", "Let us start the review."]


def test_consecutive_short_turns_all_fold_in_order():
    out = _fold_interjections([turn("00:01", "Ann", "We deploy to the test environment first."),
                               turn("00:02", "Bob", "Mhm."), turn("00:03", "Cy", "Okay.")], 20)
    assert out == [turn("00:01", "Ann", "We deploy to the test environment first. [Bob: Mhm.] [Cy: Okay.]")]


def test_turn_without_speaker_never_prints_none():
    out = _fold_interjections([turn("00:01", "", "Long enough sentence here."), turn("00:02", "", "Ok.")], 20)
    assert out[0]["text"] == "Long enough sentence here. [Ok.]"


def test_zero_is_off_and_returns_a_copy():
    turns = [turn("00:01", "Ann", "A."), turn("00:02", "Bob", "B.")]
    out = _fold_interjections(turns, 0)
    assert out == turns and out[0] is not turns[0]


def test_vtt_default_is_one_section_per_turn(tmp_path):
    md, method = parse_one(write(tmp_path, "m.vtt", VTT), 20, 8, merge_cues=10)
    assert method == "transcript-etl" and md.count("\n## ") == 3


def test_vtt_fold_keeps_answer_next_to_question(tmp_path):
    md, _ = parse_one(write(tmp_path, "m.vtt", VTT), 20, 8, merge_cues=10, fold=20)
    assert md.count("\n## ") == 2
    assert "Which environment do we run the baseline in? [Bob: Three.]" in md


def test_srt_gets_the_same_fold(tmp_path):
    srt = ("1\n00:00:01,000 --> 00:00:04,000\nAnn: We run the baseline on Friday.\n\n"
           "2\n00:00:05,000 --> 00:00:05,500\nBob: Yeah.\n")
    md, _ = parse_one(write(tmp_path, "m.srt", srt), 20, 8, merge_cues=10, fold=20)
    assert md.count("\n## ") == 1 and "[Bob: Yeah.]" in md


def test_speaker_name_with_parentheses_stays_unambiguous():
    out = _fold_interjections([turn("00:01", "Ann", "We copy the build to the test server."),
                               turn("00:02", "Tester, Sample (Partner)", "OK.")], 20)
    assert out[0]["text"] == "We copy the build to the test server. [Tester, Sample (Partner): OK.]"
