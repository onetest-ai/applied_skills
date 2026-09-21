import json, os, sqlite3, tempfile, unittest

import health as H
import taxonomy_review as R
from taxo_fixtures import tagged_store, taxonomy, write_json


def add_fts(db):
    c = sqlite3.connect(db)
    c.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(text)")
    c.executemany("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)",
                  [(r[0], r[1]) for r in c.execute("SELECT id, text FROM chunks")])
    c.execute("INSERT INTO chunks VALUES(9,'doc9.md','Payment plans','customer asks for payment plans to split the bill')")
    c.execute("INSERT INTO chunks_fts(rowid, text) VALUES(9,'customer asks for payment plans to split the bill')")
    c.commit(); c.close()


class DiagnoseTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        tax = taxonomy(version=1); tax["descriptions"] = {"Refunds": "Money back."}
        tax["intent_taxonomy"]["tree"]["Billing & Payments"].append("Payment Plans")   # 0 tags
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, tax); add_fts(self.db)
        self.out = os.path.join(self.dir, "work", "health")

    def tearDown(self):
        self.td.cleanup()

    def test_problems_and_tasks(self):
        res = H.diagnose(self.cur, self.db, self.out)
        p = json.load(open(os.path.join(self.out, "problems.json")))
        self.assertIn("Payment Plans", [x["node"] for x in p["no_tags"]])
        self.assertNotIn("Refunds", [x["node"] for x in p["missing_description"]])
        self.assertIn({"a": "Billing & Payments", "b": "Billing & Payments Admin"},
                      [{"a": x["a"], "b": x["b"]} for x in p["near_duplicate"]])
        self.assertIn("Transform", [x["node"] for x in p["off_axis"]])
        self.assertEqual(p["untagged_sections"][0]["count"], 2)          # chunks 8 and 9
        self.assertIn(("Avg Handle Time", "Average Handle Time"),
                      [tuple(sorted((x["a"], x["b"]), reverse=True)) for x in p["similar_metrics"]])
        kinds = {t["kind"] for t in res["tasks"]}
        self.assertTrue({"describe", "notags", "structure", "metrics", "untagged"} <= kinds)
        for t in res["tasks"]:
            self.assertTrue(os.path.exists(os.path.join(t["dir"], "instructions.md")))

    def test_notags_candidates_exclude_already_tagged(self):
        H.diagnose(self.cur, self.db, self.out)
        p = json.load(open(os.path.join(self.out, "problems.json")))
        tr = [x for x in p["no_tags"] if x["node"] == "Transform"]
        self.assertEqual(tr, [])                                          # Transform has 1 tag → not no_tags

    def test_open_request_note_attaches_via_review_file(self):
        review_id = "r-test"
        item = {"id": "i-abc123", "kind": "no_tags",
                "op": {"type": "tag", "node": "Payment Plans", "chunk_ids": []},
                "fingerprint": "tag|payment plans|"}
        write_json(os.path.join(self.dir, "reviews", f"review_{review_id}.json"),
                   {"review_id": review_id, "items": [item]})
        requests_path = os.path.join(self.dir, "work", "requests.jsonl")
        os.makedirs(os.path.dirname(requests_path), exist_ok=True)
        with open(requests_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "req-1", "ts": "t", "review_id": review_id, "item_id": "i-abc123",
                                "note": "please retry with more candidates", "status": "open"}) + "\n")
            # a request whose review file is missing must be skipped, not crash
            f.write(json.dumps({"id": "req-2", "ts": "t", "review_id": "r-missing", "item_id": "i-xxx",
                                "note": "n/a", "status": "open"}) + "\n")

        res = H.diagnose(self.cur, self.db, self.out)
        p = json.load(open(os.path.join(self.out, "problems.json")))
        self.assertIn({"request_id": "req-1", "review_id": review_id, "item_id": "i-abc123",
                       "subject": "Payment Plans", "kind": "no_tags",
                       "fingerprint": "tag|payment plans|", "note": "please retry with more candidates"},
                      p["open_requests"])

        notags_dir = next(t["dir"] for t in res["tasks"] if t["kind"] == "notags")
        batch = json.load(open(os.path.join(notags_dir, "batch_0.json")))
        entry = next(x for x in batch if x["node"] == "Payment Plans")
        self.assertEqual(entry.get("note"), "please retry with more candidates")

    def test_cli_prints_one_json_line(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = R.main(["diagnose", "--taxonomy", self.cur, "--db", self.db, "--out", self.out])
        self.assertEqual(code, 0)
        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("problems", json.loads(lines[0]))


class HealthPlanTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        tax = taxonomy(version=1)
        tax["intent_taxonomy"]["tree"]["Billing & Payments"].append("Payment Plans")
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, tax); add_fts(self.db)
        self.work = os.path.join(self.dir, "work", "health")
        H.diagnose(self.cur, self.db, self.work)
        write_json(os.path.join(self.work, "describe", "result_0.json"),
                   {"descriptions": [{"node": "Duplicate Charge", "description": "Billed twice."}]})
        write_json(os.path.join(self.work, "notags", "result_0.json"),
                   {"fixes": [{"node": "Payment Plans", "fix": "tag", "chunk_ids": [9, 999], "reason": "fits"}]})
        write_json(os.path.join(self.work, "structure", "result_0.json"), {"fixes": [
            {"kind": "near_duplicate", "subject": "Billing & Payments Admin", "fix": "merge",
             "into": "Billing & Payments", "reason": "same thing"}]})
        write_json(os.path.join(self.work, "metrics", "result_0.json"), "not a dict")

    def tearDown(self):
        self.td.cleanup()

    def test_health_items_grouping_and_fallbacks(self):
        from datetime import datetime, timezone
        _, rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                             now=datetime(2026, 9, 21, tzinfo=timezone.utc))
        by = {}
        for i in rv["items"]:
            by.setdefault(i["kind"], []).append(i)
        self.assertEqual(by["missing_description"][0]["group"], "missing_description")
        tag = next(i for i in by["no_tags"] if i["op"]["node"] == "Payment Plans")
        self.assertEqual(tag["op"], {"type": "tag", "node": "Payment Plans", "chunk_ids": [9]})   # 999 not a candidate
        self.assertTrue(any(a["type"] == "remove" for a in tag["alternatives"]))
        nd = by["near_duplicate"][0]
        self.assertIsNone(nd["group"]); self.assertEqual(nd["op"]["type"], "merge")
        self.assertEqual(by["off_axis"][0]["op"]["type"], "keep")                                # no result → keep
        self.assertIn(os.path.join(self.work, "metrics", "result_0.json"), rv["skipped_files"])
        self.assertEqual(rv["context"]["title"], "Health review")

    def test_respond_records_revision(self):
        from datetime import datetime, timezone
        path, rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                                now=datetime(2026, 9, 21, tzinfo=timezone.utc))
        item = next(i for i in rv["items"] if i["kind"] == "no_tags")
        req = os.path.join(self.dir, "work", "requests.jsonl")
        with open(req, "a") as f:
            f.write(json.dumps({"id": "q-1", "item_id": item["id"], "note": "not these", "status": "open"}) + "\n")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = R.main(["respond", "--review", path, "--request", "q-1", "--op",
                           json.dumps(item["alternatives"][0]), "--reason", "merge instead"])
        self.assertEqual(code, 0, buf.getvalue())
        resp = [json.loads(l) for l in open(os.path.join(self.dir, "work", "responses.jsonl"))]
        self.assertEqual(resp[-1]["item_id"], item["id"])
        with redirect_stdout(io.StringIO()):
            bad = R.main(["respond", "--review", path, "--request", "q-1", "--op",
                          json.dumps({"type": "move", "node": "Refunds", "new_parent": "Transform"})])
        self.assertEqual(bad, 2)


if __name__ == "__main__":
    unittest.main()
