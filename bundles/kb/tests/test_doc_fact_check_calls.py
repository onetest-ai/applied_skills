"""doc-fact-check call economy: once-per-run Brain calls stay in the main session; quote problems are reported by the chunker.

Measured on 5 real fast runs: every repeated health / list_metrics / list_sources call and every
(always empty) get_current_fact call came from stage-E/V workers, because a worker reads this whole
SKILL.md, Brain contract included, and acts as if it were a new invocation.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import chunk_claims as C
from test_plugin_structure import KB_ROOT

SKILL_DIR = KB_ROOT / "skills" / "doc-fact-check"
SKILL = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
WORKER_LINE = ("You are a doc-fact-check worker. The main session has already resolved the Brain and read its "
               "basics into `<work dir>/brain_context.json`: do not run step 0, step 0b or the Brain contract's "
               "resolution steps, and never call `health`, `list_metrics` or `list_sources`. "
               "Every Verified finding that is not a number claim records the step-4 name check in its `trail` "
               "(`name check: <name> → <what the Brain uses it for>`, or `name check: none`). "
               "Follow the command rules in Inputs.")


def section(start, end):
    return SKILL[SKILL.index(start):SKILL.index(end)]


class OncePerRunCallsStayInTheMainSession(unittest.TestCase):
    def test_step_0_writes_brain_context(self):
        step0 = section("### 0. Resolve", "### 0b.")
        self.assertIn("write `<work dir>/brain_context.json` with the Write tool (it creates the folder): "
                      "`knowledge_version`, `about`, `metric_names` (names only) and `has_current_facts`", step0)

    def test_both_worker_dispatches_start_with_the_worker_line(self):
        step1b = section("### 1b.", "### 2.")
        self.assertEqual(SKILL.count(WORKER_LINE), 1, "the worker line is defined once")
        self.assertIn(WORKER_LINE, step1b)
        for stage in ("**Stage E (extract)", "**Stage V (verify)"):
            para = step1b[step1b.index(stage):].split("\n")[0]
            with self.subTest(stage=stage):
                self.assertIn("starts with the worker line", para)

    def test_current_fact_probe_runs_once_before_stage_v(self):
        step1b = section("### 1b.", "### 2.")
        self.assertIn("Before stage V, the main session probes `get_current_fact` for up to 3 distinct entities "
                      "of TIME / STATUS / OWN claims", step1b)
        self.assertIn("sets `has_current_facts` to false only when every probe returns nothing", step1b)

    def test_workers_skip_current_fact_without_a_store_but_keep_both_searches(self):
        rule = [l for l in SKILL.splitlines() if l.startswith("- TIME / STATUS / OWN")][0]
        self.assertIn("unless `brain_context.json` has `has_current_facts: false`", rule)
        self.assertIn("run `search_knowledge` twice", rule)
        step_c = [l for l in SKILL.splitlines() if l.strip().startswith("**c.**")][0]
        self.assertIn("unless `brain_context.json` has `has_current_facts: false`", step_c)


class WorkersDoTheWork(unittest.TestCase):
    """b15/b17/b20: the main session sometimes extracted or verified itself, once inventing 17 chunks of findings."""

    def test_main_session_never_writes_stage_files_while_agents_exist(self):
        flat = re.sub(r"\s+", " ", SKILL)
        self.assertIn("While the Agent tool is available, the main session dispatches every batch and every chunk "
                      "and never writes `claims_batch_*` or `findings_chunk_*` itself", flat)
        self.assertIn("if a worker fails or writes nothing, re-dispatch it once; if it fails again, stop and report "
                      "which batch or chunk failed", flat)

    def test_fallback_only_without_the_agent_tool(self):
        self.assertIn("only when the Agent tool is not in your tool list", SKILL)

    def test_quote_warnings_are_informational(self):
        flat = re.sub(r"\s+", " ", SKILL)
        self.assertIn("the warnings are informational: continue", flat)
        self.assertNotIn("re-dispatch that batch if a warned quote matters", flat)

    def test_step_0_lets_the_write_tool_create_the_work_dir(self):
        step0 = section("### 0. Resolve", "### 0b.")
        self.assertIn("with the Write tool (it creates the folder)", step0)
        self.assertNotIn("create `<work dir>` first", step0)


class ChunkerReportsQuoteProblems(unittest.TestCase):
    """Warn-only: 34 of 871 real claims elide with '...'; failing them would force paid stage-E re-runs."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        sents = [{"s_id": "p2s1", "text": "Revenue was 40 million in 2025 across green fields.", "risk": ["num"]},
                 {"s_id": "p2s2", "text": "The team owns billing.", "risk": ["ownership"]}]
        self.w("batches.json", [{"batch": 1, "file": "batch_1.json", "sections": [{"section_id": "s01", "section": "Scope"}]}])
        self.w("batch_1.json", {"batch": 1, "sections": [{"section_id": "s01", "section": "Scope",
                                "paragraphs": [{"p_id": "p2", "text": "x", "sentences": sents}],
                                "tables": [], "figures": []}]})

    def w(self, name, obj):
        (self.d / name).write_text(json.dumps(obj))

    def claims(self, *quotes):
        self.w("claims_batch_1.json", [{"claim_id": f"B1-C0{i}", "p_id": "p2", "s_id": f"p2s{i}", "section": "Scope",
                                        "quote": q, "type": "NUM"} for i, q in enumerate(quotes, 1)])

    def test_verbatim_short_quotes_give_no_warning(self):
        self.claims("40 million in 2025", "The team owns billing.")
        warnings: list[str] = []
        _, errors = C.chunk(self.d, "all", 8, warnings=warnings)
        self.assertEqual((errors, warnings), ([], []))

    def test_non_verbatim_and_long_quotes_are_warned_not_failed(self):
        self.claims("40 million...green fields", " ".join(["word"] * 26))
        warnings: list[str] = []
        index, errors = C.chunk(self.d, "all", 8, warnings=warnings)
        self.assertEqual(errors, [])
        self.assertEqual(len(index), 1, "chunks are still written")
        self.assertTrue(any("B1-C01" in w and "not verbatim" in w for w in warnings), warnings)
        self.assertTrue(any("B1-C02" in w and "26 words" in w for w in warnings), warnings)

    def test_cli_prints_warnings_and_exits_zero(self):
        self.claims("40 million...green fields", "The team owns billing.")
        r = subprocess.run([sys.executable, str(SKILL_DIR / "chunk_claims.py"), str(self.d), "--scope", "all"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("B1-C01", r.stderr)
        self.assertIn("warning", r.stderr.lower())

    def test_skill_says_figure_transcriptions_are_exempt_and_never_hand_edit(self):
        flat = re.sub(r"\s+", " ", SKILL)
        self.assertIn("figure transcriptions (`I*`) are exempt from the 25-word limit", flat)
        self.assertIn("never shorten or edit a stage-E claim in the main session", flat)


if __name__ == "__main__":
    unittest.main()


class CommandDiscipline(unittest.TestCase):
    RULE = ("Run only the documented commands, exactly as written, with absolute paths. Read files with the Read "
            "tool; never `cat`, `head`, `ls` or `grep` the work files. No inline Python (`python -c`, heredocs), "
            "no `cd`, no variable assignments, no `&&` chains. If a step seems to need logic, it is missing from a "
            "script: report it, do not improvise it.")

    def test_rule_is_stated_once(self):
        self.assertEqual(re.sub(r"\s+", " ", SKILL).count(self.RULE), 1)

    def test_rule_sits_before_step_0(self):
        flat = re.sub(r"\s+", " ", SKILL)
        self.assertLess(flat.index(self.RULE), flat.index("### 0. Resolve"))

    def test_workers_get_the_rule_too(self):
        # the worker line points workers at the same rule
        self.assertIn("Follow the command rules in Inputs", SKILL)


class BaselineStaysInTheRunFolder(unittest.TestCase):
    def test_baseline_location_and_reuse_conditions(self):
        flat = re.sub(r"\s+", " ", SKILL)
        self.assertIn("Write the baseline only to `<run dir>/baseline.md`, never to project knowledge, memory or "
                      "any shared folder", flat)
        self.assertIn("Read a baseline only from `<run dir>/baseline.md` of this document; ignore any other "
                      "fact-check baseline you can see (project files, earlier runs on other documents)", flat)
        self.assertIn("reuse it only when the document's `source_sha256` and the `knowledge_version` both match "
                      "the ones it records", flat)
