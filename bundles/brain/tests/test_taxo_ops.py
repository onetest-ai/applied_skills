import unittest

import taxo_ops as O
from taxo_fixtures import taxonomy


def tree(t):
    return t["intent_taxonomy"]["tree"]


class AddRenameTests(unittest.TestCase):
    def test_add_l1_and_l2_under_new_l1(self):
        t, migs, _ = O.apply_ops(taxonomy(), [
            {"type": "add", "level": "L2", "name": "Handoffs", "parent": "AI & Automation"},
            {"type": "add", "level": "L1", "name": "AI & Automation"}])
        self.assertEqual(tree(t)["AI & Automation"], ["Handoffs"])
        self.assertIn("AI & Automation", t["intent_taxonomy"]["l1"])
        self.assertEqual(migs, [])

    def test_add_collision_is_an_error(self):
        errs = O.validate(taxonomy(), [{"type": "add", "level": "L2", "name": "refunds!", "parent": "Transform"}])
        self.assertTrue(any("collides" in e for e in errs), errs)

    def test_rename_l2_repoints_and_aliases(self):
        t, migs, _ = O.apply_ops(taxonomy(), [{"type": "rename", "node": "Refunds", "new_name": "Refund Requests"}])
        self.assertEqual(tree(t)["Billing & Payments"], ["Duplicate Charge", "Refund Requests"])
        self.assertEqual(t["aliases"], {"Refunds": "Refund Requests"})
        self.assertEqual(migs, [{"kind": "repoint", "from_id": "refunds", "to_id": "refund_requests",
                                 "label": "Refund Requests", "to_kind": "intent_l2"}])

    def test_rename_l1_keeps_order_and_children(self):
        t, _, _ = O.apply_ops(taxonomy(), [{"type": "rename", "node": "Transform", "new_name": "Programs"}])
        self.assertEqual(list(tree(t)), ["Billing & Payments", "Billing & Payments Admin", "Delivery & Pickup", "Programs"])

    def test_rename_case_only_is_allowed(self):
        t, migs, _ = O.apply_ops(taxonomy(), [{"type": "rename", "node": "Refunds", "new_name": "REFUNDS"}])
        self.assertEqual(migs[0]["from_id"], migs[0]["to_id"])


class MergeMoveSplitRemoveTests(unittest.TestCase):
    def test_merge_l1_moves_children_no_reclassify(self):
        t, migs, _ = O.apply_ops(taxonomy(), [{"type": "merge", "from": "Billing & Payments Admin",
                                               "into": "Billing & Payments"}])
        self.assertNotIn("Billing & Payments Admin", tree(t))
        self.assertEqual([m["kind"] for m in migs], ["repoint"])
        self.assertEqual(t["aliases"]["Billing & Payments Admin"], "Billing & Payments")

    def test_merge_l2_across_parents_reclassifies_first(self):
        _, migs, _ = O.apply_ops(taxonomy(), [{"type": "merge", "from": "Proof of Delivery", "into": "Refunds"}])
        self.assertEqual([m["kind"] for m in migs], ["reclassify_node", "repoint"])

    def test_merge_across_levels_is_an_error(self):
        errs = O.validate(taxonomy(), [{"type": "merge", "from": "Refunds", "into": "Transform"}])
        self.assertTrue(errs)

    def test_move_reclassifies(self):
        t, migs, _ = O.apply_ops(taxonomy(), [{"type": "move", "node": "Proof of Delivery",
                                               "new_parent": "Billing & Payments"}])
        self.assertIn("Proof of Delivery", tree(t)["Billing & Payments"])
        self.assertEqual(migs, [{"kind": "reclassify_node", "node_id": "proof_of_delivery",
                                 "reason": "moved to Billing & Payments"}])

    def test_split_l2_with_retire(self):
        t, migs, _ = O.apply_ops(taxonomy(), [{"type": "split", "node": "Refunds", "into": ["Full Refund", "Partial Refund"],
                                               "retire": True}])
        self.assertEqual(tree(t)["Billing & Payments"], ["Duplicate Charge", "Full Refund", "Partial Refund"])
        self.assertEqual([m["kind"] for m in migs], ["reclassify_node", "delete_node"])
        self.assertIn(["Refunds", 0], t["demoted"])

    def test_split_l1_cannot_retire(self):
        self.assertTrue(O.validate(taxonomy(), [{"type": "split", "node": "Transform", "into": ["A", "B"], "retire": True}]))

    def test_remove_l1_takes_children_and_demotes(self):
        t, migs, _ = O.apply_ops(taxonomy(), [{"type": "remove", "node": "Delivery & Pickup", "disposition": "demote",
                                               "reason": "out of scope"}])
        self.assertNotIn("Delivery & Pickup", tree(t))
        self.assertEqual(sorted(m["node_id"] for m in migs if m["kind"] == "delete_node"),
                         ["delivery_pickup", "proof_of_delivery", "track_delivery"])
        kinds = [m["kind"] for m in migs]
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "reclassify_node"),
                        min(i for i, k in enumerate(kinds) if k == "delete_node"))

    def test_remove_to_entity(self):
        t, _, _ = O.apply_ops(taxonomy(), [{"type": "remove", "node": "Transform", "disposition": "entity:initiative"}])
        self.assertEqual(t["entities"]["initiative"], ["Transform"])


