import io
import json
import os
import sqlite3
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from datetime import datetime, timezone

import decisions as D
import graph_migrate as GM
import taxonomy_review as R
from taxo_fixtures import TAGS, tagged_store, taxonomy, write_json

NOW = datetime(2026, 9, 21, 14, 2, 55, tzinfo=timezone.utc)


def cli(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = R.main(list(argv))
    return code, json.loads(buf.getvalue().strip().splitlines()[-1])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.v0 = os.path.join(self.dir, "taxonomy_v0.json")
        write_json(self.v0, taxonomy())
        self.db = os.path.join(self.td.name, "k.sqlite")
        tagged_store(self.db, taxonomy())

    def tearDown(self):
        self.td.cleanup()

    def test_draft_with_consolidated_evidence_and_flags(self):
        write_json(os.path.join(self.dir, "work", "consolidated.json"), {"clusters": {"intent_classes": [
            {"canonical_guess": "Transform", "variants": ["Transform", "Transformation"], "n_sources": 3,
             "avg_confidence": 0.5, "members": [{"evidence": "Transform workstream kicks off", "source": "s.pptx"}]}]}})
        path, rv = R.build_plan("draft", self.v0, now=NOW)
        self.assertTrue(path.endswith(f"reviews/review_{rv['review_id']}.json"))
        self.assertTrue(rv["evidence_available"])
        tr = next(i for i in rv["items"] if i["op"]["node"] == "Transform")
        self.assertEqual(tr["flags"][0]["kind"], "off_axis")
        self.assertEqual(tr["evidence"][0]["quote"], "Transform workstream kicks off")
        self.assertEqual({i["level"] for i in rv["items"]}, {"L1", "L2", "entity"})

    def test_draft_without_consolidated_uses_tagged_samples(self):
        _, rv = R.build_plan("draft", self.v0, db=self.db, now=NOW)
        self.assertFalse(rv["evidence_available"])
        refunds = next(i for i in rv["items"] if i["op"]["node"] == "Refunds")
        self.assertEqual(refunds["support"]["tags"], 2)
        self.assertEqual([s["chunk_id"] for s in refunds["support"]["samples"]], [2, 4])

    def test_ids_are_deterministic(self):
        _, a = R.build_plan("draft", self.v0, now=NOW)
        _, b = R.build_plan("draft", self.v0, now=NOW)
        self.assertEqual([i["id"] for i in a["items"]], [i["id"] for i in b["items"]])

    def test_drift_items_statuses(self):
        cur = os.path.join(self.dir, "current.json")
        write_json(cur, taxonomy(version=1))
        props = os.path.join(self.td.name, "props")
        write_json(os.path.join(props, "result_0.json"), {"proposals": [
            {"name": "Payment Plans", "level": "L2", "parent": "Billing & Payments", "evidence": "instalments",
             "example_ids": [8]},
            {"name": "Q3 Offsite", "level": "L1", "evidence": "agenda"},
            {"name": "Refunds", "level": "L2", "parent": "Billing & Payments"},
            {"name": "Orphan", "level": "L2", "parent": "Nowhere"}]})
        D.append(os.path.join(self.dir, "decisions.jsonl"),
                 {"review_id": "r-old", "item_id": "i-x", "action": "reject", "reason": "not a call reason",
                  "fingerprint": "add|l1|q3 offsite|"})
        _, rv = R.build_plan("drift", cur, proposals_dir=props, db=self.db, now=NOW)
        st = {i["op"]["name"]: i["status"] for i in rv["items"]}
        self.assertEqual(st, {"Payment Plans": "proposed", "Q3 Offsite": "suppressed",
                              "Refunds": "auto_skipped", "Orphan": "invalid"})
        pp = next(i for i in rv["items"] if i["op"]["name"] == "Payment Plans")
        self.assertEqual(pp["support"]["projected_coverage_gain_pts"], 12.5)   # 1 untagged of 8 chunks
        self.assertEqual(rv["stats"], {"total_chunks": 8, "untagged_chunks": 1})

    def test_drift_plan_stdout_is_one_json_line_listing_skipped_files(self):
        cur = os.path.join(self.dir, "current.json")
        write_json(cur, taxonomy(version=1))
        props = os.path.join(self.td.name, "props")
        write_json(os.path.join(props, "result_0.json"), {"proposals": [
            {"name": "Payment Plans", "level": "L2", "parent": "Billing & Payments", "example_ids": [8]}]})
        bad = os.path.join(props, "result_1.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), unittest.mock.patch("sys.stderr", err):
            code = R.main(["plan", "--mode", "drift", "--taxonomy", cur, "--proposals", props])
        self.assertEqual(code, 0)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        out = json.loads(lines[0])
        self.assertEqual((out["items"], out["skipped_files"]), (1, [bad]))
        self.assertIn("result_1.json", err.getvalue())

    def test_browse_and_drift_on_a_version_file_are_refused_naming_adopt(self):
        v1 = os.path.join(self.dir, "taxonomy_v1.json")
        write_json(v1, taxonomy(version=1))
        for mode in ("browse", "drift"):
            code, out = cli("plan", "--mode", mode, "--taxonomy", v1, "--proposals", self.td.name)
            self.assertEqual((code, out["status"]), (2, "error"))
            self.assertIn("adopt", out["errors"][0])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "reviews")))

    def test_plan_refuses_to_overwrite_a_differing_review(self):
        path, rv1 = R.build_plan("draft", self.v0, db=self.db, now=NOW)
        raw1 = open(path, "rb").read()
        db2 = os.path.join(self.td.name, "k2.sqlite")
        tags2 = dict(TAGS)
        tags2[2] = ["Duplicate Charge"]   # differs from self.db's tags -> different support.tags counts
        tagged_store(db2, taxonomy(), tags=tags2)
        with self.assertRaises(FileExistsError) as ctx:
            R.build_plan("draft", self.v0, db=db2, now=NOW)
        self.assertIn(path, str(ctx.exception))
        self.assertIn("immutable", str(ctx.exception))
        self.assertEqual(open(path, "rb").read(), raw1)   # untouched

    def test_plan_is_idempotent_when_content_is_identical(self):
        path1, rv1 = R.build_plan("draft", self.v0, db=self.db, now=NOW)
        path2, rv2 = R.build_plan("draft", self.v0, db=self.db, now=NOW)
        self.assertEqual(path1, path2)
        self.assertEqual(rv1, rv2)


class RecordStatusTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        cur = os.path.join(self.dir, "current.json")
        write_json(cur, taxonomy(version=1))
        self.path, self.rv = R.build_plan("browse", cur, now=NOW)

    def tearDown(self):
        self.td.cleanup()

    def test_record_add_then_submit_and_status(self):
        op = json.dumps({"type": "add", "level": "L1", "name": "AI & Automation"})
        code, out = cli("record", "--review", self.path, "--action", "propose", "--op", op, "--reviewer", "Pat")
        self.assertEqual((code, out["status"]), (0, "recorded"))
        code, out = cli("status", "--review", self.path)
        self.assertEqual(out["proposals"], 1)
        code, out = cli("record", "--review", self.path, "--submit", "--reviewer", "Pat")
        self.assertEqual(code, 0)
        self.assertTrue(cli("status", "--review", self.path)[1]["submitted"])

    def test_record_refuses_structural_ops(self):
        op = json.dumps({"type": "rename", "node": "Refunds", "new_name": "Refund Requests"})
        code, out = cli("record", "--review", self.path, "--action", "propose", "--op", op)
        self.assertEqual(code, 2)
        self.assertIn("review app", out["errors"][0])


class AdoptTests(unittest.TestCase):
    def test_adopt_match_and_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "taxonomy")
            v1 = os.path.join(d, "taxonomy_v1.json")
            write_json(v1, taxonomy(version=1))
            db = os.path.join(td, "k.sqlite")
            tagged_store(db, taxonomy(version=1))
            bad = taxonomy(version=1)
            bad["intent_taxonomy"]["tree"]["Transform"].append("Extra")
            write_json(os.path.join(d, "taxonomy_bad.json"), bad)
            code, out = cli("adopt", "--taxonomy", os.path.join(d, "taxonomy_bad.json"), "--db", db)
            self.assertEqual((code, out["only_in_file"]), (2, ["extra"]))
            code, out = cli("adopt", "--taxonomy", v1, "--db", db)
            self.assertEqual((code, out["status"]), (0, "adopted"))
            self.assertEqual(open(os.path.join(d, "current.json"), "rb").read(), open(v1, "rb").read())
            self.assertEqual(GM.read_version(sqlite3.connect(db)), 1)


