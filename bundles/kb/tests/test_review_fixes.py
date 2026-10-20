"""Regression tests for the final whole-branch review of deck-fact-check (findings 1–15), each built to fail on the
code as it was reviewed."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_plugin_structure import KB_ROOT

import sys
DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

try:
    import pptx  # noqa: F401
    import pymupdf  # noqa: F401
    HAVE_LIBS = True
except ImportError:
    HAVE_LIBS = False

if HAVE_LIBS:
    import annotate_cli
    import chunk_claims as C
    import deck_fixtures as F
    import fact_check_invariants as I
    import findings_report as R
    import pptx_annotate as A
    import pptx_sections as P
    from render_pages import soffice_bin
    HAVE_SOFFICE = soffice_bin() is not None
else:
    HAVE_SOFFICE = False


def _prs():
    from pptx import Presentation
    return Presentation()


def logo_deck(d: Path) -> Path:
    from pptx.util import Inches
    prs = _prs()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Delivery plan"
    s.placeholders[1].text = "Go-live is set for Q2 2027."
    s.shapes.add_picture(str(F._png(d / "logo.png")), Inches(9), Inches(0.1), Inches(0.5), Inches(0.4))
    return F._save(prs, d / "logo.pptx")


def numbered_deck(d: Path) -> Path:
    prs = _prs()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Next steps"
    body = s.placeholders[1].text_frame
    body.text = "Migrate the billing feed"
    for t in ("Retire the mainframe", "Train the support team"):
        body.add_paragraph().text = t
    for p in body.paragraphs:                    # auto-numbering: rendered "1." "2." "3.", never in the text
        ppr = p._p.get_or_add_pPr()
        from pptx.oxml.ns import qn
        from lxml import etree
        etree.SubElement(ppr, qn("a:buAutoNum"), type="arabicPeriod")
    return F._save(prs, d / "numbered.pptx")


@unittest.skipUnless(HAVE_LIBS, "needs python-pptx and PyMuPDF")
class Pictures(unittest.TestCase):
    """Findings 1 and 2: a picture in a placeholder is a picture; a logo is not a figure; reader and view agree."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_picture_in_a_placeholder_is_seen_by_reader_and_view(self):
        path = F.picture_placeholder_deck(self.d)
        self.assertTrue(P.read_deck(path)["kinds"][1]["picture"])
        self.assertTrue(I.doc_view(path).has_images())

    def test_a_logo_neither_flags_the_slide_nor_demands_figure_findings(self):
        path = logo_deck(self.d)
        self.assertFalse(any(P.read_deck(path)["kinds"][1].values()))
        self.assertFalse(I.doc_view(path).has_images())

    def test_a_chart_only_deck_demands_figure_findings(self):
        self.assertTrue(I.doc_view(F.chart_deck(self.d)).has_images())

    @unittest.skipUnless(HAVE_SOFFICE, "needs LibreOffice")
    def test_picture_placeholder_slide_goes_to_vision(self):
        res = P.write_outputs(F.picture_placeholder_deck(self.d), self.d / "out")
        self.assertEqual(res["flagged"], [1])


@unittest.skipUnless(HAVE_LIBS, "needs python-pptx and PyMuPDF")
class ReviewedDeck(unittest.TestCase):
    """Finding 3: a reviewer's comment already in the draft is kept and does not fail the self-check."""

    def test_reviewed_deck_is_clean(self):
        from test_deck_annotate import COVERAGE, FINDINGS, finding, S1
        d = Path(tempfile.mkdtemp())
        src = F.full_deck(d)
        reviewed = d / "reviewed.pptx"
        with patch.object(A, "AUTHOR", "Reviewer"), patch.object(A, "comment_text", lambda f, fields=None: "Please check the dates."):
            A.annotate(src, reviewed, [finding("X1", "s1-sh3-p1", S1, "q", "Incorrect", "Major")])
        out = d / "reviewed — fact-checked.pptx"
        written, _ = A.annotate(reviewed, out, [f for f in FINDINGS if I.wants_comment(f, "major")])
        marked = annotate_cli.mark_destinations(FINDINGS, written, label="Deck comment")
        import hashlib
        vs = I.check_run(marked, out, original_path=reviewed, coverage=COVERAGE, run={"comments": "major"},
                         original_sha256=hashlib.sha256(reviewed.read_bytes()).hexdigest())
        self.assertEqual(vs, [])
        # but a comment that is neither the reviewer's nor approved still fails
        self.assertTrue(any("never approved" in v or "first line is not" in v
                            for v in I.check_run(marked, out, original_path=src, coverage=COVERAGE,
                                                 run={"comments": "major"})))


