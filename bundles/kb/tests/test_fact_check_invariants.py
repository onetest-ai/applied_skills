"""Eval invariants for a /kb:fact-check run: what any run must satisfy whatever the Brain or corpus."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from test_fact_check_code import HAVE_DOCX, load_helpers

if HAVE_DOCX:
    from fact_check_invariants import check_run


def finding(id, verdict, severity, destination, quote="", **kw):
    return dict(id=id, verdict=verdict, severity=severity, destination=destination, quote=quote,
                section="1", evidence="Brain: x (2026-01-01, f.md)", fix="Fix it", source="f.md", **kw)


GOOD = [
    finding("C01", "Incorrect", "Major", "Word comment", "40 million in"),
    finding("C02", "Misleading", "Minor", "Word comment", "green fields"),
    finding("C03", "No Evidence", "Minor", "log only"),
    finding("C04", "Verified", "Minor", "count only"),
]


@unittest.skipUnless(HAVE_DOCX, "needs python-docx >= 1.2")
class TestRunInvariants(unittest.TestCase):
    def setUp(self):
        from docx import Document
        self.tmp = Path(tempfile.mkdtemp())
        d = Document()
        p = d.add_paragraph("Revenue was 40 million in 2025 across green fields.")
        p.add_run(" And more.")
        self.orig = self.tmp / "orig.docx"
        d.save(self.orig)
        self.sha = hashlib.sha256(self.orig.read_bytes()).hexdigest()
        self.out = self.tmp / "out.docx"

    def _annotate(self, findings):
        self.h = load_helpers()
        self.h["annotate"](str(self.orig), str(self.out), [f for f in findings if "comment" in f["destination"].lower()])

    def _check(self, findings, **kw):
        return check_run(findings, self.out, original_sha256=kw.pop("sha", self.sha),
                         original_path=kw.pop("original_path", self.orig), **kw)

    def test_a_correct_run_has_no_violations(self):
        self._annotate(GOOD)
        self.assertEqual(self._check(GOOD), [])

    def test_a_minor_defect_that_was_only_logged_is_a_violation(self):
        bad = [dict(f) for f in GOOD]
        bad[1]["destination"] = "log only"
        self._annotate(bad)
        self.assertTrue(any("C02" in v and "comment" in v for v in self._check(bad)))

    def test_no_evidence_must_be_minor_and_uncommented(self):
        bad = [dict(f) for f in GOOD]
        bad[2].update(severity="Major", destination="Word comment", quote="40 million in")
        self._annotate(bad)
        vs = self._check(bad)
        self.assertTrue(any("C03" in v and "No Evidence" in v for v in vs))

    def test_verified_claim_must_not_be_commented(self):
        bad = [dict(f) for f in GOOD]
        bad[3].update(destination="Word comment", quote="And more.")
        self._annotate(bad)
        self.assertTrue(any("C04" in v and "Verified" in v for v in self._check(bad)))

    def test_duplicate_claim_ids_are_a_violation(self):
        bad = GOOD + [finding("C01", "Verified", "Minor", "count only")]
        self._annotate(GOOD)
        self.assertTrue(any("duplicate" in v.lower() and "C01" in v for v in self._check(bad)))

    def test_unknown_verdict_or_severity_is_a_violation(self):
        bad = [dict(f) for f in GOOD]
        bad[0].update(verdict="Wrong", severity="Huge")
        self._annotate(GOOD)
        vs = self._check(bad)
        self.assertTrue(any("verdict" in v.lower() for v in vs))
        self.assertTrue(any("severity" in v.lower() for v in vs))

    def test_comment_must_be_anchored_to_the_quoted_words_only(self):
        self._annotate(GOOD)
        bad = [dict(f) for f in GOOD]
        bad[0]["quote"] = "Revenue was 40 million in 2025 across green fields."   # wider than what was highlighted
        self.assertTrue(any("C01" in v and "anchor" in v.lower() for v in self._check(bad)))

    def test_comment_count_must_match_the_approved_set(self):
        self._annotate(GOOD)
        bad = [dict(f) for f in GOOD]
        bad.append(finding("C05", "Incorrect", "Major", "Word comment", "And more."))
        self.assertTrue(any("C05" in v for v in self._check(bad)))

    def test_comment_longer_than_sixty_words_is_a_violation(self):
        fs = [dict(f) for f in GOOD]
        fs[0]["fix"] = "word " * 70
        self._annotate(fs)
        self.assertTrue(any("C01" in v and "60 words" in v for v in self._check(fs)))

    def test_original_document_must_be_untouched(self):
        self._annotate(GOOD)
        self.assertTrue(any("original" in v.lower() for v in self._check(GOOD, sha="0" * 64)))

    def test_output_must_not_overwrite_the_original(self):
        self._annotate(GOOD)
        self.assertTrue(any("overwrite" in v.lower() for v in
                            check_run(GOOD, self.orig, original_sha256=self.sha, original_path=self.orig)))

    def test_xml_special_characters_in_the_quote_are_not_false_violations(self):
        from docx import Document
        d = Document()
        d.add_paragraph("Green means < 3 Seconds & AT&T stays.")
        d.save(self.orig)
        self.sha = hashlib.sha256(self.orig.read_bytes()).hexdigest()
        fs = [finding("C01", "Misleading", "Major", "Word comment", "< 3 Seconds & AT&T")]
        self._annotate(fs)
        self.assertEqual(self._check(fs), [])

    def test_verified_claims_need_no_severity_but_defects_do(self):
        fs = [dict(f) for f in GOOD]
        fs[3]["severity"] = "-"
        self._annotate(fs)
        self.assertEqual(self._check(fs), [])
        fs[0]["severity"] = "-"
        self.assertTrue(any("C01" in v and "severity" in v for v in self._check(fs)))


if __name__ == "__main__":
    unittest.main()
