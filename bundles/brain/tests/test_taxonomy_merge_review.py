import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import decisions as D
import taxonomy_merge as M
import taxo_io as IO
from taxo_fixtures import taxonomy, write_json

MERGE_PY = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction" / "taxonomy_merge.py"

PROPOSALS = {"proposals": [
    {"name": "AI & Automation", "level": "L1", "parent": None, "evidence": "bot handoffs", "example_ids": [8]},
    {"name": "AI & automation", "level": "L1", "parent": None, "evidence": "dup", "example_ids": [7]},
    {"name": "Handoffs", "level": "L2", "parent": "AI & Automation", "evidence": "x", "example_ids": [8]},
    {"name": "Refunds", "level": "L2", "parent": "Billing & Payments", "evidence": "dup existing"},
    {"name": "Orphan", "level": "L2", "parent": "Nowhere", "evidence": "x"}]}

EXPECTED_DRY_RUN = """=== taxonomy merge (from {p}) — DRY RUN ===
proposed: +1 L1, +1 L2 ; dropped 3 dup/invalid
  + L1  AI & Automation
  + L2  Handoffs   (under AI & Automation)
  · skip AI & automation  — dup of L1 'AI & Automation'
  · skip Refunds  — dup of L2 'Refunds'
  · skip Orphan  — L2 parent 'Nowhere' not an existing/new L1
"""


def run(*args):
    return subprocess.run([sys.executable, str(MERGE_PY), *args], text=True, capture_output=True)


class PlanAdditionsTests(unittest.TestCase):
    def test_items_and_skipped(self):
        items, skipped = M.plan_additions(taxonomy(), PROPOSALS["proposals"])
        self.assertEqual([(i["level"], i["name"], i["parent"]) for i in items],
                         [("L1", "AI & Automation", None), ("L2", "Handoffs", "AI & Automation")])
        self.assertEqual(len(items[0]["sources"]), 2)
        self.assertEqual([s["kind"] for s in skipped], ["dup_new", "dup_existing", "invalid"])

    def test_alias_is_auto_skipped(self):
        tax = taxonomy()
        tax["aliases"] = {"Double charge": "Duplicate Charge"}
        _, skipped = M.plan_additions(tax, [{"name": "Double charge", "level": "L2", "parent": "Billing & Payments"}])
        self.assertEqual(skipped[0]["kind"], "alias")


class LegacyCliTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tax = os.path.join(self.td.name, "taxonomy", "current.json")
        write_json(self.tax, taxonomy(version=2))
        self.props = os.path.join(self.td.name, "props")
        write_json(os.path.join(self.props, "result_0.json"), PROPOSALS)

    def tearDown(self):
        self.td.cleanup()

    def test_dry_run_diff_lines_unchanged(self):
        r = run("--taxonomy", self.tax, "--proposals", self.props)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(EXPECTED_DRY_RUN.format(p=self.props)), r.stdout)

    def test_malformed_result_file_is_reported_on_stderr_not_stdout(self):
        with open(os.path.join(self.props, "result_1.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        r = run("--taxonomy", self.tax, "--proposals", self.props)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(EXPECTED_DRY_RUN.format(p=self.props)), r.stdout)
        self.assertIn("result_1.json", r.stderr)

    def test_apply_without_review_refuses(self):
        r = run("--taxonomy", self.tax, "--proposals", self.props, "--apply")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--without-review", r.stderr)

    def test_apply_without_review_flag_is_add_only_and_marked(self):
        r = run("--taxonomy", self.tax, "--proposals", self.props, "--apply", "--without-review", "--reviewer", "Pat")
        self.assertEqual(r.returncode, 0, r.stderr)
        v3 = IO.load_json(os.path.join(os.path.dirname(self.tax), "taxonomy_v3.json"))
        self.assertEqual(v3["history"][-1]["without_review"], True)
        self.assertEqual(v3["history"][-1]["reviewer"], "Pat")
        self.assertIn("AI & Automation", v3["intent_taxonomy"]["tree"])
        self.assertEqual(open(self.tax, "rb").read(),
                         open(os.path.join(os.path.dirname(self.tax), "taxonomy_v3.json"), "rb").read())

    def test_dry_run_lists_suppressed(self):
        D.append(os.path.join(os.path.dirname(self.tax), "decisions.jsonl"),
                 {"review_id": "r-old", "item_id": "i-x", "action": "reject", "reason": "not a call reason",
                  "fingerprint": "add|l2|handoffs|ai automation", "reviewer": "Pat"})
        r = run("--taxonomy", self.tax, "--proposals", self.props)
        self.assertIn("· suppressed Handoffs  — rejected before: not a call reason (Pat)", r.stdout)
        self.assertIn("proposed: +1 L1, +0 L2", r.stdout)

    def test_stale_taxonomy_file_refused_when_current_exists(self):
        # self.tax IS current.json (a sibling of itself); point --taxonomy at a different, stale file instead.
        stale = os.path.join(os.path.dirname(self.tax), "taxonomy_v1.json")
        write_json(stale, taxonomy(version=1))
        cur_bytes = open(self.tax, "rb").read()
        r = run("--taxonomy", stale, "--proposals", self.props, "--apply", "--without-review")
        self.assertEqual(r.returncode, 2)
        self.assertIn("current.json", r.stderr)
        self.assertEqual(open(self.tax, "rb").read(), cur_bytes)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tax), "taxonomy_v2.json")))

    def test_existing_ratified_version_file_refused_and_untouched(self):
        v3 = os.path.join(os.path.dirname(self.tax), "taxonomy_v3.json")
        write_json(v3, {"sentinel": True})
        sentinel_bytes = open(v3, "rb").read()
        cur_bytes = open(self.tax, "rb").read()
        r = run("--taxonomy", self.tax, "--proposals", self.props, "--apply", "--without-review")
        self.assertEqual(r.returncode, 2)
        self.assertIn("taxonomy_v3.json", r.stderr)
        self.assertEqual(open(v3, "rb").read(), sentinel_bytes)
        self.assertEqual(open(self.tax, "rb").read(), cur_bytes)

    def test_out_outside_taxonomy_dir_leaves_current_untouched(self):
        out = os.path.join(self.td.name, "outside", "out.json")
        cur_bytes = open(self.tax, "rb").read()
        r = run("--taxonomy", self.tax, "--proposals", self.props, "--apply", "--without-review", "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(open(self.tax, "rb").read(), cur_bytes)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tax), "taxonomy_v3.json")))

    def test_zero_additions_after_dedup_writes_nothing(self):
        only_dups = {"proposals": [
            {"name": "Refunds", "level": "L2", "parent": "Billing & Payments", "evidence": "dup existing"},
            {"name": "Orphan", "level": "L2", "parent": "Nowhere", "evidence": "x"}]}
        props2 = os.path.join(self.td.name, "props2")
        write_json(os.path.join(props2, "result_0.json"), only_dups)
        cur_bytes = open(self.tax, "rb").read()
        r = run("--taxonomy", self.tax, "--proposals", props2, "--apply", "--without-review")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to apply", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tax), "taxonomy_v3.json")))
        self.assertEqual(open(self.tax, "rb").read(), cur_bytes)


class ApplyReviewTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.cur = os.path.join(self.dir, "current.json")
        write_json(self.cur, taxonomy(version=1))
        self.rid = "r-t"
        self.review_path = os.path.join(self.dir, "reviews", f"review_{self.rid}.json")
        self.review = {"schema": 1, "review_id": self.rid, "mode": "browse",
                       "base": {"path": self.cur, "version": 1, "sha256": IO.sha256_file(self.cur)}, "items": []}
        write_json(self.review_path, self.review)
        self.dec = os.path.join(self.dir, "decisions.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def propose(self, op, surface="browser"):
        D.append(self.dec, {"review_id": self.rid, "item_id": D.new_human_id(), "action": "propose", "op": op,
                            "surface": surface, "reviewer": "Pat"})

    def submit(self):
        D.append(self.dec, {"review_id": self.rid, "action": "submit", "reviewer": "Pat", "surface": "browser"})

    def assert_nothing_written(self, cur_bytes_before):
        self.assertFalse(os.path.exists(os.path.join(self.dir, "taxonomy_v2.json")))
        self.assertEqual(open(self.cur, "rb").read(), cur_bytes_before)
        self.assertIsNone(D.review_state(D.read(self.dec), self.rid)["applied"])

    def test_refuses_unsubmitted(self):
        self.propose({"type": "rename", "node": "Refunds", "new_name": "Refund Requests"})
        cur_bytes = open(self.cur, "rb").read()
        with self.assertRaises(M.Refused):
            M.apply_review(self.review_path)
        self.assert_nothing_written(cur_bytes)

    def test_applies_writes_current_history_and_applied_record(self):
        self.propose({"type": "rename", "node": "Refunds", "new_name": "Refund Requests"})
        self.submit()
        res = M.apply_review(self.review_path)
        self.assertEqual(res["version"], 2)
        v2 = IO.load_json(os.path.join(self.dir, "taxonomy_v2.json"))
        h = v2["history"][-1]
        self.assertEqual((h["review_id"], h["reviewer"], h["version"]), (self.rid, "Pat", 2))
        self.assertEqual(h["migrations"][0]["kind"], "repoint")
        self.assertEqual(open(self.cur, "rb").read(), open(os.path.join(self.dir, "taxonomy_v2.json"), "rb").read())
        self.assertEqual(D.review_state(D.read(self.dec), self.rid)["applied"]["version"], 2)
        with self.assertRaises(M.Refused):
            M.apply_review(self.review_path)

    def test_refuses_when_base_changed(self):
        self.submit()
        write_json(self.cur, taxonomy(version=1) | {"goal": "edited"})
        cur_bytes = open(self.cur, "rb").read()
        with self.assertRaises(M.Refused):
            M.apply_review(self.review_path)
        self.assert_nothing_written(cur_bytes)

    def test_refuses_non_add_from_agent_surface(self):
        self.propose({"type": "remove", "node": "Transform", "disposition": "demote"}, surface="terminal")
        self.submit()
        cur_bytes = open(self.cur, "rb").read()
        with self.assertRaises(M.Refused) as cm:
            M.apply_review(self.review_path)
        self.assertIn("review app", str(cm.exception))
        self.assert_nothing_written(cur_bytes)

    def test_refuses_when_changeset_is_invalid(self):
        # Directly appended via D.append (bypasses the review app's own validate_record check).
        self.propose({"type": "rename", "node": "Refunds", "new_name": "Duplicate Charge"})
        self.submit()
        cur_bytes = open(self.cur, "rb").read()
        with self.assertRaises(M.Refused) as cm:
            M.apply_review(self.review_path)
        self.assertIn("changeset is invalid", str(cm.exception))
        self.assert_nothing_written(cur_bytes)

    def test_refuses_when_version_file_already_exists(self):
        self.propose({"type": "rename", "node": "Refunds", "new_name": "Refund Requests"})
        self.submit()
        v2_path = os.path.join(self.dir, "taxonomy_v2.json")
        write_json(v2_path, {"sentinel": True})
        sentinel_bytes = open(v2_path, "rb").read()
        cur_bytes = open(self.cur, "rb").read()
        with self.assertRaises(M.Refused) as cm:
            M.apply_review(self.review_path)
        self.assertIn("taxonomy_v2.json", str(cm.exception))
        self.assertIn(self.cur, str(cm.exception))
        self.assertEqual(open(v2_path, "rb").read(), sentinel_bytes)
        self.assertEqual(open(self.cur, "rb").read(), cur_bytes)
        self.assertIsNone(D.review_state(D.read(self.dec), self.rid)["applied"])

    def test_refuses_non_draft_review_not_based_on_current(self):
        v1 = os.path.join(self.dir, "taxonomy_v1.json")
        write_json(v1, taxonomy(version=1))
        os.remove(self.cur)
        self.review["base"] = {"path": v1, "version": 1, "sha256": IO.sha256_file(v1)}
        write_json(self.review_path, self.review)          # mode stays "browse"
        self.propose({"type": "add", "level": "L1", "name": "AI & Automation"})
        self.submit()
        with self.assertRaises(M.Refused) as cm:
            M.apply_review(self.review_path)
        self.assertIn("adopt", str(cm.exception))
        self.assertFalse(os.path.exists(self.cur))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "taxonomy_v2.json")))
        self.assertIsNone(D.review_state(D.read(self.dec), self.rid)["applied"])

    def test_no_ops_is_no_changes(self):
        self.submit()
        self.assertEqual(M.apply_review(self.review_path)["status"], "no_changes")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "taxonomy_v2.json")))

    def test_draft_ratifies_even_with_no_ops_and_refuses_over_current(self):
        os.remove(self.cur)
        v0 = os.path.join(self.dir, "taxonomy_v0.json")
        write_json(v0, taxonomy())
        self.review["base"] = {"path": v0, "version": 0, "sha256": IO.sha256_file(v0)}
        self.review["mode"] = "draft"
        write_json(self.review_path, self.review)
        self.submit()
        res = M.apply_review(self.review_path)
        self.assertEqual((res["status"], res["version"]), ("applied", 1))
        self.assertTrue(os.path.exists(self.cur))
        rid2 = "r-t2"
        path2 = os.path.join(self.dir, "reviews", f"review_{rid2}.json")
        write_json(path2, {**self.review, "review_id": rid2})
        D.append(self.dec, {"review_id": rid2, "action": "submit", "reviewer": "Pat", "surface": "browser"})
        with self.assertRaises(M.Refused):
            M.apply_review(path2)


class SideOutputTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        self.cur = os.path.join(self.dir, "current.json")
        write_json(self.cur, taxonomy(version=1))
        self.rid = "r-h"
        self.path = os.path.join(self.dir, "reviews", f"review_{self.rid}.json")
        write_json(self.path, {"schema": 1, "review_id": self.rid, "mode": "health",
                               "base": {"path": self.cur, "version": 1, "sha256": IO.sha256_file(self.cur)}, "items": []})
        self.dec = os.path.join(self.dir, "decisions.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def prop(self, op):
        D.append(self.dec, {"review_id": self.rid, "item_id": D.new_human_id(), "action": "propose", "op": op,
                            "surface": "browser", "reviewer": "Pat"})

    def test_tags_only_writes_tags_file_and_no_new_version(self):
        self.prop({"type": "tag", "node": "Refunds", "chunk_ids": [5, 7]})
        D.append(self.dec, {"review_id": self.rid, "action": "submit", "reviewer": "Pat", "surface": "browser"})
        res = M.apply_review(self.path)
        self.assertFalse(res["taxonomy_changed"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "taxonomy_v2.json")))
        self.assertEqual(json.load(open(res["tags_file"])), {"5": ["Refunds"], "7": ["Refunds"]})

    def test_mixed_review_writes_version_tags_and_governed_drafts(self):
        self.prop({"type": "add", "level": "L2", "name": "Payment Plans", "parent": "Billing & Payments",
                   "description": "Split bills."})
        self.prop({"type": "tag", "node": "Payment Plans", "chunk_ids": [8]})
        self.prop({"type": "metric_govern", "metric": "Porch Rate",
                   "draft": {"key": "porch_rate", "family": "delivery", "unit": "ratio", "desc": "d", "grain": "branch"}})
        D.append(self.dec, {"review_id": self.rid, "action": "submit", "reviewer": "Pat", "surface": "browser"})
        res = M.apply_review(self.path)
        self.assertTrue(res["taxonomy_changed"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "taxonomy_v2.json")))
        self.assertEqual(json.load(open(res["tags_file"])), {"8": ["Payment Plans"]})
        drafts = json.load(open(res["governed_drafts_file"]))
        self.assertEqual(drafts[-1]["draft"]["key"], "porch_rate")
        self.assertEqual(drafts[-1]["review_id"], self.rid)
