"""Round 7 (user judgement 2026-10-19: 3 of 8 comments useful): one comment shape for deck and Word — the category
only, the statement (deck), a plain-language Why naming the source and its date, a Suggested sentence; a comment
only with hard evidence; disagreements and "confirm…" findings stay on the findings page. Invented names only."""
from __future__ import annotations

import sys
import unittest

from test_plugin_structure import KB_ROOT

DOC = KB_ROOT / "skills" / "doc-fact-check"
DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DOC))

import fact_check_invariants as I  # noqa: E402


def _f(**kw):
    f = {"id": "C01", "p_id": "s3-sh5-p1", "section": "Slide 3", "quote": "One ERP job failure raises a priority ticket",
         "verdict": "Incorrect", "severity": "Major", "evidence": "'two or three successive failures' raise a ticket",
         "fix": "Use two or three.", "source": "transcripts/Atlas Session - RouteDB-20260925_145826UTC-Meeting Recording.mp4.md",
         "conflicts": [], "why": "a priority ticket is logged only after two or three failures in a row",
         "suggested": "Two or three ERP job failures in a row raise a priority ticket."}
    f.update(kw)
    return f


class SourceLabel(unittest.TestCase):
    def test_meeting_recording(self):
        self.assertEqual(I.source_label("transcripts/Atlas Session - RouteDB-20260925_145826UTC-Meeting Recording.mp4.md"),
                         ("Atlas Session – RouteDB", "25 Sep 2026"))

    def test_dated_document_prefix(self):
        self.assertEqual(I.source_label("docs/team__Board Notes__260406 - Steering Pack v2.pptx.md"),
                         ("Steering Pack v2", "6 Apr 2026"))

    def test_iso_date_in_name(self):
        self.assertEqual(I.source_label("transcripts/From Kim__2026-09-17-routing-migration.txt.md"),
                         ("routing migration", "17 Sep 2026"))

    def test_undated(self):
        self.assertEqual(I.source_label("docs/Platform Overview.pdf.md"), ("Platform Overview", "undated"))


class CommentShape(unittest.TestCase):
    def test_deck_comment(self):
        self.assertEqual(I.comment_text(_f()),
                         '[Incorrect]\n"One ERP job failure raises a priority ticket"\n\n'
                         "Why: Atlas Session – RouteDB (25 Sep 2026) says a priority ticket is logged only after two or "
                         "three failures in a row.\n\n"
                         'Suggested: "Two or three ERP job failures in a row raise a priority ticket."')

    def test_word_comment_has_no_quote_line(self):
        t = I.comment_text(_f(p_id="p12"))
        self.assertTrue(t.startswith("[Incorrect]\nWhy: Atlas Session – RouteDB (25 Sep 2026) says"))
        self.assertNotIn('"One ERP', t)

    def test_no_ids_no_severity_in_any_comment(self):
        for f in (_f(), _f(p_id="p12")):
            t = I.comment_text(f)
            self.assertNotIn("Major", t); self.assertNotIn("C01", t); self.assertNotIn("·", t.split("\n")[0])

    def test_header_pattern_reads_the_category(self):
        self.assertEqual(I.HEADER.match(I.comment_text(_f()))["verdict"], "Incorrect")

    def test_owner_found_by_its_text(self):
        fs = [_f(), _f(id="C02", quote="Another statement")]
        self.assertEqual(I.comment_owner(I.comment_text(fs[1]), fs), "C02")


class OnlyWithHardEvidence(unittest.TestCase):
    def test_a_supported_defect_is_commented(self):
        self.assertTrue(I.wants_comment(_f()))

    def test_disagreement_goes_to_the_findings_page(self):
        self.assertFalse(I.wants_comment(_f(verdict="Controversial")))

    def test_confirm_is_not_a_fix(self):
        for fix in ("Confirm the cutoff with operations.", "Check with the team.", "Verify the date with the owner."):
            self.assertFalse(I.wants_comment(_f(fix=fix, suggested="")), fix)

    def test_no_why_no_comment(self):
        self.assertFalse(I.wants_comment(_f(why="")))

    def test_minor_still_not_commented_under_major(self):
        self.assertFalse(I.wants_comment(_f(severity="Minor")))


class WhyChecks(unittest.TestCase):
    def test_a_good_why_passes(self):
        self.assertEqual(I.why_problem(_f()), None)

    def test_too_long(self):
        self.assertIn("45 words", I.why_problem(_f(why=" ".join(["word"] * 46))))

    def test_edit_instruction_is_not_a_suggestion(self):
        self.assertIn("full sentence", I.why_problem(_f(suggested="Change 'One' to 'two or three'")))

    def test_a_figure_in_why_must_be_in_the_evidence(self):
        self.assertIn("19,000", I.why_problem(_f(why="the team counts 19,000 calls a day")))

    def test_jargon_refused(self):
        self.assertIn("Brain", I.why_problem(_f(why="the Brain shows two or three failures")))


class Briefs(unittest.TestCase):
    def test_verify_brief_asks_for_why_and_suggested(self):
        v = (DOC / "verify.md").read_text(encoding="utf-8")
        for s in ("`why`", "`suggested`", "date unknown", "the same speaker revises it in the same exchange"):
            self.assertIn(s, v)

    def test_both_skills_describe_the_shape(self):
        for skill in (DOC, DECK):
            s = (skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill.name):
                self.assertIn("[Incorrect]", s)
                self.assertIn("findings page only", s)


if __name__ == "__main__":
    unittest.main()


class MergeRefusesACommentWithoutWhy(unittest.TestCase):
    """A defect the comment policy would comment, written without a plain-language why, must be re-done by its
    worker — silently leaving it uncommented would turn a caught error into a miss."""

    def _errs(self, **kw):
        import merge_findings as MF
        f = _f(claim="c", type="NUM", confidence="High", trail="t", sources=[], **kw)
        errs = []
        MF._check_finding(f, "chunk 1", errs)
        return errs

    def test_missing_why_refused(self):
        self.assertTrue(any("why" in x for x in self._errs(why="", suggested="")))

    def test_good_why_accepted(self):
        self.assertEqual([x for x in self._errs() if "why" in x or "suggested" in x], [])

    def test_disagreement_needs_no_why(self):
        self.assertEqual([x for x in self._errs(verdict="Controversial", why="", suggested="",
                          conflicts=[{"span": "s", "source": "a.md", "date": "2026-01-01"}]) if "why" in x], [])
