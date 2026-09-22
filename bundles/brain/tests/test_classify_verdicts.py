import json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

from taxo_fixtures import tagged_store, taxonomy

CTE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"


def write(db, results_dir, results, *flags):
    os.makedirs(results_dir, exist_ok=True)
    for f in Path(results_dir).glob("result_*.json"):
        f.unlink()
    json.dump(results, open(os.path.join(results_dir, "result_0.json"), "w"))
    r = subprocess.run([sys.executable, str(CTE / "classify_write.py"), "--db", db, "--results", results_dir, *flags],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return r


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "k.sqlite")
        tagged_store(self.db, taxonomy())
        self.res = os.path.join(self.td.name, "cls")

    def tearDown(self):
        self.td.cleanup()

    def q(self, sql, *a):
        c = sqlite3.connect(self.db); r = c.execute(sql, a).fetchall(); c.close(); return r

    def test_sentinel_records_verdict_and_no_tags(self):
        write(self.db, self.res, {"1": ["__no_topic__"], "8": []})
        self.assertEqual(self.q("SELECT chunk_id, verdict FROM chunk_verdicts"), [(1, "no_topic")])
        self.assertEqual(self.q("SELECT COUNT(*) FROM chunk_topics WHERE chunk_id=1"), [(0,)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM chunk_verdicts WHERE chunk_id=8"), [(0,)])   # [] is not no-topic

    def test_a_later_tag_clears_the_verdict(self):
        write(self.db, self.res, {"1": ["__no_topic__"]})
        write(self.db, self.res, {"1": ["Refunds"]})
        self.assertEqual(self.q("SELECT COUNT(*) FROM chunk_verdicts"), [(0,)])
        self.assertEqual(self.q("SELECT category_id FROM chunk_topics WHERE chunk_id=1 ORDER BY 1"),
                         [("billing_payments",), ("refunds",)])

    def test_merge_tag_clears_the_verdict(self):
        write(self.db, self.res, {"8": ["__no_topic__"]})
        write(self.db, self.res, {"8": ["Refunds"]}, "--merge")
        self.assertEqual(self.q("SELECT COUNT(*) FROM chunk_verdicts"), [(0,)])

    def test_sentinel_mixed_with_labels_keeps_the_labels(self):
        write(self.db, self.res, {"1": ["__no_topic__", "Refunds"]})
        self.assertEqual(self.q("SELECT COUNT(*) FROM chunk_verdicts"), [(0,)])
        self.assertIn(("refunds",), self.q("SELECT category_id FROM chunk_topics WHERE chunk_id=1"))

    def test_refine_prep_skips_no_topic_chunks(self):
        write(self.db, self.res, {"8": ["__no_topic__"]})
        out = os.path.join(self.td.name, "refine")
        r = subprocess.run([sys.executable, str(CTE / "taxonomy_refine_prep.py"), "--db", self.db,
                            "--taxonomy", self._tax(), "--out", out],
                           text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        ids = [x["id"] for f in Path(out).glob("batch_*.json") for x in json.load(open(f))]
        self.assertNotIn(8, ids)

    def _tax(self):
        p = os.path.join(self.td.name, "tax.json"); json.dump(taxonomy(), open(p, "w")); return p

    def test_prep_instructions_teach_the_sentinel(self):
        out = os.path.join(self.td.name, "prep")
        subprocess.run([sys.executable, str(CTE / "classify_prep.py"), "--db", self.db, "--taxonomy", self._tax(),
                        "--out", out], check=True, capture_output=True)
        self.assertIn("__no_topic__", open(os.path.join(out, "instructions.md")).read())
