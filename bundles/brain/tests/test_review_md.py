import os
import tempfile
import unittest
from datetime import datetime, timezone

import review_md as MD
import taxonomy_review as R
from taxo_fixtures import taxonomy, write_json

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


class MdTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = os.path.join(self.td.name, "taxonomy")
        self.v0 = os.path.join(d, "taxonomy_v0.json")
        write_json(self.v0, taxonomy())
        _, self.rv = R.build_plan("draft", self.v0, now=NOW)
        self.tax = taxonomy()
        self.ids = {i["op"]["node"]: i["id"] for i in self.rv["items"]}

    def tearDown(self):
        self.td.cleanup()

    def test_round_trip_adds_nothing(self):
        text = MD.export_md(self.rv, [])
        self.assertEqual(MD.import_md(text, self.rv, self.tax, [], "Pat"), [])

    def test_filled_decisions_and_proposals(self):
        text = MD.export_md(self.rv, [])
        text = text.replace(f"### {self.ids['Transform']} ·", f"### {self.ids['Transform']} ·", 1)
        lines = text.splitlines()
        out, cur = [], None
        for line in lines:
            if line.startswith("### "):
                cur = line.split()[1]
            if line == "decision: " and cur == self.ids["Transform"]:
                line = "decision: remove: entity:initiative"
            if line == "decision: " and cur == self.ids["Billing & Payments Admin"]:
                line = 'decision: merge: "Billing & Payments"'
            out.append(line)
        out += ['propose: add L2 "Payment Plans" under "Billing & Payments"',
                'propose: metric_merge "Avg Handle Time" -> "Average Handle Time"',
                'propose: metric_edit "Porch Rate" grain=division']
        recs = MD.import_md("\n".join(out), self.rv, self.tax, [], "Pat")
        self.assertEqual([r["action"] for r in recs], ["amend", "amend", "propose", "propose", "propose"])
        self.assertTrue(all(r["surface"] == "markdown" for r in recs))
        self.assertEqual(recs[1]["op"], {"type": "remove", "node": "Transform", "disposition": "entity:initiative",
                                         "reason": ""})

    def test_every_bad_line_reported(self):
        text = MD.export_md(self.rv, []) + "\npropose: frobnicate \"x\"\npropose: move \"Nope\" -> \"Transform\"\n"
        text = text.replace("decision: ", "decision: explode", 1)
        with self.assertRaises(MD.MdImportError) as cm:
            MD.import_md(text, self.rv, self.tax, [], "Pat")
        self.assertEqual(len(cm.exception.errors), 3)
        self.assertTrue(all(e.startswith("line ") for e in cm.exception.errors))

    def test_examples_in_export_are_not_parsed(self):
        text = MD.export_md(self.rv, [])
        self.assertIn('    propose: rename "Old label" -> "New label"', text)
