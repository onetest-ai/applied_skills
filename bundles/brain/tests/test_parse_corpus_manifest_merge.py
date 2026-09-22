from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import parse_corpus

VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A>hello</v>\n"


class ManifestMergeTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        self.corpus = r / "c"; self.corpus.mkdir(); self.out = r / "parsed"
        (self.corpus / "talk.vtt").write_text(VTT)
        (self.corpus / "notes.md").write_text("# Notes\nbody")

    def tearDown(self):
        self.t.cleanup()

    def _run(self, *args):
        parse_corpus.main(["--corpus", str(self.corpus), "--out", str(self.out), *args])
        return {e["source"]: e for e in json.loads((self.out / "manifest.json").read_text())}

    def test_two_passes_keep_both(self):
        self._run("--formats", "vtt,srt", "--merge-cues", "10")
        man = self._run("--formats", "md")
        self.assertEqual(sorted(man), ["notes.md", "talk.vtt"])

    def test_video_lane_entry_survives_a_parse_run(self):
        self.out.mkdir()
        (self.out / "manifest.json").write_text(json.dumps([{"source": "rec.mp4", "md": "rec.mp4.md", "method": "video-lane"}]))
        man = self._run("--formats", "vtt,srt,md")
        self.assertIn("rec.mp4", man)

    def test_rerun_rederives_its_own_formats(self):
        self._run("--formats", "vtt,srt,md")
        (self.corpus / "notes.md").unlink()
        man = self._run("--formats", "vtt,srt,md")
        self.assertEqual(sorted(man), ["talk.vtt"])

    def test_consume_flag_skips_sidecar_with_real_spelling(self):
        (self.corpus / "talk.MP4").write_bytes(b"v")
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10", "--consume-video-sidecars")
        self.assertEqual(man["talk.vtt"], {"source": "talk.vtt", "skipped": True,
                                           "method": "consumed-by-video", "consumed_by": "talk.MP4"})
        self.assertFalse((self.out / "talk.vtt.md").exists())

    def test_without_flag_output_is_unchanged(self):
        (self.corpus / "talk.mp4").write_bytes(b"v")
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertEqual(man["talk.vtt"]["md"], "talk.vtt.md")
        self.assertTrue((self.out / "talk.vtt.md").exists())
