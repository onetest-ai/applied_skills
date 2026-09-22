from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _tools
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

    def _video_lane(self, source, md_present=True, sidecar="same-stem"):
        """Seed the out dir as `video_capture assemble` would leave it for `source`.
        sidecar: "same-stem" (the .vtt next to it), a source-relative path, or None (asr)."""
        self.out.mkdir(exist_ok=True)
        md = source.replace("/", "__") + ".md"
        if md_present:
            (self.out / md).write_text("# SOURCE: x\n# method: video-lane (transcript: sidecar)\n")
        if sidecar == "same-stem":
            sidecar = source.rsplit(".", 1)[0] + ".vtt"
        (self.out / "manifest.json").write_text(json.dumps(
            [{"source": source, "md": md, "method": "video-lane",
              "inputs": [source] + ([sidecar] if sidecar else [])}]))

    def test_no_video_lane_entry_parses_sidecar_normally_byte_identical(self):
        # Zoom-style mp4+vtt pair in a corpus that does not ingest video: unchanged output.
        self._run("--formats", "vtt,srt", "--merge-cues", "10")
        before = (self.out / "talk.vtt.md").read_bytes()
        (self.corpus / "talk.mp4").write_bytes(b"v")
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertEqual(man["talk.vtt"]["md"], "talk.vtt.md")
        self.assertEqual((self.out / "talk.vtt.md").read_bytes(), before)

    def test_video_lane_entry_consumes_sidecar_with_real_spelling(self):
        (self.corpus / "talk.MP4").write_bytes(b"v")
        self._video_lane("talk.MP4")
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertEqual(man["talk.vtt"], {"source": "talk.vtt", "skipped": True,
                                           "method": "consumed-by-video", "consumed_by": "talk.MP4"})
        self.assertFalse((self.out / "talk.vtt.md").exists())
        self.assertIn("talk.MP4", man)  # the video-lane entry itself survives

    def test_video_lane_entry_removes_stale_parsed_doc(self):
        self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertTrue((self.out / "talk.vtt.md").exists())
        (self.corpus / "talk.mp4").write_bytes(b"v")
        man0 = json.loads((self.out / "manifest.json").read_text())
        (self.out / "talk.mp4.md").write_text("# SOURCE: talk.mp4\n")
        (self.out / "manifest.json").write_text(json.dumps(
            man0 + [{"source": "talk.mp4", "md": "talk.mp4.md", "method": "video-lane",
                     "inputs": ["talk.mp4", "talk.vtt"]}]))
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertFalse((self.out / "talk.vtt.md").exists())
        self.assertEqual(man["talk.vtt"], {"source": "talk.vtt", "skipped": True,
                                           "method": "consumed-by-video", "consumed_by": "talk.mp4"})

    def test_video_lane_entry_in_subdir_consumes_sidecar(self):
        sub = self.corpus / "m"; sub.mkdir()
        (sub / "standup.vtt").write_text(VTT); (sub / "standup.mp4").write_bytes(b"v")
        self._video_lane("m/standup.mp4")
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertEqual(man["m/standup.vtt"]["method"], "consumed-by-video")
        self.assertEqual(man["m/standup.vtt"]["consumed_by"], "m/standup.mp4")

    def test_video_lane_entry_whose_md_is_missing_does_not_consume(self):
        (self.corpus / "talk.mp4").write_bytes(b"v")
        self._video_lane("talk.mp4", md_present=False)
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertEqual(man["talk.vtt"]["md"], "talk.vtt.md")
        self.assertTrue((self.out / "talk.vtt.md").exists())

    def test_consumption_follows_inputs_not_the_file_name(self):
        # A transcript with a different name than the recording, consumed because the
        # video's manifest entry lists it in `inputs`.
        (self.corpus / "Weekly-20260105-Meeting Recording.mp4").write_bytes(b"v")
        self._video_lane("Weekly-20260105-Meeting Recording.mp4", sidecar="talk.vtt")
        stale = self.out / "talk.vtt.md"; stale.write_text("# SOURCE: talk.vtt\n")
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertEqual(man["talk.vtt"], {"source": "talk.vtt", "skipped": True, "method": "consumed-by-video",
                                           "consumed_by": "Weekly-20260105-Meeting Recording.mp4"})
        self.assertFalse(stale.exists())

    def test_teams_docx_in_inputs_is_consumed_without_parsing(self):
        sub = self.corpus / "rec"; sub.mkdir()
        (sub / "Sync-20260105-Meeting Recording.mp4").write_bytes(b"v")
        _tools.make_teams_docx(sub / "Acme_ Sync.docx", "Sync-20260105-Meeting Recording", "5m",
                               [("Dana Rivers", "0:05", "hello")])
        self._video_lane("rec/Sync-20260105-Meeting Recording.mp4", sidecar="rec/Acme_ Sync.docx")
        stale = self.out / "rec__Acme_ Sync.docx.md"; stale.write_text("# SOURCE: rec/Acme_ Sync.docx\n")
        # parse_one would need soffice for a .docx; consumption must not get that far
        orig = parse_corpus.parse_one
        parse_corpus.parse_one = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parsed"))
        try:
            man = self._run("--formats", "docx")
        finally:
            parse_corpus.parse_one = orig
        self.assertEqual(man["rec/Acme_ Sync.docx"], {
            "source": "rec/Acme_ Sync.docx", "skipped": True, "method": "consumed-by-video",
            "consumed_by": "rec/Sync-20260105-Meeting Recording.mp4"})
        self.assertFalse(stale.exists())

    def test_asr_assembled_video_does_not_consume_its_same_stem_transcript(self):
        # `--transcript asr` put only the video in inputs: the .vtt next to it is its own doc.
        (self.corpus / "talk.mp4").write_bytes(b"v")
        self._video_lane("talk.mp4", sidecar=None)
        man = self._run("--formats", "vtt,srt", "--merge-cues", "10")
        self.assertEqual(man["talk.vtt"]["md"], "talk.vtt.md")
        self.assertTrue((self.out / "talk.vtt.md").exists())

    def test_flag_is_gone(self):
        with self.assertRaises(SystemExit):
            parse_corpus.main(["--corpus", str(self.corpus), "--out", str(self.out),
                               "--consume-video-sidecars"])
