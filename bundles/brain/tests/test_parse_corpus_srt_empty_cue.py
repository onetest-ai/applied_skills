"""M2: an SRT cue that is empty only AFTER _clean_cue_text strips markup must produce no
turn at all — matching VTT, which already filters after cleaning (parse_corpus.py:317-318).
Before this fix, _parse_srt checked emptiness on the raw cue_text BEFORE cleaning, so a cue
like `Ann: <00:00:01.500>` (markup-only after the speaker prefix) survived as a hollow turn:
a timestamp-only paragraph when packed, a marker-only section when unpacked (dropped by F,
so silently invisible), or a stray `[Ann: ]` when folded.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "corpus-taxonomy-extraction"))
from parse_corpus import parse_one  # noqa: E402


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_srt_cue_empty_only_after_cleaning_produces_no_turn(tmp_path):
    srt = (
        "1\n00:00:01,000 --> 00:00:04,000\nAnn: <00:00:01.500>\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\nBob: Real content.\n"
    )
    md, _ = parse_one(write(tmp_path, "m.srt", srt), 20, 8, merge_cues=1)
    assert md.count("\n## ") == 1
    assert "Real content." in md
    assert "Ann" not in md
