import os
import tempfile
import unittest

import decisions as D
from taxo_fixtures import taxonomy

RID = "r-test"


def review(items):
    return {"review_id": RID, "mode": "drift", "base": {"path": "x", "version": 0, "sha256": "s"}, "items": items}


ADD = {"id": "i-1", "origin": "refine", "status": "proposed", "fingerprint": "add|l2|payment plans|billing payments",
       "op": {"type": "add", "level": "L2", "name": "Payment Plans", "parent": "Billing & Payments"}}
KEEP = {"id": "i-2", "origin": "induction", "status": "proposed", "fingerprint": "keep|transform|",
        "op": {"type": "keep", "node": "Transform"}, "level": "L1", "parent": None}
SUPP = {"id": "i-3", "origin": "refine", "status": "suppressed", "fingerprint": "add|l1|q3 offsite|",
        "op": {"type": "add", "level": "L1", "name": "Q3 Offsite", "parent": None}}


class LogTests(unittest.TestCase):
    def test_append_read_round_trip_and_malformed_line(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "decisions.jsonl")
            D.append(p, {"review_id": RID, "item_id": "i-1", "action": "approve", "surface": "browser"})
            self.assertEqual(D.read(p)[0]["action"], "approve")
            with open(p, "a") as f:
                f.write("{not json\n")
            with self.assertRaises(D.DecisionLogError) as cm:
                D.read(p)
            self.assertIn(":2:", str(cm.exception))

    def test_reject_needs_reason(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(D.DecisionLogError):
                D.append(os.path.join(td, "d.jsonl"), {"review_id": RID, "item_id": "i-1", "action": "reject"})


class StateTests(unittest.TestCase):
    def test_last_write_wins_and_withdraw(self):
        recs = [
            {"review_id": RID, "item_id": "i-1", "action": "reject", "reason": "no"},
            {"review_id": RID, "item_id": "i-1", "action": "approve", "surface": "terminal"},
            {"review_id": RID, "item_id": "h-1", "action": "propose", "surface": "browser",
             "op": {"type": "rename", "node": "Refunds", "new_name": "Refund Requests"}},
            {"review_id": RID, "item_id": "h-2", "action": "propose", "surface": "browser",
             "op": {"type": "remove", "node": "Transform", "disposition": "demote"}},
            {"review_id": RID, "item_id": "h-2", "action": "withdraw", "surface": "browser"},
            {"review_id": "other", "item_id": "i-1", "action": "reject", "reason": "x"}]
        entries = D.effective_ops(review([ADD]), recs)
        self.assertEqual([e["item_id"] for e in entries], ["i-1", "h-1"])

    def test_induction_amend_and_undecided_keep(self):
        recs = [{"review_id": RID, "item_id": "i-2", "action": "amend", "surface": "browser",
                 "op": {"type": "remove", "node": "Transform", "disposition": "demote"}}]
        self.assertEqual(D.effective_ops(review([KEEP]), recs)[0]["op"]["type"], "remove")
        self.assertEqual(D.effective_ops(review([KEEP]), []), [])

    def test_suppressed_needs_approval_to_apply(self):
        self.assertEqual(D.effective_ops(review([SUPP]), []), [])


class AuthorshipTests(unittest.TestCase):
    def test_non_add_needs_human_surface(self):
        entries = [{"item_id": "h-1", "op": {"type": "merge", "from": "a", "into": "b"}, "origin": "human",
                    "surface": "terminal"},
                   {"item_id": "i-1", "op": {"type": "add"}, "origin": "refine", "surface": "terminal"},
                   {"item_id": "h-2", "op": {"type": "metric_edit"}, "origin": "human", "surface": "markdown"}]
        errs = D.authorship_errors(entries)
        self.assertEqual(len(errs), 1)
        self.assertIn("h-1", errs[0])


class SuppressionTests(unittest.TestCase):
    def test_reject_then_reopen(self):
        recs = [{"action": "reject", "fingerprint": "add|l1|q3 offsite|", "reason": "not a call reason"},
                {"action": "reject", "fingerprint": "add|l2|x|y", "reason": "no"},
                {"action": "reopen", "fingerprint": "add|l2|x|y"}]
        rej = D.standing_rejections(recs)
        self.assertEqual(list(rej), ["add|l1|q3 offsite|"])

    def test_cleared_reject_does_not_stand(self):
        fp = "add|l1|q3 offsite|"
        recs = [{"review_id": "A", "item_id": "i-1", "action": "reject", "fingerprint": fp, "reason": "no"},
                {"review_id": "A", "item_id": "i-1", "action": "clear"}]
        self.assertEqual(D.standing_rejections(recs), {})

    def test_cleared_reject_restores_the_earlier_rejection(self):
        fp = "add|l1|q3 offsite|"
        first = {"review_id": "A", "item_id": "i-1", "action": "reject", "fingerprint": fp, "reason": "first"}
        recs = [first, {"review_id": "B", "item_id": "i-9", "action": "reject", "fingerprint": fp, "reason": "again"},
                {"review_id": "B", "item_id": "i-9", "action": "clear"}]
        self.assertIs(D.standing_rejections(recs)[fp], first)

    def test_cleared_reopen_restores_the_rejection(self):
        fp = "add|l1|q3 offsite|"
        recs = [{"review_id": "A", "item_id": "i-1", "action": "reject", "fingerprint": fp, "reason": "no"},
                {"review_id": "B", "item_id": "i-7", "action": "reopen", "fingerprint": fp},
                {"review_id": "B", "item_id": "i-7", "action": "clear"}]
        self.assertEqual(D.standing_rejections(recs)[fp]["reason"], "no")

    def test_cross_review_reopen_still_lifts(self):
        fp = "add|l1|q3 offsite|"
        recs = [{"review_id": "A", "item_id": "i-1", "action": "reject", "fingerprint": fp, "reason": "no"},
                {"review_id": "B", "item_id": "i-7", "action": "reopen", "fingerprint": fp}]
        self.assertEqual(D.standing_rejections(recs), {})

    def test_clear_of_an_approve_leaves_rejections_alone(self):
        fp = "add|l1|q3 offsite|"
        recs = [{"review_id": "A", "item_id": "i-1", "action": "reject", "fingerprint": fp, "reason": "no"},
                {"review_id": "B", "item_id": "i-7", "action": "reopen", "fingerprint": fp},
                {"review_id": "B", "item_id": "i-7", "action": "approve"},
                {"review_id": "B", "item_id": "i-7", "action": "clear"}]
        self.assertEqual(D.standing_rejections(recs), {})

    def test_reopen_after_undone_reopen_can_be_undone_again(self):
        fp = "add|l1|q3 offsite|"
        recs = [{"review_id": "A", "item_id": "i-1", "action": "reject", "fingerprint": fp, "reason": "no"},
                {"review_id": "B", "item_id": "i-7", "action": "reopen", "fingerprint": fp},
                {"review_id": "B", "item_id": "i-7", "action": "approve"},
                {"review_id": "B", "item_id": "i-7", "action": "clear"},
                {"review_id": "B", "item_id": "i-7", "action": "reopen", "fingerprint": fp},
                {"review_id": "B", "item_id": "i-7", "action": "clear"}]
        self.assertIn(fp, D.standing_rejections(recs))

    def test_fuzzy_match_same_level_and_parent(self):
        rej = {"add|l1|q3 offsite|": {"reason": "no"}}
        self.assertIsNotNone(D.match_rejection("add|l1|q3 offsites|", rej))
        self.assertIsNone(D.match_rejection("add|l2|q3 offsite|billing", rej))


class ValidateRecordTests(unittest.TestCase):
    def setUp(self):
        self.tax = taxonomy()

    def v(self, items, records, rec):
        return D.validate_record(review(items), self.tax, records, rec)

    def test_ok_approve(self):
        self.assertEqual(self.v([ADD], [], {"review_id": RID, "item_id": "i-1", "action": "approve",
                                            "surface": "terminal"}), [])

    def test_induction_item_cannot_be_rejected(self):
        self.assertTrue(self.v([KEEP], [], {"review_id": RID, "item_id": "i-2", "action": "reject", "reason": "x"}))

    def test_refine_item_amend_must_stay_add(self):
        self.assertTrue(self.v([ADD], [], {"review_id": RID, "item_id": "i-1", "action": "amend", "surface": "browser",
                                           "op": {"type": "remove", "node": "Refunds"}}))

    def test_suppressed_needs_reopen_first(self):
        self.assertTrue(self.v([SUPP], [], {"review_id": RID, "item_id": "i-3", "action": "approve"}))
        recs = [{"review_id": RID, "item_id": "i-3", "action": "reopen", "fingerprint": SUPP["fingerprint"]}]
        self.assertEqual(self.v([SUPP], recs, {"review_id": RID, "item_id": "i-3", "action": "approve",
                                               "surface": "terminal"}), [])

    def test_propose_invalid_op_is_refused(self):
        errs = self.v([], [], {"review_id": RID, "item_id": "h-9", "action": "propose", "surface": "browser",
                               "op": {"type": "move", "node": "Nope", "new_parent": "Transform"}})
        self.assertTrue(errs)

    def test_propose_non_add_from_terminal_is_refused(self):
        errs = self.v([], [], {"review_id": RID, "item_id": "h-9", "action": "propose", "surface": "terminal",
                               "op": {"type": "rename", "node": "Refunds", "new_name": "R2"}})
        self.assertTrue(any("review app" in e for e in errs), errs)

    def test_nothing_after_submit(self):
        recs = [{"review_id": RID, "action": "submit"}]
        self.assertTrue(self.v([ADD], recs, {"review_id": RID, "item_id": "i-1", "action": "approve"}))

    def test_submit_counts(self):
        recs = [{"review_id": RID, "item_id": "i-1", "action": "approve"}]
        self.assertEqual(D.submit_counts(review([ADD, KEEP]), recs),
                         {"approved": 1, "rejected": 0, "amended": 0, "undecided": 1, "proposals": 0})


DESC = {"id": "i-4", "origin": "describe", "status": "proposed", "fingerprint": "describe|refunds|",
        "op": {"type": "describe", "node": "Refunds", "description": "Money returned."}, "level": "L2",
        "parent": "Billing & Payments"}


class DescribeAndClearTests(unittest.TestCase):
    def setUp(self):
        self.tax = taxonomy()

    def v(self, items, records, rec):
        return D.validate_record(review(items), self.tax, records, rec)

    def test_describe_is_agent_allowed(self):
        self.assertIn("describe", D.AGENT_ALLOWED)
        self.assertEqual(self.v([DESC], [], {"review_id": RID, "item_id": "i-4", "action": "approve",
                                             "surface": "terminal"}), [])

    def test_describe_amend_must_stay_on_node(self):
        bad = {"review_id": RID, "item_id": "i-4", "action": "amend", "surface": "browser",
               "op": {"type": "describe", "node": "Duplicate Charge", "description": "x"}}
        self.assertTrue(self.v([DESC], [], bad))
        ok = dict(bad, op={"type": "describe", "node": "Refunds", "description": "Better."})
        self.assertEqual(self.v([DESC], [], ok), [])

    def test_clear_returns_item_to_undecided(self):
        recs = [{"review_id": RID, "item_id": "i-1", "action": "approve", "surface": "terminal"}]
        clear = {"review_id": RID, "item_id": "i-1", "action": "clear", "surface": "browser"}
        self.assertEqual(self.v([ADD], recs, clear), [])
        self.assertEqual(D.effective_ops(review([ADD]), recs + [clear]), [])
        self.assertEqual(D.submit_counts(review([ADD]), recs + [clear])["undecided"], 1)

    def test_clear_needs_a_decision_to_undo(self):
        self.assertTrue(self.v([ADD], [], {"review_id": RID, "item_id": "i-1", "action": "clear"}))

    def test_clear_allowed_on_induction_items(self):
        recs = [{"review_id": RID, "item_id": "i-2", "action": "approve", "surface": "browser"}]
        self.assertEqual(self.v([KEEP], recs, {"review_id": RID, "item_id": "i-2", "action": "clear"}), [])

    def test_browser_add_needs_description(self):
        rec = {"review_id": RID, "item_id": "h-9", "action": "propose", "surface": "browser",
               "op": {"type": "add", "level": "L1", "name": "Brand New"}}
        self.assertTrue(any("description" in e for e in self.v([], [], rec)))
        rec["op"]["description"] = "What it covers."
        self.assertEqual(self.v([], [], rec), [])
        term = dict(rec, surface="terminal", op={"type": "add", "level": "L1", "name": "Other New"})
        self.assertEqual(self.v([], [], term), [])


HEALTH = {"id": "i-9", "origin": "health", "kind": "no_tags", "group": "no_tags", "status": "proposed",
          "fingerprint": "tag|transform|", "op": {"type": "tag", "node": "Transform", "chunk_ids": [7, 8]},
          "alternatives": [{"type": "remove", "node": "Transform", "disposition": "demote", "reason": "no tags"}]}


class HealthAmendTests(unittest.TestCase):
    def v(self, recs, rec):
        return D.validate_record(review([HEALTH]), taxonomy(), recs, rec)

    def test_tag_and_govern_are_agent_allowed(self):
        self.assertTrue({"tag", "metric_govern"} <= D.AGENT_ALLOWED)

    def test_amend_to_alternative_or_same_type_same_subject(self):
        alt = {"review_id": RID, "item_id": "i-9", "action": "amend", "surface": "browser", "op": HEALTH["alternatives"][0]}
        self.assertEqual(self.v([], alt), [])
        subset = dict(alt, op={"type": "tag", "node": "Transform", "chunk_ids": [7]})
        self.assertEqual(self.v([], subset), [])
        other = dict(alt, op={"type": "tag", "node": "Refunds", "chunk_ids": [7]})
        self.assertTrue(self.v([], other))
        foreign = dict(alt, op={"type": "move", "node": "Refunds", "new_parent": "Transform"})
        self.assertTrue(self.v([], foreign))


HEALTH_KEEP_FALLBACK = {
    "id": "i-10", "origin": "health", "kind": "missing_description", "group": "missing_description",
    "status": "proposed", "fingerprint": "keep|refunds|", "op": {"type": "keep", "node": "Refunds"},
    "alternatives": [{"type": "describe", "node": "Refunds", "description": ""}]}


class HealthAmendFallbackTemplateTests(unittest.TestCase):
    def v(self, rec):
        return D.validate_record(review([HEALTH_KEEP_FALLBACK]), taxonomy(), [], rec)

    def test_amend_keep_fallback_into_its_describe_template_with_a_real_description(self):
        rec = {"review_id": RID, "item_id": "i-10", "action": "amend", "surface": "browser",
               "op": {"type": "describe", "node": "Refunds", "description": "Money back to the customer."}}
        self.assertEqual(self.v(rec), [])

    def test_amend_refuses_an_unrelated_type(self):
        rec = {"review_id": RID, "item_id": "i-10", "action": "amend", "surface": "browser",
               "op": {"type": "remove", "node": "Refunds", "disposition": "demote"}}
        self.assertTrue(self.v(rec))


class HealthAmendOkTests(unittest.TestCase):
    """health_amend_ok checks every candidate (item op + alternatives), not just the item's
    primary op — this is what lets an edited template alternative through."""

    def test_same_type_same_subject_against_an_alternative_not_the_primary_op(self):
        item = {"op": {"type": "keep", "metric": "Porch Rate"},
                "alternatives": [{"type": "metric_govern", "metric": "Porch Rate", "draft": {}}]}
        self.assertTrue(D.health_amend_ok(
            item, {"type": "metric_govern", "metric": "Porch Rate", "draft": {"key": "porch_rate"}}))

    def test_refuses_unrelated_type_and_subject(self):
        item = {"op": {"type": "keep", "metric": "Porch Rate"},
                "alternatives": [{"type": "metric_govern", "metric": "Porch Rate", "draft": {}}]}
        self.assertFalse(D.health_amend_ok(item, {"type": "metric_merge", "from": "Porch Rate", "into": "X"}))
        self.assertFalse(D.health_amend_ok(item, {"type": "metric_govern", "metric": "Other Metric", "draft": {}}))