class ConflictTests(unittest.TestCase):
    def test_two_ops_on_one_node(self):
        errs = O.validate(taxonomy(), [{"type": "rename", "node": "Refunds", "new_name": "X"},
                                       {"type": "remove", "node": "Refunds", "disposition": "demote"}])
        self.assertTrue(any("changed twice" in e for e in errs), errs)

    def test_target_consumed_by_other_op(self):
        errs = O.validate(taxonomy(), [{"type": "move", "node": "Refunds", "new_parent": "Transform"},
                                       {"type": "remove", "node": "Transform", "disposition": "demote"}])
        self.assertTrue(any("targets 'Transform'" in e for e in errs), errs)

    def test_apply_raises_with_all_errors(self):
        with self.assertRaises(O.ChangesetError) as cm:
            O.apply_ops(taxonomy(), [{"type": "move", "node": "Nope", "new_parent": "Transform"},
                                     {"type": "bogus"}])
        self.assertEqual(len(cm.exception.errors), 2)

    def test_input_taxonomy_is_not_mutated(self):
        t = taxonomy()
        O.apply_ops(t, [{"type": "remove", "node": "Transform", "disposition": "demote"}])
        self.assertEqual(t, taxonomy())


class L1ResurrectionTests(unittest.TestCase):
    def test_merge_removes_l1_ghost_on_later_move(self):
        """L1 removed by merge should not reappear as ghost when later op runs."""
        t, _, _ = O.apply_ops(taxonomy(), [
            {"type": "merge", "from": "Billing & Payments Admin", "into": "Billing & Payments"},
            {"type": "move", "node": "Proof of Delivery", "new_parent": "Billing & Payments"}])
        self.assertNotIn("Billing & Payments Admin", tree(t))
        self.assertNotIn("Billing & Payments Admin", t["intent_taxonomy"].get("l1", []))

    def test_rename_removes_l1_ghost_on_later_merge(self):
        """L1 renamed by first op should not reappear when second op runs."""
        t, _, _ = O.apply_ops(taxonomy(), [
            {"type": "rename", "node": "Transform", "new_name": "Programs"},
            {"type": "merge", "from": "Billing & Payments Admin", "into": "Billing & Payments"}])
        self.assertNotIn("Transform", tree(t))
        self.assertNotIn("Transform", t["intent_taxonomy"].get("l1", []))
        self.assertIn("Programs", tree(t))


