import json, os, sqlite3, tempfile, unittest

import health as H
import taxonomy_review as R
from taxo_fixtures import TAGS, tagged_store, taxonomy, write_json


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
        # keeps, and the untagged_sections informational placeholder. The 18 problems include
        # the fixture's 4 sparse L2s (fit kinds count, like every other problem kind).
        self.assertEqual(rv["context"]["subtitle"], "v1 · 18 problems · 3 fixes proposed")

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
        self.assertTrue(items)   # Average Handle Time and Refund Rate are both computable/ungoverned
        for i in items:
            self.assertEqual(i["op"]["type"], "keep")
            self.assertEqual(i["reason"], "no fix proposed")
            self.assertEqual(i["alternatives"][0]["type"], "metric_govern")

    def test_metric_not_governed_explicit_agent_keep_keeps_its_reason_and_template(self):
        write_json(os.path.join(self.work, "metrics", "result_0.json"),
                   {"fixes": [{"kind": "metric_not_governed", "subject": "Refund Rate", "fix": "keep",
                               "reason": "not worth governing yet"}]})
        _, rv = self._plan()
        item = next(i for i in rv["items"]
                    if i["kind"] == "metric_not_governed" and i["op"].get("metric") == "Refund Rate")
        self.assertEqual(item["op"], {"type": "keep", "metric": "Refund Rate"})
        self.assertEqual(item["reason"], "not worth governing yet")
        self.assertEqual(item["alternatives"], [{"type": "metric_govern", "metric": "Refund Rate", "draft": {}}])

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

    def test_non_list_chunk_ids_and_non_string_disposition_are_dropped_and_counted(self):
        write_json(os.path.join(self.work, "notags", "result_0.json"), {"fixes": [
            {"node": "Payment Plans", "fix": "tag", "chunk_ids": 5},          # chunk_ids not a list
            {"node": "Autopay Setup", "fix": "remove", "disposition": 3},     # disposition not a string
        ]})
        from datetime import datetime, timezone
        _, rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                             now=datetime(2026, 9, 21, tzinfo=timezone.utc))
        # both entries were dropped as malformed -> both nodes fall back to "no fix proposed"
        pp = next(i for i in rv["items"] if i["kind"] == "no_tags" and i["op"].get("node") == "Payment Plans")
        self.assertEqual(pp["reason"], "no fix proposed")
        auto = next(i for i in rv["items"] if i["kind"] == "no_tags" and i["op"].get("node") == "Autopay Setup")
        self.assertEqual(auto["reason"], "no fix proposed")

    def test_untagged_proposal_with_non_list_example_ids_never_crashes(self):
        write_json(os.path.join(self.work, "untagged", "result_0.json"), {
            "proposals": [{"name": "Bad Proposal", "level": "L1", "example_ids": "not-a-list"}]})
        from datetime import datetime, timezone
        _, rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                             now=datetime(2026, 9, 21, tzinfo=timezone.utc))
        # the malformed proposal never became an add op; with nothing else usable, the
        # informational untagged_sections item shows instead
        self.assertFalse(any(i["op"].get("name") == "Bad Proposal" for i in rv["items"]))
        untagged = [i for i in rv["items"] if i["kind"] == "untagged_sections"]
        self.assertEqual(len(untagged), 1)
        self.assertEqual(untagged[0]["op"], {"type": "keep", "node": "__untagged__"})


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
        self.assertEqual(refunds["support"]["candidate_ids"], [8, 9, 10])   # the whole untagged batch

    def test_map_naming_only_out_of_set_ids_with_no_proposals_still_shows_the_item(self):
        # I-4 gap: the "nothing usable" check must run AFTER the chunk-id filter, not before.
        write_json(os.path.join(self.work, "untagged", "result_0.json"),
                   {"map": {"999": ["Refunds"], "998": ["Track Delivery"]}})   # both out of the untagged set
        _, rv = self._plan()
        items = [i for i in rv["items"] if i["kind"] == "untagged_sections"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["op"], {"type": "keep", "node": "__untagged__"})
        self.assertEqual(items[0]["reason"], "no fix proposed")

    def test_missing_or_unreadable_batch_files_treat_the_allowed_set_as_empty(self):
        # simulate the untagged batch files vanishing/corrupting between diagnose and plan
        for bf in os.listdir(os.path.join(self.work, "untagged")):
            if bf.startswith("batch_"):
                os.remove(os.path.join(self.work, "untagged", bf))
        write_json(os.path.join(self.work, "untagged", "result_0.json"),
                   {"map": {"8": ["Refunds"]}})   # a chunk that WAS legitimately untagged
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            _, rv = self._plan()
        items = [i for i in rv["items"] if i["kind"] == "untagged_sections"]
        # the tag is dropped (allowed ids treated as empty, not "unrestricted"), so nothing
        # usable came out of this and the informational item shows instead
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["op"], {"type": "keep", "node": "__untagged__"})
        self.assertIn("untagged", buf.getvalue())


