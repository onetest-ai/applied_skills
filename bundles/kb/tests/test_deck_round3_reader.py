"""Round 3 of the deck reader (spec §10): the vision trigger counts real words only (X1), a flagged slide lists its
visual elements so a figure claim names one (X2), navigation repeated across slides is page furniture (X5), and an
attribution line under a quote is that quote's context, not a claim of its own (X9)."""
from __future__ import annotations

import json
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
except ImportError:
    HAVE = False


def _run(builder):
    d = Path(tempfile.mkdtemp())
    res = P.write_outputs(builder(d), d / "work")
    secs = json.loads((d / "work" / "sections.json").read_text(encoding="utf-8"))
    stats = json.loads((d / "work" / "stats.json").read_text(encoding="utf-8"))
    return res, secs, stats


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class ResidualTokens(unittest.TestCase):
    """X1: letter-spaced fragments and pieces of words the slide already holds are not unseen text."""

    def test_letter_spacing_is_not_residual(self):
        self.assertEqual(P.residual_tokens("C U R R E N T   S T A T E   R E A D O U T", "CURRENT STATE READOUT"), [])

    def test_fragments_of_held_words_are_not_residual(self):
        self.assertEqual(P.residual_tokens("why it matte rs th e evidence", "why it matters the evidence"), [])

    def test_real_unseen_words_are(self):
        self.assertEqual(P.residual_tokens("Southfield uptime 97.4%", "Regional review"), ["southfield", "uptime", "97.4"])

    def test_spaced_slide_is_not_sent_to_vision(self):
        res, _, _ = _run(F.spaced_deck)
        self.assertEqual(res["flagged"], [])


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class FigureElements(unittest.TestCase):
    """X2: a flagged slide's figure record lists its pictures and charts (shape id, kind, name, box)."""

    def test_elements_listed_with_boxes(self):
        _, secs, _ = _run(F.elements_deck)
        fig = secs[0]["figures"][0]
        els = {e["kind"]: e for e in fig["elements"]}
        self.assertEqual(set(els), {"picture", "chart"})
        self.assertEqual(els["picture"]["name"], "Uptime callout")
        for kind, box in (("picture", F.ELEMENT_PIC), ("chart", F.ELEMENT_CHART)):
            self.assertEqual(tuple(els[kind]["box"][k] for k in "xywh"), tuple(int(v) for v in box))


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class Navigation(unittest.TestCase):
    """X5: text at the same place on 3+ visible slides is navigation: never a block, reported in stats."""

    def test_tabs_left_out_and_reported(self):
        _, secs, stats = _run(F.tabs_deck)
        texts = [p["text"] for s in secs for p in s["paragraphs"]]
        for t in F.TABS:
            self.assertNotIn(t, texts)
        for t in F.TAB_BODIES:
            self.assertIn(t, texts)
        self.assertEqual(sorted(stats["chrome"]["texts"]), sorted(F.TABS))
        self.assertEqual(stats["chrome"]["slides"], [1, 2, 3, 4])

    def test_two_slides_are_not_navigation(self):
        d = Path(tempfile.mkdtemp())
        deck = F.tabs_deck(d)
        from pptx import Presentation
        prs = Presentation(str(deck))
        lst = prs.slides._sldIdLst
        for el in list(lst)[2:]:
            lst.remove(el)
        prs.save(str(deck))
        P.write_outputs(deck, d / "work")
        texts = [p["text"] for s in json.loads((d / "work" / "sections.json").read_text()) for p in s["paragraphs"]]
        self.assertIn(F.TABS[0], texts)


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class Attribution(unittest.TestCase):
    """X9: "<source>; <timestamp or date>" under a statement is folded into its ctx as `attribution`."""

    def test_attribution_folded(self):
        _, secs, _ = _run(F.attribution_deck)
        paras = {p["text"]: p for s in secs for p in s["paragraphs"]}
        self.assertNotIn(F.ATTR_LINE, paras)
        self.assertNotIn(F.ATTR_LINE_2, paras)
        self.assertEqual(paras[F.ATTR_QUOTE]["ctx"]["attribution"], F.ATTR_LINE)
        self.assertEqual(paras[F.ATTR_STATEMENT]["ctx"]["attribution"], F.ATTR_LINE_2)

    def test_attribution_pattern(self):
        for t in (F.ATTR_LINE, F.ATTR_LINE_2, "Atlas Technology Session – RouteDB; 00:42:07–00:42:19",
                  "Discovery Session w/ Vendor – Ordering; 01:27:46"):
            self.assertTrue(P.is_attribution(t), t)
        for t in ("Uptime was 99.9% in 2025; outages fell.", "Go-live is set for Q2 2027.", "Depot A; Depot B"):
            self.assertFalse(P.is_attribution(t), t)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class SmallPicturesAndBadges(unittest.TestCase):
    """A picture of 1% of the slide or more (a callout) goes to vision and is an element; a logo does not; a bare
    step number ("1", "2") is a badge, not a figure to check."""

    @classmethod
    def setUpClass(cls):
        cls.res, cls.secs, cls.stats = _run(F.callout_deck)

    def test_callout_flagged_logo_not(self):
        self.assertEqual(self.res["flagged"], [1])
        els = self.secs[0]["figures"][0]["elements"]
        self.assertEqual([e["name"] for e in els], ["Hours callout"])

    def test_step_badges_carry_no_risk(self):
        badges = [x for p in self.secs[2]["paragraphs"] for x in p["sentences"] if x["text"] in ("1", "2", "3")]
        self.assertEqual(len(badges), 3)
        self.assertTrue(all(x["risk"] == [] for x in badges), badges)
