from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import pandas as pd  # noqa: F401
    import build_marts as B  # imports openpyxl at module load
    _DEPS = True
except Exception:  # pragma: no cover - environment without pandas/openpyxl
    _DEPS = False


@unittest.skipUnless(_DEPS, "requires pandas + openpyxl")
class CoverageReportTests(unittest.TestCase):
    def _df(self, rows):
        return pd.DataFrame(rows, columns=["family", "metric", "grain", "entity", "month", "value", "source_file"])

    def test_month_range_inclusive_across_year_boundary(self):
        self.assertEqual(B._month_range("2025-11", "2026-02"),
                         ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_intra_family_hole_detects_disappearing_entity(self):
        # Southeast present only in Jan while peers run Jan–Mar -> hole with no config.
        rows = [("cv", "calls", "division", e, m, 1.0, "f")
                for m in ("2026-01", "2026-02", "2026-03") for e in ("NE", "MW")]
        rows.append(("cv", "calls", "division", "SE", "2026-01", 1.0, "f"))
        rep = B.coverage_report(self._df(rows), {})
        holes = {h["family"]: h["holes"] for h in rep["intra_family_holes"]}
        self.assertIn("cv", holes)
        self.assertEqual(holes["cv"]["SE"], ["2026-02", "2026-03"])
        # peers have no holes
        self.assertNotIn("NE", holes["cv"])

    def test_expected_roster_flags_missing_month(self):
        rows = [("rc", "n", "branch", "Chicago", m, 1.0, "f")
                for m in ("2026-03", "2026-04", "2026-06")]  # May missing
        rep = B.coverage_report(self._df(rows),
                                {"coverage": {"rc": {"month_range": ["2026-03", "2026-06"]}}})
        self.assertEqual(rep["expected_violations"],
                         [{"family": "rc", "grain": "branch", "missing_months": ["2026-05"]}])

    def test_expected_roster_flags_missing_entity(self):
        rows = [("cv", "calls", "division", "NE", "2026-01", 1.0, "f")]
        rep = B.coverage_report(self._df(rows),
                                {"coverage": {"cv": {"entities": ["NE", "Southeast"]}}})
        self.assertEqual(rep["expected_violations"],
                         [{"family": "cv", "grain": "division", "missing_entities": ["Southeast"]}])

    def test_grain_visibility_lists_family_grains(self):
        rows = [("wf", "occ", "overall", "company", "2026-01", 1.0, "f")]
        rep = B.coverage_report(self._df(rows), {})
        self.assertEqual(rep["family_grains"], {"wf": ["overall"]})

    def test_complete_grid_has_no_holes_or_violations(self):
        rows = [("cv", "calls", "division", e, m, 1.0, "f")
                for m in ("2026-01", "2026-02") for e in ("NE", "MW")]
        rep = B.coverage_report(self._df(rows), {"coverage": {"cv": {"month_range": ["2026-01", "2026-02"]}}})
        self.assertEqual(rep["intra_family_holes"], [])
        self.assertEqual(rep["expected_violations"], [])


if __name__ == "__main__":
    unittest.main()
