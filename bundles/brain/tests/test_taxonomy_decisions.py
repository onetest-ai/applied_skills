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
