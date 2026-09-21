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


if __name__ == "__main__":
    unittest.main()