@unittest.skipUnless(HAVE_LIBS, "needs python-pptx and PyMuPDF")
class FooterFurniture(unittest.TestCase):
    """Finding 4: date / footer / slide-number placeholders are not claims and not context."""

    def test_footer_placeholders_are_neither_blocks_nor_footnotes(self):
        deck = P.read_deck(F.footer_placeholder_deck(Path(tempfile.mkdtemp())))
        texts = [r["text"] for k, r, _ in deck["sections"][0]["blocks"] if k == "p"]
        self.assertEqual(texts, ["Service levels", "Uptime held at 99.9%."])
        self.assertFalse(any("ctx" in r for _, r, _ in deck["sections"][0]["blocks"]))
        self.assertIn("10/12/2026", deck["walk_text"][1])           # seen, so never residual

    def test_a_large_bottom_label_is_not_a_footnote_but_a_source_line_is(self):
        from pptx.util import Inches
        d = Path(tempfile.mkdtemp())
        prs = _prs()
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = "Timeline"
        s.placeholders[1].text = "Phase two starts in May."
        s.shapes.add_textbox(Inches(1), Inches(6.9), Inches(3), Inches(0.4)).text_frame.text = "Milestone 3"
        s.shapes.add_textbox(Inches(5), Inches(6.9), Inches(4), Inches(0.4)).text_frame.text = "Source: PMO plan"
        deck = P.read_deck(F._save(prs, d / "labels.pptx"))
        body = next(r for k, r, _ in deck["sections"][0]["blocks"] if r.get("text") == "Phase two starts in May.")
        self.assertEqual(body["ctx"]["footnote"], "Source: PMO plan")


class HiddenSlidesInTheCoverageLine(unittest.TestCase):
    """Finding 5."""

    def test_coverage_line_names_hidden_slides(self):
        line = R.coverage_line({"scope": "all", "sections_total": 3, "claims_verified": 9, "hidden_slides": [2, 7]})
        self.assertTrue(line.endswith("Not checked: hidden slides 2, 7."), line)
        one = R.coverage_line({"scope": "risk", "claims_verified": 1, "claims_extracted": 4, "hidden_slides": [2]})
        self.assertTrue(one.endswith("Not checked: hidden slide 2."), one)
        self.assertNotIn("hidden", R.coverage_line({"scope": "all", "sections_total": 3, "claims_verified": 9}))

    def test_merge_carries_hidden_slides_into_run_json(self):
        src = (KB_ROOT / "skills" / "doc-fact-check" / "merge_findings.py").read_text()
        self.assertIn('run["hidden_slides"] = stats["hidden_slides"]', src)


