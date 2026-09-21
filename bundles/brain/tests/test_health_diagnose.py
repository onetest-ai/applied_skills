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
        # M-4: a near_duplicate merge item also carries the reverse-direction merge, plus keep
        alt_types = sorted(a["type"] for a in nd["alternatives"])
        self.assertEqual(alt_types, ["keep", "merge"])
        reverse = next(a for a in nd["alternatives"] if a["type"] == "merge")
        self.assertEqual(reverse, {"type": "merge", "from": nd["op"]["into"], "into": nd["op"]["from"]})
        self.assertEqual(by["off_axis"][0]["op"]["type"], "keep")                                # no result → keep
        self.assertIn(os.path.join(self.work, "metrics", "result_0.json"), rv["skipped_files"])
        self.assertEqual(rv["context"]["title"], "Health review")
        # no item ever carries the internal "_fallback" bookkeeping flag once persisted
        self.assertTrue(all("_fallback" not in i for i in rv["items"]))
        # M-2: "fixes proposed" counts only the real agent fixes — missing_description (1 real
        # draft out of 9 labels), no_tags (1 real tag), near_duplicate (1 real merge) — and
        # excludes the 8 missing_description fallbacks, the off_axis/similar_metrics fallback
        # keeps, and the untagged_sections informational placeholder.
        self.assertEqual(rv["context"]["subtitle"], "v1 · 14 problems · 3 fixes proposed")

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

    def test_respond_requires_the_request_to_be_open(self):
        from datetime import datetime, timezone
        path, rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                                now=datetime(2026, 9, 21, tzinfo=timezone.utc))
        item = next(i for i in rv["items"] if i["kind"] == "no_tags")
        req = os.path.join(self.dir, "work", "requests.jsonl")
        with open(req, "a") as f:
            f.write(json.dumps({"id": "q-3", "item_id": item["id"], "note": "n", "status": "open"}) + "\n")
            f.write(json.dumps({"id": "q-3", "item_id": item["id"], "status": "closed"}) + "\n")   # latest wins
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            code = R.main(["respond", "--review", path, "--request", "q-3", "--op",
                          json.dumps(item["alternatives"][0])])
        self.assertEqual(code, 2)

    def test_respond_missing_review_file_is_refused(self):
        import io, json as _json
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = R.main(["respond", "--review", os.path.join(self.dir, "reviews", "review_missing.json"),
                          "--request", "q-1", "--op", '{"type": "keep", "node": "x"}'])
        self.assertEqual(code, 2)
        self.assertEqual(_json.loads(buf.getvalue().strip())["status"], "refused")


class HealthEveryProblemInboxTests(unittest.TestCase):
    """I-1: the binding product rule — every problem `diagnose` detected reaches the inbox,
    with a Claude fix when one is usable, else a manual/template fallback. Nothing vanishes."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.tax = taxonomy(version=1)
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, self.tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, self.tax); add_fts(self.db)
        schema_dir = os.path.join(self.td.name, "schema")
        write_json(os.path.join(schema_dir, "metrics.acme.json"), {"metrics": {}})   # nothing governed
        self.work = os.path.join(self.dir, "work", "health")
        H.diagnose(self.cur, self.db, self.work)

    def tearDown(self):
        self.td.cleanup()

    def _plan(self):
        from datetime import datetime, timezone
        return R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                            now=datetime(2026, 9, 21, tzinfo=timezone.utc))

    def test_missing_description_without_a_draft_still_gets_an_item(self):
        _, rv = self._plan()   # no describe/result_*.json at all
        items = [i for i in rv["items"] if i["kind"] == "missing_description"]
        self.assertTrue(items)
        self.assertTrue(all(i["op"] == {"type": "keep", "node": i["op"]["node"]} for i in items))
        self.assertTrue(all(i["alternatives"] == [{"type": "describe", "node": i["op"]["node"],
                                                    "description": ""}] for i in items))
        self.assertTrue(all(i["reason"] == "no fix proposed" for i in items))

    def test_similar_metrics_without_a_fix_still_gets_an_item(self):
        _, rv = self._plan()   # no metrics/result_*.json at all
        items = [i for i in rv["items"] if i["kind"] == "similar_metrics"]
        self.assertTrue(items)
        self.assertEqual(items[0]["op"]["type"], "keep")
        self.assertEqual(items[0]["reason"], "no fix proposed")
        self.assertEqual({a["type"] for a in items[0]["alternatives"]}, {"metric_merge"})
        self.assertEqual(len(items[0]["alternatives"]), 2)   # both directions

    def test_metric_not_governed_without_a_fix_still_gets_an_item(self):
        _, rv = self._plan()
        items = [i for i in rv["items"] if i["kind"] == "metric_not_governed"]
        self.assertTrue(items)   # Average Handle Time and Porch Rate are both computable/ungoverned
        for i in items:
            self.assertEqual(i["op"]["type"], "keep")
            self.assertEqual(i["reason"], "no fix proposed")
            self.assertEqual(i["alternatives"][0]["type"], "metric_govern")

    def test_metric_not_governed_explicit_agent_keep_keeps_its_reason_and_template(self):
        write_json(os.path.join(self.work, "metrics", "result_0.json"),
                   {"fixes": [{"kind": "metric_not_governed", "subject": "Porch Rate", "fix": "keep",
                               "reason": "not worth governing yet"}]})
        _, rv = self._plan()
        item = next(i for i in rv["items"]
                    if i["kind"] == "metric_not_governed" and i["op"].get("metric") == "Porch Rate")
        self.assertEqual(item["op"], {"type": "keep", "metric": "Porch Rate"})
        self.assertEqual(item["reason"], "not worth governing yet")
        self.assertEqual(item["alternatives"], [{"type": "metric_govern", "metric": "Porch Rate", "draft": {}}])

    def test_untagged_sections_without_usable_results_gets_one_informational_item(self):
        _, rv = self._plan()   # untagged/ task dir exists (chunk 8) but no result files
        items = [i for i in rv["items"] if i["kind"] == "untagged_sections"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["op"], {"type": "keep", "node": "__untagged__"})
        self.assertEqual(items[0]["reason"], "no fix proposed")
        self.assertEqual(items[0]["group"], "untagged_sections")


class HealthFieldGuardsTests(unittest.TestCase):
    """I-3: chunk_ids/example_ids keep only real ints; node/subject/into/description must be
    strings. A malformed entry is dropped and counted (stderr), never crashes planning."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        tax = taxonomy(version=1)
        tax["intent_taxonomy"]["tree"]["Billing & Payments"].append("Payment Plans")
        tax["intent_taxonomy"]["tree"]["Billing & Payments"].append("Autopay Setup")
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, tax); add_fts(self.db)
        self.work = os.path.join(self.dir, "work", "health")
        H.diagnose(self.cur, self.db, self.work)

    def tearDown(self):
        self.td.cleanup()

    def test_malformed_entries_are_dropped_and_counted_never_crash(self):
        write_json(os.path.join(self.work, "notags", "result_0.json"), {"fixes": [
            {"node": 12345, "fix": "tag", "chunk_ids": [9]},                              # node not a string
            {"node": "Payment Plans", "fix": "tag", "chunk_ids": [9, "nine", True, 999], "reason": "ok"},
            "not a dict",
        ]})
        from datetime import datetime, timezone
        _, rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                             now=datetime(2026, 9, 21, tzinfo=timezone.utc))
        pp = next(i for i in rv["items"] if i["kind"] == "no_tags" and i["op"].get("node") == "Payment Plans")
        self.assertEqual(pp["op"]["chunk_ids"], [9])          # "nine"/True dropped, 999 not a candidate
        auto = next(i for i in rv["items"] if i["kind"] == "no_tags" and i["op"].get("node") == "Autopay Setup")
        self.assertEqual(auto["op"]["type"], "remove")        # the 12345-node entry never attached to it
        self.assertEqual(auto["reason"], "no fix proposed")


