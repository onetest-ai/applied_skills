from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import chunking
import video_capture as V


class AssembleTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        self.corpus = r / "corpus" / "rec"; self.corpus.mkdir(parents=True)
        (self.corpus / "standup.mp4").write_bytes(b"v")
        self.sidecar = self.corpus / "standup.vtt"
        self.sidecar.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v Alice>Look at the roadmap.</v>\n\n"
                                "01:00:05.000 --> 01:00:07.000\n<v Bob>Done.</v>\n")
        self.slug = "rec__standup"
        self.rd = r / "assets" / self.slug; self.rd.mkdir(parents=True)
        pages = []
        for n, (t0, t1) in enumerate([(2, 30), (40, 50)], 1):
            (self.rd / f"p{n:02d}.png").write_bytes(b"png"); (self.rd / f"p{n:02d}.txt").write_text("")
            pages.append({"page": n, "image": f"{self.slug}/p{n:02d}.png", "img_sha": f"sha{n}", "flagged": True,
                          "why": "video-frame", "t_start": t0, "t_end": t1,
                          "shown_at": [[t0, t1]] + ([[3700, 3710]] if n == 1 else [])})
        (self.rd / "pages.json").write_text(json.dumps({"doc": "standup.mp4", "slug": self.slug, "medium": "video",
                                                        "duration": 3720.0, "frames_capped": False, "pages": pages}))
        self.work = r / "video" / self.slug; self.work.mkdir(parents=True)
        self.probe = self.work / "probe.json"
        self.probe.write_text(json.dumps({"source": "rec/standup.mp4", "video": str(self.corpus / "standup.mp4"),
                                          "slug": self.slug, "duration": 3720.0, "has_audio": True,
                                          "sidecar": str(self.sidecar), "sidecar_source": "rec/standup.vtt",
                                          "transcript": "sidecar", "warnings": []}))
        self.results = r / "vision"; self.results.mkdir()
        self.parsed = r / "parsed"; self.parsed.mkdir()

    def tearDown(self):
        self.t.cleanup()

    def _results(self, mapping):
        (self.results / "result_0.json").write_text(json.dumps(mapping))

    def _assemble(self):
        return V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                       "--results", str(self.results), "--parsed", str(self.parsed)])

    def test_interleaves_by_time_with_hms_and_markers(self):
        self._results({"sha1": "# Q3 Roadmap\n\n- ship", "sha2": "# Risks\n\n- none"})
        self.assertEqual(self._assemble(), 0)
        md = (self.parsed / "rec__standup.mp4.md").read_text()
        self.assertTrue(md.startswith("# SOURCE: rec/standup.mp4\n# method: video-lane (transcript: sidecar)\n"
                                      "# fidelity: full\n\n"))
        heads = [l for l in md.splitlines() if l.startswith("## ")]
        self.assertEqual(heads, ["## 00:00:01 — Alice (cue 1)", "## 00:00:02 · Q3 Roadmap (frame p01)",
                                 "## 00:00:40 · Risks (frame p02)", "## 01:00:05 — Bob (cue 2)"])
        self.assertIn(f"<!-- image: {self.slug}/p01.png -->", md)
        self.assertIn("<!-- on-screen: 00:00:02–00:00:30, 01:01:40–01:01:50 -->", md)
        self.assertIn("<!-- speaker: Alice -->", md)

    def test_preamble_is_the_only_h1_through_the_real_chunker(self):
        self._results({"sha1": "# Q3 Roadmap\n\n## detail\n\n- ship", "sha2": "# Risks"})
        self._assemble()
        md = (self.parsed / "rec__standup.mp4.md").read_text()
        body = chunking.strip_preamble(md)
        self.assertFalse([l for l in body.splitlines() if l.startswith("# ")])
        for rec in chunking.section_records(md):
            self.assertNotIn("SOURCE", str(rec))

    def test_no_content_frames_dropped_and_png_deleted(self):
        self._results({"sha1": "# Q3 Roadmap", "sha2": V.NO_CONTENT})
        self._assemble()
        md = (self.parsed / "rec__standup.mp4.md").read_text()
        self.assertNotIn("p02", md)
        self.assertFalse((self.rd / "p02.png").exists())
        pages = json.loads((self.rd / "pages.json").read_text())["pages"]
        self.assertEqual(pages[1]["dropped"], "no-content")
        man = {e["source"]: e for e in json.loads((self.parsed / "manifest.json").read_text())}
        self.assertEqual(man["rec/standup.mp4"]["frames_kept"], 1)
        self.assertEqual(man["rec/standup.mp4"]["frames_dropped_no_content"], 1)
        self.assertEqual(man["rec/standup.mp4"]["md"], "rec__standup.mp4.md")
        self.assertEqual(man["rec/standup.vtt"], {"source": "rec/standup.vtt", "skipped": True,
                                                  "method": "consumed-by-video", "consumed_by": "rec/standup.mp4"})
        self.assertEqual(self._assemble(), 0)  # re-run: dropped page needs no VLM result

    def test_refuses_when_a_frame_has_no_vlm_result(self):
        self._results({"sha1": "# Q3 Roadmap"})
        self.assertEqual(self._assemble(), 1)
        self.assertFalse((self.parsed / "rec__standup.mp4.md").exists())

    def test_transcript_none_is_frames_only(self):
        p = json.loads(self.probe.read_text()); p.update(transcript="none", sidecar=None, sidecar_source=None)
        self.probe.write_text(json.dumps(p))
        self._results({"sha1": "# A", "sha2": "# B"})
        self._assemble()
        md = (self.parsed / "rec__standup.mp4.md").read_text()
        self.assertIn("# method: video-lane (transcript: none)", md)
        self.assertNotIn("cue", md)

    def test_manifest_upsert_keeps_other_entries(self):
        (self.parsed / "manifest.json").write_text(json.dumps([{"source": "a.pdf", "md": "a.pdf.md"}]))
        self._results({"sha1": "# A", "sha2": "# B"})
        self._assemble(); self._assemble()
        srcs = [e["source"] for e in json.loads((self.parsed / "manifest.json").read_text())]
        self.assertEqual(sorted(srcs), ["a.pdf", "rec/standup.mp4", "rec/standup.vtt"])

    def test_forget_removes_doc_entries_and_assets(self):
        self._results({"sha1": "# A", "sha2": "# B"})
        self._assemble()
        code = V.main(["forget", "--source", "rec/standup.mp4", "--parsed", str(self.parsed),
                       "--assets-root", str(self.rd.parent)])
        self.assertEqual(code, 0)
        self.assertFalse((self.parsed / "rec__standup.mp4.md").exists())
        self.assertEqual(json.loads((self.parsed / "manifest.json").read_text()), [])
        self.assertFalse(self.rd.exists())
