"""Second dedup round: frames whose VLM transcriptions repeat an earlier kept frame's.

Pixel dedup (96x54 thumbnails) cannot separate a scrolled email from a different slide on
the same template; the transcription text can. The round is recomputed on every assemble
(duplicate PNGs are kept), so changing or disabling the threshold restores frames.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import video_capture as V

EMAIL = ("# Launch Documents — Email\n\nFrom Alex Doe to the release readiness team. "
         "Please find the launch documents attached: ExportOrdersStaging.csv, "
         "Setup_Guide.docx, CatalogVisibility_UserStories.xlsx, "
         "Load Test Approach.docx and the sample data generation deck.")
DIAGRAM = ("# Systems overview\n\nWeb storefront, social lead forms, support desk customer "
           "care, bulk uploads, integration layer, product catalogue service, pricing engine, "
           "coupon service, customer data hub, reporting dashboards.")
VP = Path(__file__).resolve().parent.parent / "skills" / "visual-parse" / "vision_prep.py"


def _p(n, t0, t1):
    return {"page": n, "img_sha": f"sha{n}", "t_start": t0, "t_end": t1, "shown_at": [[t0, t1]]}


class DedupContentTests(unittest.TestCase):
    def test_repeat_of_an_earlier_frame_is_marked_duplicate_of_it(self):
        kept = [(_p(1, 0, 10), EMAIL), (_p(2, 20, 30), DIAGRAM), (_p(3, 40, 50), EMAIL + "\n\nRegards.")]
        live, dups = V.dedup_content(kept)
        self.assertEqual([p["page"] for p, _ in live], [1, 2])
        self.assertEqual([(d["page"], d["duplicate_of"]) for d in dups], [(3, 1)])

    def test_same_template_different_content_is_kept(self):
        other = DIAGRAM.replace("Web storefront, social lead forms", "Mail search results, shared inbox") \
                       .replace("product catalogue service, pricing engine", "team chat, shared drive files")
        live, dups = V.dedup_content([(_p(1, 0, 10), DIAGRAM), (_p(2, 20, 30), other)], jaccard=0.8)
        self.assertEqual(dups, [])
        self.assertEqual(len(live), 2)

    def test_tiny_transcriptions_are_never_merged(self):
        live, dups = V.dedup_content([(_p(1, 0, 10), "# Observations"), (_p(2, 20, 30), "# Observations")])
        self.assertEqual(dups, [])

    def test_threshold_zero_disables_the_round(self):
        live, dups = V.dedup_content([(_p(1, 0, 10), EMAIL), (_p(2, 20, 30), EMAIL)], jaccard=0)
        self.assertEqual(dups, [])


class AssembleContentDedupTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        self.slug = "rec__standup--mp4"
        self.rd = r / "assets" / self.slug; self.rd.mkdir(parents=True)
        pages = []
        for n, (t0, t1) in enumerate([(10, 20), (30, 40), (50, 60)], 1):
            (self.rd / f"p{n:02d}.png").write_bytes(b"png")
            pages.append({"page": n, "image": f"{self.slug}/p{n:02d}.png", "img_sha": f"sha{n}", "flagged": True,
                          "why": "video-frame", "t_start": t0, "t_end": t1, "shown_at": [[t0, t1]]})
        self.pj = self.rd / "pages.json"
        self.pj.write_text(json.dumps({"doc": "standup.mp4", "slug": self.slug, "medium": "video",
                                       "duration": 70.0, "pages": pages}))
        self.probe = r / "probe.json"
        self.probe.write_text(json.dumps({"source": "rec/standup.mp4", "transcript": "none", "sidecar": None,
                                          "sidecar_source": None, "warnings": []}))
        self.results = r / "vision"; self.results.mkdir()
        (self.results / "result_0.json").write_text(json.dumps({"sha1": EMAIL, "sha2": DIAGRAM,
                                                                 "sha3": EMAIL + "\n\nRegards."}))
        self.parsed = r / "parsed"; self.parsed.mkdir()
        self.db = r / "k.sqlite"; sqlite3.connect(self.db).close()

    def tearDown(self):
        self.t.cleanup()

    def _assemble(self, *extra, results=True):
        # dedup mechanics in isolation: the review round is skipped explicitly (it has its own tests)
        args = ["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                "--parsed", str(self.parsed), "--db", str(self.db), "--no-review", *extra]
        if results:
            args += ["--results", str(self.results)]
        return V.main(args)

    def _md(self):
        return (self.parsed / "rec__standup.mp4.md").read_text()

    def _pages(self):
        return {p["page"]: p for p in json.loads(self.pj.read_text())["pages"]}

    def test_duplicate_frame_is_left_out_and_its_showing_joins_the_survivor(self):
        self.assertEqual(self._assemble(), 0)
        md = self._md()
        self.assertNotIn("(frame p03)", md)
        self.assertIn("<!-- on-screen: 00:00:10–00:00:20, 00:00:50–00:01:00 -->", md)
        p = self._pages()
        self.assertEqual((p[3]["dropped"], p[3]["duplicate_of"]), ("duplicate", 1))
        self.assertEqual(p[1]["shown_at"], [[10, 20]])  # survivor's own windows untouched on disk
        self.assertTrue((self.rd / "p03.png").exists())  # duplicates keep their PNG

    def test_manifest_counts_duplicates_separately_from_no_content(self):
        self._assemble()
        entry = json.loads((self.parsed / "manifest.json").read_text())[0]
        self.assertEqual((entry["frames_kept"], entry["frames_dropped_duplicate"],
                          entry["frames_dropped_no_content"]), (2, 1, 0))

    def test_rerun_with_the_round_disabled_restores_the_frame(self):
        self._assemble()
        self.assertEqual(self._assemble("--content-dup", "0"), 0)
        self.assertIn("(frame p03)", self._md())
        self.assertNotIn("dropped", self._pages()[3])
        self.assertNotIn("duplicate_of", self._pages()[3])

    def test_duplicate_without_a_current_result_stays_dropped_instead_of_refusing(self):
        self._assemble()
        c = sqlite3.connect(self.db); c.execute("DELETE FROM page_render WHERE img_sha='sha3'"); c.commit(); c.close()
        self.assertEqual(self._assemble(results=False), 0)
        self.assertEqual(self._pages()[3]["dropped"], "duplicate")

    def test_stuck_duplicate_whose_survivor_is_gone_warns_instead_of_vanishing(self):
        pages = json.loads(self.pj.read_text())
        pages["pages"][2].update(dropped="duplicate", duplicate_of=9)   # no such kept page
        pages["pages"][1].update(dropped="duplicate")                   # hand-edited: no duplicate_of
        self.pj.write_text(json.dumps(pages))
        (self.results / "result_0.json").write_text(json.dumps({"sha1": EMAIL}))  # no current result for p2/p3
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._assemble(), 0)
        self.assertIn("p02", err.getvalue())
        self.assertIn("p03", err.getvalue())

    def test_vision_prep_batches_duplicates_but_not_no_content_frames(self):
        pages = json.loads(self.pj.read_text())
        pages["pages"][1]["dropped"] = "no-content"
        pages["pages"][2].update(dropped="duplicate", duplicate_of=1)
        self.pj.write_text(json.dumps(pages))
        out = Path(self.t.name) / "prep"
        subprocess.run([sys.executable, str(VP), "--render-dir", str(self.rd), "--out", str(out)],
                       check=True, capture_output=True, text=True)
        items = [i for b in sorted(out.glob("batch_*.json")) for i in json.loads(b.read_text())]
        self.assertEqual(sorted(i["page"] for i in items), [1, 3])


if __name__ == "__main__":
    unittest.main()
