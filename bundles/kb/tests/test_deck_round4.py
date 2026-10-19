"""Round 4 (spec §11): slide context travels with every claim (Y1), structural labels are not claims (Y2), and a
per-slide review of comments can only lower findings, deterministically applied (Y3)."""
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

import apply_slide_review as SR  # noqa: E402


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class SlideContextAndLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        P.write_outputs(F.context_deck(cls.d), cls.d / "w")
        cls.w = cls.d / "w"
        cls.secs = json.loads((cls.w / "sections.json").read_text())

    def test_slide_context_has_title_lede_and_key(self):
        ctx = self.secs[0]["slide_context"]
        self.assertEqual(ctx["title"], F.CTX_TITLE)
        self.assertEqual(ctx["lede"], F.CTX_LEDE)
        self.assertIn(F.CTX_KEY, ctx["key"])

    def test_labels_untagged_figure_tagged(self):
        risk = {p["text"]: [t for x in p["sentences"] for t in x["risk"]] for p in self.secs[0]["paragraphs"]}
        for lab in F.CTX_LABELS:
            self.assertEqual(risk[lab], [], lab)
        self.assertIn("num", risk[F.CTX_FIGURE_LABEL])

    def test_chunk_claims_carry_the_context(self):
        p = next(p for p in self.secs[0]["paragraphs"] if p["text"] == F.CTX_CARD)
        b = json.loads((self.w / "batches.json").read_text())[0]
        (self.w / "claims_batch_1.json").write_text(json.dumps([{
            "claim_id": "B1-C01", "p_id": p["p_id"], "s_id": p["sentences"][0]["s_id"], "section": self.secs[0]["section"],
            "quote": F.CTX_CARD, "claim": "In the scenario, OrderHub saves the change as the master record.", "type": "OWN",
            "questions": ["which system masters agent-made order changes"]}]))
        (self.w / "coverage_batch_1.json").write_text("[]")
        out, errors = C.chunk(self.w, "all", 6)
        self.assertEqual(errors, [])
        claim = json.loads((self.w / "chunk_1.json").read_text())["claims"][0]
        self.assertIn(F.CTX_TITLE, claim["context"])
        self.assertIn(F.CTX_LEDE, claim["context"])
        self.assertIn("Key: ", claim["context"])
        self.assertLessEqual(len(claim["context"].split()), 80)


def _finding(fid, verdict="Misleading", severity="Major", p_id="s1-sh5-p1", **kw):
    f = {"id": fid, "p_id": p_id, "section": "Slide 1", "quote": "q", "claim": "c", "type": "TOPO", "verdict": verdict,
         "severity": severity, "confidence": "High", "evidence": "e", "trail": "t", "conflicts": [], "fix": "f",
         "source": "s", "sources": []}
    f.update(kw)
    return f


