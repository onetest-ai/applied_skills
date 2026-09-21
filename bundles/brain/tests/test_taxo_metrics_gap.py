import os
import tempfile
import unittest

import metrics_gap as MG
from taxo_fixtures import taxonomy, write_json

GOVERNED = {"_comment": "x", "metrics": {
    "average_handle_time": {"family": "calls", "metric": "average_handle_time", "desc": "average handle time"},
    "total_calls": {"family": "calls", "desc": "inbound calls"}}}


class GapTests(unittest.TestCase):
    def test_find_and_match(self):
        with tempfile.TemporaryDirectory() as td:
            write_json(os.path.join(td, "schema", "metrics.acme.json"), GOVERNED)
            write_json(os.path.join(td, "schema", "metrics.example.json"), {"metrics": {}})
            path = MG.find_governed(os.path.join(td, "taxonomy"))
            self.assertTrue(path.endswith("metrics.acme.json"))
            gov = MG.load_governed(path)
            self.assertEqual({g["key"] for g in gov}, {"average_handle_time", "total_calls"})
            by = {m["metric"]: m["governed"] for m in MG.annotate(taxonomy()["metrics"], gov)}
            self.assertEqual(by["Average Handle Time"]["status"], "yes")
            self.assertEqual(by["Porch Rate"]["status"], "no")

    def test_gap_lists_only_ungoverned_computable(self):
        md = MG.gap_markdown(taxonomy()["metrics"], MG.load_governed(None) or [])
        self.assertIn("| Porch Rate |", md)
        self.assertIn("| Average Handle Time |", md)
        self.assertNotIn("| Avg Handle Time |", md)   # stated, not computable