class CliPlanConflictTests(unittest.TestCase):
    def test_cli_plan_exits_2_on_conflicting_review(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = os.path.join(td.name, "taxonomy")
        v0 = os.path.join(d, "taxonomy_v0.json")
        write_json(v0, taxonomy())
        db1 = os.path.join(td.name, "k1.sqlite")
        tagged_store(db1, taxonomy())
        db2 = os.path.join(td.name, "k2.sqlite")
        tags2 = dict(TAGS)
        tags2[2] = ["Duplicate Charge"]
        tagged_store(db2, taxonomy(), tags=tags2)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW

        with unittest.mock.patch.object(R, "datetime", FixedDatetime):
            code, out = cli("plan", "--mode", "draft", "--taxonomy", v0, "--db", db1)
            self.assertEqual(code, 0)
            path = out["review"]
            raw1 = open(path, "rb").read()
            code, out = cli("plan", "--mode", "draft", "--taxonomy", v0, "--db", db2)
            self.assertEqual(code, 2)
            self.assertEqual(out["status"], "error")
            self.assertIn("immutable", out["errors"][0])
            self.assertEqual(open(path, "rb").read(), raw1)   # untouched


class GapCliTests(unittest.TestCase):
    def test_gap_writes_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            cur = os.path.join(td, "taxonomy", "current.json")
            write_json(cur, taxonomy(version=1))
            code, out = cli("gap", "--taxonomy", cur)
            self.assertEqual(code, 0)
            self.assertIn("| Porch Rate |", open(out["out"]).read())


class AdoptMetaOnlyTests(unittest.TestCase):
    def test_meta_only_sets_meta_and_leaves_current_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "taxonomy")
            v1 = os.path.join(d, "taxonomy_v1.json")
            write_json(v1, taxonomy(version=1))
            cur = os.path.join(d, "current.json")
            ahead = taxonomy(version=2)
            ahead["intent_taxonomy"]["tree"]["Transform"].append("Roadmap")
            write_json(cur, ahead)
            cur_bytes = open(cur, "rb").read()
            db = os.path.join(td, "k.sqlite")
            tagged_store(db, taxonomy(version=1))
            code, out = cli("adopt", "--taxonomy", v1, "--db", db)       # without --meta-only: refused
            self.assertEqual(code, 2)
            self.assertIn("--meta-only", out["reason"])
            self.assertIsNone(GM.read_version(sqlite3.connect(db)))
            code, out = cli("adopt", "--taxonomy", v1, "--db", db, "--meta-only")
            self.assertEqual((code, out["status"], out["meta_only"]), (0, "adopted", True))
            self.assertEqual(GM.read_version(sqlite3.connect(db)), 1)
            self.assertEqual(open(cur, "rb").read(), cur_bytes)

    def test_meta_only_still_checks_the_node_set(self):
        with tempfile.TemporaryDirectory() as td:
            bad = taxonomy(version=1)
            bad["intent_taxonomy"]["tree"]["Transform"].append("Extra")
            path = os.path.join(td, "taxonomy", "taxonomy_v1.json")
            write_json(path, bad)
            db = os.path.join(td, "k.sqlite")
            tagged_store(db, taxonomy(version=1))
            code, out = cli("adopt", "--taxonomy", path, "--db", db, "--meta-only")
            self.assertEqual((code, out["only_in_file"]), (2, ["extra"]))
            self.assertIsNone(GM.read_version(sqlite3.connect(db)))


class DescribeFlowTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        tax = taxonomy(version=1); tax["descriptions"] = {"Refunds": "Money back."}
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, tax)

    def tearDown(self):
        self.td.cleanup()

    def test_describe_prep_skips_described_nodes(self):
        out = os.path.join(self.td.name, "dp")
        res = R.describe_prep(self.cur, self.db, out, batches=2)
        nodes = [e["node"] for k in range(res["batches"]) for e in json.load(open(os.path.join(out, f"batch_{k}.json")))]
        self.assertNotIn("Refunds", nodes)
        self.assertIn("Duplicate Charge", nodes)
        dup = next(e for k in range(res["batches"]) for e in json.load(open(os.path.join(out, f"batch_{k}.json")))
                   if e["node"] == "Duplicate Charge")
        self.assertEqual(dup["parent"], "Billing & Payments")
        self.assertIn("Refunds", dup["siblings"])
        self.assertTrue(dup["samples"])
        self.assertTrue(os.path.exists(os.path.join(out, "instructions.md")))

    def test_plan_describe_items_and_context(self):
        props = os.path.join(self.td.name, "props")
        write_json(os.path.join(props, "result_0.json"), {"descriptions": [
            {"node": "Duplicate Charge", "description": "Customer billed twice for one order."},
            {"node": "Nope", "description": "x"}]})
        _, rv = R.build_plan("describe", self.cur, proposals_dir=props, db=self.db, now=NOW)
        st = {i["op"]["node"]: i["status"] for i in rv["items"]}
        self.assertEqual(st, {"Duplicate Charge": "proposed", "Nope": "invalid"})
        self.assertEqual(rv["items"][0]["origin"], "describe")
        self.assertEqual(rv["context"]["title"], "Description review")

    def test_context_for_browse(self):
        _, rv = R.build_plan("browse", self.cur, now=NOW)
        self.assertEqual(rv["context"]["title"], "Taxonomy editor")
        self.assertIn("without a description", rv["context"]["subtitle"])
