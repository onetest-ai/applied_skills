"""Frame echo: each video transcription starts with <!-- frame: pNN --> naming the batch item it
answers, so a result filed under the wrong image hash (seen in a live run: a batch shifted by
one frame) is caught by code instead of being indexed under the wrong frame."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import video_capture as V
import vision_prep as VP


class FrameEchoTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        self.rd = r / "assets" / "rec--mp4"; self.rd.mkdir(parents=True)
        pages = []
        for n in (1, 2):
            (self.rd / f"p{n:02d}.png").write_bytes(b"png")
            pages.append({"page": n, "image": f"rec--mp4/p{n:02d}.png", "img_sha": f"sha{n}", "flagged": True,
                          "t_start": n * 10, "t_end": n * 10 + 5, "shown_at": [[n * 10, n * 10 + 5]]})
        (self.rd / "pages.json").write_text(json.dumps({"doc": "rec.mp4", "slug": "rec--mp4", "medium": "video",
                                                        "duration": 60.0, "pages": pages}))
        self.probe = r / "probe.json"
        self.probe.write_text(json.dumps({"source": "rec.mp4", "transcript": "none", "sidecar": None,
                                          "sidecar_source": None, "warnings": []}))
        self.results = r / "vision"; self.results.mkdir()
        self.parsed = r / "parsed"; self.parsed.mkdir()

    def tearDown(self):
        self.t.cleanup()

    def _assemble(self, mapping):
        (self.results / "result_0.json").write_text(json.dumps(mapping))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                         "--results", str(self.results), "--parsed", str(self.parsed)])
        return rc, err.getvalue()

    def test_video_gate_asks_for_the_frame_echo(self):
        self.assertIn("<!-- frame: p", VP.VIDEO_GATE)

    def test_result_filed_under_another_frame_is_refused(self):
        rc, err = self._assemble({"sha1": "<!-- frame: p02 -->\n# Roadmap\n\n- ship",
                                  "sha2": "<!-- frame: p01 -->\n# Risks\n\n- none"})
        self.assertEqual(rc, 1)
        self.assertIn("p01", err); self.assertIn("p02", err)
        self.assertFalse((self.parsed / "rec.mp4.md").exists())

    def test_matching_echo_is_stripped_from_the_doc(self):
        rc, _ = self._assemble({"sha1": "<!-- frame: p01 -->\n# Roadmap\n\n- ship",
                                "sha2": "<!-- frame: p02 -->\n# Risks\n\n- none"})
        self.assertEqual(rc, 0)
        md = (self.parsed / "rec.mp4.md").read_text()
        self.assertNotIn("<!-- frame:", md)
        self.assertIn("# Roadmap", md.replace("**Roadmap**", "# Roadmap"))

    def test_no_content_answers_need_no_echo(self):
        # the gate demands exactly <!-- no-content --> for people-only frames: no echo there
        rc, err = self._assemble({"sha1": "<!-- no-content -->", "sha2": "<!-- frame: p02 -->\n# Risks\n\n- none"})
        self.assertEqual(rc, 0)
        self.assertNotIn("frame echo", err)

    def test_results_without_an_echo_are_accepted_with_a_warning(self):
        rc, err = self._assemble({"sha1": "# Roadmap\n\n- ship", "sha2": "# Risks\n\n- none"})
        self.assertEqual(rc, 0)
        self.assertIn("frame echo", err)


if __name__ == "__main__":
    unittest.main()
