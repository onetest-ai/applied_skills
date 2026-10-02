"""Pin the fact-check SKILL.md rules that came out of the smoke baseline."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "fact-check" / "SKILL.md"


def section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


def flat(t: str) -> str:
    return re.sub(r"\s+", " ", t)


class TestFactCheckRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")
        cls.step4 = flat(section(cls.text, "### 4.", "### 5."))
        cls.step5 = flat(section(cls.text, "### 5.", "### 6."))
        cls.rules = flat(section(cls.text, "## Rules", "## The Brain contract"))
        cls.s6b = flat(section(cls.text, "### 6b.", "### 6c."))

    def has(self, hay, *phrases):
        for ph in phrases:
            self.assertIn(ph, hay)

    def time_bullet(self):
        return next(l for l in self.step4.split("- ") if l.startswith("TIME"))

    # FX-2
    def test_time_status_owner_runs_two_searches(self):
        b = self.time_bullet()
        self.has(b, "run `search_knowledge` twice with the same query: once default, once with `latest_only=false`",
                 "`get_current_fact` when an entity/predicate exists",
                 "cite both dated sources",
                 "the `Brain:` line carries both, as \"newer (date) vs older (date)\"")

    def test_replaced_means_explicit_supersedes_only(self):
        b = self.time_bullet()
        self.has(b, "An older source counts as replaced only by an explicit supersedes/retracts relation "
                    "(`get_current_fact`, or a SUPERSEDED status); otherwise apply step 5")
        self.assertNotIn("replaced by a newer one", b)

    # FX-20
    def test_never_compute_with_carve_out(self):
        self.has(self.step4,
                 "never compute, sum, average, round or convert a figure you write into `evidence` or `fix`",
                 "quoted exactly as a tool returned it",
                 "the Brain holds the components, not the total",
                 "Comparing the draft's figure with a tool value is allowed",
                 "a rounded draft figure that matches the tool value at its stated precision is Verified",
                 "counting items in the draft is not a computed figure",
                 "A unit gap with no tool-returned conversion is Misleading when the Brain holds the figure at another unit, otherwise No Evidence",
                 "the `fix` is \"needs owner input\"",
                 "findings page and `baseline.md`")
        self.has(self.rules, "Never compute, sum, average, round or convert a figure you write into `evidence` or `fix`",
                 "the Brain holds the components, not the total")
        self.assertNotIn("EVIDENCE", self.step4 + self.rules)
        self.assertNotIn("FIX", self.step4 + self.rules)

    # Outdated definition
    def test_outdated_needs_two_statements(self):
        self.has(self.step5,
                 "Outdated must cite the superseded statement and the current one",
                 "a chunk span, a `get_current_fact` result, or a `get_metric_history` row with `reported_in`",
                 "If only one is found it is not Outdated; it takes the verdict that one source supports (Incorrect or Verified), stated explicitly")

    # FX-1
    def test_6b_dispatch_scope_and_verdicts(self):
        s = self.s6b
        self.has(s, "only for findings whose evidence is a **chunk id** (pass the chunk ids exactly as returned; do not reformat them) or a **governed metric row**",
                 "not Outdated or Controversial findings",
                 "For a finding on a draft figure, a verifier \"uncited-number\" leaves the verdict unchanged, because the draft carries no citation tags; No Evidence applies only when the finding's own evidence figure has no tool source")
        self.assertNotIn("A finding whose citation it cannot re-resolve drops to No Evidence", s)
        self.assertNotIn("\"unsupported\" or \"uncited-number\" drops it to No Evidence", s)

    def test_6b_outdated_controversial_checked_by_content(self):
        s = self.s6b
        self.has(s, "stands only when each re-opened chunk (`get_evidence` on its chunk id) contains the quoted span verbatim "
                    "and the date cited for it matches that chunk's `event_date` (or the source's own date when `event_date` is null)",
                 "Otherwise it goes through the normal checks and takes the verdict the remaining verified source supports")

    def test_6b_metric_backed_conflicts_reread_not_verifier(self):
        s = self.s6b
        self.has(s, "A metric-backed Outdated or Controversial finding (`restated`, `conflicting`, `other_reported_values`) "
                    "is checked by re-reading the same `get_metric` / `get_metric_history` rows: both values and their `reported_in` or source must be present. It is not sent to `verifier`")

    def test_6b_self_contradiction(self):
        self.has(self.s6b, "contradicts itself (two spans of the draft disagree) needs no Brain source and no `verifier`: "
                           "check that both quoted spans exist verbatim in the extracted draft")

    # W2-1
    def test_numbers_compare_not_compute(self):
        self.has(self.step4,
                 "When a governed row (`get_metric` / `get_metric_history`) or a chunk states a value for the same subject, grain and period as the draft, compare it with the draft",
                 "equal at the draft's stated precision is Verified",
                 "a different value is **Incorrect**",
                 "the same value at another grain, period or scope is **Misleading**",
                 "Quote the draft value and the Brain value verbatim, each with its source and date",
                 "No Evidence is only for a claim that no row or chunk covers at all")

    # W2-2
    def test_6b_verifier_judges_the_draft_claim(self):
        s = self.s6b
        self.has(s, "`verifier` judges the draft's claim",
                 "For an Incorrect or Misleading finding, a verifier verdict of `unsupported` or `grain-mismatch` **confirms** the finding and the verdict stays",
                 "Only a verifier `verified` overturns a finding, and then the finding is **re-examined** by re-reading the cited chunk or row")

    # W2-4
    def test_quoted_figures_and_self_check(self):
        s = flat(section(self.text, "### 6c.", "### 7."))
        self.has(s, "Quote every figure in the `evidence` field in quotation marks, each with its source",
                 "every figure in `evidence` and `fix` appears verbatim in a tool result or in the draft's own text",
                 "remove or re-source any that does not")

    def test_prose_figure_ruling(self):
        for hay in (self.step4, self.rules):
            self.has(hay, "A figure from a narrative chunk may be used only as a verbatim quotation of the chunk span, attributed with its chunk id and date",
                     "never restate, convert or compute with it")
        self.assertNotIn("never take a number from prose", self.step4)
        self.has(self.step4, "Governed figures come only from `get_metric`, `get_metric_history` or an extracted table cell")

    def test_total_row_and_differing_value(self):
        for hay in (self.step4, self.rules):
            self.has(hay, "If a governed total row exists, compare it; if only components exist, the total is No Evidence (components listed verbatim)")
        self.has(self.step4, "this never turns a differing value into No Evidence")
        self.has(self.rules, "A differing value in a covering row or chunk is Incorrect or Misleading, never No Evidence")

    def test_6b_dispatch_payload_and_reexamination(self):
        s = self.s6b
        self.has(s, "pass the draft's verbatim quote as the claim, plus the chunk ids or metric reference of the evidence, never the Brain-side value as the claim",
                 "if it still contradicts the draft, the finding stands and both readings are recorded; if it supports the draft, the claim becomes Verified; if it cannot be re-opened, it is No Evidence",
                 "An Incorrect finding with `grain-mismatch` is re-checked for Misleading (right value at another grain)")
        self.assertNotIn("any other finding", s)

    def test_6c_after_6b_and_figure_definition(self):
        self.assertLess(self.text.index("### 6b."), self.text.index("### 6c."))
        s = flat(section(self.text, "### 6c.", "### 7."))
        self.has(s, "figure means a numeric value or a date",
                 "A rounded draft figure is covered by the draft's own text")


if __name__ == "__main__":
    unittest.main()
