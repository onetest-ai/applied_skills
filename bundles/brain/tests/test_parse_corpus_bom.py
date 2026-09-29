"""M6: a .vtt/.srt file that starts with a UTF-8 BOM (U+FEFF) must parse identically to the
same file without one. Before this fix, `open(path, encoding="utf-8")` left the BOM as a
literal leading `﻿` character, corrupting the first line ("﻿WEBVTT" fails the
`^WEBVTT` strip in _parse_vtt; a BOM'd SRT sequence number fails `.isdigit()`). Fix: open
with `encoding="utf-8-sig"`, which strips a leading BOM if present and is a no-op otherwise.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "corpus-taxonomy-extraction"))
from parse_corpus import parse_one  # noqa: E402

BOM = "﻿"

VTT = "WEBVTT\n\na-0\n00:00:01.000 --> 00:00:04.000\n<v Ann>Run the baseline.</v>\n"
SRT = "1\n00:00:01,000 --> 00:00:04,000\nAnn: Run the baseline.\n"


def write_bytes(tmp_path, name, text, bom):
    p = tmp_path / name
    p.write_bytes((bom + text).encode("utf-8"))
    return str(p)


def test_vtt_with_bom_parses_identically_to_without(tmp_path):
    plain, _ = parse_one(write_bytes(tmp_path, "plain.vtt", VTT, ""), 20, 8, merge_cues=1)
    bommed, _ = parse_one(write_bytes(tmp_path, "bom.vtt", VTT, BOM), 20, 8, merge_cues=1)
    assert bommed == plain
    assert "Run the baseline." in bommed


def test_srt_with_bom_parses_identically_to_without(tmp_path):
    plain, _ = parse_one(write_bytes(tmp_path, "plain.srt", SRT, ""), 20, 8, merge_cues=1)
    bommed, _ = parse_one(write_bytes(tmp_path, "bom.srt", SRT, BOM), 20, 8, merge_cues=1)
    assert bommed == plain
    assert "Run the baseline." in bommed
