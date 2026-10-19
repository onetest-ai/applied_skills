"""Round 5 (plan 2026-10-17): stride chunks (T1), extract batches (T2), auto chunk size and rolling dispatch, clocks
recorded by the scripts (T4), and the brief changes (T3, T6, T7)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

import chunk_claims as C  # noqa: E402

SKILL = lambda: (DECK / "SKILL.md").read_text(encoding="utf-8")


def _claims(n, figures=()):
    out = [{"claim_id": f"B1-C{i:02d}", "type": "NUM", "s_id": f"s1-sh{i}-p1s1"} for i in range(1, n + 1)]
    return out + [{"claim_id": f, "type": "TOPO", "s_id": "s3"} for f in figures]


class Stride(unittest.TestCase):
    def test_stride_separates_figures_and_neighbours(self):
        chunks = C.stride(_claims(10, ("I01", "I02")), 4)
        where = {c["claim_id"]: k for k, ch in enumerate(chunks) for c in ch}
        self.assertNotEqual(where["I01"], where["I02"])
        self.assertNotEqual(where["B1-C01"], where["B1-C02"])

    def test_stride_deterministic_and_bounded(self):
        a, b = C.stride(_claims(10), 4), C.stride(_claims(10), 4)
        self.assertEqual([len(ch) for ch in a], [4, 3, 3])
        self.assertEqual(a, b)
        self.assertEqual(sorted(c["claim_id"] for ch in a for c in ch), sorted(c["claim_id"] for c in _claims(10)))

    def test_stride_keeps_document_order_inside_a_chunk(self):
        for ch in C.stride(_claims(9), 4):
            ids = [c["claim_id"] for c in ch if c["claim_id"].startswith("B")]
            self.assertEqual(ids, sorted(ids))

    def test_word_chunks_unchanged(self):
        self.assertNotIn("--stride", (KB_ROOT / "skills" / "doc-fact-check" / "SKILL.md").read_text(encoding="utf-8"))


class ExtractBatches(unittest.TestCase):
    def test_deck_skill_extract_batches_500(self):
        self.assertIn('--out "<work dir>" --max-words 500', SKILL())
        self.assertNotIn("--max-words 1500", SKILL())


class AutoSizeAndDispatch(unittest.TestCase):
    def test_auto_chunk_size(self):
        self.assertEqual([C.auto_size(n) for n in (30, 80, 81, 450)], [4, 4, 6, 6])   # ≤ 20: 3 (round 6)

    def test_cli_accepts_auto(self):
        r = subprocess.run([sys.executable, "-I", str(DECK / "chunk_claims.py"), "--help"], capture_output=True, text=True)
        self.assertIn("auto", r.stdout)

    def test_skill_chunks_and_dispatch(self):
        s = SKILL()
        self.assertIn("--chunk-size auto --stride", s)
        self.assertIn("When the chunks fit in one wave (no more than `wave_size`), send them all in one message", s)
        self.assertIn("keep up to `wave_size` workers running; when one finishes, dispatch the next chunk", s)

    def test_estimate_uses_auto_size(self):
        from section_io import estimate_minutes
        self.assertEqual(estimate_minutes(450, 450, chunk=C.auto_size)["fast"], estimate_minutes(450, 450, chunk=6)["fast"])
        self.assertEqual(estimate_minutes(30, 30, chunk=C.auto_size)["fast"], estimate_minutes(30, 30, chunk=4)["fast"])


def _timings(d: Path) -> dict:
    p = d / "timings.json"
    return json.loads(p.read_text())["timings"] if p.exists() else {}


class ScriptClocks(unittest.TestCase):
    """T4: deterministic scripts record their own stage with --clock <work dir>; nothing without it; self-checks never."""

    def merge_dir(self):
        from test_fact_check_merge import MergeCase
        m = MergeCase()
        m.setUp()
        return m.dir

    def test_chunk_and_merge_record_with_clock_only(self):
        d = self.merge_dir()
        import merge_findings as M
        self.assertEqual(M.main([str(d), "--out", str(d / "out" / "findings.json"), "--scope", "all"]), 0)
        self.assertEqual(_timings(d), {})
        self.assertEqual(M.main([str(d), "--out", str(d / "out" / "findings.json"), "--scope", "all", "--clock", str(d)]), 0)
        self.assertEqual(set(_timings(d)["merge"]), {"start", "end"})
        d2 = self.merge_dir()
        for b in d2.glob("claims_batch_*.json"):                 # the merge fixture's claims carry no questions
            b.write_text(json.dumps([{**c, "questions": ["how many services run"]} for c in json.loads(b.read_text())]))
        self.assertEqual(C.main([str(d2), "--scope", "all", "--chunk-size", "4", "--clock", str(d2)]), 0)
        self.assertEqual(set(_timings(d2)["chunk"]), {"start", "end"})

    def test_self_checks_never_stamp(self):
        d = self.merge_dir()
        C.main([str(d), "--scope", "all", "--check-batch", "1", "--clock", str(d)])
        import merge_findings as M
        M.main([str(d), "--scope", "all", "--check-chunk", "1", "--clock", str(d)])
        self.assertEqual(_timings(d), {})

    def test_report_and_review_record(self):
        d = self.merge_dir()
        import merge_findings as M
        out = d / "run" / "findings.json"
        M.main([str(d), "--out", str(out), "--scope", "all"])
        import findings_report as R
        self.assertEqual(R.main([str(out), "--out", str(d / "run" / "page.html"), "--run", str(d / "run" / "run.json"),
                                 "--clock", str(d)]), 0)
        self.assertEqual(set(_timings(d)["report"]), {"start", "end"})
        import apply_slide_review as SR
        (d / "slide_review_1.json").write_text("[]")
        self.assertEqual(SR.main([str(out), "--run", str(d / "run" / "run.json"), str(d / "slide_review_1.json"),
                                  "--clock", str(d)]), 0)
        doc = json.loads((d / "timings.json").read_text())
        self.assertIn("end", doc["timings"]["review"])
        self.assertEqual(doc["workers"]["review"], 1)

    def test_skill_has_nine_clock_calls(self):
        self.assertEqual(SKILL().count('run_clock.py" "<work dir>"'), 9)
        for stage in ("chunk", "merge", "annotate", "report"):
            self.assertNotIn(f'run_clock.py" "<work dir>" {stage} ', SKILL())
        self.assertIn('--clock "<work dir>"', SKILL())


class Briefs(unittest.TestCase):
    """T3 (stop early on No Evidence; one line per lettered step), T6 (small listed sources), T7 (deck-only quotes)."""

    def test_verify_early_stop(self):
        v = (DECK / "verify.md").read_text(encoding="utf-8")
        self.assertIn("When R2 found nothing about the claim's subject, make at most one R3 read, then write No Evidence", v)
        self.assertIn("Write each lettered step as one line", v)

    # T6 (listed sources) was removed in round 6: no stage-V worker of r5a or r5b used it (test_deck_round6)

    def test_t7_is_deck_only(self):
        e = (DECK / "extract.md").read_text(encoding="utf-8")
        self.assertIn("For a deck claim (its section has `slide_context`), the quote is the whole sentence that holds the "
                      "risk-tagged words; a sentence over 25 words is cut to the clause that holds them", e)
        self.assertIn("verbatim quote ≤25 words", e)          # the general (Word) rule is unchanged


class SearchDepth(unittest.TestCase):
    """T8: the Brain ranks a decisive passage 4th-10th depending on wording (D1 on r5a); R1 must look at 10 hits."""

    def test_r1_reads_ten_hits(self):
        v = (DECK / "verify.md").read_text(encoding="utf-8")
        self.assertIn("`search_knowledge(query=<its first question>, limit=10)`", v)
        self.assertNotIn("limit=3", v)
        self.assertIn("Every `search_knowledge` call reads 10 hits (`limit=10`)", v)


class ReviewScenario(unittest.TestCase):
    def test_minor_covers_a_conflict_about_another_scenario(self):
        r = (DECK / "slide_review.md").read_text(encoding="utf-8")
        self.assertIn("whose only conflict is about a different process, system, attribute or scenario", r)
