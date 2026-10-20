"""Round 3, fixes from the whole-diff review: each test is a probe that failed on the first round-3 build."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

import sys
DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

import chunk_claims as C  # noqa: E402
import fact_check_invariants as I  # noqa: E402
from section_io import tag_risk  # noqa: E402

try:
    import pptx  # noqa: F401
    import pymupdf  # noqa: F401
    import pptx_sections as P
    import pptx_annotate as A
    HAVE = True
except ImportError:
    HAVE = False


def _sec(*texts):
    return {"s01": {"section_id": "s01", "paragraphs": [{"p_id": f"s1-sh{i}-p1", "text": t} for i, t in enumerate(texts, 2)],
                    "tables": []}}


def _fig(cid, quote):
    return {"claim_id": cid, "s_id": "s1", "p_id": "s1-sh9", "quote": quote, "kind": "figure", "figure": 1}


class DuplicateFigures(unittest.TestCase):
    """A figure claim is a duplicate only when one block holds its words AND every figure it states."""

    def kept(self, quote, *texts):
        kept, _ = C.duplicate_figures(Path(tempfile.mkdtemp()), [_fig("I01", quote)], _sec(*texts))
        return bool(kept)

    def test_a_contradicting_number_is_kept(self):
        self.assertTrue(self.kept("Southfield uptime 97.4%", "Southfield uptime 99.1% in FY2025"))
        self.assertTrue(self.kept("Migration completed in March 2025 for all EU customers",
                                  "Migration completed in May 2025 for all EU customers"))

    def test_short_quotes_are_kept(self):
        self.assertTrue(self.kept("12%", "Launched in 2012"))
        self.assertTrue(self.kept("Q3", "Q3 results"))

    def test_omission_claims_are_kept(self):
        self.assertTrue(self.kept("Salesforce absent from figure 1", "Salesforce sends the change to RouteDB"))

    def test_words_spread_over_several_blocks_are_kept(self):
        self.assertTrue(self.kept("OrderHub saves the change and IPX sends it to RouteDB",
                                  "OrderHub saves the change", "IPX sends it to RouteDB"))

    def test_a_true_repeat_is_dropped(self):
        self.assertFalse(self.kept("Regional depots handled 4,200 returns in March 2025",
                                   "Regional depots handled 4,200 returns in March 2025 across the north."))


class SelfCheckCountsDroppedFigures(unittest.TestCase):
    def test_all_figure_claims_dropped_is_not_a_skipped_step(self):
        self.assertTrue(I.figures_accounted([], {"dropped_duplicate_figure": [{"claim_id": "I01"}]}))
        self.assertFalse(I.figures_accounted([], {}))
        self.assertTrue(I.figures_accounted([{"id": "I01"}], None))


class NumberWords(unittest.TestCase):
    def test_compound_numbers_keep_their_tag(self):
        for t in ("twenty-one sites", "thirty-one days", "one-way trips cost 4"):
            self.assertIn("num", tag_risk(t), t)

    def test_spec_idioms_have_none(self):
        for t in ("ONE-OFF ORDERS", "one of the depots", "one-to-one coaching", "a one-time setup", "one another"):
            self.assertNotIn("num", tag_risk(t), t)


@unittest.skipUnless(HAVE, "needs python-pptx and PyMuPDF")
class ReaderProbes(unittest.TestCase):
    def test_milestones_are_not_attributions(self):
        for t in ("Phase 2 cutover; Oct 15", "Design freeze; 3 Mar 2026", "Go-live; 2026-01-15",
                  "Revenue: 12% up; Sep 30, 2025"):
            self.assertFalse(P.is_attribution(t), t)
        for t in ("Discovery Session w/ Vendor – Ordering; 01:27:46", "Current State Readout discussion with Acme; Oct 14",
                  "Routing Workshop; Oct 3", "Atlas Technology Session – RouteDB; 00:42:07–00:42:19"):
            self.assertTrue(P.is_attribution(t), t)

    def test_unseen_numbers_stay_residual(self):
        self.assertEqual(P.residual_tokens("Revenue 45 74 Q1 Q2 2025 net", "FY2025 revenue 1745 internet Q12"),
                         ["45", "74", "q1", "q2", "2025"])


@unittest.skipUnless(HAVE, "needs python-pptx and PyMuPDF")
class ElementLabels(unittest.TestCase):
    def test_generic_names_become_positions(self):
        for name in ("Picture Placeholder 2", "Rectangle 5", "Google Shape;123;p4", "Content Placeholder 3"):
            self.assertTrue(A._GENERIC.match(name), name)
        self.assertFalse(A._GENERIC.match("Uptime callout"))

    def test_figure_header_without_element_label_says_figure(self):
        f = {"id": "I01", "verdict": "Incorrect", "severity": "Major", "section": "Figure", "p_id": "s3-sh7",
             "quote": "bar North = 97.4%", "fix": "x", "evidence": "e", "source": "s"}
        self.assertTrue(I.comment_text(f).startswith('[Incorrect]\n"bar North = 97.4%"'))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE, "needs python-pptx and PyMuPDF")
class FieldsFallBackWhenTooLong(unittest.TestCase):
    def test_long_brain_line_falls_back_to_quote_and_fix(self):
        import deck_fixtures as F
        d = Path(tempfile.mkdtemp())
        deck = F.full_deck(d)
        f = {"id": "C01", "verdict": "Incorrect", "severity": "Major", "section": "Slide 1", "p_id": "s1-sh3-p2",
             "quote": "Uptime held at 99.9% all year.", "evidence": " ".join(["word"] * 100), "fix": "Use 99.1%.",
             "why": "uptime differs from the reported value", "suggested": "Uptime was 99.1%.", "source": "ops.md"}
        out = d / "out.pptx"
        written, _ = A.annotate(deck, out, [f], fields=I.COMMENT_FIELDS)
        self.assertEqual(written, ["C01"])
        text = I.read_pptx_comments(out)[0]["text"]
        self.assertNotIn("\nBrain:", text)
        self.assertIn('Suggested: "Uptime was 99.1%."', text)
