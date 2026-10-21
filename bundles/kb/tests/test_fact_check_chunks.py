"""chunk_claims.py: stage-E claims -> verify chunks (synthetic fixtures)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import chunk_claims as C
from test_plugin_structure import KB_ROOT

SCRIPT = KB_ROOT / "skills" / "doc-fact-check" / "chunk_claims.py"


def claim(k, n, s_id, section="Scope"):
    return {"claim_id": f"B{k}-C{n:02d}", "p_id": s_id.split("s")[0], "s_id": s_id, "section": section,
            "quote": f"claim {k}.{n}", "claim": f"claim {k}.{n}", "type": "NUM",
            "questions": ["what does the source say about this"]}


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


def fclaim(n, s_id="p5", quote="edge A to B", **kw):
    return {"claim_id": f"I{n:02d}", "p_id": s_id, "s_id": s_id, "section": "Figure", "quote": quote,
            "type": "TOPO", "kind": "figure", "figure": 1, "questions": ["what does the source say about this"],
            "claim": quote, **kw}


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
               "type": "NUM", "kind": "embedded", "questions": ["what is the sheet total"], "claim": "the sheet total is 12"}
        self.w("claims_figures.json", [fclaim(1), emb])
        ids, errors = self.ids("risk")
        self.assertEqual((errors, ids[-2:]), ([], ["I01", "I03"]))
        self.w("claims_figures.json", [{**emb, "s_id": "p77"}])
        self.assertTrue(any("I03" in e and "p77" in e for e in self.ids("all")[1]))
        self.w("claims_figures.json", [{**emb, "kind": "diagram"}])
        self.assertTrue(any("I03" in e and "kind" in e for e in self.ids("all")[1]))


class V2QuestionsCase(ChunkCase):
    def test_claim_without_questions_is_refused(self):
        for bad in ([], ["a", "b", "c", "d"], [""], ["  "], "q", [1]):
            self.w("claims_batch_1.json", [{**claim(1, 1, "p2s1"), "questions": bad}])
            index, errors = C.chunk(self.d, "all", 8)
            self.assertEqual(index, [], bad)
            self.assertTrue(any("questions must be a list of 1-3 non-empty strings" in e for e in errors), (bad, errors))
        self.w("claims_batch_1.json", [{k: v for k, v in claim(1, 1, "p2s1").items() if k != "questions"}])
        self.assertTrue(any("B1-C01" in e and "questions must be" in e for e in C.chunk(self.d, "all", 8)[1]))

    def test_questions_travel_into_the_chunk(self):
        self.w("claims_batch_1.json", [{**claim(1, 1, "p2s1"), "questions": ["how many databases are there"]}])
        index, errors = C.chunk(self.d, "all", 8)
        self.assertEqual(errors, [])
        got = json.loads((self.d / "chunk_1.json").read_text())
        self.assertEqual(got["claims"][0]["questions"], ["how many databases are there"])

    def test_fast_refuses_two_claims_on_one_sentence(self):
        a, b = claim(1, 1, "p2s1"), claim(1, 2, "p2s1")
        self.w("claims_batch_1.json", [a, b])
        self.w("coverage_batch_1.json", [waiver(i) for i in (3, 5, 7, 9)])
        index, errors = C.chunk(self.d, "risk", 8)
        self.assertEqual(index, [])
        msg = next(e for e in errors if "at most one claim per sentence" in e)
        for needle in (a["claim_id"], b["claim_id"], "p2s1"):
            self.assertIn(needle, msg)

    def test_deep_allows_two_claims_on_one_sentence(self):
        self.w("claims_batch_1.json", [claim(1, 1, "p2s1"), claim(1, 2, "p2s1")])
        _, errors = C.chunk(self.d, "all", 8)
        self.assertEqual(errors, [])

    def test_risk_is_the_union_over_anchor_and_s_ids(self):
        # p2s2 is untagged, p2s3 is num-tagged: a claim anchored on p2s2 that spans p2s3 is in scope
        c = dict(claim(1, 1, "p2s2"), s_ids=["p2s2", "p2s3"])
        self.w("claims_batch_1.json", [c])
        self.w("coverage_batch_1.json", [waiver(i) for i in (1, 5, 7, 9)])
        index, errors = C.chunk(self.d, "risk", 8)
        self.assertEqual(errors, [])
        self.assertEqual([i for x in index for i in x["claim_ids"]], ["B1-C01"])
        body = json.loads((self.d / "chunk_1.json").read_text())
        self.assertEqual(body["claims"][0]["risk"], ["num"])

    def test_claim_risk_helper_unions_without_duplicates(self):
        risk = {"a": ["num"], "b": ["date", "num"], "c": []}
        self.assertEqual(C.claim_risk({"s_id": "c", "s_ids": ["c", "a", "b"]}, risk), ["num", "date"])
        self.assertEqual(C.claim_risk({"s_id": "c"}, risk), [])

    def test_fast_one_claim_rule_skips_untagged_anchors(self):
        # two claims on untagged p2s2 are out of scope in Fast and are not refused
        self.w("claims_batch_1.json", [claim(1, 1, "p2s2"), claim(1, 2, "p2s2")])
        self.w("coverage_batch_1.json", [waiver(i) for i in (1, 3, 5, 7, 9)])
        index, errors = C.chunk(self.d, "risk", 8)
        self.assertEqual((index, errors), ([], []))

    def test_fast_one_claim_rule_uses_the_union_risk(self):
        a = dict(claim(1, 1, "p2s2"), s_ids=["p2s2", "p2s3"])
        b = claim(1, 2, "p2s2")
        self.w("claims_batch_1.json", [a, b])
        self.w("coverage_batch_1.json", [waiver(i) for i in (1, 5, 7, 9)])
        _, errors = C.chunk(self.d, "risk", 8)
        self.assertFalse(any("at most one claim per sentence" in e for e in errors), errors)
        b = dict(claim(1, 2, "p2s2"), s_ids=["p2s2", "p2s5"])           # now both are in scope on p2s2
        self.w("claims_batch_1.json", [a, b])
        self.w("coverage_batch_1.json", [waiver(i) for i in (1, 7, 9)])
        _, errors = C.chunk(self.d, "risk", 8)
        self.assertTrue(any("at most one claim per sentence" in e and "p2s2" in e for e in errors), errors)

    def test_default_chunk_size_is_15(self):
        import inspect
        self.assertEqual(inspect.signature(C.chunk).parameters["size"].default, 15)
        self.assertEqual(C.build_parser().parse_args(["d"]).chunk_size, 15)   # the CLI default, not --help text

    def test_figure_claim_needs_questions(self):
        FigureClaimCase.setUp(self)
        for bad in ([], ["q"] * 4):
            self.w("claims_figures.json", [{**fclaim(1), "questions": bad}])
            errors = C.chunk(self.d, "all", 8)[1]
            self.assertTrue(any("claims_figures.json: I01: questions must be a list of 1-3 non-empty strings" in e
                                for e in errors), errors)
        self.w("claims_figures.json", [{k: v for k, v in fclaim(1).items() if k != "questions"}])
        self.assertTrue(any("claims_figures.json: I01: questions must be" in e for e in C.chunk(self.d, "all", 8)[1]))


if __name__ == "__main__":
    unittest.main()


class SnapQuote(unittest.TestCase):
    SRC = "The nightly import runs at 02:00 and retries once; after two failures a priority ticket is raised."

    def test_a_verbatim_quote_is_returned_as_written(self):
        q = "after two failures a priority ticket is raised"
        self.assertEqual(C.snap_quote(q, self.SRC), q + ".")   # the source's own full stop is attached to the last word

    def test_an_attribution_wrapper_is_dropped(self):
        q = 'The ops lead says: "the nightly import runs at 02:00 and retries once"'
        self.assertEqual(C.snap_quote(q, self.SRC), "The nightly import runs at 02:00 and retries once;")

    def test_two_joined_spans_give_the_longest_one(self):
        q = "the nightly import runs at 02:00 ... a priority ticket is raised"
        self.assertEqual(C.snap_quote(q, self.SRC), "The nightly import runs at 02:00")

    def test_curly_quotes_and_dashes_do_not_matter(self):
        self.assertEqual(C.snap_quote("it’s fine", "it's fine and well", min_words=2), "it's fine")

    def test_fewer_than_four_words_in_a_row_gives_none(self):
        self.assertIsNone(C.snap_quote("import runs at nightly", self.SRC))

    def test_the_result_is_text_exactly_as_the_source_wrote_it(self):
        self.assertIn(C.snap_quote("runs at 02:00 and retries once", self.SRC), self.SRC)


class ChunkSnapsQuotes(unittest.TestCase):
    SRC = "The nightly import runs at 02:00 and retries once; after two failures a priority ticket is raised."
    SRC2 = "Operators then check the dashboard for stuck updates."

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        sents = [{"s_id": "p2s1", "text": self.SRC, "risk": ["num"]}, {"s_id": "p2s2", "text": self.SRC2, "risk": []}]
        batch = {"batch": 1, "sections": [{"section_id": "s01", "section": "Scope",
                                           "paragraphs": [{"p_id": "p2", "text": "x", "sentences": sents}],
                                           "tables": [], "figures": []}]}
        self.w("batches.json", [{"batch": 1, "file": "batch_1.json", "sections": [{"section_id": "s01", "section": "Scope"}]}])
        self.w("batch_1.json", batch)
        self.w("coverage_batch_1.json", [waiver(2)])

    def w(self, name, obj):
        (self.d / name).write_text(json.dumps(obj))

    def chunk_with(self, quote, **extra):
        c = {**claim(1, 1, "p2s1"), "quote": quote, "claim": "The nightly import retries once and a priority ticket follows two failures", **extra}
        self.w("claims_batch_1.json", [c])
        index, errors = C.chunk(self.d, "all", 4, repair=True)
        self.assertEqual(errors, [])
        return json.loads((self.d / "chunk_1.json").read_text())["claims"][0]

    def test_a_non_verbatim_quote_is_snapped_and_the_original_kept(self):
        c = self.chunk_with('He says: "the nightly import runs at 02:00 and retries once"')
        self.assertEqual(c["quote"], "The nightly import runs at 02:00 and retries once;")
        self.assertTrue(c["quote_original"].startswith("He says:"))

    def test_a_verbatim_quote_is_left_alone_and_gets_no_original(self):
        c = self.chunk_with("The nightly import runs at 02:00")
        self.assertEqual(c["quote"], "The nightly import runs at 02:00")
        self.assertNotIn("quote_original", c)

    def test_a_quote_with_no_four_word_run_is_unchanged_and_still_warned(self):
        q = "import runs at nightly"
        c = self.chunk_with(q)
        self.assertEqual(c["quote"], q)
        self.assertNotIn("quote_original", c)
        warnings: list[str] = []
        C.chunk(self.d, "all", 4, warnings=warnings, dry_run=True, repair=True)
        self.assertTrue(any("not verbatim" in w for w in warnings))

    def test_a_claim_spanning_two_sentences_is_searched_across_both(self):
        c = self.chunk_with("priority ticket is raised. Operators then check the dashboard for stuck updates: see below",
                            s_ids=["p2s1", "p2s2"])
        self.assertEqual(c["quote"], "priority ticket is raised. Operators then check the dashboard for stuck updates.")

    def test_a_figure_claim_is_never_snapped(self):
        text = {"p2s1": self.SRC}
        fig = {"claim_id": "I01", "s_id": "p2s1", "type": "FIGURE", "quote": 'x says: "the nightly import runs at 02:00 and retries once"'}
        self.assertEqual(C.snap_claim(fig, text), fig)

    def test_a_snapped_quote_is_no_longer_warned_about(self):
        self.chunk_with('He says: "the nightly import runs at 02:00 and retries once"')
        warnings: list[str] = []
        C.chunk(self.d, "all", 4, warnings=warnings, dry_run=True, repair=True)
        self.assertEqual(warnings, [])

    def test_repair_is_off_by_default_so_the_deck_path_is_unchanged(self):
        q = 'He says: "the nightly import runs at 02:00 and retries once"'
        self.w("claims_batch_1.json", [{**claim(1, 1, "p2s1"), "quote": q, "claim": "The nightly import retries once"}])
        C.chunk(self.d, "all", 4)
        c = json.loads((self.d / "chunk_1.json").read_text())["claims"][0]
        self.assertEqual(c["quote"], q)
        self.assertNotIn("quote_original", c)

    def test_the_command_line_flag_turns_it_on(self):
        q = 'He says: "the nightly import runs at 02:00 and retries once"'
        self.w("claims_batch_1.json", [{**claim(1, 1, "p2s1"), "quote": q, "claim": "The nightly import retries once"}])
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), str(self.d), "--scope", "all", "--repair-anchors"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads((self.d / "chunk_1.json").read_text())["claims"][0]["quote"],
                         "The nightly import runs at 02:00 and retries once;")


class RowSnapsQuotes(unittest.TestCase):
    """A table-row quote may join cells; a comment anchors inside one cell, so the longest verbatim run of any single cell is kept."""
    CELLS = ["Import schedule", "The nightly import runs at 02:00 and retries once"]

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        sents = [{"s_id": "p2s1", "text": "Intro.", "risk": []}]
        rows = [{"r_id": "t1r1", "cells": ["Item", "Value"], "s_id": "t1r1", "risk": []},
                {"r_id": "t1r2", "cells": self.CELLS, "s_id": "t1r2", "risk": ["num"]}]
        batch = {"batch": 1, "sections": [{"section_id": "s01", "section": "Scope",
                                           "paragraphs": [{"p_id": "p2", "text": "x", "sentences": sents}],
                                           "tables": [{"t_id": "t1", "rows": rows}], "figures": []}]}
        (self.d / "batches.json").write_text(json.dumps([{"batch": 1, "file": "batch_1.json", "sections": [{"section_id": "s01", "section": "Scope"}]}]))
        (self.d / "batch_1.json").write_text(json.dumps(batch))
        (self.d / "coverage_batch_1.json").write_text(json.dumps([{"s_id": "p2s1", "reason": "no checkable statement"},
                                                                  {"s_id": "t1r1", "reason": "no checkable statement"}]))

    def chunk_with(self, quote):
        c = {**claim(1, 1, "t1r2"), "quote": quote, "claim": "The nightly import retries once"}
        (self.d / "claims_batch_1.json").write_text(json.dumps([c]))
        _, errors = C.chunk(self.d, "all", 4, repair=True)
        self.assertEqual(errors, [])
        return json.loads((self.d / "chunk_1.json").read_text())["claims"][0]

    def test_a_quote_joining_two_cells_keeps_the_longest_single_cell_run(self):
        c = self.chunk_with("Import schedule: the nightly import runs at 02:00 and retries once")
        self.assertEqual(c["quote"], "The nightly import runs at 02:00 and retries once")
        self.assertTrue(c["quote_original"].startswith("Import schedule:"))

    def test_a_quote_verbatim_in_one_cell_is_left_alone(self):
        c = self.chunk_with("The nightly import runs at 02:00")
        self.assertNotIn("quote_original", c)

    def test_cells_too_short_for_a_run_leave_the_quote_unchanged(self):
        q = "Import schedule: nightly"
        self.assertEqual(self.chunk_with(q)["quote"], q)


class RepairFlagIsWiredToTheWordSkillOnly(unittest.TestCase):
    """The Word skill's documented chunk command passes --repair-anchors; the deck's does not (its comments anchor to shapes)."""

    def command(self, skill):
        text = (KB_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        return next(m.group(0) for m in re.finditer(r'python "<skill dir>/chunk_claims\.py"[^`]*', text) if "--scope <scope>" in m.group(0))

    def test_the_word_skill_passes_the_flag(self):
        self.assertIn("--repair-anchors", self.command("doc-fact-check"))

    def test_the_deck_skill_does_not(self):
        self.assertNotIn("--repair-anchors", self.command("deck-fact-check"))


class RowPidRepair(unittest.TestCase):
    """A row claim's p_id must be its row id (the id Word's paragraph locator gives the cell); stage E sometimes writes a nearby body paragraph id."""

    setUp = RowSnapsQuotes.setUp
    CELLS = RowSnapsQuotes.CELLS

    def chunk_row(self, p_id, repair=True, s_id="t1r2"):
        c = {**claim(1, 1, s_id), "p_id": p_id, "quote": "The nightly import runs at 02:00", "claim": "The nightly import runs at 02:00"}
        (self.d / "claims_batch_1.json").write_text(json.dumps([c]))
        _, errors = C.chunk(self.d, "all", 4, repair=repair)
        self.assertEqual(errors, [])
        return json.loads((self.d / "chunk_1.json").read_text())["claims"][0]

    def test_a_row_claim_with_a_body_paragraph_id_gets_its_row_id(self):
        c = self.chunk_row("p2")
        self.assertEqual((c["p_id"], c["p_id_original"]), ("t1r2", "p2"))

    def test_a_row_claim_already_on_its_row_id_is_untouched(self):
        c = self.chunk_row("t1r2")
        self.assertEqual(c["p_id"], "t1r2")
        self.assertNotIn("p_id_original", c)

    def test_a_paragraph_claim_keeps_its_p_id(self):
        d = Path(tempfile.mkdtemp())
        sents = [{"s_id": "p2s1", "text": "The nightly import runs at 02:00", "risk": ["num"]}]
        batch = {"batch": 1, "sections": [{"section_id": "s01", "section": "Scope",
                                           "paragraphs": [{"p_id": "p2", "text": "x", "sentences": sents}], "tables": [], "figures": []}]}
        (d / "batches.json").write_text(json.dumps([{"batch": 1, "file": "batch_1.json", "sections": [{"section_id": "s01", "section": "Scope"}]}]))
        (d / "batch_1.json").write_text(json.dumps(batch))
        (d / "claims_batch_1.json").write_text(json.dumps([{**claim(1, 1, "p2s1"), "p_id": "p2", "quote": "The nightly import runs at 02:00", "claim": "The nightly import runs at 02:00"}]))
        C.chunk(d, "all", 4, repair=True)
        self.assertEqual(json.loads((d / "chunk_1.json").read_text())["claims"][0]["p_id"], "p2")

    def test_the_row_id_is_not_imposed_without_the_flag(self):
        self.assertEqual(self.chunk_row("p2", repair=False)["p_id"], "p2")