class NamingPatternRuleTests(unittest.TestCase):
    """The deterministic rule that tells a patterned family from a real near-duplicate."""

    def test_examples(self):
        import flags
        self.assertEqual(flags.naming_pattern(["Delivery exception type 001", "Delivery exception type 002"]),
                         (True, "Delivery exception type NNN"))
        self.assertTrue(flags.naming_pattern(["EMEA sales", "APAC sales"])[0])
        self.assertTrue(flags.naming_pattern(["LATAM sales", "EMEA sales", "US sales"])[0])
        self.assertTrue(flags.naming_pattern(["Returns 2024", "Returns 2025"])[0])
        self.assertFalse(flags.naming_pattern(["Billing & Payments", "Billing and Payments"])[0])
        self.assertFalse(flags.naming_pattern(["Customer Onboarding", "Customer Onboardng"])[0])
        # every member must carry a variable token: an exact-stem pair with none is not a pattern
        self.assertFalse(flags.naming_pattern(["Refunds", "refunds"])[0])
        # a common stem is required, not just digits somewhere
        self.assertFalse(flags.naming_pattern(["Plan 1 upgrades", "Plan 2 downgrades"])[0])

    def test_large_groups_are_patterns_without_a_stem(self):
        import flags
        labels = [f"Topic {c}lpha" for c in "abcdefghi"]   # 9 members, no variable token
        self.assertEqual(flags.naming_pattern(labels), (True, None))
        self.assertFalse(flags.naming_pattern(labels[:8])[0])


def cluster_taxonomy():
    tax = taxonomy(version=1)
    tree = tax["intent_taxonomy"]["tree"]
    tree["Delivery Exceptions"] = [f"Delivery exception type {i:03d}" for i in range(1, 151)]
    tree["Accounts"] = ["Account Access", "Acount Access", "Account Acess", "Password Reset"]
    tree["Onboarding"] = ["Customer Onboarding", "Customer Onboardng"]
    tax["intent_taxonomy"]["l1"] += ["Delivery Exceptions", "Accounts", "Onboarding"]
    return tax


CLUSTER_TAGS = {**TAGS,
                20: ["Account Access"], 21: ["Account Access", "Acount Access"], 22: ["Account Access"],
                23: ["Account Acess"], 24: ["Customer Onboarding"], 25: ["Customer Onboarding"],
                26: ["Customer Onboardng"], 30: ["Delivery exception type 007"],
                31: ["Delivery exception type 007"], 32: ["Delivery exception type 042"]}


