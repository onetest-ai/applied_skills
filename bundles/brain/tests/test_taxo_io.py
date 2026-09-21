import json
import os
import tempfile
import unittest

import build_graph as G
import taxo_io as IO


class IdTests(unittest.TestCase):
    def test_nid_matches_build_graph(self):
        for s in ["Billing & Payments", "  Proof of Delivery ", "AI/Automation", "!!!"]:
            self.assertEqual(IO.nid(s), G.nid(s))

    def test_fingerprint_add_uses_level_name_parent(self):
        fp = IO.fingerprint({"type": "add", "level": "L2", "name": "Proof of Delivery!",
                             "parent": "Delivery & Pickup"})
        self.assertEqual(fp, "add|l2|proof of delivery|delivery pickup")

    def test_fingerprint_non_add(self):
        self.assertEqual(IO.fingerprint({"type": "merge", "from": "A b", "into": "C"}), "merge|a b|c")
        self.assertEqual(IO.fingerprint({"type": "split", "node": "X", "into": ["Y", "Z"]}), "split|x|y z")
        self.assertEqual(IO.fingerprint({"type": "keep", "node": "X"}), "keep|x|")


class StructureTests(unittest.TestCase):
    TAX = {"intent_taxonomy": {"l1": ["A", "B"], "tree": {"A": ["a1"]}, "unassigned_l2": ["u"]},
           "entities": {"branch": []}, "aliases": {"Old A": "A"}}

    def test_intent_normalizes_l1_into_tree(self):
        tax = json.loads(json.dumps(self.TAX))
        it = IO.intent(tax)
        self.assertEqual(it["tree"], {"A": ["a1"], "B": []})

    def test_locate(self):
        tax = json.loads(json.dumps(self.TAX))
        self.assertEqual(IO.locate(tax, "A"), ("L1", None))
        self.assertEqual(IO.locate(tax, "a1"), ("L2", "A"))
        self.assertEqual(IO.locate(tax, "u"), ("L2", None))
        self.assertEqual(IO.locate(tax, "branch"), ("entity", None))
        self.assertEqual(IO.locate(tax, "nope"), (None, None))

    def test_node_ids_cover_intents_and_entity_kinds(self):
        tax = json.loads(json.dumps(self.TAX))
        self.assertEqual(IO.node_ids(tax), {"a", "b", "a1", "u", "branch"})

    def test_label_taken_checks_ids_text_and_aliases(self):
        tax = json.loads(json.dumps(self.TAX))
        self.assertEqual(IO.label_taken(tax, "a"), "A")          # same graph id
        self.assertEqual(IO.label_taken(tax, "old a"), "Old A")  # alias
        self.assertIsNone(IO.label_taken(tax, "Brand New"))


class WriteTests(unittest.TestCase):
    def test_version_and_current_are_byte_identical_and_immutable(self):
        with tempfile.TemporaryDirectory() as td:
            out, sha = IO.write_version_and_current(td, {"version": 3, "x": 1})
            self.assertEqual(out, os.path.join(td, "taxonomy_v3.json"))
            a = open(out, "rb").read()
            b = open(os.path.join(td, IO.CURRENT), "rb").read()
            self.assertEqual(a, b)
            self.assertEqual(sha, IO.sha256_bytes(a))
            with self.assertRaises(FileExistsError):
                IO.write_version_and_current(td, {"version": 3, "x": 2})

    def test_failed_write_leaves_previous_current(self):
        with tempfile.TemporaryDirectory() as td:
            IO.write_version_and_current(td, {"version": 1})
            before = open(os.path.join(td, IO.CURRENT), "rb").read()
            with self.assertRaises(TypeError):
                IO.write_version_and_current(td, {"version": 2, "bad": object()})
            self.assertEqual(open(os.path.join(td, IO.CURRENT), "rb").read(), before)
            self.assertFalse([f for f in os.listdir(td) if f.startswith(".tmp-")])


class ReviewerTests(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(IO.reviewer_name("Pat"), "Pat")

    def test_utc_now_format(self):
        self.assertRegex(IO.utc_now(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
