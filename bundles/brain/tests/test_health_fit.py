import json, os, sqlite3, tempfile, unittest

import health as H
from taxo_fixtures import tagged_store, taxonomy, write_json

# Fixture tags (taxo_fixtures.TAGS): Duplicate Charge 1, Refunds 2, Track Delivery 1, Proof of Delivery 1,
# Billing & Payments Admin 2, Transform 1. Every L2 with 1-2 tags is "sparse" at sparse_max=2.


class FitDetectTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.cur = os.path.join(self.dir, "current.json")
        write_json(self.cur, taxonomy(version=1))
        self.db = os.path.join(self.td.name, "k.sqlite")
        tagged_store(self.db, taxonomy(version=1))
        self.work = os.path.join(self.dir, "work", "health")

    def tearDown(self):
        self.td.cleanup()

    def detect(self, **kw):
        problems, *_rest = H.detect(self.cur, self.db, **kw)
        _rest[2] and _rest[2].close()
        return problems

    def test_sparse_lists_l2s_with_one_or_two_tags(self):
        p = self.detect(sparse_max=2)
        self.assertEqual(sorted(x["node"] for x in p["sparse"]),
                         ["Duplicate Charge", "Proof of Delivery", "Refunds", "Track Delivery"])
        refunds = next(x for x in p["sparse"] if x["node"] == "Refunds")
        self.assertEqual((refunds["parent"], refunds["tags"]), ("Billing & Payments", 2))
        self.assertIn({"node": "Duplicate Charge", "tags": 1}, refunds["siblings"])

    def test_overloaded_l1_by_factor_over_median(self):
        p = self.detect(overload_factor=1.2)
        self.assertEqual([x["node"] for x in p["overloaded"]], ["Billing & Payments"])
        self.assertEqual(self.detect(overload_factor=10)["overloaded"], [])

    def test_signals_file_drives_near_duplicates_and_misplaced(self):
        sig = os.path.join(self.dir, "work", "signals.json")
        write_json(sig, {"schema": 1, "label_pairs": [],
                         "label_clusters": [{"members": ["Refunds", "Duplicate Charge"], "level": "L2",
                                             "parent": "Billing & Payments"},
                                            {"members": ["Gone", "Refunds"], "level": "L2", "parent": None}],
                         "misplaced": [{"node": "Refunds", "parent": "Billing & Payments",
                                        "better_parent": "Delivery & Pickup", "margin": 0.05, "chunks": 3},
                                       {"node": "Gone", "parent": "X", "better_parent": "Y", "margin": 0.2, "chunks": 9}]})
        p = self.detect(signals_path=sig)
        self.assertEqual(p["near_duplicate_source"], "label-embedding")
        self.assertEqual([sorted(x["members"]) for x in p["near_duplicate"]], [["Duplicate Charge", "Refunds"]])
        self.assertEqual([x["node"] for x in p["misplaced"]], ["Refunds"])       # "Gone" is not in the tree

    def test_without_signals_string_detector_runs(self):
        p = self.detect()
        self.assertEqual(p["near_duplicate_source"], "string")
        self.assertEqual(p["misplaced"], [])

    def test_untagged_count_excludes_no_topic(self):
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE chunk_verdicts(chunk_id INTEGER PRIMARY KEY, verdict TEXT, taxonomy_version INT)")
        c.execute("INSERT INTO chunk_verdicts VALUES(8,'no_topic',1)")
        c.commit(); c.close()
        u = self.detect()["untagged_sections"][0]
        self.assertEqual((u["count"], u["no_topic"]), (0, 1))

    def test_diagnose_writes_fit_task(self):
        res = H.diagnose(self.cur, self.db, self.work, sparse_max=2, overload_factor=1.2)
        fit = next(t for t in res["tasks"] if t["kind"] == "fit")
        entries = [e for f in sorted(os.listdir(fit["dir"])) if f.startswith("batch_")
                   for e in json.load(open(os.path.join(fit["dir"], f)))]
        self.assertEqual({e["kind"] for e in entries}, {"sparse", "overloaded"})
        self.assertIn("sparse", open(os.path.join(fit["dir"], "instructions.md")).read())
        self.assertEqual(res["near_duplicate_source"], "string")
        self.assertTrue(all(isinstance(v, int) for v in res["problems"].values()))
        self.assertNotIn("near_duplicate_source", res["problems"])