class SlideReview(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.findings = [_finding("C02"), _finding("C09"), _finding("C11"), _finding("C13", "Controversial"),
                         _finding("C14", "No Evidence", "Minor")]
        (self.d / "findings.json").write_text(json.dumps(self.findings))
        (self.d / "run.json").write_text(json.dumps({"scope": "risk"}))

    def apply(self, review):
        (self.d / "slide_review_1.json").write_text(json.dumps(review))
        return SR.apply(self.d / "findings.json", self.d / "run.json", [self.d / "slide_review_1.json"])

    def test_lowering_actions(self):
        errors = self.apply([{"id": "C02", "action": "minor", "reason": "simplified flow"},
                             {"id": "C09", "action": "verified_in_context", "reason": "agent-made change: OrderHub masters it"},
                             {"id": "C11", "action": "duplicate_of", "of": "C13", "reason": "same fact as a kept one"},
                             {"id": "C13", "action": "keep", "reason": "sources disagree"}])
        self.assertEqual(errors, [])
        by = {f["id"]: f for f in json.loads((self.d / "findings.json").read_text())}
        self.assertEqual((by["C02"]["verdict"], by["C02"]["severity"]), ("Misleading", "Minor"))
        self.assertEqual((by["C09"]["verdict"], by["C09"]["severity"], by["C09"]["fix"]), ("Verified", "Minor", "None."))
        self.assertEqual(by["C11"]["severity"], "Minor")
        self.assertEqual(by["C13"]["severity"], "Major")
        self.assertIn("Slide review: verified in context: agent-made change", by["C09"]["trail"])
        run = json.loads((self.d / "run.json").read_text())
        self.assertEqual([x["id"] for x in run["slide_review"]], ["C02", "C09", "C11"])

    def test_refuses_unknown_raise_and_uncommented(self):
        for bad in ([{"id": "C99", "action": "minor", "reason": "x"}],
                    [{"id": "C02", "action": "major", "reason": "x"}],
                    [{"id": "C14", "action": "minor", "reason": "x"}],
                    [{"id": "C02", "action": "minor"}],
                    [{"id": "C11", "action": "duplicate_of", "of": "C77", "reason": "x"}]):
            with self.subTest(bad=bad):
                self.assertTrue(self.apply(bad))
                self.assertEqual(json.loads((self.d / "findings.json").read_text()), self.findings)

    def test_cli(self):
        (self.d / "slide_review_1.json").write_text(json.dumps([{"id": "C02", "action": "minor", "reason": "x"}]))
        r = subprocess.run([sys.executable, "-I", str(DECK / "apply_slide_review.py"), str(self.d / "findings.json"),
                            "--run", str(self.d / "run.json"), str(self.d / "slide_review_1.json")],
                           capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()


class ControversialNeedsADisagreement(unittest.TestCase):
    """Round 4 one-slide run: a 'Controversial' rested on one source stating a path and another leaving a hop out."""

    def test_rule_in_verify_and_slide_review(self):
        v = (DECK / "verify.md").read_text(encoding="utf-8")
        self.assertIn("A source that leaves out or is silent on something another source states does not conflict with it", v)
        r = (DECK / "slide_review.md").read_text(encoding="utf-8")
        self.assertIn("one source only leaves out what the other states", r)


class StageEFormatTolerance(unittest.TestCase):
    """4-slide run on r4: the chunker refused stage E twice, once for a deck claim of 46 words (the slide's scenario
    resolved into it, spec §11 Y1) and once for 'Slide 3 > title', the heading path extract.md asks for, where the
    reader names the section 'Slide 3: title'."""

    def test_slide_section_forms_are_one_key(self):
        from fact_check_invariants import section_key
        self.assertEqual(section_key("Slide 3 > A changed delivery"), section_key("Slide 3: A changed delivery"))
        self.assertNotEqual(section_key("Slide 3: A"), section_key("Slide 4: A"))

    def test_deck_claims_may_be_longer_word_claims_may_not(self):
        claim = " ".join(["word"] * 50)
        self.assertIsNone(C.claim_problem({"claim": claim, "s_id": "s3-sh5-p1s1"}))
        self.assertIsNotNone(C.claim_problem({"claim": claim, "s_id": "p12s1"}))
        self.assertIsNotNone(C.claim_problem({"claim": " ".join(["word"] * 56), "s_id": "s3-sh5-p1s1"}))


@unittest.skipUnless(HAVE, "needs python-pptx, PyMuPDF and soffice")
class ChunkerSelfCheck(unittest.TestCase):
    def test_check_batch_validates_without_writing_chunks(self):
        d = Path(tempfile.mkdtemp())
        P.write_outputs(F.context_deck(d), d / "w")
        w = d / "w"
        sec = json.loads((w / "sections.json").read_text())[0]
        p = next(p for p in sec["paragraphs"] if p["text"] == F.CTX_FIGURE_LABEL)
        claim = {"claim_id": "B1-C01", "p_id": p["p_id"], "s_id": p["sentences"][0]["s_id"], "section": sec["section"],
                 "quote": F.CTX_FIGURE_LABEL, "claim": "Uptime is 99.9%.", "type": "NUM", "questions": ["what is uptime"]}
        (w / "claims_batch_1.json").write_text(json.dumps([claim]))
        (w / "coverage_batch_1.json").write_text("[]")
        cmd = [sys.executable, "-I", str(DECK / "chunk_claims.py"), str(w), "--scope", "all", "--check-batch", "1"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd="/")
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1]), (0, "batch 1: OK"), r.stdout + r.stderr)
        self.assertFalse(list(w.glob("chunk_*.json")))
        (w / "claims_batch_1.json").write_text(json.dumps([{**claim, "claim": " ".join(["word"] * 60)}]))
        r = subprocess.run(cmd, capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 2)
        self.assertIn("B1-C01: claim is 60 words", r.stdout)


class SelfCheckInBriefs(unittest.TestCase):
    def test_workers_check_their_own_files(self):
        self.assertIn("--check-batch <k>`", (DECK / "extract.md").read_text(encoding="utf-8"))
        self.assertIn("--check-chunk <j>`", (DECK / "verify.md").read_text(encoding="utf-8"))
        self.assertIn("with `--skip-chunk <j>` added", (DECK / "SKILL.md").read_text(encoding="utf-8"))
