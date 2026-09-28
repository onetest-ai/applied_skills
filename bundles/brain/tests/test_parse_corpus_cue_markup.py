"""_clean_cue_text: strip WebVTT inline markup (<b>, <i>, <u>, <c.class>, <lang ..>, <ruby>,
<rt>, cue timestamps <00:01.000>) and decode HTML entities (&amp; -> &) in cue text and
speaker names, applied after _speaker_and_text in both _parse_vtt and _parse_srt."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "corpus-taxonomy-extraction"))
from parse_corpus import _clean_cue_text, parse_one  # noqa: E402


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_vtt_inline_markup_and_entities_are_cleaned(tmp_path):
    vtt = ("WEBVTT\n\na-0\n00:00:01.000 --> 00:00:04.000\n"
           "<v Ann>Run the <b>baseline</b> &amp; the <c.yellow>90th</c> percentile.</v>\n")
    md, _ = parse_one(write(tmp_path, "m.vtt", vtt), 20, 8, merge_cues=10)
    assert "Run the baseline & the 90th percentile." in md
    assert "&amp;" not in md and "<b>" not in md and "<c" not in md


def test_srt_entities_are_decoded(tmp_path):
    srt = "1\n00:00:01,000 --> 00:00:04,000\nAnn: Run the baseline &amp; the report.\n"
    md, _ = parse_one(write(tmp_path, "m.srt", srt), 20, 8, merge_cues=10)
    assert "Run the baseline & the report." in md
    assert "&amp;" not in md


def test_speaker_name_with_entity_is_decoded_in_heading_and_marker(tmp_path):
    vtt = "WEBVTT\n\na-0\n00:00:01.000 --> 00:00:04.000\n<v R&amp;D Team>Ship it.</v>\n"
    md, _ = parse_one(write(tmp_path, "m.vtt", vtt), 20, 8, merge_cues=10)
    assert "R&D Team" in md
    assert "&amp;" not in md


def test_cue_timestamp_tags_are_removed(tmp_path):
    vtt = ("WEBVTT\n\na-0\n00:00:01.000 --> 00:00:04.000\n"
           "<v Ann>Run the <00:00:02.000>baseline<00:00:03.000> report.</v>\n")
    md, _ = parse_one(write(tmp_path, "m.vtt", vtt), 20, 8, merge_cues=10)
    assert "Run the baseline report." in md
    assert "<00:00:02.000>" not in md


def test_plain_text_without_markup_is_unchanged():
    assert _clean_cue_text("Run the baseline report.") == "Run the baseline report."


def test_clean_cue_text_strips_various_tags():
    assert _clean_cue_text("<i>Hi</i> <u>there</u> <lang en>word</lang> <ruby>k</ruby><rt>a</rt>") == "Hi there word ka"


def test_literal_text_shaped_like_a_tag_is_kept_verbatim():
    # Only <v ...> and <lang ...> carry a space-separated annotation per the WebVTT spec;
    # <i>/<b>/<u>/<c>/<ruby>/<rt> take only an optional .class suffix. A broader pattern would
    # swallow spoken text that happens to look like a tag — silent word loss.
    assert _clean_cue_text("<i can't believe it>") == "<i can't believe it>"


def test_literal_tag_shaped_text_with_entity_is_kept_and_entity_decoded():
    assert _clean_cue_text("<i can't believe it &amp; more>") == "<i can't believe it & more>"


def test_class_suffixed_bold_tag_is_stripped():
    assert _clean_cue_text("<b.loud>x</b>") == "x"


def test_multi_dotted_class_suffixed_c_tag_is_stripped():
    assert _clean_cue_text("<c.yellow.bg_blue>x</c>") == "x"


def test_lang_tag_with_annotation_is_stripped():
    assert _clean_cue_text("<lang en-GB>colour</lang>") == "colour"
