"""Restated figures are kept as vintages, never silently dropped (brain v6 finding).

A monthly snapshot workbook (layout matrix_month_cols) carries every prior month of
the year; a later workbook can restate earlier months. The build used to read only
the newest snapshot, so the earlier reported figures vanished without a trace. Files
were also ordered by NAME, so "latest wins" meant "alphabetically last" ("May" > "August").
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "tabular-semantic-layer"
sys.path.insert(0, str(HERE))

try:
    import openpyxl
    import pandas as pd  # noqa: F401
    import build_marts as B
    _DEPS = True
except Exception:  # pragma: no cover
    _DEPS = False

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August"]


def _snapshot(path: Path, upto: int, values: dict[str, float]):
    """A MOM sheet: row 0 title, row 1 month headers, then metric rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MOM"
    ws.append(["Workforce MOM"])
    ws.append(["Metric"] + [f"{m} 2026" for m in MONTHS[:upto]])
    ws.append(["Calls Abandoned"] + [values.get(m, 100.0) for m in MONTHS[:upto]])
    wb.save(path)


@unittest.skipUnless(_DEPS, "requires pandas + openpyxl")
class RestatementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.out = self.tmp / "out"

    def _build(self, families):
        cfg = self.tmp / "families.json"
        cfg.write_text(json.dumps({"families": families}))
        r = subprocess.run([sys.executable, str(HERE / "build_marts.py"), "--root", str(self.root),
                            "--config", str(cfg), "--out-dir", str(self.out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        con = sqlite3.connect(self.out / "knowledge.sqlite")
        con.row_factory = sqlite3.Row
        return con, json.loads((self.out / "build_audit.json").read_text()), r.stderr

    MOM = {"name": "wf", "glob": "*Daily*.xlsx", "month_from": "matrix_header",
           "layout": "matrix_month_cols", "sheet": "MOM", "month_header_row": 1, "metric_col": 0,
           "grains": {"overall": {"entity": "OVERALL"}},
           "measures": {"calls_abandoned": "Calls Abandoned"}}

    def test_snapshot_restatement_keeps_both_vintages(self):
        _snapshot(self.root / "June 2026 Daily.xlsx", 6, {"June": 41413})
        _snapshot(self.root / "July 2026 Daily.xlsx", 7, {"June": 42635, "July": 27862})
        _snapshot(self.root / "May 2026 Daily.xlsx", 5, {})
        con, audit, err = self._build([self.MOM])

        # facts: the newest report wins (July workbook, not the alphabetically-last "May")
        june = con.execute("SELECT value, source_file FROM facts WHERE month='2026-06'").fetchone()
        self.assertEqual((june["value"], june["source_file"]), (42635.0, "July 2026 Daily.xlsx"))
        # months only an older snapshot carries are still read
        self.assertEqual(con.execute("SELECT count(DISTINCT month) FROM facts").fetchone()[0], 7)

        # the June workbook's original figure survives as an earlier vintage
        rows = [dict(r) for r in con.execute(
            "SELECT value, source_file, reported_in, is_current FROM fact_versions "
            "WHERE month='2026-06' ORDER BY reported_in")]
        self.assertEqual(rows, [
            {"value": 41413.0, "source_file": "June 2026 Daily.xlsx", "reported_in": "2026-06", "is_current": 0},
            {"value": 42635.0, "source_file": "July 2026 Daily.xlsx", "reported_in": "2026-07", "is_current": 1},
        ])
        # unchanged months are not versions
        self.assertEqual(con.execute("SELECT count(*) FROM fact_versions WHERE month<>'2026-06'").fetchone()[0], 0)

        # operators see it at build time
        self.assertEqual((audit["summary"]["restated"], audit["summary"]["conflicts"]), (1, 0))
        r = audit["restatements"][0]
        self.assertEqual(r["kind"], "restated")
        self.assertEqual((r["metric"], r["month"], r["current"]["value"]), ("calls_abandoned", "2026-06", 42635.0))
        self.assertEqual(r["earlier"], [{"value": 41413.0, "source_file": "June 2026 Daily.xlsx"}])
        self.assertIn("RESTATED", err)

    def test_same_month_reports_that_differ_are_a_conflict(self):
        _snapshot(self.root / "June 2026 Daily A.xlsx", 6, {"June": 1})
        _snapshot(self.root / "June 2026 Daily B.xlsx", 6, {"June": 2})
        con, audit, _ = self._build([self.MOM])
        self.assertEqual((audit["summary"]["restated"], audit["summary"]["conflicts"]), (0, 1))
        self.assertEqual(con.execute("SELECT count(*) FROM fact_versions").fetchone()[0], 2)

    def test_unchanged_rereport_is_not_a_version(self):
        _snapshot(self.root / "June 2026 Daily.xlsx", 6, {"June": 41413})
        _snapshot(self.root / "July 2026 Daily.xlsx", 7, {"June": 42635})
        _snapshot(self.root / "August 2026 Daily.xlsx", 8, {"June": 42635})
        con, audit, _ = self._build([self.MOM])
        rows = [tuple(r) for r in con.execute(
            "SELECT value, source_file, is_current FROM fact_versions WHERE month='2026-06' ORDER BY is_current")]
        # the earlier value once (first reporter); current cites the same file `facts` does
        self.assertEqual(rows, [(41413.0, "June 2026 Daily.xlsx", 0), (42635.0, "August 2026 Daily.xlsx", 1)])

    def test_same_file_disagreement_is_a_collision_not_a_restatement(self):
        import pandas as pd
        df = pd.DataFrame([("f", "m", "g", "e", "2026-03", 1.0, "a.xlsx"),
                           ("f", "m", "g", "e", "2026-03", 2.0, "a.xlsx")],
                          columns=B.KEY + ["value", "source_file"])
        versions, restated, collisions = B.restatements(df, {"a.xlsx": "2026-03"})
        self.assertEqual((len(versions), restated), (0, []))
        self.assertEqual(collisions[0]["values"], [1.0, 2.0])

    def test_no_restatement_writes_empty_versions_table(self):
        _snapshot(self.root / "June 2026 Daily.xlsx", 6, {})
        _snapshot(self.root / "July 2026 Daily.xlsx", 7, {})
        con, audit, _ = self._build([self.MOM])
        self.assertEqual(con.execute("SELECT count(*) FROM fact_versions").fetchone()[0], 0)
        self.assertEqual(audit["summary"]["restated"], 0)

    def test_files_order_by_report_month_not_name(self):
        # "latest wins" must mean the newest report, not the alphabetically last file
        names = ["x/May 2026 Daily.xlsx", "x/August 2026 Daily.xlsx", "x/June 2026 Daily.xlsx",
                 "x/undated.xlsx"]
        self.assertEqual(sorted(names, key=B.vintage_key),
                         ["x/undated.xlsx", "x/May 2026 Daily.xlsx", "x/June 2026 Daily.xlsx",
                          "x/August 2026 Daily.xlsx"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(_DEPS, "requires pandas + openpyxl")
class TolerantGrainTests(unittest.TestCase):
    """A sheet carrying several dimension columns is keyed by the one that identifies
    its rows, not by whichever candidate the config lists first (brain v6: a by-Branch
    NPS export with a Region column became 254 colliding "region" facts)."""
    DIMS = {"division": ["Division"], "region": ["Region"], "branch": ["Branch Name", "Branch"]}
    MEAS = {"overall_nps": "Overall NPS", "n_records": "# of records"}

    def _load(self, rows):
        return B.load_tolerant_sheet(rows, {"name": "nps"}, "2026-01", self.DIMS, self.MEAS, {}, None)

    def test_branch_rows_with_a_region_column_are_branch_facts(self):
        rows = [("Survey Export",),
                ("Rank", "Branch Name", "Region", "RGM", "# of records", "Overall NPS"),
                (3, "NORTHFIELD", "R1", "A. B", 1, -100),
                (2, "EASTON", "R1", "A. B", 9, -88.9),
                (1, "WESTBROOK", "R2", "C. D", 50, -84)]
        facts, diag = self._load(rows)
        self.assertEqual(diag["grain"], "branch")
        nps = sorted((f[3], f[5]) for f in facts if f[1] == "overall_nps")
        self.assertEqual(nps, [("EASTON", -88.9), ("NORTHFIELD", -100.0), ("WESTBROOK", -84.0)])

    def test_single_dimension_sheet_unchanged(self):
        rows = [("Rank", "Region", "# of records", "Overall NPS"), (1, "R1", 10, 20), (2, "R2", 5, 30)]
        facts, diag = self._load(rows)
        self.assertEqual(diag["grain"], "region")
        self.assertEqual(len([f for f in facts if f[1] == "overall_nps"]), 2)
