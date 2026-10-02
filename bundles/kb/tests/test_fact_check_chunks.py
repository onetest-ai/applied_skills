"""chunk_claims.py: stage-E claims -> verify chunks (synthetic fixtures)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import chunk_claims as C
from test_plugin_structure import KB_ROOT

SCRIPT = KB_ROOT / "skills" / "fact-check" / "chunk_claims.py"


def claim(k, n, s_id, section="Scope"):
    return {"claim_id": f"B{k}-C{n:02d}", "p_id": s_id.split("s")[0], "s_id": s_id, "section": section,
            "quote": f"claim {k}.{n}", "type": "NUM"}


def waiver(i):
    return {"s_id": f"p2s{i}", "reason": "no checkable statement"}


class ChunkCase(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        sents = [{"s_id": f"p2s{i}", "text": f"s{i}", "risk": (["num"] if i % 2 else [])} for i in range(1, 11)]
        batch = {"batch": 1, "sections": [{"section_id": "s01", "section": "Scope",
                                           "paragraphs": [{"p_id": "p2", "text": "x", "sentences": sents}],
                                           "tables": [], "figures": []}]}
        self.w("batches.json", [{"batch": 1, "file": "batch_1.json", "sections": [{"section_id": "s01", "section": "Scope"}]}])
        self.w("batch_1.json", batch)
        self.w("claims_batch_1.json", [claim(1, i, f"p2s{i}") for i in range(1, 11)])

    def w(self, name, obj):
        (self.d / name).write_text(json.dumps(obj))

    def test_chunk_size_and_order(self):
        index, errors = C.chunk(self.d, "all", 4)
        self.assertEqual(errors, [])
        self.assertEqual([len(c["claim_ids"]) for c in index], [4, 4, 2])
        self.assertEqual(index[0]["claim_ids"], ["B1-C01", "B1-C02", "B1-C03", "B1-C04"])
        body = json.loads((self.d / "chunk_1.json").read_text())
        self.assertEqual(body["scope"], "all")
        self.assertEqual(body["claims"][0]["risk"], ["num"])

    def test_risk_scope_keeps_tagged_only(self):
        index, _ = C.chunk(self.d, "risk", 8)
        ids = [i for c in index for i in c["claim_ids"]]
        self.assertEqual(ids, ["B1-C01", "B1-C03", "B1-C05", "B1-C07", "B1-C09"])

    def test_empty_scope_gives_zero_chunks(self):
        self.w("claims_batch_1.json", [claim(1, 2, "p2s2")])
        self.w("coverage_batch_1.json", [waiver(i) for i in (1, 3, 5, 7, 9)])
        index, errors = C.chunk(self.d, "risk", 8)
        self.assertEqual((index, errors), ([], []))
        self.assertEqual(json.loads((self.d / "chunks.json").read_text()), [])

    def test_unknown_s_id_fails_and_names_the_claim(self):
        self.w("claims_batch_1.json", [claim(1, 1, "p9s9")])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("B1-C01" in e and "p9s9" in e for e in errors), errors)

    def test_bad_claim_id_prefix_fails(self):
        self.w("claims_batch_1.json", [dict(claim(1, 1, "p2s1"), claim_id="B2-C01")])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("B2-C01" in e for e in errors), errors)

    def chunk_files(self):
        return sorted(p.name for p in self.d.glob("chunk_*.json"))

    def test_risk_scope_fails_before_any_chunk_and_names_every_uncovered_s_id(self):
        self.w("claims_batch_1.json", [claim(1, 1, "p2s1")])          # p2s3, 5, 7, 9 are tagged, unclaimed
        index, errors = C.chunk(self.d, "risk", 8)
        self.assertEqual(index, [])
        for s_id in ("p2s3", "p2s5", "p2s7", "p2s9"):
            self.assertTrue(any(s_id in e for e in errors), (s_id, errors))
        self.assertFalse(any("p2s1 " in e for e in errors), errors)
        self.assertEqual(self.chunk_files(), [])
        self.assertFalse((self.d / "chunks.json").exists())

    def test_scope_all_does_not_need_every_tagged_statement_claimed(self):
        self.w("claims_batch_1.json", [claim(1, 1, "p2s1")])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertEqual(errors, [])

    def test_claim_s_ids_cover_the_sentences_it_spans(self):
        c = dict(claim(1, 1, "p2s1"), s_ids=["p2s1", "p2s3", "p2s5", "p2s7", "p2s9"])
        self.w("claims_batch_1.json", [c])
        index, errors = C.chunk(self.d, "risk", 8)
        self.assertEqual(errors, [])
        self.assertEqual([i for x in index for i in x["claim_ids"]], ["B1-C01"])

    def test_claim_s_ids_must_be_real_s_ids_of_the_batch(self):
        self.w("claims_batch_1.json", [dict(claim(1, 1, "p2s1"), s_ids=["p2s1", "p9s9"])])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("B1-C01" in e and "p9s9" in e for e in errors), errors)

    def test_claim_s_ids_must_be_a_list(self):
        self.w("claims_batch_1.json", [dict(claim(1, 1, "p2s1"), s_ids="p2s1")])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("B1-C01" in e and "s_ids" in e for e in errors), errors)

    def test_per_statement_waiver_covers_a_tagged_sentence(self):
        self.w("claims_batch_1.json", [claim(1, 1, "p2s1")])
        self.w("coverage_batch_1.json", [waiver(i) for i in (3, 5, 7, 9)])
        index, errors = C.chunk(self.d, "risk", 8)
        self.assertEqual(errors, [])
        self.assertEqual([i for x in index for i in x["claim_ids"]], ["B1-C01"])

    def test_waiver_must_name_a_real_s_id_of_the_batch(self):
        self.w("coverage_batch_1.json", [waiver(99)])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("p2s99" in e for e in errors), errors)

    def test_waiver_reason_is_checked(self):
        self.w("coverage_batch_1.json", [{"s_id": "p2s1", "reason": "because"}])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("reason" in e for e in errors), errors)

    def test_section_coverage_for_a_section_with_claims_is_rejected(self):
        self.w("coverage_batch_1.json", [{"section_id": "s01", "reason": "no checkable statement"}])
        for scope in ("all", "risk"):
            _, errors = C.chunk(self.d, scope, 8)
            self.assertTrue(any("s01" in e and "claim" in e for e in errors), (scope, errors))

    def test_unhashable_ids_are_listed_errors_not_a_traceback(self):
        self.w("claims_batch_1.json", [dict(claim(1, 1, "p2s1"), s_ids=[["p2s1"]]), dict(claim(1, 2, "p2s2"), s_id=["p2s2"])])
        self.w("coverage_batch_1.json", [{"s_id": ["p2s1"], "reason": "no checkable statement"}])
        for scope in ("all", "risk"):
            _, errors = C.chunk(self.d, scope, 8)
            self.assertTrue(errors, scope)
            r = subprocess.run([sys.executable, str(SCRIPT), str(self.d), "--scope", scope], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("s_id", r.stderr)

    def test_one_helper_serves_chunk_and_merge(self):
        import merge_findings
        self.assertIs(merge_findings.risk_coverage_errors, C.risk_coverage_errors)

    def test_deterministic(self):
        C.chunk(self.d, "all", 3)
        a = (self.d / "chunks.json").read_bytes()
        C.chunk(self.d, "all", 3)
        self.assertEqual(a, (self.d / "chunks.json").read_bytes())

    def test_cli_exit_codes(self):
        ok = subprocess.run([sys.executable, str(SCRIPT), str(self.d), "--scope", "all"], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.w("claims_batch_1.json", [claim(1, 1, "p9s9")])
        bad = subprocess.run([sys.executable, str(SCRIPT), str(self.d)], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("p9s9", bad.stderr)


class FixRoundCase(ChunkCase):
    def test_failed_run_leaves_no_chunk_files(self):
        C.chunk(self.d, "all", 4)
        self.assertTrue((self.d / "chunks.json").exists())
        self.w("claims_batch_1.json", [claim(1, 1, "p9s9")])
        bad = subprocess.run([sys.executable, str(SCRIPT), str(self.d)], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertFalse((self.d / "chunks.json").exists())
        self.assertEqual(list(self.d.glob("chunk_*.json")), [])

    def test_non_dict_claim_item_is_listed(self):
        self.w("claims_batch_1.json", ["oops", claim(1, 1, "p2s1")])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(errors)

    def test_batch_record_without_batch_is_listed(self):
        self.w("batches.json", [{"file": "batch_1.json"}])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(errors)

    def test_non_list_batches_json_is_listed(self):
        self.w("batches.json", {"batch": 1})
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(errors)

    def test_non_dict_batch_file_is_listed(self):
        self.w("batch_1.json", [1])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(errors)

    def test_sentence_and_row_without_s_id_do_not_crash(self):
        batch = {"batch": 1, "sections": [{"paragraphs": [{"p_id": "p2", "sentences": [{"text": "x"}, {"s_id": "p2s1", "risk": []}]}],
                                           "tables": [{"rows": [{"text": "no id"}]}]}]}
        self.w("batch_1.json", batch)
        self.w("claims_batch_1.json", [claim(1, 1, "p2s1")])
        index, errors = C.chunk(self.d, "all", 8)
        self.assertEqual(errors, [])

    def test_missing_or_empty_s_id_fails(self):
        self.w("claims_batch_1.json", [{"claim_id": "B1-C01"}, dict(claim(1, 2, "p2s1"), s_id="")])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("B1-C01" in e and "missing s_id" in e for e in errors), errors)
        self.assertTrue(any("B1-C02" in e and "missing s_id" in e for e in errors), errors)

    def test_missing_s_id_not_matched_by_keyless_row(self):
        batch = {"batch": 1, "sections": [{"tables": [{"rows": [{"text": "no id"}]}]}]}
        self.w("batch_1.json", batch)
        self.w("claims_batch_1.json", [{"claim_id": "B1-C01"}])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("missing s_id" in e for e in errors), errors)

    def test_invalid_scope_raises(self):
        with self.assertRaises(ValueError):
            C.chunk(self.d, "bogus", 8)

    def test_duplicate_claim_id_fails(self):
        self.w("claims_batch_1.json", [claim(1, 1, "p2s1"), claim(1, 1, "p2s3")])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("B1-C01" in e and "duplicate" in e for e in errors), errors)

    def test_table_row_keyed_by_s_id_inherits_risk(self):
        batch = {"batch": 1, "sections": [{"paragraphs": [], "tables": [{"rows": [{"s_id": "t1r1", "r_id": "t1r1", "risk": ["num"]}]}]}]}
        self.w("batch_1.json", batch)
        self.w("claims_batch_1.json", [claim(1, 1, "t1r1")])
        index, errors = C.chunk(self.d, "risk", 8)
        self.assertEqual(errors, [])
        self.assertEqual(index[0]["claim_ids"], ["B1-C01"])
        self.assertEqual(json.loads((self.d / "chunk_1.json").read_text())["claims"][0]["risk"], ["num"])

class FinalFixChunkTests(ChunkCase):
    def test_rechunk_deletes_stale_findings_chunk_files(self):
        self.w("findings_chunk_1.json", []); self.w("findings_chunk_9.json", [])
        C.chunk(self.d, "all", 8)
        self.assertEqual(list(self.d.glob("findings_chunk_*.json")), [])
        self.w("findings_chunk_1.json", [])
        C.chunk(self.d, "all", 8)                       # a failing re-chunk clears them too
        self.assertEqual(list(self.d.glob("findings_chunk_*.json")), [])

    def test_uncovered_section_fails_before_any_chunk_is_written(self):
        self.w("claims_batch_1.json", [claim(1, 1, "p2s1", section="Scope")])
        self.w("batches.json", [{"batch": 1, "file": "batch_1.json", "sections": [
            {"section_id": "s01", "section": "Scope"}, {"section_id": "s02", "section": "Other"}]}])
        out, errors = C.chunk(self.d, "all", 8)
        self.assertEqual(out, [])
        self.assertTrue(any("Other" in e and "neither a claim nor a coverage entry" in e for e in errors), errors)
        self.assertEqual(list(self.d.glob("chunk*.json")), [])
        self.w("coverage_batch_1.json", [{"section_id": "s02", "reason": "no checkable statement"}])
        out, errors = C.chunk(self.d, "all", 8)
        self.assertEqual(errors, [])
        self.assertEqual(len(out), 1)

    def test_bad_coverage_entry_fails_the_chunking(self):
        self.w("coverage_batch_1.json", [{"section_id": "s99", "reason": "no checkable statement"}])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertTrue(any("s99" in e for e in errors), errors)

    def test_section_coverage_has_one_shared_implementation(self):
        import merge_findings
        for mod in (C, merge_findings):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            self.assertIn("section_coverage", src)
            self.assertNotIn("def section_coverage", src)


if __name__ == "__main__":
    unittest.main()


def fclaim(n, s_id="p5", quote="edge A to B", **kw):
    return {"claim_id": f"I{n:02d}", "p_id": s_id, "s_id": s_id, "section": "Figure", "quote": quote,
            "type": "TOPO", "kind": "figure", "figure": 1, **kw}


class FigureClaimCase(unittest.TestCase):
    w = ChunkCase.w

    def setUp(self):
        ChunkCase.setUp(self)
        b = json.loads((self.d / "batch_1.json").read_text())
        b["sections"][0]["figures"] = [{"figure": 1, "p_id": "p5", "image": "image1.png"}]
        self.w("batch_1.json", b)
        self.w("claims_figures.json", [fclaim(1), fclaim(2, quote="Z absent from figure 1")])

    def ids(self, scope, size=8):
        index, errors = C.chunk(self.d, scope, size)
        return [i for c in index for i in c["claim_ids"]], errors

    def test_figure_claims_are_in_both_scopes_after_the_text_claims(self):
        ids, errors = self.ids("all")
        self.assertEqual(errors, [])
        self.assertEqual(ids[-2:], ["I01", "I02"])
        self.assertEqual(len(ids), 12)
        ids, errors = self.ids("risk")
        self.assertEqual(ids, ["B1-C01", "B1-C03", "B1-C05", "B1-C07", "B1-C09", "I01", "I02"])

    def test_figure_claims_get_the_figure_risk_tag(self):
        C.chunk(self.d, "all", 4)
        last = json.loads((self.d / "chunk_3.json").read_text())["claims"]
        self.assertEqual([(c["claim_id"], c["risk"]) for c in last][-2:], [("I01", ["figure"]), ("I02", ["figure"])])

    def test_chunking_follows_the_size_rule_across_text_and_figures(self):
        index, errors = C.chunk(self.d, "all", 4)
        self.assertEqual([len(c["claim_ids"]) for c in index], [4, 4, 4])
        self.assertEqual(index[2]["claim_ids"][-2:], ["I01", "I02"])

    def test_no_claims_figures_file_changes_nothing(self):
        (self.d / "claims_figures.json").unlink()
        ids, errors = self.ids("all")
        self.assertEqual((len(ids), errors), (10, []))

    def test_bad_id_duplicate_and_unknown_s_id_are_errors(self):
        self.w("claims_figures.json", [{**fclaim(1), "claim_id": "X01"}])
        self.assertTrue(any("X01" in e and "start with I" in e for e in self.ids("all")[1]))
        self.w("claims_figures.json", [fclaim(1), fclaim(1)])
        self.assertTrue(any("I01" in e and "duplicate" in e for e in self.ids("all")[1]))
        self.w("claims_figures.json", [fclaim(1, s_id="p99")])
        self.assertTrue(any("I01" in e and "p99" in e for e in self.ids("all")[1]))
        self.w("claims_figures.json", [fclaim(1, s_ids=["p5", "p98"])])
        self.assertTrue(any("I01" in e and "p98" in e for e in self.ids("all")[1]))
        self.assertEqual(list(self.d.glob("chunk_*.json")), [])

    def test_s_id_is_checked_against_sections_json_figures_too(self):
        b = json.loads((self.d / "batch_1.json").read_text())
        b["sections"][0]["figures"] = []
        self.w("batch_1.json", b)
        self.assertTrue(self.ids("all")[1])
        self.w("sections.json", [{"section_id": "s01", "figures": [{"figure": 1, "p_id": "p5", "image": "i.png"}]}])
        self.assertEqual(self.ids("all")[1], [])

    def test_figure_number_is_passed_through_to_the_chunk_claims(self):
        C.chunk(self.d, "all", 20)
        got = {c["claim_id"]: c for c in json.loads((self.d / "chunk_1.json").read_text())["claims"]}
        self.assertEqual((got["I01"]["figure"], got["I01"]["kind"]), (1, "figure"))

    def test_figure_kind_needs_an_int_figure_matching_the_holder(self):
        for bad in (fclaim(1, figure=None), fclaim(1, figure="1"), fclaim(1, figure=2)):
            self.w("claims_figures.json", [bad])
            self.assertTrue(any("I01" in e and "figure" in e for e in self.ids("all")[1]), bad)
        self.w("claims_figures.json", [{k: v for k, v in fclaim(1).items() if k != "figure"}])
        self.assertTrue(any("I01" in e and "figure" in e for e in self.ids("all")[1]))

    def test_embedded_object_claim_may_use_any_paragraph_or_row_id(self):
        emb = {"claim_id": "I03", "p_id": "p2", "s_id": "p2s4", "section": "Figure", "quote": "sheet total 12",
               "type": "NUM", "kind": "embedded"}
        self.w("claims_figures.json", [fclaim(1), emb])
        ids, errors = self.ids("risk")
        self.assertEqual((errors, ids[-2:]), ([], ["I01", "I03"]))
        self.w("claims_figures.json", [{**emb, "s_id": "p77"}])
        self.assertTrue(any("I03" in e and "p77" in e for e in self.ids("all")[1]))
        self.w("claims_figures.json", [{**emb, "kind": "diagram"}])
        self.assertTrue(any("I03" in e and "kind" in e for e in self.ids("all")[1]))
