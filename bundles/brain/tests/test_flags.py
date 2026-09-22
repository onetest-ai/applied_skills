#!/usr/bin/env python3
"""Unit tests for flags.py detectors (near_duplicate_labels, off_axis_l1).

Run: python3 -m unittest test_flags
"""
import unittest

from flags import near_duplicate_labels, off_axis_l1


class TestNearDuplicateLabels(unittest.TestCase):
    def test_billing_family_grouped_network_outage_excluded(self):
        labels = [
            "Billing & Payments",
            "Billing & Payments Admin",
            "Billing & Payments Administration",
            "Duplicate Charge",
            "Network Outage",
        ]
        groups = near_duplicate_labels(labels)
        self.assertEqual(len(groups), 1, groups)
        billing_group = groups[0]
        self.assertEqual(set(billing_group), {
            "Billing & Payments",
            "Billing & Payments Admin",
            "Billing & Payments Administration",
        })
        flat = {lbl for g in groups for lbl in g}
        self.assertNotIn("Network Outage", flat)
        self.assertNotIn("Duplicate Charge", flat)

    def test_no_duplicates_returns_empty(self):
        labels = ["Duplicate Charge", "Network Outage", "Password Reset"]
        self.assertEqual(near_duplicate_labels(labels), [])

    def test_empty_input(self):
        self.assertEqual(near_duplicate_labels([]), [])


class TestOffAxisL1(unittest.TestCase):
    def test_phase_label_flagged(self):
        reason = off_axis_l1("Transform")
        self.assertIsNotNone(reason)
        self.assertIn("phase", reason)

    def test_bare_entity_flagged(self):
        reason = off_axis_l1("Costco")
        self.assertIsNotNone(reason)

    def test_legitimate_domain_not_flagged(self):
        self.assertIsNone(off_axis_l1("Billing & Payments"))

    def test_other_phase_words(self):
        for label in ["Roadmap", "Vision 2027", "Future State", "Milestone Planning"]:
            with self.subTest(label=label):
                self.assertIsNotNone(off_axis_l1(label))

    def test_empty_label(self):
        self.assertIsNone(off_axis_l1(""))
        self.assertIsNone(off_axis_l1(None))

    def test_phase_word_inside_another_word_is_not_flagged(self):
        # 'vision' inside Divisional / Provisioning, 'wave' inside Microwave
        self.assertIsNone(off_axis_l1("Regional & Divisional Performance Management"))
        self.assertIsNone(off_axis_l1("System Access Provisioning"))
        self.assertIsNone(off_axis_l1("Microwave Ordering"))

    def test_phase_word_at_word_start_is_still_flagged(self):
        self.assertIn("vision", off_axis_l1("Contact Center Service Vision"))
        self.assertIn("transform", off_axis_l1("CX Transformation Workstreams"))
        self.assertIn("roadmap", off_axis_l1("AI & Technology Roadmap"))


if __name__ == "__main__":
    unittest.main()
