"""Whole-branch guards: malformed ops are refused (never a traceback/500), respond checks the
taxonomy, governed drafts append idempotently, classify_write refuses unsafe flag mixes and a
bad reclassify queue, the apply crash window, redo requests only on health
reviews, and tag proposals naming sections the store doesn't have."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import decisions as D
import health as H
import review_md as MD
import review_server as S
import taxo_io as IO
import taxo_ops
import taxonomy_merge as M
import taxonomy_review as R
from taxo_fixtures import tag_rows, tagged_store, taxonomy, write_json

CTE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)

MALFORMED = [
    {"type": "metric_govern", "metric": "Porch Rate", "draft": "x"},
    {"type": "describe", "node": "Refunds", "description": 5},
    {"type": "add", "level": "L1", "name": "New Thing", "description": 5},
    {"type": "merge", "from": ["A"], "into": "Refunds"},
]


def cli(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = R.main(list(argv))
    return code, json.loads(buf.getvalue().strip().splitlines()[-1])


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.cur = os.path.join(self.dir, "current.json")
        write_json(self.cur, taxonomy(version=1))
        self.db = os.path.join(self.td.name, "k.sqlite")
        tagged_store(self.db, taxonomy(version=1))
        self.dec = os.path.join(self.dir, "decisions.jsonl")

    def tearDown(self):
        self.td.cleanup()


class MalformedOpTests(Base):
    def test_validate_refuses_each_malformed_op(self):
        tax = taxonomy(version=1)
        for op in MALFORMED:
            errs = taxo_ops.validate(tax, [op])
            self.assertTrue(errs, op)
        self.assertTrue(taxo_ops.validate(tax, [{"type": ["merge"], "from": "Refunds", "into": "Transform"}]))

    def test_server_refuses_with_400_not_500(self):
        path, _ = R.build_plan("browse", self.cur, db=self.db, now=NOW)
        app = S.ReviewApp(path, db_path=self.db, reviewer="Pat")
        for op in MALFORMED + [5, ["x"]]:
            code, out = app.decide({"action": "propose", "op": op})
            self.assertEqual(code, 400, (op, out))

    def test_record_and_import_md_refuse_cleanly(self):
        path, _ = R.build_plan("browse", self.cur, db=self.db, now=NOW)
        for op in MALFORMED + [5]:
            code, out = cli("record", "--review", path, "--action", "propose", "--op", json.dumps(op))
            self.assertEqual((code, out["status"]), (2, "refused"), op)
        item = {"id": "i-1", "origin": "health", "op": {"type": "keep", "node": "Refunds"}, "fingerprint": "f"}
        for bad in ("amend: 5", 'amend: ["x"]'):
            with self.assertRaises(ValueError):
                MD.parse_decision(bad, item)


class HealthRespondTests(Base):
    def setUp(self):
        super().setUp()
        self.work = os.path.join(self.dir, "work", "health")
        H.diagnose(self.cur, self.db, self.work)
        self.path, self.rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db, now=NOW)

    def _req(self, item):
        with open(os.path.join(self.dir, "work", "requests.jsonl"), "a") as f:
            f.write(json.dumps({"id": "q-1", "item_id": item["id"], "note": "n", "status": "open"}) + "\n")

    def test_respond_refuses_an_empty_template(self):
        item = next(i for i in self.rv["items"] if i["kind"] == "missing_description")
        self._req(item)
        template = item["alternatives"][0]
        self.assertEqual(template["description"], "")
        for extra in (["--check"], []):
            code, out = cli("respond", "--review", self.path, "--request", "q-1", "--op", json.dumps(template), *extra)
            self.assertEqual((code, out["status"]), (2, "refused"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "work", "responses.jsonl")))
        good = dict(template, description="Money back to the customer.")
        code, out = cli("respond", "--review", self.path, "--request", "q-1", "--op", json.dumps(good), "--check")
        self.assertEqual((code, out["status"]), (0, "ok"))

    def test_respond_refuses_a_conflict_with_an_approved_op(self):
        pair = next(i for i in self.rv["items"] if i["kind"] == "near_duplicate")
        merge = next(c for c in [pair["op"]] + pair["alternatives"]
                     if c["type"] == "merge" and c["from"] == "Billing & Payments Admin")
        D.append(self.dec, {"review_id": self.rv["review_id"], "item_id": pair["id"], "action": "amend",
                            "op": merge, "reviewer": "Pat", "surface": "browser"})
        desc = next(i for i in self.rv["items"] if i["kind"] == "missing_description"
                    and i["op"]["node"] == "Billing & Payments Admin")
        self._req(desc)
        op = dict(desc["alternatives"][0], description="Admin side of billing.")
        code, out = cli("respond", "--review", self.path, "--request", "q-1", "--op", json.dumps(op), "--check")
        self.assertEqual((code, out["status"]), (2, "refused"))
        self.assertIn("changed twice", out["errors"][0])

    def test_redo_request_refused_on_a_non_health_review(self):
        path, rv = R.build_plan("browse", self.cur, db=self.db,
                                now=datetime(2026, 9, 22, tzinfo=timezone.utc))
        write_json(path, dict(json.load(open(path)), items=[dict(self.rv["items"][0])]))
        code, out = S.ReviewApp(path, db_path=self.db, reviewer="Pat").request(
            {"item_id": self.rv["items"][0]["id"], "note": "x"})
        self.assertEqual(code, 400)
        self.assertIn("health", out["errors"][0])


class GovernedDraftsTests(Base):
    def test_retry_is_idempotent_and_corrupt_file_is_refused(self):
        ops = [{"type": "metric_govern", "metric": "Porch Rate", "draft": {"key": "porch_rate"}}]
        _, f = M._write_side_outputs(self.dir, "r-1", ops)
        M._write_side_outputs(self.dir, "r-1", ops)
        M._write_side_outputs(self.dir, "r-2", ops)
        drafts = json.load(open(f))
        self.assertEqual([d["review_id"] for d in drafts], ["r-1", "r-2"])
        for bad in ("{not json", '{"a": 1}'):
            open(f, "w").write(bad)
            with self.assertRaises(M.Refused):
                M._write_side_outputs(self.dir, "r-3", ops)


class ClassifyWriteTests(Base):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(CTE / "classify_write.py"), "--db", self.db, *args],
                              capture_output=True, text=True)

    def test_merge_with_reclassify_done_is_refused(self):
        r = self._run("--results", self.td.name, "--merge", "--reclassify-done", os.path.join(self.td.name, "q.json"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("mutually exclusive", r.stderr)

    def test_bad_queue_is_refused_before_any_write(self):
        res = os.path.join(self.td.name, "res")
        write_json(os.path.join(res, "result_0.json"), {"8": ["Refunds"]})
        q = os.path.join(self.td.name, "q.json")
        open(q, "w").write('{"chunk_ids": [8]}')          # no "reasons"
        before = tag_rows(self.db)
        r = self._run("--results", res, "--reclassify-done", q)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(tag_rows(self.db), before)
        self.assertTrue(os.path.exists(q))


class CrashWindowTests(Base):
    def test_retry_after_a_crash_before_the_applied_record_recovers(self):
        rid = "r-crash"
        review_path = os.path.join(self.dir, "reviews", f"review_{rid}.json")
        write_json(review_path, {"schema": 1, "review_id": rid, "mode": "browse", "items": [],
                                 "base": {"path": self.cur, "version": 1, "sha256": IO.sha256_file(self.cur)}})
        D.append(self.dec, {"review_id": rid, "item_id": D.new_human_id(), "action": "propose", "reviewer": "Pat",
                            "surface": "browser", "op": {"type": "rename", "node": "Refunds", "new_name": "Refund Asks"}})
        D.append(self.dec, {"review_id": rid, "action": "submit", "reviewer": "Pat", "surface": "browser"})
        lines = open(self.dec).read().splitlines()
        self.assertEqual(M.apply_review(review_path)["version"], 2)
        open(self.dec, "w").write("\n".join(lines) + "\n")    # simulate: crashed before logging `applied`
        res = M.apply_review(review_path)
        self.assertEqual((res["status"], res["version"], res.get("recovered")), ("applied", 2, True))
        st = D.review_state(D.read(self.dec), rid)
        self.assertEqual(st["applied"]["version"], 2)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "taxonomy_v3.json")))
        with self.assertRaises(M.Refused):
            M.apply_review(review_path)                        # now genuinely applied


class TagProposalChunkTests(Base):
    def test_tag_proposal_naming_a_missing_section_is_refused(self):
        path, _ = R.build_plan("browse", self.cur, db=self.db, now=NOW)
        app = S.ReviewApp(path, db_path=self.db, reviewer="Pat")
        code, out = app.decide({"action": "propose", "op": {"type": "tag", "node": "Refunds", "chunk_ids": [8, 999]}})
        self.assertEqual(code, 400)
        self.assertIn("999", out["errors"][0])
        code, out = app.decide({"action": "propose", "op": {"type": "tag", "node": "Refunds", "chunk_ids": [8]}})
        self.assertEqual(code, 200, out)


if __name__ == "__main__":
    unittest.main()
