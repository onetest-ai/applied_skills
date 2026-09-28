"""M7: test gaps flagged by the final review (first three items).

1. Pin the VTT multi-turn heading STRING exactly through parse_one, not just a `##` count —
   a regression in `_vtt_label` inside a pack (e.g. emitting raw `00:00:01.000`) would pass
   `test_vtt_pack_merges_all_turns_into_one_section`, which only counts headings.
2. Pin the pack budget boundary: a pack whose rendered size == max_chars stays in one pack;
   max_chars + 1 forces a split. Untested before this file.
3. An end-to-end test that runs a packed doc through chunking.section_records +
   knowledge_index._split_speaker and asserts exactly one chunk, speaker is None, and the
   chunk title equals the range heading — the invariant V2 exists to protect (no speaker is
   lifted for a multi-turn section), previously guaranteed only by string assertions on the
   renderer in isolation.
"""
import pathlib
import sys

BRAIN_SKILLS = pathlib.Path(__file__).resolve().parents[1] / "skills"
sys.path.insert(0, str(BRAIN_SKILLS / "corpus-taxonomy-extraction"))
sys.path.insert(0, str(BRAIN_SKILLS / "knowledge-index"))
from parse_corpus import _pack_turns, parse_one  # noqa: E402
import chunking  # noqa: E402
import knowledge_index  # noqa: E402


def turn(ts, speaker, text):
    return {"ts": ts, "speaker": speaker, "text": text}


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


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


# --- 1. exact multi-turn heading string, through parse_one --------------------------

def test_vtt_pack_heading_string_is_pinned_exactly(tmp_path):
    md, _ = parse_one(write(tmp_path, "m.vtt", VTT), 20, 8, merge_cues=10, pack=1000)
    assert "\n## 00:01–00:06 (cues 1–3)\n\n" in md
    # Guards specifically against a raw/unformatted timestamp leaking through.
    assert "00:00:01.000" not in md
    assert "00:00:06.000" not in md


# --- 2. pack budget boundary: == max_chars stays together, +1 splits ------------------

def test_pack_budget_boundary_equal_stays_together_one_over_splits():
    # n(t) = len(label) + 1 + speaker_part + len(text) + 2, label defaults to identity.
    # ts="00:01" (5 chars), no speaker: n = 5 + 1 + 0 + len(text) + 2 = 8 + len(text).
    t1 = turn("00:01", "", "a" * 10)  # n = 18
    t2 = turn("00:02", "", "b" * 10)  # n = 18
    # Budget == exact sum (36): both fit in one pack.
    packs_equal = _pack_turns([t1, t2], 36)
    assert [len(p) for p in packs_equal] == [2]
    # Budget == sum - 1 (35): must split.
    packs_over = _pack_turns([t1, t2], 35)
    assert [len(p) for p in packs_over] == [1, 1]


# --- 3. end-to-end: packed doc -> section_records -> _split_speaker -------------------

def test_packed_doc_end_to_end_yields_one_chunk_no_speaker_title_is_range_heading(tmp_path):
    md, _ = parse_one(write(tmp_path, "m.vtt", VTT), 20, 8, merge_cues=10, pack=1000)
    records = chunking.section_records(md, max_chars=1600)
    assert len(records) == 1
    record = records[0]
    assert record["title"] == "00:01–00:06 (cues 1–3)"
    speaker, body = knowledge_index._split_speaker(record["body"])
    assert speaker is None
    assert "Which environment do we run the baseline in?" in body
    assert "Then we compare the 90th percentile." in body
