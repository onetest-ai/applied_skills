"""run_clock.py: per-stage timings for doc-fact-check runs (spec §4.7)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

import run_clock as R

SCRIPT = KB_ROOT / "skills" / "doc-fact-check" / "run_clock.py"


class Clock(unittest.TestCase):
    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp())
        self.work = self.run_dir / "work"
        self.work.mkdir()

    def test_start_end_and_total(self):
        R.record(self.work, "extract", "start", now="2026-10-06T10:00:00+00:00")
        doc = R.record(self.work, "extract", "end", workers=3, now="2026-10-06T10:04:30+00:00")
        self.assertEqual(doc["timings"]["extract"], {"start": "2026-10-06T10:00:00+00:00",
                                                     "end": "2026-10-06T10:04:30+00:00"})
        self.assertEqual(doc["workers"], {"extract": 3})
        self.assertEqual(doc["minutes_total"], 4.5)
        self.assertEqual(json.loads((self.work / "timings.json").read_text()), doc)

    def test_mirrors_into_run_json_when_present(self):
        (self.run_dir / "run.json").write_text(json.dumps({"mode": "fast"}))
        R.record(self.work, "merge", "start", now="2026-10-06T10:00:00+00:00")
        R.record(self.work, "merge", "end", now="2026-10-06T10:01:00+00:00")
        run = json.loads((self.run_dir / "run.json").read_text())
        self.assertEqual(run["mode"], "fast")
        self.assertEqual(run["minutes_total"], 1.0)
        self.assertIn("merge", run["timings"])

    def test_works_before_run_json_exists(self):
        R.record(self.work, "extract", "start", now="2026-10-06T10:00:00+00:00")
        self.assertFalse((self.run_dir / "run.json").exists())
        self.assertTrue((self.work / "timings.json").exists())

    def test_end_without_start_is_reported_not_fatal(self):
        doc = R.record(self.work, "verify", "end", now="2026-10-06T10:00:00+00:00")
        self.assertEqual(doc["missing_start"], ["verify"])

    def test_unknown_stage_exits_2(self):
        p = subprocess.run([sys.executable, str(SCRIPT), str(self.work), "lunch", "start"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_extract_start_begins_a_new_run(self):  # M7
        R.record(self.work, "extract", "start", now="2026-10-01T10:00:00+00:00")
        R.record(self.work, "verify", "end", workers=4, now="2026-10-01T11:00:00+00:00")
        doc = R.record(self.work, "extract", "start", now="2026-10-06T10:00:00+00:00")
        self.assertEqual(doc["timings"], {"extract": {"start": "2026-10-06T10:00:00+00:00"}})
        self.assertEqual((doc["workers"], doc["minutes_total"], doc["missing_start"]), ({}, None, []))

    def test_other_stage_start_keeps_earlier_timings(self):  # M7
        R.record(self.work, "extract", "start", now="2026-10-06T10:00:00+00:00")
        doc = R.record(self.work, "chunk", "start", now="2026-10-06T10:05:00+00:00")
        self.assertEqual(set(doc["timings"]), {"extract", "chunk"})

    def test_non_dict_timings_or_workers_are_treated_as_empty(self):  # T6
        (self.work / "timings.json").write_text(json.dumps({"timings": [1, 2], "workers": "x"}))
        doc = R.record(self.work, "merge", "start", workers=1, now="2026-10-06T10:00:00+00:00")
        self.assertEqual(doc["timings"], {"merge": {"start": "2026-10-06T10:00:00+00:00"}})
        self.assertEqual(doc["workers"], {"merge": 1})
        (self.work / "timings.json").write_text(json.dumps({"timings": {"merge": "bad", "chunk": {"start": 3}},
                                                            "workers": {}}))
        doc = R.record(self.work, "merge", "end", now="2026-10-06T10:01:00+00:00")
        self.assertEqual(doc["timings"]["merge"], {"end": "2026-10-06T10:01:00+00:00"})
        self.assertIsNone(doc["minutes_total"])

    def test_page_shows_minutes(self):
        import findings_report
        html = findings_report.render_html([], run={"mode": "fast", "minutes_total": 12.5,
                                                    "claims_verified": 0, "claims_in_scope": 0,
                                                    "claims_extracted": 0, "sections_total": 0})
        self.assertIn("Minutes <b>12.5</b>", html)


if __name__ == "__main__":
    unittest.main()
