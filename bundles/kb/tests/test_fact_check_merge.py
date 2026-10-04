"""merge_findings.py: claim files + chunk findings -> findings.json, coverage.json, run.json (synthetic fixtures)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

import merge_findings as M

SCRIPT = KB_ROOT / "skills" / "doc-fact-check" / "merge_findings.py"
SKILL = KB_ROOT / "skills" / "doc-fact-check" / "SKILL.md"

S1 = "Overview > 1 Scope"
S2 = "Overview > 1 Scope > 1.1 Systems"
S3 = "Overview > 2 Roadmap"
BATCHES = [
    {"batch": 1, "file": "batch_1.json", "words": 40,
     "sections": [{"section_id": "s01", "section": S1}, {"section_id": "s02", "section": S2}]},
    {"batch": 2, "file": "batch_2.json", "words": 80,
     "sections": [{"section_id": "s03", "section": S3, "part": 1}]},
]


def finding(fid, section, verdict="Incorrect", **kw):
    f = {"id": fid, "p_id": "p3", "section": section, "quote": "runs 12 services", "type": "NUM",
         "verdict": verdict, "severity": "Major", "confidence": "High", "evidence": "e", "trail": "t", "fix": "f",
         "source": "s", "sources": [{"name": "s", "link": "", "folder": ""}]}
    f.update(kw)
    return f


def claim(k, n, section):
    return {"claim_id": f"B{k}-C{n:02d}", "p_id": "p3", "s_id": f"b{k}s{n}", "section": section,
            "quote": "runs 12 services", "type": "NUM"}


def fclaim(n):
    return {"claim_id": f"I{n:02d}", "p_id": "p9", "s_id": "p9", "section": "Figure", "quote": "edge A to B",
            "type": "TOPO", "kind": "figure", "figure": 1}


class MergeCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.write("batches.json", BATCHES)
        self.write("stats.json", {"sections_total": 3, "statements_total": 9, "statements_risk": 4,
                                  "estimate_minutes": {"fast": 1, "deep": 2}})
        self.write("claims_batch_1.json", [claim(1, 1, S1), claim(1, 2, S2)])
        self.write("claims_batch_2.json", [claim(2, 1, S3)])
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json",
                                    "claim_ids": ["B1-C01", "B1-C02", "B2-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B1-C02", S2, verdict="Verified"),
                                             finding("B2-C01", S3, verdict="No Evidence", severity="Minor")])
        self.batch_files()

    def batch_files(self, risky=lambda cid: True, extra=()):
        """Write batch_<k>.json: one sentence per stage-E claim (its s_id), risk-tagged when risky(claim_id).
        extra: (batch, section, s_id, risk) sentences no claim covers."""
        for b in BATCHES:
            k = b["batch"]
            found = json.loads((self.dir / f"claims_batch_{k}.json").read_text())
            secs = []
            for s in b["sections"]:
                sents = [{"s_id": c["s_id"], "text": "t", "risk": ["num"] if risky(c["claim_id"]) else []}
                         for c in found if c["section"] == s["section"]]
                sents += [{"s_id": sid, "text": "t", "risk": r} for (kk, sec, sid, r) in extra
                          if kk == k and sec == s["section"]]
                secs.append({"section_id": s["section_id"], "section": s["section"],
                             "paragraphs": [{"p_id": "p3", "text": "x", "sentences": sents}], "tables": []})
            self.write(f"batch_{k}.json", {"batch": k, "sections": secs})

    def add_figures(self, n):
        """n figure claims, verified in their own chunk 2 (as chunk_claims.py would chunk them)."""
        self.write("claims_figures.json", [fclaim(i) for i in range(1, n + 1)])
        chunks = json.loads((self.dir / "chunks.json").read_text())
        ids = [f"I{i:02d}" for i in range(1, n + 1)]
        self.write("chunks.json", chunks + [{"chunk": 2, "file": "chunk_2.json", "claim_ids": ids}])
        self.write("findings_chunk_2.json", [finding(i, "Figure", type="TOPO", quote="edge A to B", p_id="p9", anchor="drawing", figure=1) for i in ids])

    def write(self, name, obj):
        p = self.dir / name
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        return p

    def errors(self, **kw):
        _, _, _, errors = M.merge(self.dir, **kw)
        return errors

    def has_error(self, *needles, **kw):
        errors = self.errors(**kw)
        self.assertTrue(any(all(n in e for n in needles) for e in errors), errors)


class TestMergeHappyPath(MergeCase):
    def test_clean_merge_renumbers_in_document_order_and_writes_run(self):
        findings, coverage, run, errors = M.merge(self.dir)
        self.assertEqual(errors, [])
        self.assertEqual([f["id"] for f in findings], ["C01", "C02", "C03"])
        self.assertEqual([f["section"] for f in findings], [S1, S2, S3])
        self.assertEqual(run["mode"], "deep")
        self.assertEqual((run["claims_extracted"], run["claims_in_scope"], run["claims_verified"], run["chunks"]),
                         (3, 3, 3, 1))
        self.assertEqual((run["sections_total"], run["statements_risk"]), (3, 4))

    def test_run_carries_the_source_hash_recorded_by_the_extractor(self):
        self.assertNotIn("source_sha256", M.merge(self.dir)[2])
        stats = json.loads((self.dir / "stats.json").read_text())
        self.write("stats.json", dict(stats, source_sha256="ab" * 32))
        self.assertEqual(M.merge(self.dir)[2]["source_sha256"], "ab" * 32)

    def test_order_follows_claim_order_not_finding_order(self):
        self.write("findings_chunk_1.json", [finding("B2-C01", S3), finding("B1-C02", S2), finding("B1-C01", S1)])
        findings, _, _, errors = M.merge(self.dir)
        self.assertEqual(errors, [])
        self.assertEqual([f["section"] for f in findings], [S1, S2, S3])

    def test_figure_ids_are_kept_and_appended(self):
        self.add_figures(2)
        findings, _, _, errors = M.merge(self.dir)
        self.assertEqual(errors, [])
        self.assertEqual([f["id"] for f in findings], ["C01", "C02", "C03", "I01", "I02"])

    def test_destination_is_optional_before_step_8(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B1-C02", S2),
                                             finding("B2-C01", S3, destination="log only")])
        findings, _, _, errors = M.merge(self.dir)
        self.assertEqual(errors, [])
        self.assertEqual(findings[2]["destination"], "log only")
        self.assertNotIn("destination", findings[0])

    def test_coverage_is_carried_with_the_canonical_reason(self):
        self.write("claims_batch_1.json", [claim(1, 1, S1)])
        self.write("coverage_batch_1.json", [{"heading": S2, "section_id": "s02", "reason": "no checkable statement"}])
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01", "B2-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B2-C01", S3)])
        _, coverage, _, errors = M.merge(self.dir)
        self.assertEqual(errors, [])
        self.assertEqual(coverage, [{"heading": "1.1 Systems", "section": S2, "reason": "no checkable statement"}])

    def test_risk_scope_zero_chunks_is_clean_and_auto_covers_sections(self):
        self.write("chunks.json", [])
        self.batch_files(risky=lambda cid: False)
        for p in self.dir.glob("findings_chunk_*.json"):
            p.unlink()
        findings, coverage, run, errors = M.merge(self.dir, scope="risk")
        self.assertEqual(errors, [])
        self.assertEqual(findings, [])
        self.assertEqual(run["mode"], "fast")
        self.assertEqual(run["claims_verified"], 0)
        self.assertEqual(len(coverage), 3)
        self.assertTrue(all(c["reason"] == M.RISK_COVERAGE_REASON for c in coverage))

    def test_cli_writes_three_files(self):
        out = self.dir / "run" / "findings.json"
        out.parent.mkdir()
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.dir), "--out", str(out), "--scope", "all"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("findings.json", "coverage.json", "run.json"):
            self.assertTrue((out.parent / name).exists(), name)

    def test_cli_failure_writes_nothing(self):
        (self.dir / "findings_chunk_1.json").unlink()
        out = self.dir / "run" / "findings.json"
        out.parent.mkdir()
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.dir), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("findings_chunk_1.json", r.stderr)
        self.assertEqual(list(out.parent.iterdir()), [])


class TestMergeFailsLoudly(MergeCase):
    def test_in_scope_claim_without_finding_fails_and_is_listed(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B1-C02", S2)])
        self.has_error("B2-C01", "no finding")

    def test_duplicate_finding_for_a_claim_fails(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B1-C01", S1),
                                             finding("B1-C02", S2), finding("B2-C01", S3)])
        self.has_error("B1-C01", "duplicate")

    def test_finding_for_claim_not_in_its_chunk_fails(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B1-C02", S2),
                                             finding("B2-C01", S3), finding("B9-C01", S3)])
        self.has_error("B9-C01", "chunk 1")

    def test_section_without_claims_or_coverage_fails(self):
        self.write("claims_batch_1.json", [claim(1, 1, S1)])        # S2 lost its only claim
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01", "B2-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B2-C01", S3)])
        self.has_error(S2, "neither a claim nor a coverage entry")

    def test_coverage_with_another_reason_does_not_count(self):
        self.write("claims_batch_1.json", [claim(1, 1, S1)])
        self.write("coverage_batch_1.json", [{"section_id": "s02", "reason": "ran out of time"}])
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01", "B2-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B2-C01", S3)])
        self.has_error("coverage_batch_1.json", "ran out of time")

    def test_chunk_claim_id_not_a_stage_e_claim(self):
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json",
                                    "claim_ids": ["B1-C01", "B1-C02", "B2-C01", "B7-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B1-C02", S2), finding("B2-C01", S3)])
        self.has_error("B7-C01", "not a stage-E claim")

    def test_missing_inputs(self):
        for name in ("batches.json", "stats.json", "chunks.json", "findings_chunk_1.json", "claims_batch_2.json"):
            with self.subTest(name=name):
                saved = (self.dir / name).read_text()
                (self.dir / name).unlink()
                self.has_error(name, "missing")
                self.write(name, saved)

    def test_malformed_json(self):
        self.write("findings_chunk_1.json", "[{not json")
        self.has_error("findings_chunk_1.json", "malformed")

    def test_object_wrapper_is_not_a_list(self):
        self.write("findings_chunk_1.json", {"findings": []})
        self.has_error("findings_chunk_1.json", "list")

    def test_missing_schema_key(self):
        f = finding("B1-C01", S1)
        del f["confidence"]
        self.write("findings_chunk_1.json", [f, finding("B1-C02", S2), finding("B2-C01", S3)])
        self.has_error("B1-C01", "confidence")

    def test_unknown_key(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, checks="a,b"), finding("B1-C02", S2),
                                             finding("B2-C01", S3)])
        self.has_error("B1-C01", "checks")

    def test_unknown_verdict_and_severity(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, verdict="Wrong"),
                                             finding("B1-C02", S2, severity="Huge"), finding("B2-C01", S3)])
        self.has_error("B1-C01", "Wrong")
        self.has_error("B1-C02", "Huge")

    def test_empty_section(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", ""), finding("B1-C02", S2), finding("B2-C01", S3)])
        self.has_error("B1-C01", "section")

    def test_sources_must_be_a_list(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B1-C02", S2),
                                             finding("B2-C01", S3, sources="s.md")])
        self.has_error("B2-C01", "sources")

    def test_missing_sources_defaults_to_empty_list(self):
        # A worker without an inventory has no source links: the merge fills [] instead of refusing,
        # so the main session never patches findings_chunk_*.json by hand (seen in controlled runs).
        rows = [finding("B1-C01", S1), finding("B1-C02", S2), finding("B2-C01", S3)]
        del rows[2]["sources"]
        self.write("findings_chunk_1.json", rows)
        findings, _, _, errors = M.merge(self.dir, scope="all")
        self.assertEqual(errors, [])
        self.assertEqual(sorted(len(f["sources"]) for f in findings), [0, 1, 1])

    def test_trail_is_required(self):
        rows = [finding("B1-C01", S1), finding("B1-C02", S2), finding("B2-C01", S3)]
        del rows[0]["trail"]
        self.write("findings_chunk_1.json", rows)
        self.has_error("B1-C01", "trail")

    # A real stage-V evidence text from a Cowork run: the audit trail written where the reader looks.
    AUDIT_IN_EVIDENCE = (
        'No governed metric: get_metric(name="vendor_data_retention_days") returned "Unknown metric". The source '
        'states: "Phone recordings are retained for 60 days" (chunk 6028315016624218065, Questions.docx, section 7).')

    def test_evidence_with_tool_calls_or_chunk_ids_is_refused(self):
        for bad in (self.AUDIT_IN_EVIDENCE, 'search_knowledge("x", latest_only=false) found nothing',
                    'The source states "60 days" (chunk 6028315016624218065).'):
            with self.subTest(bad=bad[:40]):
                self.write("findings_chunk_1.json", [finding("B1-C01", S1, evidence=bad), finding("B1-C02", S2),
                                                     finding("B2-C01", S3)])
                self.has_error("B1-C01", "trail")

    def test_evidence_over_the_word_limit_is_refused(self):
        long = " ".join(["word"] * (M.MAX_EVIDENCE_WORDS + 1))
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, evidence=long), finding("B1-C02", S2),
                                             finding("B2-C01", S3)])
        self.has_error("B1-C01", "words")

    def test_plain_quoted_evidence_passes(self):
        ok = 'The source says "Phone recordings are retained for 60 days" (Questions.docx, §7, 2026-09-21).'
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, evidence=ok), finding("B1-C02", S2),
                                             finding("B2-C01", S3)])
        self.assertEqual(M.merge(self.dir, scope="all")[3], [])

    def test_verified_non_number_needs_a_name_check_in_its_trail(self):
        # Stage E typed "hosted by <vendor>" as TOPO/OWN half the time, so an ENTITY-only rule was skipped.
        rows = [finding("B1-C01", S1, verdict="Verified", type="TOPO", trail="searched; supported"),
                finding("B1-C02", S2, verdict="Verified", type="OWN", trail="name check: none"),
                finding("B2-C01", S3, verdict="Verified", type="NUM", trail="get_metric row")]
        self.write("findings_chunk_1.json", rows)
        errors = self.errors()
        self.assertTrue(any("B1-C01" in e and "name check:" in e for e in errors), errors)
        self.assertFalse(any("B1-C02" in e or "B2-C01" in e for e in errors), errors)

    def test_a_defect_finding_needs_no_name_check(self):
        rows = [finding("B1-C01", S1, type="TOPO", trail="searched"), finding("B1-C02", S2), finding("B2-C01", S3)]
        self.write("findings_chunk_1.json", rows)
        self.assertEqual(M.merge(self.dir, scope="all")[3], [])

    def test_claim_fields_copied_into_a_finding_are_dropped_not_refused(self):
        # Workers copied s_id / kind from the claim record; the merge refused and the main session hand-edited files.
        rows = [finding("B1-C01", S1, s_id="b1s1", s_ids=["b1s1"], risk=["num"]), finding("B1-C02", S2),
                finding("B2-C01", S3, kind="figure")]
        self.write("findings_chunk_1.json", rows)
        findings, _, _, errors = M.merge(self.dir, scope="all")
        self.assertEqual(errors, [])
        self.assertFalse(any(k in f for f in findings for k in ("s_id", "s_ids", "risk", "kind")))

    def test_other_unknown_keys_are_still_refused(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, note="x"), finding("B1-C02", S2), finding("B2-C01", S3)])
        self.has_error("B1-C01", "unknown key(s) note")

    def test_figure_claim_ids_must_start_with_i(self):
        self.write("claims_figures.json", [fclaim(1), {**fclaim(2), "claim_id": "C09"}])
        self.has_error("C09", "start with I")

    def test_figure_claim_without_a_finding_is_an_error(self):
        self.add_figures(2)
        self.write("findings_chunk_2.json", [finding("I01", "Figure", type="TOPO", quote="edge A to B", p_id="p9", anchor="drawing", figure=1)])
        self.has_error("I02", "no finding")
        for scope in ("all", "risk"):
            with self.subTest(scope=scope):
                self.has_error("I02", "no finding", scope=scope)

    def test_findings_figures_file_is_an_error(self):
        self.write("findings_figures.json", [finding("I01", "Figure")])
        self.has_error("findings_figures.json", "claims_figures.json")

    def test_a_chunk_of_identical_no_evidence_findings_is_rejected_as_unverified(self):
        # b20: the main session wrote "No Evidence" stubs for chunks it never verified; the merge accepted them
        stub = dict(verdict="No Evidence", severity="Minor", evidence="not verified")
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, **stub), finding("B1-C02", S2, **stub),
                                             finding("B2-C01", S3, **stub)])
        self.has_error("findings_chunk_1.json", "unverified", "re-dispatch stage V for chunk 1")

    def test_genuine_no_evidence_findings_with_their_own_evidence_pass(self):
        ne = dict(verdict="No Evidence", severity="Minor")
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, evidence="no source covers the uptime", **ne),
                                             finding("B1-C02", S2, evidence="no metric for licences", **ne),
                                             finding("B2-C01", S3, evidence="roadmap date not in the Brain", **ne)])
        self.assertFalse(any("unverified" in e for e in self.errors()), self.errors())

    def test_every_error_is_reported_not_only_the_first(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, verdict="Wrong"), finding("B1-C02", S2)])
        errors = self.errors()
        self.assertTrue(any("Wrong" in e for e in errors), errors)
        self.assertTrue(any("B2-C01" in e and "no finding" in e for e in errors), errors)


class TestMergeMalformedShapesAreErrorsNotTracebacks(MergeCase):
    def test_chunks_json_not_a_list(self):
        self.write("chunks.json", {"chunk": 1})
        self.has_error("chunks.json", "list")

    def test_stats_json_not_an_object(self):
        self.write("stats.json", [1, 2])
        self.has_error("stats.json", "object")

    def test_chunk_record_without_int_chunk(self):
        self.write("chunks.json", [{"file": "chunk_1.json", "claim_ids": ["B1-C01"]},
                                   "oops", {"chunk": "1", "claim_ids": []}])
        self.has_error("chunks.json", "malformed chunk record")

    def test_chunk_claim_ids_not_a_list(self):
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": "B1-C01"}])
        self.has_error("chunks.json", "claim_ids")

    def test_non_dict_claim_item(self):
        self.write("claims_batch_1.json", ["B1-C01", claim(1, 2, S2)])
        self.has_error("claims_batch_1.json", "must be an object")

    def test_non_dict_coverage_item(self):
        self.write("coverage_batch_1.json", ["x"])
        self.has_error("coverage_batch_1.json", "must be an object")

    def test_non_dict_finding_item(self):
        self.write("findings_chunk_1.json", ["x"])
        self.has_error("findings_chunk_1.json", "must be an object")


class TestMergeReviewFixes(MergeCase):
    def test_all_scope_claim_dropped_from_chunks_fails(self):
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01", "B1-C02"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B1-C02", S2)])
        self.has_error("B2-C01", "stage-E claim is in no chunk")

    def test_all_scope_empty_chunks_with_claims_fails(self):
        self.write("chunks.json", [])
        self.has_error("B1-C01", "in no chunk")

    def test_risk_scope_claim_outside_chunks_is_fine(self):
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1)])
        self.batch_files(risky=lambda cid: cid == "B1-C01")
        _, _, run, errors = M.merge(self.dir, scope="risk")
        self.assertEqual(errors, [])
        self.assertEqual(run["claims_in_scope"], 1)

    def test_all_scope_claims_in_scope_counts_every_claim(self):
        _, _, run, _ = M.merge(self.dir)
        self.assertEqual(run["claims_in_scope"], 3)

    def test_claim_in_two_chunks_fails(self):
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01", "B1-C02", "B2-C01"]},
                                   {"chunk": 2, "file": "chunk_2.json", "claim_ids": ["B2-C01"]}])
        self.write("findings_chunk_2.json", [finding("B2-C01", S3)])
        for scope in ("all", "risk"):
            with self.subTest(scope=scope):
                self.has_error("B2-C01", "more than one chunk", scope=scope)

    def test_duplicate_figure_id_fails(self):
        self.write("claims_figures.json", [fclaim(1), fclaim(1)])
        self.has_error("I01", "duplicate")

    def test_duplicate_claim_id_fails_within_and_across_batches(self):
        self.write("claims_batch_1.json", [claim(1, 1, S1), claim(1, 1, S2)])
        self.has_error("B1-C01", "duplicate claim_id")
        self.write("claims_batch_1.json", [claim(1, 1, S1), claim(1, 2, S2)])
        self.write("claims_batch_2.json", [{**claim(2, 1, S3), "claim_id": "B1-C01"}])
        self.has_error("B1-C01", "duplicate claim_id")

    def test_missing_or_empty_claim_id_fails(self):
        for bad in ({k: v for k, v in claim(1, 1, S1).items() if k != "claim_id"}, {**claim(1, 1, S1), "claim_id": ""}):
            with self.subTest(bad=bad):
                self.write("claims_batch_1.json", [bad, claim(1, 2, S2)])
                self.has_error("claims_batch_1.json", "claim_id")

    def test_wrongly_prefixed_claim_id_fails(self):
        self.write("claims_batch_1.json", [{**claim(1, 1, S1), "claim_id": "B2-C09"}, claim(1, 2, S2)])
        self.has_error("B2-C09", "B1-")

    def test_finding_section_must_match_its_claim(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S3), finding("B1-C02", S2), finding("B2-C01", S3)])
        self.has_error("B1-C01", S1, S3)

    def test_coverage_entry_without_section_id(self):
        self.write("coverage_batch_1.json", [{"heading": S2, "reason": "no checkable statement"}])
        self.has_error("coverage_batch_1.json", "entry needs section_id")

    def test_cli_rejects_bad_wave_size(self):
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.dir), "--out", str(self.dir / "f.json"),
                            "--wave-size", "0"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("wave-size", r.stderr)

    def test_cli_creates_missing_out_directory(self):
        out = self.dir / "a" / "b" / "findings.json"
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.dir), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.with_name("run.json").exists())


class TestFinalFixWave(MergeCase):
    def test_fast_coverage_reason_has_one_definition(self):
        import fact_check_invariants as I
        self.assertIs(M.RISK_COVERAGE_REASON, I.RISK_COVERAGE_REASON)
        self.assertNotIn("RISK_COVERAGE_REASON =", SCRIPT.read_text(encoding="utf-8"))

    # I2c
    def test_finding_quote_and_p_id_must_equal_its_claims(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, quote="runs 13 services"),
                                             finding("B1-C02", S2, p_id="p9"),
                                             finding("B2-C01", S3)])
        self.has_error("B1-C01", "quote")
        self.has_error("B1-C02", "p_id")

    # I2d
    def test_risk_scope_recomputes_the_in_scope_set_from_batch_risk(self):
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1)])
        self.has_error("chunks.json", "B1-C02", scope="risk")          # B1-C02 is risky but not chunked
        self.batch_files(risky=lambda cid: cid == "B1-C01")
        self.assertEqual(self.errors(scope="risk"), [])
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C01", "B2-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C01", S1), finding("B2-C01", S3)])
        self.has_error("chunks.json", "B2-C01", scope="risk")          # chunked but not risky

    def test_merge_reuses_the_batch_risk_helper(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("_risk_by_s_id", src)
        self.assertNotIn("def _risk_by_s_id", src)

    # I3
    def test_risk_tagged_statement_needs_a_claim_a_listing_or_a_waiver(self):
        self.batch_files(extra=[(1, S1, "b1s9", ["date"])])
        self.has_error("b1s9", scope="risk")
        self.assertEqual(self.errors(), [])                            # deep mode does not enforce it
        self.write("coverage_batch_1.json", [{"s_id": "b1s9", "reason": "no checkable statement"}])
        self.assertFalse(any("b1s9" in e for e in self.errors(scope="risk")))

    def test_claim_s_ids_cover_a_risk_tagged_statement(self):
        self.batch_files(extra=[(1, S1, "b1s9", ["date"])])
        claims = json.loads((self.dir / "claims_batch_1.json").read_text())
        claims[0]["s_ids"] = ["b1s1", "b1s9"]
        self.write("claims_batch_1.json", claims)
        self.assertFalse(any("b1s9" in e for e in self.errors(scope="risk")))

    def test_section_coverage_for_a_section_with_claims_is_an_error_in_both_scopes(self):
        self.write("coverage_batch_1.json", [{"section_id": "s01", "reason": "no checkable statement"}])
        for scope in ("all", "risk"):
            self.has_error("s01", "claim", scope=scope)

    def test_section_coverage_for_a_claimless_section_still_covers_its_statements(self):
        self.write("claims_batch_1.json", [claim(1, 2, S2)])
        self.write("chunks.json", [{"chunk": 1, "file": "chunk_1.json", "claim_ids": ["B1-C02", "B2-C01"]}])
        self.write("findings_chunk_1.json", [finding("B1-C02", S2), finding("B2-C01", S3)])
        self.batch_files(extra=[(1, S1, "b1s9", ["date"])])
        self.write("coverage_batch_1.json", [{"section_id": "s01", "reason": "no checkable statement"}])
        self.assertEqual(self.errors(scope="risk"), [])

    def test_merge_validates_a_waiver_s_id_in_scope_all_too(self):
        self.write("coverage_batch_1.json", [{"s_id": "nope", "reason": "no checkable statement"}])
        self.has_error("nope", scope="all")
        self.write("coverage_batch_1.json", [{"s_id": ["b1s1"], "reason": "no checkable statement"}])
        self.has_error("s_id", scope="all")

    def test_untagged_statement_needs_no_claim(self):
        self.batch_files(extra=[(1, S1, "b1s9", [])])
        self.assertFalse(any("b1s9" in e for e in self.errors(scope="risk")))

    def test_row_with_risk_needs_a_claim_too(self):
        self.batch_files()
        b = json.loads((self.dir / "batch_1.json").read_text())
        b["sections"][0]["tables"] = [{"t_id": "t1", "rows": [{"s_id": "t1r2", "cells": ["a"], "risk": ["num"]}]}]
        self.write("batch_1.json", b)
        self.has_error("t1r2", scope="risk")

    # M1
    def test_scope_flag_must_match_the_chunk_files(self):
        self.write("chunk_1.json", {"chunk": 1, "scope": "all", "claims": []})
        self.has_error("chunk_1.json", "scope", scope="risk")
        self.assertFalse(any("chunk_1.json" in e for e in self.errors(scope="all")))

    # M4
    def test_figure_findings_may_carry_anchor_and_figure_only(self):
        from fact_check_invariants import FIGURE_KEYS
        self.assertEqual(FIGURE_KEYS, ("anchor", "figure"))
        self.add_figures(1)
        self.write("findings_chunk_2.json", [finding("I01", "Figure", type="TOPO", quote="edge A to B", p_id="p9", anchor="drawing", figure=1)])
        self.assertEqual(self.errors(), [])
        self.write("findings_chunk_2.json", [finding("I01", "Figure", type="TOPO", quote="edge A to B", p_id="p9", anchor="drawing", figure=1, extra=1)])
        self.has_error("I01", "unknown key", "extra")

    def test_chunk_findings_may_not_carry_figure_keys(self):
        self.write("findings_chunk_1.json", [finding("B1-C01", S1, anchor="drawing"), finding("B1-C02", S2),
                                             finding("B2-C01", S3)])
        self.has_error("B1-C01", "unknown key", "anchor")


class TestSectionsToMergeRoundTrip(unittest.TestCase):
    def test_sections_output_merges_and_satisfies_the_heading_coverage_invariant(self):
        from test_fact_check_code import HAVE_DOCX
        if not HAVE_DOCX:
            self.skipTest("needs python-docx >= 1.2")
        from fact_check_invariants import _covered_headings, _headings, _norm
        from test_fact_check_sections import build_fixture, run_cli
        tmp = Path(tempfile.mkdtemp())
        build_fixture(tmp / "d.docx")
        self.assertEqual(run_cli(tmp / "d.docx", tmp / "w", "--max-words", "100").returncode, 0)
        ids, found = [], []
        for b in json.loads((tmp / "w" / "batches.json").read_text()):
            k, secs = b["batch"], b["sections"]
            (tmp / "w" / f"claims_batch_{k}.json").write_text(json.dumps([claim(k, 1, secs[-1]["section"])]))
            ids.append(f"B{k}-C01")
            found.append(finding(f"B{k}-C01", secs[-1]["section"]))
            (tmp / "w" / f"coverage_batch_{k}.json").write_text(json.dumps(
                [{"section_id": s["section_id"], "reason": "no checkable statement"} for s in secs[:-1]]))
        (tmp / "w" / "chunks.json").write_text(json.dumps([{"chunk": 1, "file": "chunk_1.json", "claim_ids": ids}]))
        (tmp / "w" / "findings_chunk_1.json").write_text(json.dumps(found))
        out = tmp / "findings.json"
        r = subprocess.run([sys.executable, str(SCRIPT), str(tmp / "w"), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        covered = _covered_headings(json.loads(out.read_text()),
                                    json.loads(out.with_name("coverage.json").read_text()))
        self.assertEqual([h for h in _headings(tmp / "d.docx") if _norm(h) not in covered], [])


class TestSchemaHasOneSource(unittest.TestCase):
    def test_invariants_key_list_matches_skill_step_9(self):
        from fact_check_invariants import FINDING_KEYS
        text = re.sub(r"\s+", " ", SKILL.read_text(encoding="utf-8"))
        step9 = text[text.index("### 9."):text.index("### 10.")]
        listed = step9.split("exactly these keys:", 1)[1].split(". ", 1)[0]
        self.assertEqual(tuple(re.findall(r"`([a-z_]+)`", listed)), FINDING_KEYS)

    def test_merge_uses_the_invariants_list(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("from fact_check_invariants import", src)
        self.assertNotIn('"confidence"', src)   # no second copy of the key list


if __name__ == "__main__":
    unittest.main()


class TestFigureAnchors(MergeCase):
    def setUp(self):
        super().setUp()
        self.add_figures(1)

    def fig(self, **kw):
        f = finding("I01", "Figure", type="TOPO", quote="edge A to B", p_id="p9", anchor="drawing", figure=1)
        for k, v in kw.items():
            f.pop(k, None) if v is None else f.update({k: v})
        self.write("findings_chunk_2.json", [f])

    def test_correct_anchor_and_figure_pass(self):
        self.fig()
        self.assertEqual(self.errors(), [])

    def test_missing_wrong_figure_or_anchor_are_errors(self):
        for kw in ({"figure": None}, {"figure": 2}, {"anchor": None}, {"anchor": "paragraph"}):
            with self.subTest(kw=kw):
                self.fig(**kw)
                self.has_error("I01", "figure" if "figure" in kw else "anchor")

    def test_embedded_claim_is_verified_like_any_other_and_anchors_on_the_paragraph(self):
        emb = {**fclaim(1), "kind": "embedded", "quote": "sheet total 12"}
        emb.pop("figure")
        self.write("claims_figures.json", [emb])
        f = finding("I01", "Figure", type="TOPO", quote="sheet total 12", p_id="p9", anchor="paragraph")
        self.write("findings_chunk_2.json", [f])
        findings, _, _, errors = M.merge(self.dir)
        self.assertEqual(errors, [])
        self.assertEqual(findings[-1]["anchor"], "paragraph")
        self.write("findings_chunk_2.json", [{**f, "anchor": "drawing", "figure": 1}])
        self.has_error("I01", "anchor")

    def test_finding_built_as_the_stage_v_text_describes_merges_cleanly(self):
        """Stage V copies p_id/quote/section/type, plus anchor and figure for a figure claim; never kind."""
        c = json.loads((self.dir / "claims_figures.json").read_text())[0]
        f = finding(c["claim_id"], c["section"], type=c["type"], quote=c["quote"], p_id=c["p_id"],
                    anchor="drawing", figure=c["figure"])
        self.assertNotIn("kind", f)
        self.write("findings_chunk_2.json", [f])
        self.assertEqual(self.errors(), [])
