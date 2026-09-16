from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "tabular-semantic-layer"
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


@unittest.skipUnless(_DEPS, "requires pandas + openpyxl")
class DerivedMetricsTests(unittest.TestCase):
    def _df(self, rows):
        return pd.DataFrame(rows, columns=["family", "metric", "grain", "entity", "month", "value", "source_file"])

    CFG = {"derived": [{"family": "cv", "metric": "calls_per_customer", "grain": "branch",
                        "numerator": "calls", "denominator": "customers"}]}

    def test_ratio_computed_and_cited_derived(self):
        rows = [("cv", "calls", "branch", "Chicago", "2026-01", 100.0, "f"),
                ("cv", "customers", "branch", "Chicago", "2026-01", 25.0, "f")]
        out = B.apply_derived(self._df(rows), self.CFG)
        d = out[out.metric == "calls_per_customer"]
        self.assertEqual(len(d), 1)
        self.assertEqual(float(d.value.iloc[0]), 4.0)
        self.assertEqual(d.source_file.iloc[0], "<derived>")

    def test_divide_by_zero_dropped(self):
        rows = [("cv", "calls", "branch", "Gardena", "2026-01", 50.0, "f"),
                ("cv", "customers", "branch", "Gardena", "2026-01", 0.0, "f")]
        out = B.apply_derived(self._df(rows), self.CFG)
        self.assertEqual(len(out[out.metric == "calls_per_customer"]), 0)  # honest, not inf

    def test_missing_operand_yields_no_row(self):
        rows = [("cv", "calls", "branch", "Reno", "2026-01", 30.0, "f")]  # no customers
        out = B.apply_derived(self._df(rows), self.CFG)
        self.assertEqual(len(out[out.metric == "calls_per_customer"]), 0)

    def test_scale_applied(self):
        rows = [("cv", "customers", "branch", "C", "2026-01", 25.0, "f"),
                ("cv", "calls", "branch", "C", "2026-01", 100.0, "f")]
        cfg = {"derived": [{"family": "cv", "metric": "pct", "grain": "branch",
                            "numerator": "customers", "denominator": "calls", "scale": 100.0}]}
        out = B.apply_derived(self._df(rows), cfg)
        self.assertEqual(float(out[out.metric == "pct"].value.iloc[0]), 25.0)


@unittest.skipUnless(_DEPS, "requires pandas + openpyxl")
class RollupCrosswalkTests(unittest.TestCase):
    def _df(self, rows):
        return pd.DataFrame(rows, columns=["family", "metric", "grain", "entity", "month", "value", "source_file"])

    def test_load_crosswalk_inline_case_insensitive(self):
        xw = B._load_crosswalk({"Gardena": "Central", "chicago ": "Central"})
        self.assertEqual(xw.get("gardena"), "Central")
        self.assertEqual(xw.get("chicago"), "Central")  # trimmed + lowered

    def test_load_crosswalk_none_is_empty(self):
        self.assertEqual(B._load_crosswalk(None), {})

    def test_crosswalk_rollup_is_weighted_not_naive(self):
        rows = [("nps", "score", "branch", "Gardena", "2026-01", 50.0, "f"),
                ("nps", "n", "branch", "Gardena", "2026-01", 100.0, "f"),
                ("nps", "score", "branch", "Chicago", "2026-01", 70.0, "f"),
                ("nps", "n", "branch", "Chicago", "2026-01", 300.0, "f")]
        cfg = {"rollups": [{"family": "nps", "from": "branch", "to": "division", "weight": "n",
                            "crosswalk": {"Gardena": "Central", "Chicago": "Central"}}]}
        out = B.apply_rollups(self._df(rows), cfg)
        div = out[out.grain == "division"]
        # weighted (50*100+70*300)/400 = 65, NOT the naive mean 60
        self.assertAlmostEqual(float(div[div.metric == "score"].value.iloc[0]), 65.0)
        self.assertEqual(float(div[div.metric == "n"].value.iloc[0]), 400.0)
        self.assertTrue((div.source_file == "<rollup>").all())

    def test_map_prefix_still_works(self):
        rows = [("nps", "score", "branch", "Gardena", "2026-01", 50.0, "f"),
                ("nps", "n", "branch", "Gardena", "2026-01", 100.0, "f")]
        cfg = {"rollups": [{"family": "nps", "from": "branch", "to": "division", "weight": "n",
                            "map_prefix": {"Gar": "West"}}]}
        out = B.apply_rollups(self._df(rows), cfg)
        self.assertEqual(sorted(out[out.grain == "division"].entity.unique()), ["West"])


if __name__ == "__main__":
    unittest.main()
