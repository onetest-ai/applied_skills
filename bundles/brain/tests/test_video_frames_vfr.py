"""A variable-frame-rate recording can make `ffmpeg -ss <t>` near the end write nothing
(exit 0 under -v error). `frames` used to crash on the missing pNN.png and fail the whole
recording; it now retries a little earlier and, failing that, drops the frame — keeping
pNN numbering contiguous and pages.json in step with the PNGs on disk."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import video_capture as V


def _span(t0, t1):
    return {"key": t0 + 1, "t_start": t0, "t_end": t1, "shown_at": [[t0, t1]]}


class FramesVfrTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); root = Path(self.t.name)
        self.corpus = root / "c"; self.corpus.mkdir()
        self.video = self.corpus / "demo.mov"; self.video.write_bytes(b"vfr")
        self.assets = root / "assets"

    def tearDown(self):
        self.t.cleanup()

    def _run(self, dead_after: float, spans):
        def fake_run(cmd, name, subject):
            t = float(cmd[cmd.index("-ss") + 1])
            if t < dead_after:
                Path(cmd[-1]).write_bytes(b"png@%.3f" % t)
            return b""  # ffmpeg exits 0 and writes nothing past the last decodable frame
        with mock.patch.object(V, "ffprobe", return_value=(100.0, True)), \
             mock.patch.object(V, "read_low_frames", return_value=None), \
             mock.patch.object(V, "select_frames", return_value=(spans, False)), \
             mock.patch.object(V, "need_tool", return_value="ffmpeg"), \
             mock.patch.object(V, "run_quiet", side_effect=fake_run):
            return V.main(["frames", "--video", str(self.video), "--rel-to", str(self.corpus),
                           "--assets-root", str(self.assets)])

    def _pages(self):
        d = self.assets / "demo--mov"
        return d, json.loads((d / "pages.json").read_text())

    def test_retries_earlier_when_the_last_timestamp_yields_no_image(self):
        spans = [_span(10, 20), _span(97, 99.9)]  # key 98 -> t = 98.5; decodable up to 98.2
        self.assertEqual(self._run(98.2, spans), 0)
        d, doc = self._pages()
        self.assertEqual([p["page"] for p in doc["pages"]], [1, 2])
        self.assertTrue((d / "p02.png").exists())
        self.assertLess(float((d / "p02.png").read_bytes()[4:]), 98.2)
        self.assertNotIn("frames_unextractable", doc)

    def test_drops_an_unextractable_frame_and_keeps_numbering_contiguous(self):
        spans = [_span(10, 20), _span(95, 96), _span(98, 99)]  # middle one decodable
        self.assertEqual(self._run(97.0, spans), 0)
        d, doc = self._pages()
        self.assertEqual([p["page"] for p in doc["pages"]], [1, 2])
        self.assertEqual([p["t_start"] for p in doc["pages"]], [10, 95])
        self.assertEqual([p["image"] for p in doc["pages"]], ["demo--mov/p01.png", "demo--mov/p02.png"])
        self.assertEqual(sorted(x.name for x in d.glob("p[0-9]*")),
                         ["p01.png", "p01.txt", "p02.png", "p02.txt"])
        self.assertEqual([u["t_start"] for u in doc["frames_unextractable"]], [98])

    def test_all_frames_extractable_writes_no_unextractable_key(self):
        self.assertEqual(self._run(1e9, [_span(10, 20)]), 0)
        _d, doc = self._pages()
        self.assertEqual(len(doc["pages"]), 1)
        self.assertNotIn("frames_unextractable", doc)


if __name__ == "__main__":
    unittest.main()