class MetricOpTests(unittest.TestCase):
    def metrics(self, t):
        return {m["metric"]: m for m in t["metrics"]}

    def test_metric_merge_unions_and_keeps_variant(self):
        t, migs, _ = O.apply_ops(taxonomy(), [{"type": "metric_merge", "from": "Avg Handle Time",
                                               "into": "Average Handle Time"}])
        m = self.metrics(t)
        self.assertNotIn("Avg Handle Time", m)
        self.assertIn("Avg Handle Time", m["Average Handle Time"]["variants"])
        self.assertEqual(m["Average Handle Time"]["n_sources"], 3)
        self.assertEqual(migs, [])

    def test_metric_edit_records_previous(self):
        t, _, applied = O.apply_ops(taxonomy(), [{"type": "metric_edit", "metric": "Refund Rate",
                                                  "fields": {"grain": "division"}}])
        self.assertEqual(self.metrics(t)["Refund Rate"]["grain"], "division")
        self.assertEqual(applied[0]["previous"], {"grain": "branch"})

    def test_metric_edit_rejects_unknown_fields_and_types(self):
        self.assertTrue(O.validate(taxonomy(), [{"type": "metric_edit", "metric": "Refund Rate", "fields": {"n_sources": 9}}]))
        self.assertTrue(O.validate(taxonomy(), [{"type": "metric_edit", "metric": "Refund Rate",
                                                 "fields": {"source_type": "guess"}}]))

    def test_metric_add_and_remove(self):
        t, _, _ = O.apply_ops(taxonomy(), [
            {"type": "metric_add", "metric": "First Contact Resolution", "source_type": "computable", "grain": "branch"},
            {"type": "metric_remove", "metric": "Refund Rate", "reason": "not a KPI here"}])
        m = self.metrics(t)
        self.assertEqual(m["First Contact Resolution"]["origin"], "human")
        self.assertNotIn("Refund Rate", m)
        self.assertIn(["Refund Rate", 0], t["demoted"])

    def test_metric_add_duplicate_of_variant(self):
        self.assertTrue(O.validate(taxonomy(), [{"type": "metric_add", "metric": "aht", "source_type": "stated"}]))


