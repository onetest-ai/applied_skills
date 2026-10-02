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
