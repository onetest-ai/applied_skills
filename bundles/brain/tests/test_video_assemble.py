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
        self.slug = "rec__standup--mp4"
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
        before = (self.rd / "pages.json").read_bytes()
        self._results({"sha1": "# Q3 Roadmap"})
        self.assertEqual(self._assemble(), 1)
        self.assertFalse((self.parsed / "rec__standup.mp4.md").exists())
        self.assertTrue((self.rd / "p01.png").exists())
        self.assertTrue((self.rd / "p02.png").exists())
        self.assertEqual((self.rd / "pages.json").read_bytes(), before)
        self.assertFalse((self.parsed / "manifest.json").exists())

    def test_refuses_on_missing_sidecar_without_touching_anything(self):
        before = (self.rd / "pages.json").read_bytes()
        p = json.loads(self.probe.read_text())
        p["sidecar"] = str(self.corpus / "does-not-exist.vtt")
        self.probe.write_text(json.dumps(p))
        self._results({"sha1": "# A", "sha2": "# B"})
        self.assertEqual(self._assemble(), 1)
        self.assertFalse((self.parsed / "rec__standup.mp4.md").exists())
        self.assertTrue((self.rd / "p01.png").exists())
        self.assertTrue((self.rd / "p02.png").exists())
        self.assertEqual((self.rd / "pages.json").read_bytes(), before)
        self.assertFalse((self.parsed / "manifest.json").exists())

    def test_asr_transcript_uses_work_dir_transcript_and_model(self):
        p = json.loads(self.probe.read_text())
        p.update(transcript="asr", sidecar=None, sidecar_source=None)
        self.probe.write_text(json.dumps(p))
        (self.work / "asr.json").write_text(json.dumps({"model": "ggml-small.en.bin"}))
        (self.work / "transcript.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v Alice>From the machine.</v>\n")
        self._results({"sha1": "# A", "sha2": "# B"})
        self.assertEqual(self._assemble(), 0)
        md = (self.parsed / "rec__standup.mp4.md").read_text()
        self.assertIn("# method: video-lane (transcript: asr:whisper.cpp:ggml-small.en)", md)
        self.assertIn("From the machine.", md)

    def test_asr_transcript_missing_vtt_refuses_without_writing_doc(self):
        p = json.loads(self.probe.read_text())
        p.update(transcript="asr", sidecar=None, sidecar_source=None)
        self.probe.write_text(json.dumps(p))
        (self.work / "asr.json").write_text(json.dumps({"model": "ggml-small.en.bin"}))
        self._results({"sha1": "# A", "sha2": "# B"})
        self.assertEqual(self._assemble(), 1)
        self.assertFalse((self.parsed / "rec__standup.mp4.md").exists())
        self.assertFalse((self.parsed / "manifest.json").exists())

    def test_db_cache_round_trip_survives_results_dir_deletion(self):
        import shutil
        import sqlite3
        db = str(Path(self.t.name) / "knowledge.sqlite")
        sqlite3.connect(db).close()
        self._results({"sha1": "# A", "sha2": "# B"})
        code = V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                       "--results", str(self.results), "--parsed", str(self.parsed), "--db", db])
        self.assertEqual(code, 0)
        first = (self.parsed / "rec__standup.mp4.md").read_text()
        shutil.rmtree(self.results)
        code = V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                       "--parsed", str(self.parsed), "--db", db])
        self.assertEqual(code, 0)
        self.assertEqual((self.parsed / "rec__standup.mp4.md").read_text(), first)

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

    def test_assemble_removes_the_sidecars_stale_parsed_doc(self):
        # An earlier parse_corpus pass indexed the sidecar as its own document.
        stale = self.parsed / "rec__standup.vtt.md"
        stale.write_text("# SOURCE: rec/standup.vtt\n# method: transcript-etl\n")
        other = self.parsed / "rec__other.vtt.md"; other.write_text("keep")
        self._results({"sha1": "# A", "sha2": "# B"})
        self.assertEqual(self._assemble(), 0)
        self.assertFalse(stale.exists())
        self.assertTrue(other.exists())
        self.assertTrue((self.parsed / "rec__standup.mp4.md").exists())

    def test_assemble_without_sidecar_removes_nothing(self):
        keep = self.parsed / "rec__standup.vtt.md"; keep.write_text("keep")
        p = json.loads(self.probe.read_text()); p.update(transcript="none", sidecar=None, sidecar_source=None)
        self.probe.write_text(json.dumps(p))
        self._results({"sha1": "# A", "sha2": "# B"})
        self.assertEqual(self._assemble(), 0)
        self.assertTrue(keep.exists())

    def test_backticked_or_quoted_no_content_sentinel_still_drops_the_frame(self):
        for reply in ("`<!-- no-content -->`", "```\n<!-- no-content -->\n```", '"<!-- No-Content -->"',
                      "  <!--no-content-->  ", "'<!--  NO-CONTENT  -->'"):
            self.assertTrue(V.is_no_content(reply), reply)
        for reply in ("# Slide\n\n<!-- no-content -->", "no-content", "<!-- no content here -->"):
            self.assertFalse(V.is_no_content(reply), reply)
        self._results({"sha1": "# Q3 Roadmap", "sha2": "`<!-- no-content -->`"})
        self.assertEqual(self._assemble(), 0)
        self.assertFalse((self.rd / "p02.png").exists())
        md = (self.parsed / "rec__standup.mp4.md").read_text()
        self.assertNotIn("no-content", md)
        self.assertNotIn("p02", md)

    def test_without_results_never_reads_result_files_in_the_cwd(self):
        import os
        import sqlite3
        db = str(Path(self.t.name) / "knowledge.sqlite")
        sqlite3.connect(db).close()
        self._results({"sha1": "# A", "sha2": "# B"})
        V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                "--results", str(self.results), "--parsed", str(self.parsed), "--db", db])
        # A stray result file in the CWD must not override the cache.
        cwd = Path(self.t.name) / "cwd"; cwd.mkdir()
        (cwd / "result_0.json").write_text(json.dumps({"sha1": "# HIJACKED", "sha2": "# B"}))
        old = os.getcwd(); os.chdir(cwd)
        try:
            code = V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                           "--parsed", str(self.parsed), "--db", db])
        finally:
            os.chdir(old)
        self.assertEqual(code, 0)
        self.assertNotIn("HIJACKED", (self.parsed / "rec__standup.mp4.md").read_text())

    def test_forget_leaves_a_same_stem_deck_render_alone(self):
        self._results({"sha1": "# A", "sha2": "# B"})
        self._assemble()
        deck = self.rd.parent / "rec__standup"; deck.mkdir()
        (deck / "pages.json").write_text(json.dumps({"doc": "standup.pptx", "pages": []}))
        code = V.main(["forget", "--source", "rec/standup.mp4", "--parsed", str(self.parsed),
                       "--assets-root", str(self.rd.parent)])
        self.assertEqual(code, 0)
        self.assertFalse(self.rd.exists())
        self.assertTrue((deck / "pages.json").exists())

    def test_forget_keeps_a_dir_whose_pages_json_is_not_video(self):
        pj = self.rd / "pages.json"
        pj.write_text(json.dumps({"doc": "x.pdf", "slug": self.slug, "pages": []}))
        code = V.main(["forget", "--source", "rec/standup.mp4", "--parsed", str(self.parsed),
                       "--assets-root", str(self.rd.parent)])
        self.assertEqual(code, 0)
        self.assertTrue(pj.exists())

    def test_forget_rejects_unsafe_sources(self):
        (self.parsed / "manifest.json").write_text(json.dumps([{"source": "a.pdf", "md": "a.pdf.md"}]))
        before = (self.parsed / "manifest.json").read_text()
        for bad in ("", ".", "/abs/standup.mp4", "../standup.mp4", "rec/../../x.mp4", "rec/./.."):
            code = V.main(["forget", "--source", bad, "--parsed", str(self.parsed),
                           "--assets-root", str(self.rd.parent), "--work", str(self.work.parent)])
            self.assertEqual(code, 1, bad)
        self.assertEqual((self.parsed / "manifest.json").read_text(), before)
        self.assertTrue(self.rd.exists()); self.assertTrue(self.work.exists())

    def test_forget_with_work_removes_work_dir(self):
        self._results({"sha1": "# A", "sha2": "# B"})
        self._assemble()
        self.assertTrue(self.work.exists())
        code = V.main(["forget", "--source", "rec/standup.mp4", "--parsed", str(self.parsed),
                       "--work", str(self.work.parent)])
        self.assertEqual(code, 0)
        self.assertFalse(self.work.exists())