class NearDuplicateClusterTests(unittest.TestCase):
    """One problem per near-duplicate group, never one per pair; naming patterns become one item."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.tax = cluster_taxonomy()
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, self.tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, self.tax, CLUSTER_TAGS)
        self.work = os.path.join(self.dir, "work", "health")
        H.diagnose(self.cur, self.db, self.work)
        self.problems = json.load(open(os.path.join(self.work, "problems.json")))

    def tearDown(self):
        self.td.cleanup()

    def _plan(self):
        from datetime import datetime, timezone
        return R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                            now=datetime(2026, 9, 21, tzinfo=timezone.utc))

    def _nd(self, first):
        return next(p for p in self.problems["near_duplicate"] if p["members"][0] == first)

    def _structure_entries(self):
        import glob
        out = []
        for bf in sorted(glob.glob(os.path.join(self.work, "structure", "batch_*.json"))):
            out += [e for e in json.load(open(bf)) if e["kind"] == "near_duplicate"]
        return out

    def test_one_problem_per_group(self):
        firsts = sorted(p["members"][0] for p in self.problems["near_duplicate"])
        self.assertEqual(firsts, ["Account Access", "Billing & Payments", "Customer Onboarding",
                                  "Delivery exception type 001"])
        pat = self._nd("Delivery exception type 001")
        self.assertTrue(pat["pattern"]); self.assertEqual(len(pat["members"]), 150)
        self.assertEqual(pat["tags"]["Delivery exception type 007"], 2)
        self.assertNotIn("overlap", pat); self.assertNotIn("overlap_with_anchor", pat)
        cluster = self._nd("Account Access")
        self.assertFalse(cluster["pattern"]); self.assertEqual(cluster["anchor"], "Account Access")
        self.assertEqual(cluster["overlap_with_anchor"], {"Acount Access": 1, "Account Acess": 0})
        pair = self._nd("Customer Onboarding")
        self.assertEqual((pair["a"], pair["b"], pair["tags_a"], pair["tags_b"], pair["overlap"]),
                         ("Customer Onboarding", "Customer Onboardng", 2, 1, 0))

    def test_pattern_group_is_not_sent_to_the_structure_agent(self):
        entries = self._structure_entries()
        self.assertFalse(any(e.get("pattern") for e in entries))
        self.assertFalse(any("Delivery exception type 001" in e["members"] for e in entries))
        cluster = next(e for e in entries if e["members"][0] == "Account Access")
        self.assertEqual(set(cluster["samples"]), {"Account Access", "Acount Access", "Account Acess"})
        instructions = open(os.path.join(self.work, "structure", "instructions.md")).read()
        self.assertIn('"from"', instructions)

    def test_150_patterned_labels_become_exactly_one_item(self):
        _, rv = self._plan()
        items = [i for i in rv["items"] if i["kind"] == "near_duplicate"
                 and (i["support"] or {}).get("pattern")]
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["title"], "Naming pattern: 150 labels like 'Delivery exception type NNN'")
        self.assertEqual(it["reason"], "Labels differ only by a number/code — probably intentional variants")
        self.assertEqual(it["op"], {"type": "keep", "node": "Delivery exception type 001"})
        self.assertEqual(it["alternatives"], [{"type": "merge", "from": "Delivery exception type 001",
                                               "into": "Delivery exception type 007"}])
        self.assertEqual(len(it["evidence"]), 10)
        self.assertEqual(it["evidence"][0], {"label": "Delivery exception type 001", "tags": 0})
        self.assertEqual(it["support"]["count"], 150)
        self.assertIsNone(it["group"]); self.assertEqual(it["status"], "proposed")
        import decisions as D
        self.assertTrue(D.health_amend_ok(it, it["alternatives"][0]))

    def test_pair_with_an_agent_fix_behaves_as_before(self):
        write_json(os.path.join(self.work, "structure", "result_0.json"), {"fixes": [
            {"kind": "near_duplicate", "subject": "Customer Onboardng", "fix": "merge",
             "into": "Customer Onboarding", "reason": "typo"}]})
        _, rv = self._plan()
        it = next(i for i in rv["items"] if i["kind"] == "near_duplicate"
                  and i["title"] == "Customer Onboarding looks like Customer Onboardng")
        self.assertIsNone(it["group"])
        self.assertEqual(it["op"], {"type": "merge", "from": "Customer Onboardng", "into": "Customer Onboarding"})
        self.assertEqual(it["alternatives"], [{"type": "keep", "node": "Customer Onboarding"},
                                              {"type": "merge", "from": "Customer Onboarding",
                                               "into": "Customer Onboardng"}])
        self.assertEqual(it["support"], {"tags_a": 2, "tags_b": 1, "overlap": 0})
        self.assertEqual(it["reason"], "typo")

    def test_cluster_with_an_agent_fix_is_a_group_of_merges_into_the_survivor(self):
        write_json(os.path.join(self.work, "structure", "result_0.json"), {"fixes": [
            {"kind": "near_duplicate", "subject": "Account Access", "fix": "merge",
             "into": "Account Access", "reason": "two typos"}]})
        _, rv = self._plan()
        rows = [i for i in rv["items"] if i["kind"] == "near_duplicate" and i["group"]
                and "Account Access" in i["title"]]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["group"] for r in rows}), 1)
        self.assertEqual(sorted((r["op"]["type"], r["op"]["from"], r["op"]["into"]) for r in rows),
                         [("merge", "Account Acess", "Account Access"), ("merge", "Acount Access", "Account Access")])
        self.assertTrue(all(r["reason"] == "two typos" and r["status"] == "proposed" for r in rows))
        acount = next(r for r in rows if r["op"]["from"] == "Acount Access")
        self.assertEqual(acount["support"]["overlap"], 1)
        import decisions as D
        # amend to another member of the cluster: fine; retarget outside it: refused
        self.assertTrue(D.health_amend_ok(acount, {"type": "merge", "from": "Acount Access", "into": "Account Acess"}))
        self.assertTrue(D.health_amend_ok(acount, {"type": "keep", "node": "Acount Access"}))
        self.assertFalse(D.health_amend_ok(acount, {"type": "merge", "from": "Acount Access", "into": "Password Reset"}))
        self.assertEqual(rv["context"]["subtitle"].split(" · ")[1], f"{self._n_problems()} problems")

    def _n_problems(self):
        p = self.problems
        return sum(len(p[k]) for k in ("missing_description", "no_tags", "near_duplicate", "off_axis",
                                         "similar_metrics", "metric_not_governed",
                                         "sparse", "misplaced", "overloaded")) + 1

    def test_cluster_fix_with_a_from_list_keeps_the_rest(self):
        write_json(os.path.join(self.work, "structure", "result_0.json"), {"fixes": [
            {"kind": "near_duplicate", "subject": "Acount Access", "fix": "merge", "into": "Account Access",
             "from": ["Acount Access"], "reason": "only the typo"}]})
        _, rv = self._plan()
        rows = {i["op"].get("from") or i["op"].get("node"): i for i in rv["items"]
                if i["kind"] == "near_duplicate" and i["group"]}
        self.assertEqual(rows["Acount Access"]["op"]["type"], "merge")
        self.assertEqual(rows["Account Acess"]["op"], {"type": "keep", "node": "Account Acess"})
        self.assertIn({"type": "merge", "from": "Account Acess", "into": "Account Access"},
                      rows["Account Acess"]["alternatives"])

    def test_cluster_without_a_fix_falls_back(self):
        _, rv = self._plan()
        rows = [i for i in rv["items"] if i["kind"] == "near_duplicate" and i["group"]]
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["op"], {"type": "keep", "node": r["op"]["node"]})
            self.assertEqual(r["reason"], "no fix proposed")
            self.assertEqual(r["alternatives"][0],
                             {"type": "merge", "from": r["op"]["node"], "into": "Account Access"})
            self.assertLessEqual(len(r["alternatives"]), 2)
        # the pattern item is a real recommendation and counts as a proposed fix; the cluster
        # fallbacks (and every other kind, with no agent results here) do not
        fixes = int(rv["context"]["subtitle"].split(" · ")[2].split()[0])
        self.assertEqual(fixes, 1)

    def test_every_near_duplicate_problem_reaches_the_inbox_once(self):
        _, rv = self._plan()
        nd = [i for i in rv["items"] if i["kind"] == "near_duplicate"]
        entries = {i["group"] or i["id"] for i in nd}
        self.assertEqual(len(entries), len(self.problems["near_duplicate"]))
        ids = [i["id"] for i in rv["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        _, rv2 = self._plan()   # deterministic: same inputs, same ids
        self.assertEqual(ids, [i["id"] for i in rv2["items"]])


class PatternRuleEdgeTests(unittest.TestCase):
    """Fix round: case-only / plural-only differences are duplicates, not variants; short
    labels never chain unrelated labels through the substring rule."""

    def test_case_and_plural_only_differences_are_not_patterns(self):
        import flags
        for group in (["API Errors", "API errors"], ["IT support", "IT Support"],
                      ["Covid-19", "COVID-19"], ["FAQ", "FAQS"]):
            self.assertFalse(flags.naming_pattern(group)[0], group)

    def test_real_variants_stay_patterns(self):
        import flags
        for group in (["Q3 results", "Q4 results"], ["Win 10 issues", "Win 11 issues"],
                      ["US sales", "UK sales"], ["Tier 1 support", "Tier 2 support"],
                      ["EMEA sales", "APAC sales"]):
            self.assertTrue(flags.naming_pattern(group)[0], group)

    def test_short_label_does_not_chain_by_substring(self):
        import flags
        self.assertEqual(flags.near_duplicate_labels(["IT", "Credit", "Security", "Quality"]), [])
        self.assertEqual(flags.near_duplicate_labels(["Billing", "Billing Admin"]), [["Billing", "Billing Admin"]])


class ClusterEdgeTests(unittest.TestCase):
    """Fix round: large unstemmed groups, agent keeps, several clusters, L1 clusters, combined
    or conflicting fixes, unusable merges, respond on a group row, and an end-to-end apply."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.cur = os.path.join(self.dir, "current.json")
        self.db = os.path.join(self.td.name, "k.sqlite")
        self.work = os.path.join(self.dir, "work", "health")

    def tearDown(self):
        self.td.cleanup()

    def _build(self, tree_extra, l1_extra=(), tags=None):
        tax = taxonomy(version=1)
        tax["intent_taxonomy"]["tree"].update(tree_extra)
        tax["intent_taxonomy"]["l1"] += [k for k in tree_extra if k not in tax["intent_taxonomy"]["l1"]]
        for l1 in l1_extra:
            tax["intent_taxonomy"]["tree"][l1] = []
            tax["intent_taxonomy"]["l1"].append(l1)
        write_json(self.cur, tax)
        tagged_store(self.db, tax, {**TAGS, **(tags or {})})
        H.diagnose(self.cur, self.db, self.work)
        self.problems = json.load(open(os.path.join(self.work, "problems.json")))
        return tax

    def _structure(self, fixes):
        write_json(os.path.join(self.work, "structure", "result_0.json"), {"fixes": fixes})

    def _plan(self):
        from datetime import datetime, timezone
        return R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                            now=datetime(2026, 9, 21, tzinfo=timezone.utc))

    def _rows(self, rv, first):
        return [i for i in rv["items"] if i["kind"] == "near_duplicate" and i["group"]
                and i["title"].startswith("Similar labels: " + first)]

    TYPOS = {"Accounts": ["Account Access", "Acount Access", "Account Acess"]}
    TYPO_TAGS = {20: ["Account Access"], 21: ["Account Access", "Acount Access"], 22: ["Account Access"],
                 23: ["Account Acess"]}

    def test_large_group_without_a_stem_is_a_fallback_not_a_recommendation(self):
        self._build({"Orders": ["Order Issues"] + [f"Order Issues {w}" for w in
                     ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel")]})
        p = next(p for p in self.problems["near_duplicate"] if p["members"][0] == "Order Issues")
        self.assertTrue(p["pattern"]); self.assertNotIn("stem", p)
        _, rv = self._plan()
        it = next(i for i in rv["items"] if i["kind"] == "near_duplicate" and i["op"]["node"] == "Order Issues")
        self.assertTrue(it.get("fallback"))
        self.assertEqual(it["reason"], "9 labels grouped as similar — too many to review one by one; not checked")

    def test_agent_keep_on_a_cluster(self):
        self._build(self.TYPOS, tags=self.TYPO_TAGS)
        self._structure([{"kind": "near_duplicate", "subject": "Account Access", "fix": "keep",
                          "reason": "three distinct flows"}])
        rows = self._rows(self._plan()[1], "Account Access")
        self.assertEqual(sorted(r["op"]["node"] for r in rows), ["Account Acess", "Acount Access"])
        for r in rows:
            self.assertEqual(r["op"]["type"], "keep")
            self.assertEqual(r["reason"], "three distinct flows")
            self.assertNotIn("fallback", r)

    def test_two_clusters_under_one_parent_are_two_groups(self):
        self._build({"Accounts": self.TYPOS["Accounts"] + ["Password Reset", "Pasword Reset", "Password Rest"]},
                    tags=self.TYPO_TAGS)
        firsts = sorted(p["members"][0] for p in self.problems["near_duplicate"] if p["parent"] == "Accounts")
        self.assertEqual(firsts, ["Account Access", "Password Reset"])
        _, rv = self._plan()
        a, b = self._rows(rv, "Account Access"), self._rows(rv, "Password Reset")
        self.assertEqual((len(a), len(b)), (2, 2))
        self.assertEqual(len({r["group"] for r in a}), 1)
        self.assertEqual(a[0]["group"], "near_duplicate:" + json.dumps(["L2", "Accounts", "Account Access"]))
        self.assertNotEqual(a[0]["group"], b[0]["group"])

    def test_l1_level_cluster(self):
        self._build({}, l1_extra=["Returns Policy", "Return Policy", "Returns Polcy"])
        p = next(p for p in self.problems["near_duplicate"] if p["members"][0] == "Returns Policy")
        self.assertEqual((p["level"], p["parent"]), ("L1", None))
        self._structure([{"kind": "near_duplicate", "subject": "Returns Policy", "fix": "merge",
                          "into": "Returns Policy", "from": ["Return Policy", "Returns Polcy"], "reason": "typos"}])
        rows = self._rows(self._plan()[1], "Returns Policy")
        self.assertEqual(sorted((r["op"]["from"], r["op"]["into"]) for r in rows),
                         [("Return Policy", "Returns Policy"), ("Returns Polcy", "Returns Policy")])
        self.assertTrue(all(r["status"] == "proposed" for r in rows))

    def test_without_from_only_the_subject_is_merged(self):
        self._build(self.TYPOS, tags=self.TYPO_TAGS)
        self._structure([{"kind": "near_duplicate", "subject": "Acount Access", "fix": "merge",
                          "into": "Account Access", "reason": "typo"}])
        rows = {r["op"].get("from") or r["op"]["node"]: r for r in self._rows(self._plan()[1], "Account Access")}
        self.assertEqual(rows["Acount Access"]["op"]["type"], "merge")
        self.assertEqual(rows["Account Acess"]["op"]["type"], "keep")

    def test_fixes_sharing_into_combine_and_a_conflicting_one_is_noted(self):
        self._build(self.TYPOS, tags=self.TYPO_TAGS)
        self._structure([
            {"kind": "near_duplicate", "subject": "Acount Access", "fix": "merge", "into": "Account Access",
             "reason": "typo one"},
            {"kind": "near_duplicate", "subject": "Account Acess", "fix": "merge", "into": "Account Access",
             "reason": "typo two"},
            {"kind": "near_duplicate", "subject": "Account Access", "fix": "merge", "into": "Acount Access",
             "reason": "backwards"}])
        rows = self._rows(self._plan()[1], "Account Access")
        self.assertEqual(sorted(r["op"]["from"] for r in rows), ["Account Acess", "Acount Access"])
        self.assertTrue(all(r["op"]["into"] == "Account Access" for r in rows))
        self.assertEqual(rows[0]["reason"], "typo one; typo two (1 other fix(es) for this group ignored)")

    def test_unusable_merges_fall_back_but_keep_the_agent_reason(self):
        self._build({**self.TYPOS, "Onboarding": ["Customer Onboarding", "Customer Onboardng"]},
                    tags=self.TYPO_TAGS)
        self._structure([
            {"kind": "near_duplicate", "subject": "Account Access", "fix": "merge", "into": "Password Reset",
             "from": ["Acount Access"], "reason": "belongs elsewhere"},
            {"kind": "near_duplicate", "subject": "Customer Onboardng", "fix": "merge", "into": "Refunds",
             "reason": "wrong target"}])
        _, rv = self._plan()
        for r in self._rows(rv, "Account Access"):
            self.assertTrue(r.get("fallback")); self.assertEqual(r["op"]["type"], "keep")
            self.assertIn("belongs elsewhere", r["reason"]); self.assertIn("'Password Reset'", r["reason"])
        pair = next(i for i in rv["items"] if i["title"] == "Customer Onboarding looks like Customer Onboardng")
        self.assertTrue(pair.get("fallback")); self.assertEqual(pair["op"]["type"], "keep")
        self.assertIn("wrong target", pair["reason"])

    def test_respond_on_a_group_row(self):
        self._build(self.TYPOS, tags=self.TYPO_TAGS)
        path, rv = self._plan()
        row = next(r for r in self._rows(rv, "Account Access") if r["op"]["node"] == "Acount Access")
        with open(os.path.join(self.dir, "work", "requests.jsonl"), "a") as f:
            f.write(json.dumps({"id": "q-1", "item_id": row["id"], "note": "merge it", "status": "open"}) + "\n")
        import io
        from contextlib import redirect_stdout
        ok = {"type": "merge", "from": "Acount Access", "into": "Account Access"}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(R.main(["respond", "--review", path, "--request", "q-1", "--op", json.dumps(ok)]), 0)
            bad = {"type": "merge", "from": "Acount Access", "into": "Refunds"}
            self.assertEqual(R.main(["respond", "--review", path, "--request", "q-1", "--op", json.dumps(bad)]), 2)

    def test_pattern_items_have_no_redo_channel(self):
        import review_server as S
        self._build({"Exceptions": [f"Exception type {i:02d}" for i in range(1, 6)]})
        path, rv = self._plan()
        it = next(i for i in rv["items"] if (i["support"] or {}).get("pattern"))
        code, out = S.ReviewApp(path, db_path=self.db, reviewer="Pat").request({"item_id": it["id"], "note": "x"})
        self.assertEqual(code, 400)
        self.assertIn("naming pattern", out["errors"][0])

    def test_cluster_merges_apply_end_to_end(self):
        import subprocess, sys
        import review_server as S
        from taxo_fixtures import tag_rows
        cte = os.path.dirname(os.path.abspath(H.__file__))
        run = lambda *a: subprocess.run([sys.executable, os.path.join(cte, a[0]), *a[1:]],
                                        capture_output=True, text=True, check=True)
        self._build(self.TYPOS, tags=self.TYPO_TAGS)
        run("build_graph.py", "--taxonomy", self.cur, "--db", self.db)
        self._structure([{"kind": "near_duplicate", "subject": "Account Access", "fix": "merge",
                          "into": "Account Access", "from": ["Acount Access", "Account Acess"], "reason": "typos"}])
        path, rv = self._plan()
        rows = self._rows(rv, "Account Access")
        app = S.ReviewApp(path, db_path=self.db, reviewer="Pat")
        code, out = app.decisions({"records": [{"action": "approve", "item_id": r["id"]} for r in rows]})
        self.assertEqual(code, 200, out)
        self.assertEqual(app.submit({})[0], 200)
        res = json.loads(run("taxonomy_merge.py", "--review", path, "--apply").stdout.strip().splitlines()[0])
        self.assertEqual(res["status"], "applied")
        run("build_graph.py", "--taxonomy", self.cur, "--db", self.db)
        new = json.load(open(self.cur))
        self.assertEqual(new["intent_taxonomy"]["tree"]["Accounts"], ["Account Access"])
        after = set(tag_rows(self.db))
        self.assertFalse({"acount_access", "account_acess"} & {cat for _, cat in after})
        for cid in (20, 21, 22, 23):
            self.assertIn((cid, "account_access"), after)


class HealthRerunAndRedoTests(unittest.TestCase):
    """G2 answered/settled requests stay closed; G3 a re-run clears stale task files; G6
    redo-prep writes the item's single task entry and respond --check is a dry run."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        tax = taxonomy(version=1)
        tax["intent_taxonomy"]["tree"]["Billing & Payments"].append("Payment Plans")
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, tax); add_fts(self.db)
        self.work = os.path.join(self.dir, "work", "health")
        H.diagnose(self.cur, self.db, self.work)
        self.requests = os.path.join(self.dir, "work", "requests.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def _plan(self):
        from datetime import datetime, timezone
        return R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                            now=datetime(2026, 9, 21, tzinfo=timezone.utc))

    def _cli(self, *argv):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = R.main(list(argv))
        return code, json.loads(buf.getvalue().strip().splitlines()[-1])

    def _request(self, rid, item, note):
        with open(self.requests, "a") as f:
            f.write(json.dumps({"id": rid, "review_id": self._rv["review_id"], "item_id": item["id"],
                                "note": note, "status": "open"}) + "\n")

    def test_answered_request_is_not_reopened(self):
        path, self._rv = self._plan()
        item = next(i for i in self._rv["items"] if i["kind"] == "no_tags")
        self._request("q-a", item, "answered already")
        self._request("q-b", item, "still waiting")
        code, _ = self._cli("respond", "--review", path, "--request", "q-a", "--op", json.dumps(item["op"]))
        self.assertEqual(code, 0)
        H.diagnose(self.cur, self.db, self.work)
        p = json.load(open(os.path.join(self.work, "problems.json")))
        self.assertEqual([r["request_id"] for r in p["open_requests"]], ["q-b"])

    def test_request_on_an_item_accepted_in_an_applied_review_is_dropped(self):
        import decisions as D
        path, self._rv = self._plan()
        item = next(i for i in self._rv["items"] if i["kind"] == "no_tags")
        other = next(i for i in self._rv["items"] if i["kind"] == "missing_description")
        self._request("q-1", item, "settled by approve")
        self._request("q-2", other, "skipped, keep for the next run")
        rid = self._rv["review_id"]
        with open(D.default_path(self.dir), "a") as f:
            for rec in ({"action": "approve", "item_id": item["id"]}, {"action": "submit"}, {"action": "applied"}):
                f.write(json.dumps(dict(rec, review_id=rid, schema=1, ts="t")) + "\n")
        H.diagnose(self.cur, self.db, self.work)
        p = json.load(open(os.path.join(self.work, "problems.json")))
        self.assertEqual([r["request_id"] for r in p["open_requests"]], ["q-2"])

    def test_rerun_clears_stale_results_and_batches(self):
        stale = os.path.join(self.work, "structure", "result_0.json")
        write_json(stale, {"fixes": [{"kind": "off_axis", "subject": "Transform", "fix": "keep", "reason": "old"}]})
        write_json(os.path.join(self.work, "structure", "batch_9.json"), [])
        keep = os.path.join(self.work, "structure", "notes.txt"); open(keep, "w").write("mine")
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            res = H.diagnose(self.cur, self.db, self.work)
        self.assertFalse(os.path.exists(stale))
        self.assertFalse(os.path.exists(os.path.join(self.work, "structure", "batch_9.json")))
        self.assertTrue(os.path.exists(keep))
        self.assertTrue(os.path.exists(os.path.join(self.work, "structure", "batch_0.json")))   # this run's
        self.assertIn(stale, res["removed_stale"]); self.assertIn("stale", err.getvalue())
        _, rv = self._plan()
        oa = next(i for i in rv["items"] if i["kind"] == "off_axis")
        self.assertEqual(oa["reason"], "no fix proposed")      # the old "keep" did not come back

    def test_redo_prep_writes_the_single_entry_with_its_note(self):
        path, self._rv = self._plan()
        item = next(i for i in self._rv["items"] if i["kind"] == "no_tags" and i["op"]["node"] == "Payment Plans")
        self._request("q-7", item, "look at chunk 9")
        code, out = self._cli("redo-prep", "--review", path, "--item", item["id"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["kind"], "notags")
        self.assertEqual(out["dir"], os.path.join(self.work, "notags", f"redo-{item['id']}"))
        batch = json.load(open(out["entry"]))
        self.assertEqual(len(batch), 1)
        self.assertEqual((batch[0]["node"], batch[0]["note"]), ("Payment Plans", "look at chunk 9"))
        self.assertIn("candidates", batch[0])
        self.assertTrue(os.path.exists(out["instructions"]))
        # plan never reads the redo dir
        write_json(out["result"], {"fixes": [{"node": "Payment Plans", "fix": "remove", "reason": "redo"}]})
        _, rv = self._plan()
        again = next(i for i in rv["items"] if i["kind"] == "no_tags" and i["op"]["node"] == "Payment Plans")
        self.assertEqual(again["reason"], "no fix proposed")

    def test_redo_prep_for_a_structure_item(self):
        path, self._rv = self._plan()
        item = next(i for i in self._rv["items"] if i["kind"] == "near_duplicate")
        code, out = self._cli("redo-prep", "--review", path, "--item", item["id"], "--out",
                              os.path.join(self.td.name, "redo"))
        self.assertEqual(code, 0, out)
        batch = json.load(open(out["entry"]))
        self.assertEqual(batch[0]["members"], ["Billing & Payments", "Billing & Payments Admin"])
        self.assertNotIn("note", batch[0])

    def test_respond_check_validates_without_writing(self):
        path, self._rv = self._plan()
        item = next(i for i in self._rv["items"] if i["kind"] == "no_tags")
        self._request("q-9", item, "n")
        resp = os.path.join(self.dir, "work", "responses.jsonl")
        code, out = self._cli("respond", "--review", path, "--request", "q-9", "--check",
                              "--op", json.dumps(item["alternatives"][0]))
        self.assertEqual((code, out["status"]), (0, "ok"))
        self.assertFalse(os.path.exists(resp))
        code, out = self._cli("respond", "--review", path, "--request", "q-9", "--check",
                              "--op", json.dumps({"type": "remove", "node": "Refunds", "disposition": "demote"}))
        self.assertEqual((code, out["status"]), (2, "refused"))
        self.assertFalse(os.path.exists(resp))


if __name__ == "__main__":
    unittest.main()
