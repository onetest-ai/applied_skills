#!/usr/bin/env python3
"""Unit tests for the capability taxonomy + addressed_by cross-links in build_graph.py.

Run: python3 -m unittest test_graph_capabilities
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_graph as G  # noqa: E402


class BuildAddressedByTests(unittest.TestCase):
    NODES = {"billing_disputes": (), "delivery_issues": (), "self_service_portal": ()}

    def test_valid_pair_becomes_edge(self):
        edges, skipped = G.build_addressed_by(
            {"addressed_by": [{"intent": "Billing Disputes", "capability": "Self-Service Portal"}]},
            self.NODES)
        self.assertEqual(edges, [("billing_disputes", "self_service_portal", "addressed_by")])
        self.assertEqual(skipped, [])

    def test_unknown_endpoint_is_skipped_not_dangling(self):
        edges, skipped = G.build_addressed_by(
            {"addressed_by": [{"intent": "Nonexistent", "capability": "Self-Service Portal"}]},
            self.NODES)
        self.assertEqual(edges, [])
        self.assertEqual(skipped, [("Nonexistent", "Self-Service Portal")])

    def test_accepts_bare_list(self):
        edges, _ = G.build_addressed_by(
            [{"intent": "Delivery Issues", "capability": "Self-Service Portal"}], self.NODES)
        self.assertEqual(edges, [("delivery_issues", "self_service_portal", "addressed_by")])


class BuildGraphEndToEndTests(unittest.TestCase):
    def _run(self, td):
        tax, cap, lnk, db = (os.path.join(td, n) for n in ("t.json", "c.json", "l.json", "k.sqlite"))
        json.dump({"intent_taxonomy": {"tree": {"Billing Disputes": ["Duplicate Charge"],
                                                 "Delivery Issues": []}}, "entities": {"branch": {}}},
                  open(tax, "w"))
        json.dump({"capability_taxonomy": {"tree": {"Self-Service Portal": ["Dispute Wizard"],
                                                    "Proactive Notifications": []}}}, open(cap, "w"))
        json.dump({"addressed_by": [{"intent": "Billing Disputes", "capability": "Self-Service Portal"},
                                    {"intent": "Nonexistent", "capability": "Self-Service Portal"}]},
                  open(lnk, "w"))
        r = subprocess.run([sys.executable, str(HERE / "build_graph.py"), "--taxonomy", tax,
                            "--capabilities", cap, "--links", lnk, "--db", db],
                           text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return db, r.stdout + r.stderr

    def test_capability_nodes_edges_and_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            db, out = self._run(td)
            self.assertIn("skipped 1 addressed_by", out)
            c = sqlite3.connect(db)
            kinds = dict(c.execute("SELECT kind, COUNT(*) FROM graph_nodes GROUP BY kind").fetchall())
            self.assertEqual(kinds.get("capability_l1"), 2)
            self.assertEqual(kinds.get("capability_l2"), 1)
            self.assertEqual(kinds.get("intent_l1"), 2)
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM graph_edges WHERE rel='addressed_by'").fetchone()[0], 1)
            # the problem -> capability JOIN resolves without narrative synthesis
            hit = [r[0] for r in c.execute(
                """SELECT cap.label FROM graph_nodes i
                   JOIN graph_edges e ON e.source=i.id AND e.rel='addressed_by'
                   JOIN graph_nodes cap ON cap.id=e.target
                   WHERE i.label='Billing Disputes'""")]
            self.assertEqual(hit, ["Self-Service Portal"])
            c.close()

    def test_rebuild_is_idempotent_and_owns_addressed_by(self):
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._run(td)
            # a second run must not duplicate the owned edges
            tax, cap, lnk = (os.path.join(td, n) for n in ("t.json", "c.json", "l.json"))
            r = subprocess.run([sys.executable, str(HERE / "build_graph.py"), "--taxonomy", tax,
                                "--capabilities", cap, "--links", lnk, "--db", db],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            c = sqlite3.connect(db)
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM graph_edges WHERE rel='addressed_by'").fetchone()[0], 1)
            c.close()


if __name__ == "__main__":
    unittest.main()
