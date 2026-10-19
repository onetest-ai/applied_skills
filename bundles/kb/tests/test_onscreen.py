"""Only what is on the slide is analysed (spec F7, eval 11). Text in a box outside the slide, text pushed past the
slide's edge and hidden-flag shapes are removed from the analysis entirely (blocks, context, statements, document
date) and reported as left out, never silently."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

import sys
DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

try:
    import pptx  # noqa: F401
    import pymupdf  # noqa: F401
    import deck_fixtures as F
    import pptx_sections as P
    from render_pages import soffice_bin
    HAVE = soffice_bin() is not None
    HAVE_PPTX = True
except ImportError:
    HAVE = HAVE_PPTX = False

sq = lambda s: re.sub(r"\W+", "", str(s).casefold())


def everything_seen(sections: list[dict]) -> str:
    """All text the analysis can see: paragraphs, row cells and every ctx value."""
    parts = []
    for s in sections:
        for p in s.get("paragraphs", []):
            parts += [p["text"], json.dumps(p.get("ctx", {}), ensure_ascii=False)]
        for t in s.get("tables", []):
            for r in t["rows"]:
                parts += [" ".join(r["cells"]), json.dumps(r.get("ctx", {}), ensure_ascii=False)]
    return sq(" ".join(parts))


@unittest.skipUnless(HAVE_PPTX, "needs python-pptx")
class GeometryOnly(unittest.TestCase):
    """Without a render, the shape walk still drops what is provably not on the slide."""

    def test_boxes_outside_the_slide_and_hidden_shapes_are_not_blocks(self):
        deck = P.read_deck(F.offscreen_deck(Path(tempfile.mkdtemp())))
        seen = everything_seen([{"paragraphs": [r for k, r, _ in s["blocks"] if k == "p"],
                                 "tables": [{"rows": [r for k, r, _ in s["blocks"] if k == "r"]}]} for s in deck["sections"]])
        for gone in F.OFFSCREEN_DROP[:3]:          # right of the slide, left of the slide, hidden="1"
            self.assertNotIn(sq(gone), seen, gone)
        reasons = {o["reason"] for o in deck["offscreen"]}
        self.assertEqual(reasons, {"outside the slide", "hidden shape"})
        self.assertEqual(sum(o["blocks"] for o in deck["offscreen"]), 3)


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and LibreOffice")
class OnScreenOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        cls.res = P.write_outputs(F.offscreen_deck(cls.d), cls.d / "out")
        cls.sections = json.loads((cls.d / "out" / "sections.json").read_text())
        cls.stats = json.loads((cls.d / "out" / "stats.json").read_text())
        cls.batch = (cls.d / "out" / "batch_1.json").read_text()
        cls.seen = everything_seen(cls.sections)

    def test_nothing_off_the_slide_reaches_the_analysis(self):
        leaked = [t for t in F.OFFSCREEN_DROP if sq(t) in self.seen]
        self.assertEqual(leaked, [])
        self.assertFalse(any(sq(t) in sq(self.batch) for t in F.OFFSCREEN_DROP), "an off-screen text reached a batch file")

    def test_everything_on_the_slide_is_kept(self):
        # guards: a letter-spaced label, a table row, text in a group, overflow that stays on the slide
        lost = [t for t in F.OFFSCREEN_KEEP if sq(t) not in self.seen]
        self.assertEqual(lost, [])

    def test_speaker_notes_never_enter(self):
        self.assertNotIn(sq(F.OFFSCREEN_NOTES), self.seen)

    def test_left_out_text_is_reported(self):
        off = self.stats["offscreen"]
        self.assertEqual(off["blocks"], len(F.OFFSCREEN_DROP))
        self.assertEqual(off["slides"], [1])
        self.assertEqual(off["by_reason"], {"outside the slide": 2, "hidden shape": 1, "not shown on the slide": 5})

    def test_statement_counts_exclude_left_out_text(self):
        statements = sum(len(p["sentences"]) for s in self.sections for p in s["paragraphs"]) + \
            sum(len(t["rows"]) for s in self.sections for t in s["tables"])
        self.assertEqual(self.stats["statements_total"], statements)

    def test_coverage_line_names_what_was_left_out(self):
        import findings_report as R
        line = R.coverage_line({"scope": "all", "sections_total": 1, "claims_verified": 3, "offscreen": self.stats["offscreen"]})
        self.assertIn("Not checked: 8 text blocks not on the slide (slide 1).", line)
        self.assertNotIn("not on the slide", R.coverage_line({"scope": "all", "sections_total": 1, "claims_verified": 3}))

    def test_merge_carries_offscreen_into_run_json(self):
        src = (KB_ROOT / "skills" / "doc-fact-check" / "merge_findings.py").read_text()
        self.assertIn('run["offscreen"] = stats["offscreen"]', src)


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and LibreOffice")
class OffscreenDocumentDate(unittest.TestCase):
    def test_a_date_parked_beside_the_title_slide_is_not_the_document_date(self):
        from pptx.util import Inches
        d = Path(tempfile.mkdtemp())
        prs = F._prs()
        t = prs.slides.add_slide(prs.slide_layouts[0])
        t.shapes.title.text = "Quarterly review"
        t.placeholders[1].text = "Operations"
        t.shapes.add_textbox(prs.slide_width + Inches(1), Inches(1), Inches(3), Inches(0.5)).text_frame.text = "March 2019"
        res = P.write_outputs(F._save(prs, d / "parked.pptx"), d / "out")
        stats = json.loads((d / "out" / "stats.json").read_text())
        self.assertNotEqual((stats.get("doc_date") or {}).get("value"), "March 2019")


if __name__ == "__main__":
    unittest.main()
