"""Round 3, figure claims (spec §10): a figure claim on a slide with visual elements names one of them by its p_id
s<N>-sh<id> (X2), and one that only repeats text the slide already holds is dropped before stage V and reported (X3)."""
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
    import chunk_claims as C
    from render_pages import soffice_bin
    HAVE = soffice_bin() is not None
except ImportError:
    HAVE = False


def fclaim(n, p_id, quote):
    return {"claim_id": f"I{n:02d}", "p_id": p_id, "s_id": "s1", "section": "Figure", "quote": quote,
            "claim": f"{quote}, per slide 1.", "type": "NUM", "kind": "figure", "figure": 1,
            "questions": ["what does the slide show"]}


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class FigureClaims(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        P.write_outputs(F.elements_deck(self.d), self.d / "w")
        self.w = self.d / "w"
        fig = json.loads((self.w / "sections.json").read_text())[0]["figures"][0]
        self.el = {e["kind"]: e["shape_id"] for e in fig["elements"]}
        for b in json.loads((self.w / "batches.json").read_text()):
            k = b["batch"]
            (self.w / f"claims_batch_{k}.json").write_text("[]")
            (self.w / f"coverage_batch_{k}.json").write_text(json.dumps(
                [{"section_id": s["section_id"], "reason": "no checkable statement"} for s in b["sections"]]))

    def put(self, claims):
        (self.w / "claims_figures.json").write_text(json.dumps(claims))
        return C.chunk(self.w, "all", 15)

    def test_claim_naming_an_element_is_accepted(self):
        out, errors = self.put([fclaim(1, f"s1-sh{self.el['picture']}", "Uptime callout reads 97.4%")])
        self.assertEqual(errors, [])
        self.assertEqual(out[0]["claim_ids"], ["I01"])

    def test_claim_must_name_an_element_when_the_slide_has_them(self):
        _, errors = self.put([fclaim(1, "s1", "Uptime callout reads 97.4%")])
        self.assertTrue(any("I01" in e and "element" in e for e in errors), errors)

    def test_unknown_element_is_refused(self):
        _, errors = self.put([fclaim(1, "s1-sh9999", "Uptime callout reads 97.4%")])
        self.assertTrue(any("I01" in e and "element" in e for e in errors), errors)

    def test_duplicate_of_slide_text_is_dropped_and_reported(self):
        out, errors = self.put([fclaim(1, f"s1-sh{self.el['picture']}", "Uptime callout reads 97.4%"),
                                fclaim(2, f"s1-sh{self.el['chart']}", "Regional uptime review")])
        self.assertEqual(errors, [])
        self.assertEqual([c for ch in out for c in ch["claim_ids"]], ["I01"])
        dropped = json.loads((self.w / "figures_dropped.json").read_text())
        self.assertEqual([x["claim_id"] for x in dropped], ["I02"])
        self.assertTrue(dropped[0]["duplicate_of"].startswith("s1-sh"))


if __name__ == "__main__":
    unittest.main()
