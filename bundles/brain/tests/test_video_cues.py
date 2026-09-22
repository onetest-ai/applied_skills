from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import parse_corpus
import video_capture as V

VTT = """WEBVTT

NOTE produced by a meeting tool

a1b2-0
00:00:04.560 --> 00:00:05.520 align:start
<v Alice Smith>We ship on Friday.</v>

a1b2-1
00:00:05.600 --> 00:00:07.000
<v Alice Smith>Pending QA.</v>

c3d4-0
01:15:02.000 --> 01:15:04.250
<v Bob>Agreed.</v>
"""

SRT = """1
00:00:01,000 --> 00:00:02,500
Alice: first point

2
01:02:03,040 --> 01:02:05,000
Note: not a speaker
"""


class CueTests(unittest.TestCase):
    def _write(self, name, text):
        d = tempfile.mkdtemp(); p = Path(d) / name; p.write_text(text); return p

    def test_vtt_keeps_hours_speakers_and_ends(self):
        cues = V.read_cues(self._write("m.vtt", VTT))
        self.assertEqual([(c["start"], c["speaker"], c["text"]) for c in cues], [
            (4.56, "Alice Smith", "We ship on Friday."),
            (5.6, "Alice Smith", "Pending QA."),
            (4502.0, "Bob", "Agreed."),
        ])
        self.assertEqual(cues[2]["end"], 4504.25)

    def test_srt_parses_hours_and_rejects_non_speaker_prefix(self):
        cues = V.read_cues(self._write("m.srt", SRT))
        self.assertEqual(cues[0]["speaker"], "Alice")
        self.assertEqual(cues[1]["start"], 3723.04)
        self.assertEqual(cues[1]["speaker"], "")

    def test_empty_file_has_no_cues(self):
        self.assertEqual(V.read_cues(self._write("e.vtt", "WEBVTT\n\n")), [])

    def test_fmt_hms(self):
        self.assertEqual(V.fmt_hms(4502.9), "01:15:02")
        self.assertEqual(V.fmt_hms(5), "00:00:05")

    def test_merge_turns_matches_merge_cues_semantics(self):
        cues = [{"start": i, "end": i + 1, "speaker": s, "text": t}
                for i, (s, t) in enumerate([("A", "1"), ("A", "2"), ("A", "3"), ("B", "4"), ("", "5"), ("", "6")])]
        turns = V.merge_turns(cues, max_cues=2)
        self.assertEqual([(t["speaker"], t["text"], t["start"], t["end"]) for t in turns], [
            ("A", "1 2", 0, 2), ("A", "3", 2, 3), ("B", "4", 3, 4), ("", "5 6", 4, 6)])


class SpeakerParityTests(unittest.TestCase):
    CASES = ["<v Alice>hi</v>", "<v.loud Bob Jones>hey", "Alice: hello there", "Mary-Jane O'Neil: yes",
             "Note: something", "Today: agenda", "no speaker here", "A B C D: too many words",
             "alice: lowercase", "Dr. Who: time", "O’Brien: hi", "D’Arcy Smith: ok"]

    def test_prefix_list_is_identical(self):
        self.assertEqual(V.NON_SPEAKER_PREFIXES, parse_corpus._NON_SPEAKER_PREFIXES)

    def test_behaviour_matches_parse_corpus(self):
        for c in self.CASES:
            self.assertEqual(V.speaker_and_text(c), parse_corpus._speaker_and_text(c), c)
