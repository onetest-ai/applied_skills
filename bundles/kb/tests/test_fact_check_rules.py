"""Pin the doc-fact-check SKILL.md rules that came out of the smoke baseline."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parent.parent
SKILL = Path(__file__).resolve().parent.parent / "skills" / "doc-fact-check" / "SKILL.md"


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


class TestFactCheckRootCauseRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")
        cls.step2 = flat(section(cls.text, "### 2.", "### 3."))
        cls.step3 = flat(section(cls.text, "### 3.", "### 4."))
        cls.step4 = flat(section(cls.text, "### 4.", "### 5."))
        cls.step9 = flat(section(cls.text, "### 9.", "### 10."))

    # RC-1
    def test_old_claim_count_target_is_gone(self):
        self.assertNotIn("Aim for", self.text)
        self.assertNotIn("≥15", self.text)

    def test_extract_every_checkable_statement_with_no_target(self):
        self.assertIn("every checkable statement", self.step2)
        self.assertIn("no count target", self.step2)
        self.assertIn("no sampling", self.step2)

    def test_coverage_self_check_present(self):
        self.assertIn("Coverage self-check", self.step2)
        self.assertIn("Re-walk any heading that has neither before verifying", self.step2)
        self.assertIn("50–90 claims", self.step2)
        self.assertIn("never as a cap", self.step2)

    # RC-2
    def test_step9_renders_after_the_final_merge_and_the_self_check_covers_the_banner(self):
        self.assertIn("render after the final merge (step 6a); the renderer reads run.json beside findings.json", self.step9)
        self.assertIn("coverage banner", self.text[self.text.index("### 10."):self.text.index("## Rules")])
        self.assertIn("fact_check_invariants.py", self.text)

    def test_figure_claims_must_be_findings_rows(self):
        self.assertIn("**must** appear in `findings.json` as its own row", self.step3)
        self.assertIn('"Figure"', self.step3)
        self.assertIn("word/media", self.step3)
        self.assertIn("no `I*` row", self.step3)
        self.assertIn("`I01…`", self.step9)
        self.assertIn('`type` and `section` "Figure"', self.step9)

    # RC-3 / RC-4: two-sided check is now a mandatory sequential algorithm (not a self-attestation).
    # The `checks` self-report field was removed because the model fabricated it (b8 evidence:
    # 23 claims declared opposing in one document but only 2 latest_only=false searches ran).
    # The new design requires explicit scratchpad steps (a)-(e) before Verified.
    def test_two_sided_check_is_sequential_algorithm(self):
        self.assertIn("execute every lettered step in order", self.step4)
        self.assertIn("record each result explicitly", self.step4)
        self.assertIn("latest_only=false", self.step4)
        self.assertIn("opposing query:", self.step4)
        self.assertIn("no opposing span found", self.step4)
        self.assertIn("otherwise the verdict is No Evidence", self.step4)
        self.assertIn("get_current_fact", self.step4)

class TestCoverageAndVerifiedRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t = SKILL.read_text(encoding="utf-8")
        cls.step2 = flat(section(t, "### 2.", "### 3."))
        cls.step4 = flat(section(t, "### 4.", "### 5."))
        cls.step5 = flat(section(t, "### 5.", "### 6."))
        cls.step9 = flat(section(t, "### 9.", "### 10."))
        cls.all = flat(t)

    # RC-1
    def test_section_is_the_heading_path_and_never_empty(self):
        self.assertIn("`section` is the heading path of its paragraph", self.step2)
        self.assertIn('"Figure" for `I*`', self.step2)
        self.assertIn("An empty `section` is invalid", self.step2)

    def test_coverage_self_check_walks_headings(self):
        self.assertIn("List every heading of the document", self.step2)
        self.assertIn("as the `section` of at least one finding", self.step2)
        self.assertIn("`coverage.json`", self.step2)
        self.assertIn('"no checkable statement"', self.step2)
        self.assertIn("Re-walk any heading that has neither", self.step2)
        self.assertNotIn("Count the non-heading paragraphs", self.step2)

    # RC-2
    def test_mandatory_claims(self):
        self.assertIn("Always extract", self.step2)
        for w in ("never", "always", "all", "none", "no", "only", "every", "consistent across"):
            self.assertIn(w, self.step2)
        self.assertIn("absolute or universal statements", self.step2)
        self.assertIn("who owns, operates, attended, decided", self.step2)
        self.assertIn("every number with a unit or count, and every date", self.step2)

    # RC-3
    def test_verified_needs_positive_evidence(self):
        self.assertIn("same subject, scope and period as the claim", self.step4)
        self.assertIn("A related or partially supporting passage is not enough", self.step4)
        self.assertIn("Misleading or No Evidence", self.step4)

    def test_opposing_evidence_blocks_verified(self):
        self.assertIn("any statement whose value, owner, status or date differs for the same subject", self.step4)
        self.assertIn("cannot be Verified", self.step4)
        self.assertIn("newer or superseding", self.step4)
        self.assertIn("same-period disagreement", self.step4)

    def test_num_claims_with_time_or_status_dimension_are_two_sided(self):
        self.assertIn("NUM claims that carry a time or status dimension", self.step4)
        for m in ('"currently"', '"today"', '"~N"', '"now"', "a year"):
            self.assertIn(m, self.step4)
        self.assertIn("run steps **b** and **d** of the two-sided check sequence below", self.step4)

    # RC-4
    def test_findings_json_is_top_level_array_and_coverage_is_separate(self):
        self.assertIn("top-level JSON array of finding objects", self.step9)
        self.assertIn("never an object wrapper", self.step9)
        self.assertIn("`coverage.json` beside findings.json", self.step9)

    # RC-5
    def test_no_client_names_in_worked_example(self):
        self.assertIn("`The speaker did not establish whether system A pushes the result into system B or B reads it.` "
                      "→ OWN (who controls that path?)", self.all)

    def test_no_self_reported_checks_field(self):
        # the field was fabricated in real runs; the skill must not ask the model to self-report searches
        self.assertNotIn("record `checks`", self.all)
        self.assertNotIn("without both checks is invalid", self.all)

    def test_num_with_time_dimension_runs_sequence_steps(self):
        self.assertIn("run steps **b** and **d** of the two-sided check sequence below", self.all)


class TestSectionParallelRules(unittest.TestCase):
    """Section-parallel design: deterministic sections, per-batch subagents, deterministic merge."""

    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")
        cls.step1 = flat(section(cls.text, "### 1.", "### 1b."))
        cls.dispatch = flat(section(cls.text, "### 1b.", "### 2."))
        cls.step2 = flat(section(cls.text, "### 2.", "### 3."))
        cls.step3 = flat(section(cls.text, "### 3.", "### 4."))
        cls.merge = flat(section(cls.text, "### 6a.", "### 6b."))

    def test_step_1_runs_sections_cli_not_improvised_extraction(self):
        self.assertIn('python "<skill dir>/sections.py" "<draft dir>/<name>.docx" --out "<work dir>"', self.step1)
        self.assertIn("--max-words 1500", self.step1)
        self.assertIn("`sections.json`", self.step1)
        self.assertIn("`batches.json`", self.step1)
        self.assertNotIn("Run python (`python-docx`) in the session", self.step1)
        self.assertIn("Never rely on a summary to read the draft", self.step1)
        self.assertIn("word/embeddings/*", self.step1)            # embedded objects still read

    def test_step_1_reads_the_expanded_embedded_xml_and_logs_unreadable_objects(self):
        for t in ("XML parts under `<work dir>/embedded/<object>/`", "`<work dir>/embedded/charts/`",
                  "`xl/sharedStrings.xml` and `xl/worksheets/*.xml` for a workbook", "`word/document.xml` for a document",
                  "the cached values in a chart's XML",
                  'An object the summary reports as "not readable" becomes one `I*` claim with verdict No Evidence '
                  'and evidence "embedded object not readable (<name>)"'):
            self.assertIn(t, self.step1)

    def test_dispatch_step_sits_between_1_and_2(self):
        t = self.text
        self.assertLess(t.index("### 1."), t.index("### 1b. Extract, chunk, verify (two stages)"))
        self.assertLess(t.index("### 1b. Extract, chunk, verify (two stages)"), t.index("### 2."))

    def test_dispatch_in_waves_of_wave_size(self):
        d = self.dispatch
        self.assertIn("waves of `wave_size` (default 10)", d)
        for item in ("`<work dir>/batch_<k>.json`", "absolute path of this SKILL.md", "`<work dir>/claims_batch_<k>.json`",
                     "`<work dir>/coverage_batch_<k>.json`", "`<work dir>/findings_chunk_<j>.json`"):
            self.assertIn(item, d)

    def test_subagents_read_step_2_then_steps_4_to_6(self):
        d = self.dispatch
        self.assertIn("reads **step 2** here", d)
        self.assertIn("reads **steps 4–6** here", d)
        self.assertIn('"reason": "no checkable statement"', d)

    def test_sequential_fallback_without_the_agent_tool(self):
        self.assertIn("only when the Agent tool is not in your tool list", self.dispatch)
        self.assertIn("runs the same stages sequentially", self.dispatch)

    def test_figures_stay_in_the_main_session(self):
        self.assertIn("main session", self.step3)
        self.assertIn("`<work dir>/claims_figures.json`", self.step3)

    def test_step3_transcribes_claims_and_verifies_nothing(self):
        self.assertIn("transcribe", self.step3)
        self.assertIn("does not verify", self.step3)
        self.assertIn('section: "Figure"', self.step3)

    def test_step3_writes_omission_claims_as_i_claims(self):
        self.assertIn("absent from figure <n>", self.step3)
        self.assertIn("omission claim", self.step3)

    def test_figure_claims_are_always_in_scope_and_verified_in_stage_v(self):
        self.assertIn("claims_figures.json", self.dispatch)
        self.assertIn("always in scope", self.dispatch)
        self.assertIn("anchor: 'drawing'", self.dispatch)

    def test_step3_schema_has_kind_and_figure_and_embedded_anchoring(self):
        for s in ('kind', '"embedded"', "`figure` is that figure's int number", 'anchor: "paragraph"'):
            self.assertIn(s, self.step3)
        self.assertIn("Numeric/date self-check before answering", self.text)
        self.assertNotIn("Figure self-check", self.text)

    def test_no_findings_figures_file_is_left(self):
        self.assertNotIn("findings_figures", self.text)

    def test_merge_runs_via_its_cli_before_step_7(self):
        self.assertIn('python "<skill dir>/merge_findings.py" "<work dir>" --out "<run dir>/findings.json"', self.merge)
        self.assertLess(self.text.index("### 6a."), self.text.index("### 7."))
        self.assertIn("On failure it lists", self.merge)
        self.assertIn("renumbers", self.merge)

    def test_step_2_coverage_is_enforced_by_the_merge(self):
        self.assertIn("`merge_findings.py` enforces section coverage", self.step2)
        self.assertIn("`coverage_batch_<k>.json`", self.step2)


class TwoModeTwoStageTests(unittest.TestCase):
    def setUp(self):
        self.all = (KB_ROOT / "skills" / "doc-fact-check" / "SKILL.md").read_text(encoding="utf-8")

    def test_step0b_mode_selection(self):
        for t in ("### 0b. Choose the mode", '"fast", "quick", "scan"', '"deep", "full", "thorough", "sign-off", "final"',
                  "ask once", "mode required: fast or deep"):
            self.assertIn(t, self.all)

    def test_step1b_two_stages_and_parameters(self):
        for t in ("Stage E", "Stage V", "chunk_claims.py\" \"<work dir>\" --scope", "at most 8 claims",
                  "per claim, never batched across claims", "waves of `wave_size` (default 10)",
                  "| `scope` | `all` | `risk` |"):
            self.assertIn(t, self.all)

    def test_fallback_runs_same_stages(self):
        self.assertIn("runs the same stages sequentially", self.all)

    def test_merge_and_report_use_scope_and_run(self):
        for t in ("merge_findings.py\" \"<work dir>\" --out \"<run dir>/findings.json\" --scope", "--run \"<run dir>/run.json\"",
                  "findings_report.py\" --coverage-line \"<run dir>/run.json\""):
            self.assertIn(t, self.all)

    def test_dry_no_second_procedure_and_patterns_only_in_sections(self):
        self.assertEqual(self.all.count("### 2. Extract atomic claims"), 1)
        self.assertEqual(self.all.count("### 4. Verify"), 1)
        self.assertNotIn("findings_batch_", self.all)
        for word in ("consistent across", "operated by", "managed by"):
            self.assertNotIn(word, self.all.split("### 2. Extract atomic claims")[0])
        sections_src = (KB_ROOT / "skills" / "doc-fact-check" / "sections.py").read_text(encoding="utf-8")
        self.assertIn("RISK_PATTERNS", sections_src)


class FixRound1Tests(unittest.TestCase):
    def setUp(self):
        self.all = flat((KB_ROOT / "skills" / "doc-fact-check" / "SKILL.md").read_text(encoding="utf-8"))

    def test_fix_round_1_sentences(self):
        for t in (
            "**The reply's** first line is the output of `python \"<skill dir>/findings_report.py\" --coverage-line \"<run dir>/run.json\"`, verbatim.",
            "the 'reused baseline of <date>, N claims' line comes second. Then the one-or-two-sentence answer.",
            "send up to `wave_size` dispatches in one message, wait for the whole wave to finish, then send the next wave.",
            "except `destination`, which step 8 sets",
            "copy `p_id`, `quote`, `section` and `type` from the chunk's claim unchanged",
            "Run the two-sided sequence per claim (step 4); never batch one search across several claims.",
            "still writes `claims_batch_<k>.json` as `[]`",
            "does not run steps 3–10 and does not dispatch subagents",
            "in stage E the claim id field is `claim_id` (`B<k>-C<n>`), renumbered to `C01…` by the merge",
            "use the `s_id` of the sentence holding its main assertion",
            "step 3 (transcribes figures and embedded objects into `<work dir>/claims_figures.json`)",
            "### 6a. Merge the chunks",
            "figure ids `I*` are kept",
            "In fast mode `findings.json` holds only the in-scope claims that stage V verified; never add rows for out-of-scope claims",
            "`estimate_minutes.fast` and `estimate_minutes.deep`",
            "stop at this step, before step 1, with \"mode required: fast or deep\" and write nothing else",
        ):
            self.assertIn(t, self.all)
        self.assertEqual(self.all.count("wait for the whole wave to finish"), 2)
        self.assertNotIn("Batch Brain calls per section", self.all)
        self.assertNotIn("Merge the batches", self.all)


class FixRound2Tests(unittest.TestCase):
    def setUp(self):
        self.all = flat((KB_ROOT / "skills" / "doc-fact-check" / "SKILL.md").read_text(encoding="utf-8"))

    def test_baseline_line_is_second(self):
        self.assertNotIn("first line of the report says", self.all)
        self.assertIn("the reply's second line, after the coverage line (step 10), says \"reused baseline of <date>, N claims\"", self.all)

    def test_merge_runs_before_6b(self):
        self.assertIn("Run it before step 6b.", self.all)
        self.assertIn("Run it before step 6b. Steps 6b–10 work on the merged `findings.json`, never on the chunk files.", self.all)


class FinalFixWaveRules(unittest.TestCase):
    def setUp(self):
        self.all = flat((KB_ROOT / "skills" / "doc-fact-check" / "SKILL.md").read_text(encoding="utf-8"))

    def test_rerunning_stage_e_means_rechunk_and_reverify(self):
        self.assertIn("After re-running a batch's stage E, re-run `chunk_claims.py` and re-verify every chunk "
                      "(stale `findings_chunk_*` files are deleted by the re-chunk).", self.all)

    def test_merge_section_rule_and_figure_append_wording(self):
        self.assertIn("neither a claim nor a coverage entry", self.all)
        self.assertNotIn("neither a finding nor a coverage entry", self.all)
        self.assertNotIn("appends it after the batches", self.all)
        self.assertIn("figure ids `I*` are kept and listed last", self.all)
        self.assertIn("`{section_id, reason}`", self.all)

    def test_stage_v_does_not_tell_the_agent_to_copy_kind(self):
        stage = self.all[self.all.index("### 1b."):self.all.index("### 2.")]
        self.assertIn("do not copy `kind`", stage)
        self.assertNotIn("copy `kind`", stage.replace("do not copy `kind`", ""))

    def test_stage_v_copies_anchor_and_figure_for_figure_claims(self):
        stage = self.all[self.all.index("### 1b."):self.all.index("### 2.")]
        self.assertIn("`figure` (the claim's own int", stage)
        self.assertIn("anchor: 'drawing'", stage)

    def test_baseline_header_records_the_mode_and_reuse_requires_it_to_match(self):
        self.assertIn("recording the document's `source_sha256` (from `stats.json`), the mode (from `run.json`), the `knowledge_version`", self.all)
        self.assertIn("its mode equals the requested mode", self.all)


class FinalReviewPins(unittest.TestCase):
    """Final whole-branch review: commands runnable as written, placeholders defined once, honest No Evidence."""

    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")
        cls.all = flat(cls.text)
        cls.inputs = flat(section(cls.text, "## Inputs", "## Procedure"))
        cls.step0 = flat(section(cls.text, "### 0. Resolve", "### 0b."))

    def test_every_dir_and_name_placeholder_in_a_command_is_quoted(self):
        lines = [l for l in self.text.splitlines() if 'python "<skill dir>/' in l]
        self.assertGreaterEqual(len(lines), 8)
        for line in lines:
            for cmd in re.findall(r'python "<skill dir>/[^`]*', line):
                with self.subTest(cmd=cmd):
                    bare = re.sub(r'"[^"]*"', '""', cmd)
                    self.assertIsNone(re.search(r"<[^<>]*dir>|<name>", bare), cmd)
                    self.assertNotIn("[--", cmd)

    def test_step_9_command_is_runnable_as_written(self):
        step9 = flat(section(self.text, "### 9.", "### 10."))
        self.assertIn('python "<skill dir>/findings_report.py" "<run dir>/findings.json" --out "<draft dir>/<name> — findings.html" '
                      '--document "<name>.docx" --brain-version <knowledge_version> --run "<run dir>/run.json"', step9)
        for flag in ("--brain-name", "--title", "--eyebrow", "--lede", "--output-name"):
            self.assertIn(flag, step9)

    def test_outputs_live_in_the_draft_dir(self):
        for t in ('"<draft dir>/<name> — fact-checked.docx"', '"<draft dir>/<name> — findings.html"'):
            self.assertIn(t, self.all)
        self.assertNotIn('"<name> — fact-checked.docx" --approved', self.all)

    def test_placeholders_are_defined_once_in_inputs(self):
        defs = ("`<draft dir>` is the folder holding the draft and `<name>` its file name without `.docx`",
                "`<doc-slug>` is `<name>` in lower case with spaces and punctuation turned into `-`",
                "`<run dir>` is `docs/kb/doc-fact-check/<doc-slug>/`",
                "`<work dir>` is `<run dir>/work/`")
        for d in defs:
            with self.subTest(d=d):
                self.assertIn(d, self.inputs)
                self.assertEqual(self.all.count(d), 1)
        self.assertEqual(self.all.count("docs/kb/doc-fact-check/<doc-slug>/"), 1)
        self.assertNotIn("is the `<work dir>`", self.all)
        self.assertNotIn("(`<run dir>`)", self.all)

    def test_step_0_writes_brain_context_with_the_write_tool_after_0b(self):
        self.assertIn("After step 0b, write `<work dir>/brain_context.json` with the Write tool (it creates the folder)",
                      self.step0)
        self.assertNotIn("create `<work dir>`", self.all)

    def test_stops_write_nothing_else(self):
        self.assertIn('"doc-fact-check needs Python with python-docx ≥ 1.2 in this environment; none is available here. '
                      'Run it in Claude Code.", and write nothing else', self.step0)
        self.assertIn('stop at this step, before step 1, with "mode required: fast or deep" and write nothing else', self.all)

    def test_python3_fallback_and_pip_through_the_interpreter(self):
        self.assertIn("If `python` is not found, use `python3` for every command in this skill", self.step0)
        self.assertIn('run `python -m pip install "python-docx>=1.2"` (or `python3 -m pip install "python-docx>=1.2"`) once',
                      self.step0)
        self.assertNotIn("run `pip install", self.all)

    def test_no_evidence_names_its_own_queries(self):
        self.assertIn("A No Evidence finding's `evidence` names this claim's own queries and tools and what each returned.",
                      self.all)

    def test_baseline_full_match_reuses_the_merged_files(self):
        self.assertIn("On a full match, reuse `<run dir>/findings.json`, `coverage.json` and `run.json` and continue at step 7; "
                      "never rebuild `findings.json` from `baseline.md` by hand.", self.inputs)


if __name__ == "__main__":
    unittest.main()

class TestStatementCoverageRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = SKILL.read_text(encoding="utf-8")
        cls.stage_e = flat(section(text, "1. **Stage E", "2. **Chunk."))
        cls.step2 = flat(section(text, "### 2.", "### 3."))
        cls.chunk = flat(section(text, "2. **Chunk.", "3. **Stage V"))

    def test_step2_multi_sentence_claim_lists_every_sentence_in_s_ids(self):
        self.assertIn("lists every sentence it spans in `s_ids`", self.step2)
        self.assertIn("anchor", self.step2)

    def test_stage_e_writes_per_sentence_waivers(self):
        self.assertIn('{s_id, reason: "no checkable statement"}', self.stage_e)
        self.assertIn("only for a tagged sentence with nothing checkable", self.stage_e)

    def test_section_entry_only_for_a_section_without_claims(self):
        self.assertIn("is only for a section with no claims at all", self.stage_e)

    def test_stage_e_is_told_the_scope(self):
        self.assertIn("and the mode's `scope` (`all` or `risk`)", self.stage_e)
        self.assertIn("A Fast (`risk`) stage E claims or waives every risk-tagged sentence and row", self.stage_e)

    def test_stage_e_claim_shape_documents_optional_s_ids(self):
        self.assertIn("optional `s_ids`", self.stage_e)

    def test_chunk_step_checks_statement_coverage_before_stage_v(self):
        self.assertIn("before any chunk is written", self.chunk)
