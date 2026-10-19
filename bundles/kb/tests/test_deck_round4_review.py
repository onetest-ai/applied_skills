"""Round 4, fixes from the whole-diff review: each test is a probe that failed on the first round-4 build."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

import sys
DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

import apply_slide_review as SR  # noqa: E402
import fact_check_invariants as I  # noqa: E402
import merge_findings as M  # noqa: E402
from section_io import estimate_minutes  # noqa: E402

try:
    import pptx  # noqa: F401
    import pymupdf  # noqa: F401
    import pptx_sections as P
    HAVE = True
except ImportError:
    HAVE = False


@unittest.skipUnless(HAVE, "needs python-pptx and PyMuPDF")
class Labels(unittest.TestCase):
    def test_structural_labels(self):
        for t in ("2 ORDER PLACED", "4 SYNC & TICKET", "5 ROUTE & ETA", "COMMERCE & IPX (INTEGRATION)",
                  "WHERE IT CAN BREAK", "FIELD OPERATIONS", "3", "10."):
            self.assertTrue(P.is_label(t), t)

    def test_all_caps_claims_are_not_labels(self):
        for t in ("10 MILLION USERS", "TWO DATA CENTRES", "3 REGIONS", "ALL ORDERS SHIP SAME DAY", "ERP OWNS BILLING.",
                  "ROUTEDB IS THE SYSTEM OF RECORD", "ERP TO ROUTEDB VIA API", "UPTIME 99.9%"):
            self.assertFalse(P.is_label(t), t)


@unittest.skipUnless(HAVE, "needs python-pptx and PyMuPDF")
class TitleIsNotTheEyebrow(unittest.TestCase):
    def test_eyebrow_skipped(self):
        class Sh:
            has_text_frame = True

            def __init__(self, sid, text):
                self.shape_id, self.text_frame = sid, type("TF", (), {"text": text})()
        shapes = [(Sh(1, "CASE STUDY · RETAIL BANKING"), {"x": 0, "y": 182880, "w": 6e6, "h": 300000}),
                  (Sh(2, "Changing an order by phone"), {"x": 0, "y": 548640, "w": 6e6, "h": 500000})]
        self.assertEqual(P.slide_context(shapes, None, 6858000, [])["title"], "Changing an order by phone")


def _f(fid, p_id="s1-sh5-p1", verdict="Misleading", severity="Major", **kw):
    f = {"id": fid, "p_id": p_id, "section": "Slide 1", "quote": "q", "claim": "c", "type": "TOPO", "verdict": verdict,
         "severity": severity, "confidence": "High", "evidence": "e", "trail": "t", "conflicts": [], "fix": "f",
         "source": "s", "sources": []}
    f.update(kw)
    return f


class SlideReviewGuards(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        conflict = [{"span": "x", "source": "s.md", "date": "2026-09-15"}]
        self.findings = [_f("C01"), _f("C02"), _f("C03", verdict="Controversial", conflicts=conflict),
                         _f("C09", p_id="s9-sh2-p1"), _f("C10", severity="Minor")]
        (self.d / "findings.json").write_text(json.dumps(self.findings))
        (self.d / "run.json").write_text(json.dumps({"comments": "major"}))

    def apply(self, review, name="slide_review_1.json"):
        (self.d / name).write_text(json.dumps(review))
        return SR.apply(self.d / "findings.json", self.d / "run.json", [self.d / name])

    def refused(self, review, name="slide_review_1.json"):
        errors = self.apply(review, name)
        self.assertTrue(errors, review)
        self.assertEqual(json.loads((self.d / "findings.json").read_text()), self.findings)
        return errors

    def test_verified_in_context_never_verifies_a_conflicted_finding(self):
        # round 6 (user decision 2026-10-17): applied as minor instead of refusing the whole review
        self.assertEqual(self.apply([{"id": "C03", "action": "verified_in_context", "reason": "x"}]), [])
        c03 = next(f for f in json.loads((self.d / "findings.json").read_text()) if f["id"] == "C03")
        self.assertEqual((c03["verdict"], c03["severity"]), ("Controversial", "Minor"))

    def test_duplicates_cannot_erase_a_fact(self):
        self.refused([{"id": "C01", "action": "duplicate_of", "of": "C02", "reason": "x"},
                      {"id": "C02", "action": "duplicate_of", "of": "C01", "reason": "x"}])
        self.refused([{"id": "C01", "action": "duplicate_of", "of": "C10", "reason": "x"}])
        self.refused([{"id": "C01", "action": "duplicate_of", "of": "C09", "reason": "x"}])

    def test_review_judges_its_own_slide_only(self):
        self.refused([{"id": "C09", "action": "minor", "reason": "x"}])
        self.refused([{"id": "C01", "action": "minor", "reason": "x"}], name="review_x.json")

    def test_malformed_ids_refused_not_crashed(self):
        self.refused([{"id": ["C01"], "action": "minor", "reason": "x"}])
        self.refused([{"id": "C01", "action": "duplicate_of", "of": ["C02"], "reason": "x"}])

    def test_a_valid_duplicate_is_applied(self):
        self.assertEqual(self.apply([{"id": "C01", "action": "duplicate_of", "of": "C02", "reason": "same fact"},
                                     {"id": "C02", "action": "keep", "reason": "clearer"}]), [])


S1, S2 = "Slide 1: Intro", "Slide 2: Plan"


class SkipAndSectionForms(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        w = lambda n, o: (self.d / n).write_text(json.dumps(o))
        w("batches.json", [{"batch": 1, "file": "batch_1.json", "words": 20,
                            "sections": [{"section_id": "s01", "section": S1}, {"section_id": "s02", "section": S2}]}])
        w("stats.json", {"sections_total": 2, "statements_total": 2, "statements_risk": 2,
                         "estimate_minutes": {"fast": 1, "deep": 1}})
        sec = lambda sid, name, s_id: {"section_id": sid, "section": name, "paragraphs": [
            {"p_id": s_id[:-2], "text": "t", "sentences": [{"s_id": s_id, "text": "t", "risk": ["num"]}]}], "tables": []}
        w("batch_1.json", {"batch": 1, "sections": [sec("s01", S1, "s1-sh2-p1s1"), sec("s02", S2, "s2-sh2-p1s1")]})
        claim = lambda n, s_id, section: {"claim_id": f"B1-C0{n}", "p_id": s_id[:-2], "s_id": s_id, "section": section,
                                          "quote": "runs 12 services", "claim": "It runs 12 services.", "type": "NUM"}
        w("claims_batch_1.json", [claim(1, "s1-sh2-p1s1", S1), claim(2, "s2-sh2-p1s1", "Slide 2 > Plan")])
        w("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01"]},
                          {"chunk": 2, "file": "chunk_2.json", "claim_ids": ["B1-C02"]}])
        fnd = lambda n, p_id, section: _f(f"B1-C0{n}", p_id=p_id, section=section, quote="runs 12 services",
                                          claim="It runs 12 services.", type="NUM", verdict="Verified", severity="Minor",
                                          trail="name check: none")
        w("findings_chunk_1.json", [fnd(1, "s1-sh2-p1", S1)])
        w("findings_chunk_2.json", [fnd(2, "s2-sh2-p1", "Slide 2 > Plan")])

    def test_finding_takes_the_readers_section_name(self):
        findings, _, _, errors = M.merge(self.d, "risk")
        self.assertEqual(errors, [])
        self.assertEqual(findings[1]["section"], S2)

    def test_skipped_section_is_covered_for_the_self_check(self):
        findings, coverage, run, errors = M.merge(self.d, "all", skip_chunks={2})
        self.assertEqual(errors, [])
        covered = I._covered_headings(findings, coverage, "deep")
        self.assertIn(I._norm(S2), covered)                  # the heading the post-run check looks for
        self.assertTrue(any(c["reason"] == I.UNVERIFIED_REASON for c in coverage))

    def test_skip_unknown_chunk_is_an_error(self):
        _, _, _, errors = M.merge(self.d, "risk", skip_chunks={7})
        self.assertTrue(any("7" in e for e in errors), errors)

    def test_figures_accounted_counts_unverified(self):
        self.assertTrue(I.figures_accounted([{"id": "C01"}], {"unverified": [{"chunk": 3, "claim_ids": ["I01"]}]}))

    def test_deep_line_does_not_claim_all_sections_when_some_were_skipped(self):
        from findings_report import coverage_line
        line = coverage_line({"scope": "all", "sections_total": 2, "claims_verified": 1,
                              "unverified": [{"chunk": 2, "claim_ids": ["B1-C02"]}]})
        self.assertNotIn("all 2 sections checked", line)
        self.assertIn("Not verified: 1 claim", line)


class Estimate(unittest.TestCase):
    def test_deck_estimate_uses_its_chunk_size(self):
        self.assertGreater(estimate_minutes(120, 30, chunk=6)["deep"], estimate_minutes(120, 30)["deep"])


class Prose(unittest.TestCase):
    def test_limits_named_for_decks(self):
        self.assertIn("a deck claim: 55", (DECK / "extract.md").read_text(encoding="utf-8"))
        self.assertIn("(at most 60 words; a deck finding: 90", (DECK / "verify.md").read_text(encoding="utf-8"))
        self.assertIn("Under `comments: all`", (DECK / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE, "needs python-pptx and PyMuPDF")
class DeckEstimate(unittest.TestCase):
    def test_stats_estimate_uses_the_decks_chunk_size(self):
        import deck_fixtures as F
        d = Path(tempfile.mkdtemp())
        P.write_outputs(F.full_deck(d), d / "w")
        st = json.loads((d / "w" / "stats.json").read_text())
        import chunk_claims
        self.assertEqual(st["estimate_minutes"],                       # round 5: the deck's chunk size is automatic
                         estimate_minutes(st["statements_total"], st["statements_risk"], chunk=chunk_claims.auto_size))
        self.assertIn("--chunk-size auto --stride", (DECK / "SKILL.md").read_text(encoding="utf-8"))


class SameItemConflicts(unittest.TestCase):
    """Confirmation run on r4: 'Corrections are slow' was Misleading on a conflict about integration sync speed."""

    def test_rule_in_verify_and_slide_review(self):
        v = (DECK / "verify.md").read_text(encoding="utf-8")
        self.assertIn("A conflict is about the same item: the same process or system and the same attribute", v)
        r = (DECK / "slide_review.md").read_text(encoding="utf-8")
        self.assertIn("whose only conflict is about a different process, system, attribute or scenario", r)
