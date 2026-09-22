"""First build, count-grounded: draft v0 → build_graph → adopt --provisional → classify (with a
no-topic verdict) → signals.json (hand-made: no model download) → diagnose → agent fixes →
plan --mode health → decisions → submit → apply (marker cleared, v1) → build_graph migrates tags."""
import json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

import decisions as D
from taxo_fixtures import tag_rows, taxonomy, write_json

CTE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"


def run(script, *args):
    r = subprocess.run([sys.executable, str(CTE / script), *args], text=True, capture_output=True)
    assert r.returncode == 0, f"{script}: {r.stdout}\n{r.stderr}"
    return r


def last_json(r):
    return json.loads(r.stdout.strip().splitlines()[-1])


class FirstBuildE2E(unittest.TestCase):
    def test_first_build(self):
        with tempfile.TemporaryDirectory() as td:
            tdir = os.path.join(td, "taxonomy")
            v0 = os.path.join(tdir, "taxonomy_v0.json")
            tax = taxonomy()                                            # draft: no version
            tax["intent_taxonomy"]["tree"]["Billing & Payments"].append("Payment Processing")
            write_json(v0, tax)
            db = os.path.join(td, "k.sqlite")
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, title TEXT, text TEXT)")
            for i in range(1, 8):
                c.execute("INSERT INTO chunks VALUES(?,?,?,?)", (i, f"d{i}.md", "t", f"text {i}"))
            c.commit(); c.close()

            run("build_graph.py", "--taxonomy", v0, "--db", db)
            self.assertEqual(last_json(run("taxonomy_review.py", "adopt", "--taxonomy", v0, "--db", db,
                                           "--provisional"))["provisional"], True)
            cur = os.path.join(tdir, "current.json")

            cls = os.path.join(td, "cls"); os.makedirs(cls)
            json.dump({"1": ["Refunds"], "2": ["Refunds"], "3": ["Payment Processing"], "4": ["Duplicate Charge"],
                       "5": ["Track Delivery"], "6": ["__no_topic__"], "7": []},
                      open(os.path.join(cls, "result_0.json"), "w"))
            run("classify_write.py", "--db", db, "--results", cls)

            work = os.path.join(tdir, "work", "health")
            write_json(os.path.join(tdir, "work", "signals.json"), {"schema": 1, "label_pairs": [], "misplaced": [],
                       "label_clusters": [{"members": ["Payment Processing", "Refunds"], "level": "L2",
                                           "parent": "Billing & Payments"}]})
            diag = last_json(run("taxonomy_review.py", "diagnose", "--taxonomy", cur, "--db", db, "--out", work))
            self.assertGreaterEqual(diag["problems"]["sparse"], 1)
            problems = json.load(open(os.path.join(work, "problems.json")))
            self.assertEqual(problems["untagged_sections"][0]["count"], 1)       # chunk 7; chunk 6 is no-topic
            self.assertEqual(problems["near_duplicate_source"], "label-embedding")

            write_json(os.path.join(work, "structure", "result_0.json"), {"fixes": [
                {"kind": "near_duplicate", "subject": "Payment Processing", "fix": "merge", "into": "Refunds",
                 "reason": "same"}]})
            plan = last_json(run("taxonomy_review.py", "plan", "--mode", "health", "--taxonomy", cur,
                                 "--db", db, "--work", work))
            review = json.load(open(plan["review"]))
            self.assertEqual(review["context"]["title"], "First-build review")
            merge = next(i for i in review["items"] if i["op"] == {"type": "merge", "from": "Payment Processing",
                                                                    "into": "Refunds"})
            dp = D.default_path(tdir)
            D.append(dp, {"review_id": review["review_id"], "action": "approve", "item_id": merge["id"],
                          "reviewer": "t", "surface": "browser"})
            D.append(dp, {"review_id": review["review_id"], "action": "submit", "reviewer": "t", "surface": "browser"})
            applied = last_json(run("taxonomy_merge.py", "--review", plan["review"], "--apply"))
            self.assertEqual(applied["version"], 1)
            self.assertFalse(os.path.exists(os.path.join(tdir, "PROVISIONAL")))

            run("build_graph.py", "--taxonomy", cur, "--db", db)
            self.assertNotIn("payment_processing", {cat for _, cat in tag_rows(db)})
            self.assertIn((3, "refunds"), tag_rows(db))


if __name__ == "__main__":
    unittest.main()
