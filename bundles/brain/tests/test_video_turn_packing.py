"""`assemble --fold-interjections N --pack-turns N`: the video lane's counterpart of
parse_corpus's VTT/SRT options (one `##` section per speaker turn flooded the index with
"Hello again." chunks). Same semantics; a frame section is never folded into or packed with
turns, and without the flags the parsed doc is byte-identical to before."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import chunking
import parse_corpus as P
import video_capture as V

CUES = [
    (1, "Alice", "Let us look at the quarterly roadmap for the billing platform today."),
    (5, "Bob", "Mhm."),
    (8, "Alice", "The first milestone is the invoice export, due in November."),
    (12, "Bob", "Who owns the export work?"),
    (15, "Alice", "Carol owns it."),
    # frame p01 at 20s
    (30, "Bob", "Sure."),
    (33, "Alice", "Next, the dunning emails, which slipped from October."),
]


def _vtt(cues):
    out = ["WEBVTT", ""]
    for t, who, text in cues:
        out += [f"00:00:{t:02d}.000 --> 00:00:{t + 2:02d}.000", f"<v {who}>{text}</v>", ""]
    return "\n".join(out)


class PackingTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        corpus = r / "corpus" / "rec"; corpus.mkdir(parents=True)
        (corpus / "sync.mp4").write_bytes(b"v")
        sidecar = corpus / "sync.vtt"; sidecar.write_text(_vtt(CUES))
        self.slug = "rec__sync--mp4"
        self.rd = r / "assets" / self.slug; self.rd.mkdir(parents=True)
        (self.rd / "p01.png").write_bytes(b"png"); (self.rd / "p01.txt").write_text("")
        (self.rd / "pages.json").write_text(json.dumps({
            "doc": "sync.mp4", "slug": self.slug, "medium": "video", "duration": 60.0, "frames_capped": False,
            "pages": [{"page": 1, "image": f"{self.slug}/p01.png", "img_sha": "sha1", "flagged": True,
                       "why": "video-frame", "t_start": 20, "t_end": 28, "shown_at": [[20, 28]]}]}))
        work = r / "video" / self.slug; work.mkdir(parents=True)
        self.probe = work / "probe.json"
        self.probe.write_text(json.dumps({"source": "rec/sync.mp4", "video": str(corpus / "sync.mp4"),
                                          "slug": self.slug, "duration": 60.0, "has_audio": True,
                                          "sidecar": str(sidecar), "sidecar_source": "rec/sync.vtt",
                                          "transcript": "sidecar", "warnings": []}))
        self.results = r / "vision"; self.results.mkdir()
        (self.results / "result_0.json").write_text(json.dumps(
            {"sha1": "<!-- frame: p01 -->\n# Billing roadmap\n\n- Invoice export — Nov\n- Dunning — slipped"}))
        self.parsed = r / "parsed"; self.parsed.mkdir()

    def tearDown(self):
        self.t.cleanup()

    def _doc(self, *extra):
        code = V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                       "--results", str(self.results), "--parsed", str(self.parsed), "--no-review", *extra])
        self.assertEqual(code, 0)
        return (self.parsed / "rec__sync.mp4.md").read_text()

    @staticmethod
    def _headings(md):
        return [ln for ln in md.splitlines() if ln.startswith("## ")]

    def test_default_output_is_one_section_per_turn(self):
        md = self._doc()
        self.assertEqual(len(self._headings(md)), 8)  # 7 turns + 1 frame, as before

    def test_zero_flags_are_byte_identical_to_the_default(self):
        default = self._doc()
        self.assertEqual(self._doc("--fold-interjections", "0", "--pack-turns", "0"), default)

    def test_fold_uses_parse_corpus_brackets_and_never_folds_across_a_frame(self):
        md = self._doc("--fold-interjections", "20")
        self.assertIn("Let us look at the quarterly roadmap for the billing platform today. [Bob: Mhm.]", md)
        self.assertIn("Who owns the export work? [Alice: Carol owns it.]", md)
        # "Sure." follows the frame: it has no preceding turn in its run, so it stays its own section
        self.assertNotIn("[Bob: Sure.]", md)
        self.assertRegex(md, r"## 00:00:30 — Bob \(cue \d+\)\n\n<!-- speaker: Bob -->\n\nSure\.")

    def test_pack_groups_turns_but_keeps_the_frame_its_own_section(self):
        md = self._doc("--fold-interjections", "20", "--pack-turns", "1000")
        heads = self._headings(md)
        self.assertEqual(heads, ["## 00:00:01–00:00:12 (cues 1–3)",
                                 "## 00:00:20 · Billing roadmap (frame p01)",
                                 "## 00:00:30–00:00:33 (cues 4–5)"])
        self.assertIn("00:00:12 Bob: Who owns the export work?", md)
        self.assertIn("<!-- image: rec__sync--mp4/p01.png -->", md)
        self.assertNotIn("<!-- speaker:", md)  # multi-turn packs carry no single speaker

    def test_pack_respects_the_size_budget(self):
        md = self._doc("--pack-turns", "120")
        for head in self._headings(md):
            self.assertNotRegex(head, r"cues 1–[3-9]")  # never more than fits in 120 chars

    def test_packed_doc_keeps_the_preamble_the_only_h1_through_the_real_chunker(self):
        md = self._doc("--fold-interjections", "20", "--pack-turns", "1000")
        self.assertFalse([ln for ln in chunking.strip_preamble(md).splitlines() if ln.startswith("# ")])
        recs = chunking.section_records(md)
        for rec in recs:
            self.assertNotIn("SOURCE", str(rec))
        self.assertEqual(len(recs), 3)

    def test_same_fold_semantics_as_parse_corpus(self):
        turns = [{"start": 1, "end": 2, "speaker": "A", "text": "long enough sentence here"},
                 {"start": 3, "end": 4, "speaker": "B", "text": "Mhm."},
                 {"start": 5, "end": 6, "speaker": "", "text": "Ok."}]
        ours = V.fold_interjections(turns, 20)
        theirs = P._fold_interjections([{"ts": t["start"], "speaker": t["speaker"], "text": t["text"]}
                                        for t in turns], 20)
        self.assertEqual([t["text"] for t in ours], [t["text"] for t in theirs])

    def test_quality_tool_still_counts_packed_transcript_sections(self):
        import video_quality as Q
        md = self._doc("--fold-interjections", "20", "--pack-turns", "1000")
        self.assertEqual(len(Q.cue_entries(md)), 2)
        cues = V.read_cues(self.probe.parent.parent.parent / "corpus" / "rec" / "sync.vtt")
        hit, total = Q.cue_coverage(md, cues)
        self.assertEqual((hit, total), (7, 7))


if __name__ == "__main__":
    unittest.main()
