"""I1: a cue past the first hour must render H:MM:SS, not wrap back to MM:SS.

`_vtt_label` kept only the last two colon-separated parts (MM:SS), so a 1h00m07s VTT
cue rendered `00:07` — indistinguishable from a cue 7 seconds into the recording. SRT's
inline label (`parts[1]:parts[2]`) dropped the hour the same way. A pack (V2) that
crosses the hour boundary produced a heading that reads BACKWARDS, e.g. `59:58-00:07`.

Fix: render `H:MM:SS` when the hour part is > 0, else today's `MM:SS` (meetings under
an hour stay byte-identical). `_pack_turns`'s budget accounting must use the actual
rendered label length (`len(label(ts))`), not the hardcoded 5, so the "renders <= N"
budget ruling still holds for H:MM:SS labels (6-8 chars).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "corpus-taxonomy-extraction"))
from parse_corpus import _vtt_label, _pack_turns, _render_turn_sections, parse_one  # noqa: E402


def turn(ts, speaker, text):
    return {"ts": ts, "speaker": speaker, "text": text}


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --- _vtt_label --------------------------------------------------------------

def test_vtt_label_under_an_hour_is_unchanged():
    assert _vtt_label("00:07:03.000") == "07:03"
    assert _vtt_label("00:00:05.500") == "00:05"


def test_vtt_label_past_an_hour_keeps_the_hour():
    assert _vtt_label("01:00:07.000") == "1:00:07"
    assert _vtt_label("01:02:05.000") == "1:02:05"


def test_vtt_label_past_ten_hours():
    assert _vtt_label("12:03:04.000") == "12:03:04"


# --- SRT integration -----------------------------------------------------------

def test_srt_cue_past_an_hour_keeps_the_hour(tmp_path):
    srt = "1\n01:02:05,000 --> 01:02:08,000\nAnn: We shipped it.\n"
    md, _ = parse_one(write(tmp_path, "m.srt", srt), 20, 8, merge_cues=1)
    assert "## 1:02:05" in md
    assert "## 02:05" not in md


def test_srt_cue_under_an_hour_is_unchanged(tmp_path):
    srt = "1\n00:02:05,000 --> 00:02:08,000\nAnn: On time.\n"
    md, _ = parse_one(write(tmp_path, "m.srt", srt), 20, 8, merge_cues=1)
    assert "## 02:05" in md


# --- VTT integration, single cue -------------------------------------------------

def test_vtt_cue_past_an_hour_keeps_the_hour(tmp_path):
    vtt = "WEBVTT\n\na-0\n01:00:07.000 --> 01:00:09.000\n<v Ann>Ship it.</v>\n"
    md, _ = parse_one(write(tmp_path, "m.vtt", vtt), 20, 8, merge_cues=1)
    assert "## 1:00:07" in md
    assert "## 00:07" not in md


# --- V2 pack range heading across the hour boundary --------------------------------

def test_pack_range_heading_across_hour_boundary_reads_forward():
    turns = [turn("00:59:58.000", "Ann", "Before the hour we check."),
             turn("01:00:07.000", "Bob", "After the hour we deploy.")]
    md = _render_turn_sections(_pack_turns(turns, 1000, _vtt_label), _vtt_label)
    assert "## 59:58–1:00:07 (cues 1–2)" in md
    # Must not read backwards / repeat a first-hour heading.
    assert "## 59:58–00:07" not in md


# --- _pack_turns budget must use the real label length, not a hardcoded 5 ------------

def test_pack_turns_uses_hour_label_length_for_budget_not_hardcoded_five():
    # n = len(label) + 1 + speaker_part + len(text) + 2. With an H:MM:SS label (7 chars,
    # not the hardcoded 5) two 1-char turns sum to 22 -- over a budget of 20, so they must
    # split. The old hardcoded-5 accounting would have summed to 18 and wrongly combined
    # them into one pack.
    t1 = turn("01:00:00.000", "", "x")
    t2 = turn("01:00:05.000", "", "x")
    packs = _pack_turns([t1, t2], 20, _vtt_label)
    assert [len(p) for p in packs] == [1, 1]
    # The same shape with MM:SS (no-hour, 5-char) labels fits within the same budget.
    t1b = turn("00:10:00.000", "", "x")
    t2b = turn("00:10:05.000", "", "x")
    packs_b = _pack_turns([t1b, t2b], 20, _vtt_label)
    assert [len(p) for p in packs_b] == [2]
