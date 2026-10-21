"""Round 3, deck comments (spec §10.7): a deck comment is the header, the quoted statement and the fix (Brain and
Source lines only on request, `--fields`); a figure comment is pinned inside the element it reads and names it; the
60-word cap counts the comment's body, not its header or quote line. Word comments are unchanged."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

import sys
DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

import fact_check_invariants as I  # noqa: E402

try:
    import pptx  # noqa: F401
    import pymupdf  # noqa: F401
    import deck_fixtures as F
    import pptx_sections as P
    import pptx_annotate as A
    from render_pages import soffice_bin
    HAVE = soffice_bin() is not None
except ImportError:
    HAVE = False

F_DECK = {"id": "C03", "verdict": "Misleading", "severity": "Major", "section": "Slide 2: Billing",
          "p_id": "s2-sh12-p1", "quote": "I would definitely say more than 50% of our calls are probably previous invoices",
          "evidence": "The speaker revises it to about fifty-fifty.", "fix": "Say about half of calls.",
          "why": "the speaker revises it to about fifty-fifty", "suggested": "About half of calls concern previous invoices.", "source": "Billing session.md"}
F_WORD = {**F_DECK, "id": "C01", "p_id": "p12", "section": "2 Scope"}


class CommentText(unittest.TestCase):
    def test_deck_default_has_statement_why_and_suggestion(self):
        text = I.comment_text(F_DECK)
        self.assertTrue(text.startswith('[Misleading]\n"I would definitely say more than 50% of our calls are probably…"'))
        self.assertIn("Why: Billing session (undated) says the speaker revises it to about fifty-fifty.", text)
        self.assertIn('Suggested: "About half of calls concern previous invoices."', text)
        self.assertNotIn("C03", text)

    def test_fields_bring_brain_and_source_back(self):
        t = I.comment_text(F_DECK, fields=I.COMMENT_FIELDS)
        self.assertIn("\nBrain: The speaker revises it to about fifty-fifty.", t)
        self.assertTrue(t.endswith("\nSource: Billing session.md"))

    def test_word_uses_the_same_correction_without_the_statement_line(self):
        self.assertEqual(I.comment_text(F_WORD), '[Misleading]\nWhy: Billing session (undated) says the speaker revises it to about fifty-fifty.\nSuggested: "About half of calls concern previous invoices."')

    def test_figure_comment_category_has_no_internal_location(self):
        f = {**F_DECK, "id": "I01", "p_id": "s3-sh79", "section": "Figure", "element": 'picture "Callout"'}
        self.assertEqual(I.comment_text(f).splitlines()[0], '[Misleading]')

    def test_cap_counts_why_and_suggested_without_the_statement(self):
        f = {**F_DECK, "suggested": " ".join(["word"] * 80)}
        text = I.comment_text(f)
        self.assertGreater(I.comment_words(text, True), I.MAX_COMMENT_WORDS)
        self.assertLess(I.comment_words(text, True), len(text.split()))


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class FigurePins(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.deck = F.elements_deck(self.d)
        P.write_outputs(self.deck, self.d / "w")
        self.sections = self.d / "w" / "sections.json"
        fig = json.loads(self.sections.read_text())[0]["figures"][0]
        self.el = {e["kind"]: e for e in fig["elements"]}

    def finding(self, kind):
        return {**F_DECK, "id": "I01", "p_id": f"s1-sh{self.el[kind]['shape_id']}", "section": "Figure",
                "anchor": "drawing", "figure": 1, "quote": "Uptime callout reads 97.4%",
                "suggested": "Uptime callout: corrected value"}       # a slide box: near the quote's length

    def test_pin_inside_the_element(self):
        box = self.el["picture"]["box"]
        boxes = I.pptx_boxes(self.deck)
        slide, x, y, _ = A.pin(self.finding("picture"), boxes)
        self.assertEqual(slide, 1)
        self.assertTrue(box["x"] < x * I.EMU_PER_POS < box["x"] + box["w"])
        self.assertTrue(box["y"] < y * I.EMU_PER_POS < box["y"] + box["h"])

    def test_written_figure_comment_quotes_its_statement(self):
        out = self.d / "out.pptx"
        written, _ = A.annotate(self.deck, out, [self.finding("picture")], sections=self.sections)
        self.assertEqual(written, ["I01"])
        self.assertIn('"Uptime callout reads 97.4%"', I.read_pptx_comments(out)[0]["text"])

    def test_generic_element_keeps_the_plain_comment_category(self):
        out = self.d / "out.pptx"
        A.annotate(self.deck, out, [self.finding("chart")], sections=self.sections)
        self.assertEqual(I.read_pptx_comments(out)[0]["text"].splitlines()[0], '[Misleading]')

    def test_fields_flag(self):
        out = self.d / "out.pptx"
        findings = self.d / "f.json"
        findings.write_text(json.dumps([{**self.finding("picture"), "claim": "x", "type": "NUM", "confidence": "High",
                                         "trail": "t", "conflicts": [], "sources": []}]))
        r = subprocess.run([sys.executable, "-I", str(DECK / "pptx_annotate.py"), str(self.deck), str(out),
                            "--findings", str(findings), "--select", "major", "--sections", str(self.sections),
                            "--fields", "quote,why,suggested,brain,source"], capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\nSource: Billing session.md", I.read_pptx_comments(out)[0]["text"])


if __name__ == "__main__":
    unittest.main()
