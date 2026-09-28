"""M1: html.unescape can turn `&#10;`/`&NewLine;` into a real newline. A multi-turn
paragraph rendered by _render_turn_sections must stay ONE line/paragraph — an
embedded `\n` could open a live `##`/`#` heading inside a transcript section, which
would then seed parent_heading/breadcrumb_path (the CLAUDE.md preamble/H1 invariant).
Fix: collapse whitespace (`" ".join(text.split())`) in cue text after unescaping.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "corpus-taxonomy-extraction"))
from parse_corpus import _clean_cue_text, parse_one  # noqa: E402


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_decoded_entity_newline_is_collapsed_to_a_space():
    assert _clean_cue_text("line one&#10;## Heading two") == "line one ## Heading two"


def test_decoded_named_entity_newline_is_collapsed_to_a_space():
    assert _clean_cue_text("line one&NewLine;line two") == "line one line two"


def test_carriage_return_is_also_collapsed():
    assert _clean_cue_text("line one&#13;line two") == "line one line two"


def test_multi_turn_pack_with_embedded_newline_stays_one_paragraph(tmp_path):
    vtt = (
        "WEBVTT\n\n"
        "a-0\n00:00:01.000 --> 00:00:04.000\n<v Ann>line one&#10;## Heading two</v>\n\n"
        "b-0\n00:00:05.000 --> 00:00:08.000\n<v Bob>Second turn.</v>\n"
    )
    md, _ = parse_one(write(tmp_path, "m.vtt", vtt), 20, 8, merge_cues=1, pack=1000)
    # No live heading was created from the decoded newline.
    assert md.count("\n## ") == 1
    assert "\n## Heading two" not in md
    assert "line one ## Heading two" in md