@unittest.skipUnless(HAVE_LIBS and HAVE_SOFFICE, "needs python-pptx, PyMuPDF and LibreOffice")
class ReaderRobustness(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_numbered_list_is_not_unseen_text(self):                                    # finding 8
        res = P.write_outputs(numbered_deck(self.d), self.d / "out")
        self.assertEqual(res["flagged"], [])
        self.assertEqual(P.residual_tokens("1. Migrate\n2) Retire\n99.1 uptime", "migrate retire uptime"), ["99.1"])

    def test_failed_render_leaves_the_previous_run_intact(self):                         # finding 9
        path = F.basic_deck(self.d)
        P.write_outputs(path, self.d / "out")
        before = {p.name: p.read_bytes() for p in (self.d / "out").glob("*.json")}
        with patch.object(P.render_pages, "render", side_effect=SystemExit("error: soffice failed")):
            with self.assertRaises(P.Refusal):
                P.write_outputs(path, self.d / "out")
        self.assertEqual({p.name: p.read_bytes() for p in (self.d / "out").glob("*.json")}, before)
        self.assertTrue((self.d / "out" / "render").is_dir())
        self.assertEqual([p for p in self.d.iterdir() if p.name.startswith(".render-")], [])

    def test_page_count_mismatch_is_refused(self):                                       # finding 10
        real = P.render_pages.render

        def short(*a, **k):
            r = real(*a, **k)
            r["pages"] = r["pages"][:0]
            return r
        with patch.object(P.render_pages, "render", side_effect=short):
            with self.assertRaises(P.Refusal) as e:
                P.write_outputs(F.basic_deck(self.d), self.d / "out")
        self.assertIn("cannot be matched to slides", str(e.exception))
        self.assertFalse((self.d / "out" / "sections.json").exists())


class ClaimNumbers(unittest.TestCase):
    """Finding 11: normalised numbers are not inventions; a split section keeps its whole context."""

    def test_short_years_and_number_words(self):
        base = {"questions": ["q"], "type": "NUM"}
        self.assertIsNone(C.claim_problem({**base, "quote": "FY25 revenue", "claim": "Revenue in FY2025 grew."}, ""))
        self.assertIsNone(C.claim_problem({**base, "quote": "in 2025", "claim": "In FY25 it grew."}, ""))
        self.assertIsNone(C.claim_problem({**base, "quote": "three regions", "claim": "The service runs in 3 regions."}, ""))
        self.assertIn("2026", C.claim_problem({**base, "quote": "FY25 revenue", "claim": "Revenue in FY2026 grew."}, ""))
        self.assertIn("27", C.claim_problem({**base, "quote": "Go-live Q2 2027", "claim": "Go-live slips 27%."}, ""))

    def test_split_section_context_comes_from_the_whole_section(self):
        part = {"sections": [{"section_id": "s04", "section": "Plan", "paragraphs": [
            {"sentences": [{"s_id": "p9s1", "text": "It went live then."}]}]}]}
        whole = {"s04": {"section_id": "s04", "section": "Plan", "paragraphs": [
            {"sentences": [{"s_id": "p2s1", "text": "Northwind went live in March 2024."}]}]}}
        self.assertNotIn("2024", C.known_text_by_s_id(part)["p9s1"])
        self.assertIn("2024", C.known_text_by_s_id(part, whole)["p9s1"])


@unittest.skipUnless(HAVE_LIBS, "needs python-pptx and PyMuPDF")
class CommentsOnSlides(unittest.TestCase):
    """Finding 12: a figure comment names its slide; several pins on one shape fan out inside it."""

    def test_figure_header_names_the_slide(self):
        f = {"id": "I01", "p_id": "s3", "section": "Figure", "verdict": "Incorrect", "severity": "Major",
             "evidence": "e", "fix": "x", "source": "s"}
        self.assertTrue(I.comment_text(f).startswith("[Incorrect]"))
        self.assertTrue(I.comment_text({**f, "p_id": "p7"}).startswith("[Incorrect]"))

    def test_three_pins_on_one_shape_fan_out_and_stay_in_the_box(self):
        from test_deck_annotate import S1, finding
        d = Path(tempfile.mkdtemp())
        src = F.full_deck(d)
        fs = [finding(f"C0{k}", f"s1-sh3-p{k}", S1, f"statement {k}", "Incorrect", "Major") for k in (1, 2, 3)]
        A.annotate(src, d / "o.pptx", fs)
        pins = sorted((c["x"], c["y"]) for c in I.read_pptx_comments(d / "o.pptx"))
        self.assertEqual(len({p for p in pins}), 3)
        view = I.doc_view(d / "o.pptx")
        by = {I.comment_owner(c.text, fs, c, view): c for c in view.comments()}
        for f in fs:
            self.assertIsNone(view.anchor_problem(by[f["id"]], f))


@unittest.skipUnless(HAVE_LIBS, "needs python-pptx and PyMuPDF")
class GroupWithoutTransform(unittest.TestCase):
    """Finding 13."""

    def test_group_without_xfrm_is_the_identity(self):
        d = Path(tempfile.mkdtemp())
        path = F.group_deck(d)
        from pptx import Presentation
        prs = Presentation(str(path))
        for g in prs.slides[0].shapes:
            x = g._element.grpSpPr.find(I.A_NS + "xfrm")
            if x is not None:
                g._element.grpSpPr.remove(x)
        prs.save(str(d / "noxfrm.pptx"))
        deck = P.read_deck(d / "noxfrm.pptx")
        self.assertEqual(len([1 for k, *_ in deck["sections"][0]["blocks"] if k == "p"]), 2)


class NotAnchored(unittest.TestCase):
    """Finding 15: a comment the annotator could not place is logged, and the self-check accepts it."""

    def test_skipped_id_is_marked_and_accepted(self):
        f = {"id": "C01", "verdict": "Incorrect", "severity": "Major", "section": "S", "quote": "q"}
        marked = annotate_cli.mark_destinations([f], [], skipped_ids={"C01"}) if HAVE_LIBS else None
        if marked is None:
            self.skipTest("needs the deck libraries for the import path")
        self.assertTrue(marked[0]["destination"].startswith(I.NOT_ANCHORED))
        self.assertFalse(I._has_comment(marked[0]))


if __name__ == "__main__":
    unittest.main()
