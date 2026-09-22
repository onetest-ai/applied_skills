from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
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

    def test_choose_transcript_rejects_asr_without_audio(self):
        with self.assertRaises(ValueError):
            V.choose_transcript(False, None, "asr")
        with self.assertRaises(ValueError):
            V.choose_transcript(False, "/a.vtt", "asr")


class VideoSlugTests(unittest.TestCase):
    def test_slug_carries_the_extension(self):
        self.assertEqual(V.video_slug("m/standup.mp4"), "m__standup--mp4")
        self.assertEqual(V.video_slug("rec/Weekly Sync.MOV"), "rec__weekly-sync--mov")
        self.assertEqual(V.video_slug("talk.webm"), "talk--webm")

    def test_slug_never_equals_the_same_stem_deck_slug(self):
        from render_pages import doc_slug
        self.assertNotEqual(V.video_slug("m/standup.mp4"), doc_slug("m/standup.pptx"))

    def test_frames_refuses_a_non_video_render_dir_without_touching_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); corpus = root / "c"; corpus.mkdir()
            (corpus / "standup.mp4").write_bytes(b"not really a video")
            d = root / "assets" / "standup--mp4"; d.mkdir(parents=True)
            (d / "p01.png").write_bytes(b"deck"); (d / "p01.txt").write_text("deck text")
            pj = json.dumps({"doc": "standup.pptx", "slug": "standup--mp4", "pages": []})
            (d / "pages.json").write_text(pj)
            code = V.main(["frames", "--video", str(corpus / "standup.mp4"), "--rel-to", str(corpus),
                           "--assets-root", str(root / "assets")])
            self.assertEqual(code, 1)
            self.assertEqual((d / "pages.json").read_text(), pj)
            self.assertEqual((d / "p01.png").read_bytes(), b"deck")
            self.assertEqual((d / "p01.txt").read_text(), "deck text")


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
        return json.loads((self.assets / "rec__standup--mp4" / "pages.json").read_text())

    def test_finds_exactly_the_three_slides(self):
        pages = self._frames()
        self.assertEqual(pages["medium"], "video")
        self.assertEqual([p["t_start"] for p in pages["pages"]], [0, 10, 30])
        for p in pages["pages"]:
            self.assertEqual(p["image"], f"rec__standup--mp4/p{p['page']:02d}.png")
            self.assertTrue((self.assets / p["image"]).is_file())
            self.assertEqual((self.assets / "rec__standup--mp4" / f"p{p['page']:02d}.txt").read_text(), "")
            self.assertTrue(p["flagged"]); self.assertEqual(p["why"], "video-frame")

    def test_same_stem_deck_render_is_left_alone(self):
        # m/standup.pptx renders to assets/rec__standup/ (render_pages.doc_slug drops the
        # extension); the recording must never delete or overwrite that deck's pages.
        deck = self.assets / "rec__standup"; deck.mkdir(parents=True, exist_ok=True)
        (deck / "p01.png").write_bytes(b"deck-png"); (deck / "p01.txt").write_text("deck text")
        pj = json.dumps({"doc": "standup.pptx", "slug": "rec__standup", "pages": [{"page": 1}]})
        (deck / "pages.json").write_text(pj)
        pages = self._frames()
        self.assertEqual(pages["slug"], "rec__standup--mp4")
        self.assertEqual((deck / "pages.json").read_text(), pj)
        self.assertEqual((deck / "p01.png").read_bytes(), b"deck-png")
        self.assertEqual((deck / "p01.txt").read_text(), "deck text")

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
        probe = json.loads((self.work / "rec__standup--mp4" / "probe.json").read_text())
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
            probe = json.loads((self.work / "rec__standup--mp4" / "probe.json").read_text())
            self.assertEqual(probe["transcript"], "sidecar")
            self.assertEqual(probe["sidecar_source"], "rec/standup.vtt")
            self.assertTrue(any("longer than the video" in w for w in probe["warnings"]))
        finally:
            side.unlink()


@_tools.require_tool("ffmpeg", "ffprobe")
class FramesRefreshIsAtomicTests(unittest.TestCase):
    """A refresh that fails part-way must leave the previous render exactly as it was:
    pages.json and its PNGs change together or not at all."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); root = Path(self.td.name)
        self.corpus = root / "corpus" / "rec"; self.corpus.mkdir(parents=True)
        self.video = _tools.make_synthetic_video(self.corpus / "standup.mp4")
        self.assets = root / "assets"
        self.outdir = self.assets / "rec__standup--mp4"

    def tearDown(self):
        self.td.cleanup()

    def _frames(self, *extra):
        return V.main(["frames", "--video", str(self.video), "--rel-to", str(self.corpus.parent),
                       "--assets-root", str(self.assets), *extra])

    def _snapshot(self):
        return {p.name: p.read_bytes() for p in sorted(self.outdir.iterdir())}

    def test_failed_extraction_keeps_the_previous_render(self):
        self.assertEqual(self._frames(), 0)
        before = self._snapshot()
        self.assertIn("pages.json", before)
        real = V.extract_frame
        calls = []

        def flaky(video, t, png):
            calls.append(png)
            if len(calls) == 2:
                V.die("ffmpeg failed (exit 1) on standup.mp4: simulated")
            real(video, t, png)

        with unittest.mock.patch.object(V, "extract_frame", side_effect=flaky):
            # different params → different frames_key → a real refresh, not a cache hit
            self.assertEqual(self._frames("--min-hold", "2"), 1)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual([p.name for p in self.assets.iterdir()], ["rec__standup--mp4"])

    def test_successful_refresh_replaces_the_render_and_leaves_no_temp_dirs(self):
        self.assertEqual(self._frames(), 0)
        (self.outdir / "p99.png").write_bytes(b"stale frame from an older render")
        self.assertEqual(self._frames("--min-hold", "2"), 0)
        names = sorted(p.name for p in self.outdir.iterdir())
        self.assertNotIn("p99.png", names)
        pages = json.loads((self.outdir / "pages.json").read_text())
        for p in pages["pages"]:
            self.assertTrue((self.assets / p["image"]).is_file())
        self.assertEqual([p.name for p in self.assets.iterdir()], ["rec__standup--mp4"])
