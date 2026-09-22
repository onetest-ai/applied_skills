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


class FitItemsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.work = self.td.name
        self.tax = taxonomy(version=1)
        problems = {"sparse": [{"node": "Refunds", "level": "L2", "parent": "Billing & Payments", "tags": 2,
                                "siblings": [{"node": "Duplicate Charge", "tags": 1}]},
                               {"node": "Track Delivery", "level": "L2", "parent": "Delivery & Pickup", "tags": 1,
                                "siblings": [{"node": "Proof of Delivery", "tags": 1}]}],
                    "misplaced": [{"node": "Proof of Delivery", "parent": "Delivery & Pickup",
                                   "better_parent": "Billing & Payments", "margin": 0.05, "tags": 1}],
                    "overloaded": [{"node": "Billing & Payments", "tags": 9, "l2_count": 2, "median_tags": 2,
                                    "median_l2": 1, "children": [{"node": "Refunds", "tags": 2}]}]}
        write_json(os.path.join(self.work, "problems.json"), problems)
        write_json(os.path.join(self.work, "fit", "result_0.json"), {"fixes": [
            {"kind": "sparse", "subject": "Refunds", "fix": "merge", "into": "Duplicate Charge", "reason": "same"},
            {"kind": "misplaced", "subject": "Proof of Delivery", "fix": "move", "new_parent": "Billing & Payments",
             "reason": "billing"},
            {"kind": "overloaded", "subject": "Billing & Payments", "fix": "restructure",
             "add": [{"name": "Refunds & Credits", "description": "Money back."}],
             "moves": [{"node": "Refunds", "new_parent": "Delivery & Pickup"}], "reason": "split"}]})

    def tearDown(self):
        self.td.cleanup()

    def items(self):
        problems = json.load(open(os.path.join(self.work, "problems.json")))
        return H._fit_items(self.tax, self.work, problems, [], {})

    def test_sparse_merge_and_fallback(self):
        it = [i for i in self.items() if i["kind"] == "sparse"]
        refunds = next(i for i in it if i["op"].get("from") == "Refunds" or i["op"].get("node") == "Refunds")
        self.assertEqual(refunds["op"], {"type": "merge", "from": "Refunds", "into": "Duplicate Charge"})
        self.assertEqual(refunds["group"], "sparse")
        track = next(i for i in it if i["op"].get("node") == "Track Delivery")
        self.assertEqual(track["op"]["type"], "keep")
        self.assertTrue(track["_fallback"])
        self.assertIn({"type": "merge", "from": "Track Delivery", "into": "Proof of Delivery"}, track["alternatives"])

    def test_misplaced_move(self):
        m = next(i for i in self.items() if i["kind"] == "misplaced")
        self.assertEqual(m["op"], {"type": "move", "node": "Proof of Delivery", "new_parent": "Billing & Payments"})
        self.assertEqual(m["status"], "proposed")

    def test_overloaded_restructure_becomes_add_and_move_items(self):
        rows = [i for i in self.items() if i["kind"] == "overloaded"]
        self.assertEqual({i["group"] for i in rows}, {"overloaded:Billing & Payments"})
        self.assertIn({"type": "add", "level": "L1", "name": "Refunds & Credits", "parent": None,
                       "description": "Money back."}, [i["op"] for i in rows])
        self.assertIn({"type": "move", "node": "Refunds", "new_parent": "Delivery & Pickup"}, [i["op"] for i in rows])

    def test_invalid_target_is_kept_as_invalid_item(self):
        write_json(os.path.join(self.work, "fit", "result_0.json"), {"fixes": [
            {"kind": "misplaced", "subject": "Proof of Delivery", "fix": "move", "new_parent": "Nope"}]})
        m = next(i for i in self.items() if i["kind"] == "misplaced")
        self.assertEqual(m["status"], "invalid")

    def test_amend_to_alternative_is_allowed(self):
        import decisions as D
        m = next(i for i in self.items() if i["kind"] == "misplaced")
        self.assertTrue(D.health_amend_ok(m, {"type": "keep", "node": "Proof of Delivery"}))
