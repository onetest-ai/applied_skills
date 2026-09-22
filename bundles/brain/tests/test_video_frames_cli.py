from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _tools
import video_capture as V


class SidecarAndChoiceTests(unittest.TestCase):
    def test_find_sidecar_uses_real_spelling(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "Standup.mp4").write_bytes(b"v"); (Path(td) / "Standup.VTT").write_text("WEBVTT\n")
            self.assertEqual(Path(V.find_sidecar(str(Path(td) / "Standup.mp4"))).name, "Standup.VTT")
            (Path(td) / "other.mp4").write_bytes(b"v")
            self.assertIsNone(V.find_sidecar(str(Path(td) / "other.mp4")))

    def test_choose_transcript(self):
        self.assertEqual(V.choose_transcript(True, "/a.vtt", "auto"), "sidecar")
        self.assertEqual(V.choose_transcript(True, None, "auto"), "asr")
        self.assertEqual(V.choose_transcript(False, None, "auto"), "none")
        self.assertEqual(V.choose_transcript(True, "/a.vtt", "asr"), "asr")
        with self.assertRaises(ValueError):
            V.choose_transcript(True, None, "sidecar")


@_tools.require_tool("ffmpeg", "ffprobe")
class FramesIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory(); root = Path(cls.td.name)
        cls.corpus = root / "corpus" / "rec"; cls.corpus.mkdir(parents=True)
        cls.video = _tools.make_synthetic_video(cls.corpus / "standup.mp4")
        cls.assets = root / "assets"; cls.work = root / "video"

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def _frames(self):
        code = V.main(["frames", "--video", str(self.video), "--rel-to", str(self.corpus.parent),
                       "--assets-root", str(self.assets)])
        self.assertEqual(code, 0)
        return json.loads((self.assets / "rec__standup" / "pages.json").read_text())

    def test_finds_exactly_the_three_slides(self):
        pages = self._frames()
        self.assertEqual(pages["medium"], "video")
        self.assertEqual([p["t_start"] for p in pages["pages"]], [0, 10, 30])
        for p in pages["pages"]:
            self.assertEqual(p["image"], f"rec__standup/p{p['page']:02d}.png")
            self.assertTrue((self.assets / p["image"]).is_file())
            self.assertEqual((self.assets / "rec__standup" / f"p{p['page']:02d}.txt").read_text(), "")
            self.assertTrue(p["flagged"]); self.assertEqual(p["why"], "video-frame")

    def test_second_run_is_a_cache_hit(self):
        first = self._frames()
        png = self.assets / first["pages"][0]["image"]
        mtime = png.stat().st_mtime_ns
        self.assertEqual(self._frames()["frames_key"], first["frames_key"])
        self.assertEqual(png.stat().st_mtime_ns, mtime)

    def test_probe_without_sidecar_and_without_audio(self):
        code = V.main(["probe", "--video", str(self.video), "--rel-to", str(self.corpus.parent),
                       "--work", str(self.work)])
        self.assertEqual(code, 0)
        probe = json.loads((self.work / "rec__standup" / "probe.json").read_text())
        self.assertEqual((probe["source"], probe["transcript"], probe["has_audio"]), ("rec/standup.mp4", "none", False))
        self.assertAlmostEqual(probe["duration"], 40.0, delta=0.5)

    def test_probe_rejects_empty_sidecar(self):
        side = self.corpus / "standup.vtt"; side.write_text("WEBVTT\n\n")
        try:
            code = V.main(["probe", "--video", str(self.video), "--rel-to", str(self.corpus.parent),
                           "--work", str(self.work)])
            self.assertEqual(code, 1)
        finally:
            side.unlink()

    def test_probe_warns_when_sidecar_outlasts_video(self):
        side = self.corpus / "standup.vtt"
        side.write_text("WEBVTT\n\n00:01:00.000 --> 00:01:02.000\nlate\n")
        try:
            self.assertEqual(V.main(["probe", "--video", str(self.video), "--rel-to", str(self.corpus.parent),
                                     "--work", str(self.work)]), 0)
            probe = json.loads((self.work / "rec__standup" / "probe.json").read_text())
            self.assertEqual(probe["transcript"], "sidecar")
            self.assertEqual(probe["sidecar_source"], "rec/standup.vtt")
            self.assertTrue(any("longer than the video" in w for w in probe["warnings"]))
        finally:
            side.unlink()
