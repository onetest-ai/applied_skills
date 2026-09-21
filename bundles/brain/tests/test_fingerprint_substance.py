"""Rejection suppression matches an op's substance, not just (type, node): a tag's chunk set, a
description's text, a removal's disposition, a governed draft's key, a health keep's kind. Old
fingerprints (type + node only) suppress only when the rejection carries an op with the same
substance; otherwise they stop suppressing."""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

import decisions as D
import health as H
import taxonomy_review as R
from taxo_fixtures import tagged_store, taxonomy, write_json
from taxo_io import fingerprint


def tag(ids):
    return {"type": "tag", "node": "Payment Plans", "chunk_ids": ids}


def rejected(*recs):
    return D.standing_rejections([dict({"action": "reject", "review_id": "r-old", "item_id": f"i-{n}",
                                        "reason": "no"}, **r) for n, r in enumerate(recs)])


class FingerprintSubstanceTests(unittest.TestCase):
    def test_tag_matches_on_the_chunk_set(self):
        rej = rejected({"fingerprint": fingerprint(tag([9, 12]))})
        self.assertIsNone(D.match_rejection(fingerprint(tag([9, 14])), rej))
        self.assertIsNotNone(D.match_rejection(fingerprint(tag([12, 9, 9])), rej))

    def test_describe_matches_on_normalised_text(self):
        op = {"type": "describe", "node": "Refunds", "description": "Money back."}
        rej = rejected({"fingerprint": fingerprint(op)})
        self.assertIsNone(D.match_rejection(fingerprint(dict(op, description="Refund requests and status.")), rej))
        self.assertIsNotNone(D.match_rejection(fingerprint(dict(op, description="  money   BACK. ")), rej))

    def test_keep_depends_on_the_health_kind(self):
        keep = {"type": "keep", "node": "Transform"}
        rej = rejected({"fingerprint": fingerprint(keep, "near_duplicate")})
        self.assertIsNone(D.match_rejection(fingerprint(keep, "off_axis"), rej))
        self.assertIsNotNone(D.match_rejection(fingerprint(keep, "near_duplicate"), rej))
        self.assertEqual(fingerprint(keep), "keep|transform|")          # non-health keeps unchanged

    def test_remove_and_govern_carry_their_target(self):
        a = {"type": "remove", "node": "Transform", "disposition": "demote"}
        self.assertNotEqual(fingerprint(a), fingerprint(dict(a, disposition="entity:program")))
        g = {"type": "metric_govern", "metric": "Refund Rate", "draft": {"key": "refund_rate"}}
        self.assertNotEqual(fingerprint(g), fingerprint(dict(g, draft={"key": "refund_pct"})))
        m = {"type": "merge", "from": "A", "into": "B"}
        self.assertNotEqual(fingerprint(m), fingerprint(dict(m, into="C")))

    def test_legacy_rejection_suppresses_only_with_a_matching_recorded_op(self):
        self.assertIsNone(D.match_rejection(fingerprint(tag([9, 12])), rejected({"fingerprint": "tag|payment plans|"})))
        with_op = rejected({"fingerprint": "tag|payment plans|", "op": tag([9, 12])})
        self.assertIsNotNone(D.match_rejection(fingerprint(tag([12, 9])), with_op))
        self.assertIsNone(D.match_rejection(fingerprint(tag([9, 14])), with_op))
        legacy_keep = rejected({"fingerprint": "keep|transform|", "op": {"type": "keep", "node": "Transform"}})
        self.assertIsNone(D.match_rejection(fingerprint({"type": "keep", "node": "Transform"}, "off_axis"),
                                            legacy_keep))
        self.assertIsNotNone(D.match_rejection("keep|transform|", legacy_keep))   # exact old match still works


class HealthSuppressionTests(unittest.TestCase):
    """End to end: a rejected tag proposal comes back suppressed only for the same chunk set."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        tax = taxonomy(version=1)
        tax["intent_taxonomy"]["tree"]["Billing & Payments"].append("Payment Plans")
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, tax)
        c = sqlite3.connect(self.db)
        c.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(text)")
        for cid in (9, 12, 14):
            text = f"customer asks about payment plans, case {cid}"
            c.execute("INSERT INTO chunks VALUES(?,?,?,?)", (cid, f"doc{cid}.md", "Payment plans", text))
            c.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)", (cid, text))
        c.commit(); c.close()
        self.work = os.path.join(self.dir, "work", "health")

    def tearDown(self):
        self.td.cleanup()

    def _plan(self, ids, day):
        H.diagnose(self.cur, self.db, self.work)
        write_json(os.path.join(self.work, "notags", "result_0.json"),
                   {"fixes": [{"node": "Payment Plans", "fix": "tag", "chunk_ids": ids, "reason": "fits"}]})
        _, rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db,
                             now=datetime(2026, 9, day, tzinfo=timezone.utc))
        return rv, next(i for i in rv["items"] if i["kind"] == "no_tags" and i["op"]["node"] == "Payment Plans")

    def test_reject_then_repropose(self):
        rv, item = self._plan([9, 12], 21)
        self.assertEqual(item["op"]["chunk_ids"], [9, 12])
        D.append(D.default_path(self.dir), {"review_id": rv["review_id"], "item_id": item["id"], "action": "reject",
                                            "reason": "wrong sections", "fingerprint": item["fingerprint"],
                                            "reviewer": "Pat", "surface": "browser"})
        _, other = self._plan([9, 14], 22)
        self.assertEqual(other["status"], "proposed")
        _, same = self._plan([12, 9], 23)
        self.assertEqual(same["status"], "suppressed")
        self.assertEqual(same["prior"]["reason"], "wrong sections")


if __name__ == "__main__":
    unittest.main()