class HealthUntaggedSectionsPathTests(unittest.TestCase):
    """I-4: one tag item per existing label with all its chunk_ids (aggregated from `map`),
    restricted to the untagged set diagnose actually sent out; an unknown label is invalid;
    a bad `map` puts the whole file in skipped_files without half-consuming its proposals."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.tax = taxonomy(version=1)
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, self.tax)
        self.db = os.path.join(self.td.name, "k.sqlite")
        tagged_store(self.db, self.tax)   # chunk 8 is untagged
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO chunks VALUES(9,'doc9.md','T9','payment plan chunk nine')")
        c.execute("INSERT INTO chunks VALUES(10,'doc10.md','T10','payment plan chunk ten')")
        c.commit(); c.close()
        self.work = os.path.join(self.dir, "work", "health")
        H.diagnose(self.cur, self.db, self.work)   # untagged batch now covers chunks 8, 9, 10

    def tearDown(self):
        self.td.cleanup()

    def _plan(self):
        from datetime import datetime, timezone
        return R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                            now=datetime(2026, 9, 21, tzinfo=timezone.utc))

    def test_map_becomes_aggregated_tag_items_restricted_and_validated(self):
        write_json(os.path.join(self.work, "untagged", "result_0.json"), {
            "proposals": [{"name": "New Concept", "level": "L2", "parent": "Delivery & Pickup",
                          "description": "one sentence.", "evidence": "e", "example_ids": [10]}],
            "map": {"8": ["Refunds"], "9": ["Refunds", "Track Delivery"],
                    "999": ["Refunds"],                 # not in the untagged batch -> excluded
                    "10": ["Nonexistent Category"]}})   # unknown label -> invalid
        # a second, malformed file: bad "map" must not half-consume its own proposals
        write_json(os.path.join(self.work, "untagged", "result_1.json"),
                   {"proposals": [{"name": "Should Not Appear", "level": "L1"}], "map": "oops"})
        path, rv = self._plan()
        self.assertIn(os.path.join(self.work, "untagged", "result_1.json"), rv["skipped_files"])
        items = [i for i in rv["items"] if i["kind"] == "untagged_sections"]
        self.assertTrue(items[0]["title"].startswith("3 "))   # problems.json's untagged count
        self.assertFalse(any(i["op"].get("name") == "Should Not Appear" for i in items))
        add = next(i for i in items if i["op"].get("type") == "add")
        self.assertEqual(add["op"], {"type": "add", "level": "L2", "name": "New Concept",
                                     "parent": "Delivery & Pickup", "description": "one sentence."})
        refunds = next(i for i in items if i["op"].get("type") == "tag" and i["op"]["node"] == "Refunds")
        self.assertEqual(refunds["op"]["chunk_ids"], [8, 9])       # 999 filtered out (not in the untagged set)
        track = next(i for i in items if i["op"].get("type") == "tag" and i["op"]["node"] == "Track Delivery")
        self.assertEqual(track["op"]["chunk_ids"], [9])
        unknown = next(i for i in items if i["op"].get("type") == "tag"
                       and i["op"]["node"] == "Nonexistent Category")
        self.assertEqual(unknown["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
