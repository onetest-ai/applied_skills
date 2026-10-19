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
          "source": "Billing session.md"}
F_WORD = {**F_DECK, "id": "C01", "p_id": "p12", "section": "2 Scope"}


class CommentText(unittest.TestCase):
    def test_deck_default_is_quote_and_fix(self):
        self.assertEqual(I.comment_text(F_DECK),
                         '[Misleading · Major · C03] §Slide 2\n'
                         'Re: "I would definitely say more than 50% of our calls are probably…"\n'
                         'Fix: Say about half of calls.')

    def test_fields_bring_brain_and_source_back(self):
        t = I.comment_text(F_DECK, fields=("quote", "brain", "fix", "source"))
        self.assertIn("\nBrain: The speaker revises it to about fifty-fifty.", t)
        self.assertTrue(t.endswith("\nSource: Billing session.md"))

    def test_word_unchanged(self):
        self.assertEqual(I.comment_text(F_WORD), "[Misleading · Major · C01] §2 Scope\n"
                                                 "Brain: The speaker revises it to about fifty-fifty.\n"
                                                 "Fix: Say about half of calls.\nSource: Billing session.md")

    def test_figure_header_names_its_element(self):
        f = {**F_DECK, "id": "I01", "p_id": "s3-sh79", "section": "Figure", "element": 'picture "Callout"'}
        self.assertTrue(I.comment_text(f).startswith('[Misleading · Major · I01] §Slide 3 · picture "Callout"\n'))

    def test_cap_counts_the_body_only(self):
        f = {**F_DECK, "fix": " ".join(["word"] * 55)}
        self.assertGreater(len(I.comment_text(f).split()), I.MAX_COMMENT_WORDS)
        self.assertEqual(I.comment_words(I.comment_text(f), True), 56)


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
                "anchor": "drawing", "figure": 1, "quote": "Uptime callout reads 97.4%"}

    def test_pin_inside_the_element(self):
        box = self.el["picture"]["box"]
        boxes = I.pptx_boxes(self.deck)
        slide, x, y, _ = A.pin(self.finding("picture"), boxes)
        self.assertEqual(slide, 1)
        self.assertTrue(box["x"] < x * I.EMU_PER_POS < box["x"] + box["w"])
        self.assertTrue(box["y"] < y * I.EMU_PER_POS < box["y"] + box["h"])

    def test_written_comment_names_the_element(self):
        out = self.d / "out.pptx"
        written, _ = A.annotate(self.deck, out, [self.finding("picture")], sections=self.sections)
        self.assertEqual(written, ["I01"])
        text = I.read_pptx_comments(out)[0]["text"]
        self.assertIn('§Slide 1 · picture "Uptime callout"', text)

    def test_generic_name_becomes_a_position(self):
        out = self.d / "out.pptx"
        A.annotate(self.deck, out, [self.finding("chart")], sections=self.sections)
        head = I.read_pptx_comments(out)[0]["text"].split("\n")[0]
        self.assertRegex(head, r"§Slide 1 · chart, (top|middle|bottom) (left|centre|right)$")

    def test_fields_flag(self):
        out = self.d / "out.pptx"
        findings = self.d / "f.json"
        findings.write_text(json.dumps([{**self.finding("picture"), "claim": "x", "type": "NUM", "confidence": "High",
                                         "trail": "t", "conflicts": [], "sources": []}]))
        r = subprocess.run([sys.executable, "-I", str(DECK / "pptx_annotate.py"), str(self.deck), str(out),
                            "--findings", str(findings), "--select", "major", "--sections", str(self.sections),
                            "--fields", "quote,brain,fix,source"], capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\nSource: Billing session.md", I.read_pptx_comments(out)[0]["text"])


if __name__ == "__main__":
    unittest.main()