class DescriptionOpTests(unittest.TestCase):
    def test_add_with_description_and_describe(self):
        t, migs, _ = O.apply_ops(taxonomy(), [
            {"type": "add", "level": "L2", "name": "Payment Plans", "parent": "Billing & Payments",
             "description": "Requests to split a bill into instalments."},
            {"type": "describe", "node": "Refunds", "description": "Money returned after a charge."}])
        self.assertEqual(t["descriptions"], {"Payment Plans": "Requests to split a bill into instalments.",
                                             "Refunds": "Money returned after a charge."})
        self.assertEqual(migs, [])

    def test_describe_collapses_embedded_whitespace(self):
        t, _, _ = O.apply_ops(taxonomy(), [{"type": "describe", "node": "Refunds", "description": "a\n b"}])
        self.assertEqual(t["descriptions"]["Refunds"], "a b")

    def test_describe_empty_or_unknown_is_an_error(self):
        self.assertTrue(O.validate(taxonomy(), [{"type": "describe", "node": "Refunds", "description": "  "}]))
        self.assertTrue(O.validate(taxonomy(), [{"type": "describe", "node": "Nope", "description": "x"}]))

    def test_describe_records_previous(self):
        base = taxonomy(); base["descriptions"] = {"Refunds": "old"}
        _, _, applied = O.apply_ops(base, [{"type": "describe", "node": "Refunds", "description": "new"}])
        self.assertEqual(applied[0]["previous"], "old")

    def test_rename_moves_description(self):
        base = taxonomy(); base["descriptions"] = {"Refunds": "Money back."}
        t, _, _ = O.apply_ops(base, [{"type": "rename", "node": "Refunds", "new_name": "Refund Requests"}])
        self.assertEqual(t["descriptions"], {"Refund Requests": "Money back."})

    def test_merge_keeps_target_or_inherits(self):
        base = taxonomy(); base["descriptions"] = {"Billing & Payments Admin": "admin", "Billing & Payments": "main"}
        t, _, _ = O.apply_ops(base, [{"type": "merge", "from": "Billing & Payments Admin", "into": "Billing & Payments"}])
        self.assertEqual(t["descriptions"], {"Billing & Payments": "main"})
        base["descriptions"] = {"Billing & Payments Admin": "admin"}
        t, _, _ = O.apply_ops(base, [{"type": "merge", "from": "Billing & Payments Admin", "into": "Billing & Payments"}])
        self.assertEqual(t["descriptions"], {"Billing & Payments": "admin"})

    def test_split_and_remove(self):
        base = taxonomy(); base["descriptions"] = {"Refunds": "x", "Delivery & Pickup": "y", "Track Delivery": "z"}
        t, _, _ = O.apply_ops(base, [
            {"type": "split", "node": "Refunds", "into": ["Full Refund", "Partial Refund"], "retire": True,
             "descriptions": {"Full Refund": "All money back."}},
            {"type": "remove", "node": "Delivery & Pickup", "disposition": "demote"}])
        self.assertEqual(t["descriptions"], {"Full Refund": "All money back."})

    def test_no_empty_descriptions_key(self):
        t, _, _ = O.apply_ops(taxonomy(), [{"type": "add", "level": "L1", "name": "New Thing"}])
        self.assertNotIn("descriptions", t)

    def test_rename_and_describe_same_node_conflict(self):
        errs = O.validate(taxonomy(), [{"type": "rename", "node": "Refunds", "new_name": "R2"},
                                       {"type": "describe", "node": "Refunds", "description": "d"}])
        self.assertTrue(any("changed twice" in e for e in errs), errs)


class TagAndGovernOpTests(unittest.TestCase):
    def test_tag_validates_node_and_ids_without_changing_taxonomy(self):
        t, migs, applied = O.apply_ops(taxonomy(), [{"type": "tag", "node": "Refunds", "chunk_ids": [5, 7]}])
        self.assertEqual(t, O.apply_ops(taxonomy(), [])[0])
        self.assertEqual((migs, applied[0]["chunk_ids"]), ([], [5, 7]))
        self.assertTrue(O.validate(taxonomy(), [{"type": "tag", "node": "Nope", "chunk_ids": [1]}]))
        self.assertTrue(O.validate(taxonomy(), [{"type": "tag", "node": "Refunds", "chunk_ids": []}]))
        self.assertTrue(O.validate(taxonomy(), [{"type": "tag", "node": "Refunds", "chunk_ids": ["x"]}]))

    def test_tag_on_node_added_in_same_review(self):
        ops = [{"type": "add", "level": "L2", "name": "Payment Plans", "parent": "Billing & Payments",
                "description": "Split bills."},
               {"type": "tag", "node": "Payment Plans", "chunk_ids": [8]}]
        self.assertEqual(O.validate(taxonomy(), ops), [])

    def test_tag_conflicts_with_rename_or_remove_of_same_node(self):
        errs = O.validate(taxonomy(), [{"type": "rename", "node": "Refunds", "new_name": "R2"},
                                       {"type": "tag", "node": "Refunds", "chunk_ids": [1]}])
        self.assertTrue(errs)

    def test_metric_govern_needs_known_metric_and_key(self):
        ok = {"type": "metric_govern", "metric": "Refund Rate",
              "draft": {"key": "refund_rate", "family": "delivery", "unit": "ratio", "desc": "d", "grain": "branch"}}
        self.assertEqual(O.validate(taxonomy(), [ok]), [])
        self.assertTrue(O.validate(taxonomy(), [dict(ok, metric="Nope")]))
        self.assertTrue(O.validate(taxonomy(), [dict(ok, draft={"family": "x"})]))
