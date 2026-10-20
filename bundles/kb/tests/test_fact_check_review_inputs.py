"""Round 6 (root-cause reviews of the r5a and r5b paid runs): the slide review gets its input from a script (S-1),
a conflicted `verified_in_context` lands as `minor` (S-2), a refused review is retried with the refusal (S-3); stage V
records only same-scenario conflicts (S-4); the main session stops hand-writing files a script can write (T-3, T-6);
small decks verify in chunks of 3 (T-5)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

import apply_slide_review as SR  # noqa: E402
import chunk_claims as C  # noqa: E402
import source_links as SL  # noqa: E402

SKILL = lambda: (DECK / "SKILL.md").read_text(encoding="utf-8")
BRIEF = lambda: (DECK / "slide_review.md").read_text(encoding="utf-8")
VERIFY = lambda: (DECK / "verify.md").read_text(encoding="utf-8")
DOC = KB_ROOT / "skills" / "doc-fact-check"
CONFLICT = [{"span": "the renewal engine owns scheduled orders", "source": "notes/billing.md", "date": "2026-03-02"}]


def _f(fid, p_id, verdict="Misleading", severity="Major", **kw):
    f = {"id": fid, "p_id": p_id, "section": "Slide 1", "quote": f"quote {fid}", "claim": f"claim {fid}", "type": "TOPO",
         "verdict": verdict, "severity": severity, "confidence": "High", "evidence": f"evidence {fid}", "trail": "t",
         "conflicts": [], "fix": f"fix {fid}", "why": "the source states another value", "suggested": "The corrected statement.", "source": "notes/a.md", "sources": []}
    f.update(kw)
    return f


class _Run(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.findings = [_f("C01", "s1-sh5-p1"), _f("C02", "s1-sh6-p1", conflicts=CONFLICT),
                         _f("C03", "s1-sh7-p1", verdict="Verified", severity="Minor"),
                         _f("C04", "s2-sh3-p1", verdict="Verified", severity="Minor"), _f("I01", "s1-sh9")]
        (self.d / "findings.json").write_text(json.dumps(self.findings))
        (self.d / "run.json").write_text(json.dumps({"comments": "major"}))


class Prepare(_Run):
    """S-1: in 4 of 6 paid runs the main session hand-wrote the reviewer's input without the conflicts it must weigh."""

    def test_prepare_writes_every_field_the_brief_names(self):
        slides = SR.prepare(self.d / "findings.json", self.d / "run.json", self.d)
        self.assertEqual(slides, [1])
        rows = json.loads((self.d / "slide_findings_1.json").read_text())
        self.assertEqual([r["id"] for r in rows], ["C01", "C02", "C03", "I01"])
        for r in rows:
            self.assertTrue({"quote", "claim", "evidence", "conflicts", "verdict", "severity", "fix", "commented"} <= set(r))
        by = {r["id"]: r for r in rows}
        self.assertEqual(by["C02"]["conflicts"], CONFLICT)
        self.assertEqual([by[i]["commented"] for i in ("C01", "C02", "C03", "I01")], [True, True, False, True])

    def test_a_slide_with_no_commented_finding_gets_no_review(self):
        SR.prepare(self.d / "findings.json", self.d / "run.json", self.d)
        self.assertFalse((self.d / "slide_findings_2.json").exists())

    def test_every_field_the_brief_tells_the_reviewer_to_use_is_prepared(self):
        for field in ("evidence", "claim", "conflicts"):
            self.assertIn(f"`{field}`", BRIEF())
            self.assertIn(field, SR.PREPARE_FIELDS)

    def test_cli_prints_the_slides(self):
        r = subprocess.run([sys.executable, "-I", str(DECK / "apply_slide_review.py"), str(self.d / "findings.json"),
                            "--run", str(self.d / "run.json"), "--prepare", str(self.d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"slides": [1]})


class ConflictedInContext(_Run):
    """S-2: r5b — the reviewer judged a card true in the slide's scenario; the finding carried off-scenario conflicts,
    the apply refused, and the whole review was discarded."""

    def _apply(self, review):
        (self.d / "slide_review_1.json").write_text(json.dumps(review))
        return SR.apply(self.d / "findings.json", self.d / "run.json", [self.d / "slide_review_1.json"])

    def test_conflicted_verified_in_context_lands_as_minor(self):
        errors = self._apply([{"id": "C01", "action": "keep", "reason": "a wrong owner"},
                              {"id": "C02", "action": "verified_in_context", "reason": "true in the slide's scenario"},
                              {"id": "I01", "action": "minor", "reason": "a simplification"}])
        self.assertEqual(errors, [])
        by = {f["id"]: f for f in json.loads((self.d / "findings.json").read_text())}
        self.assertEqual((by["C02"]["verdict"], by["C02"]["severity"]), ("Misleading", "Minor"))
        self.assertIn("recorded conflicts", by["C02"]["trail"])
        self.assertEqual(by["I01"]["severity"], "Minor")
        log = json.loads((self.d / "run.json").read_text())["slide_review"]
        self.assertIn({"id": "C02", "action": "minor", "requested": "verified_in_context",
                       "reason": "true in the slide's scenario"}, log)

    def test_unconflicted_verified_in_context_still_verifies(self):
        self.assertEqual(self._apply([{"id": "C01", "action": "verified_in_context", "reason": "true here"}]), [])
        c01 = next(f for f in json.loads((self.d / "findings.json").read_text()) if f["id"] == "C01")
        self.assertEqual((c01["verdict"], c01["severity"], c01["fix"]), ("Verified", "Minor", "None."))

    def test_brief_states_one_rule_for_conflicted_findings(self):
        b = BRIEF()
        self.assertIn("A finding with recorded `conflicts` is lowered only to `minor`", b)
        vic = next(line for line in b.splitlines() if line.startswith("- `verified_in_context`"))
        self.assertIn("no recorded `conflicts`", vic)

    def test_brief_examples_are_invented(self):
        self.assertIn("the order portal saves the change as the master record", BRIEF())   # this repo is public


class RetryCarriesTheRefusal(unittest.TestCase):
    """S-3: r5b re-dispatched the review with byte-identical text and got the same refused answer."""

    def test_skill_prepares_the_input_with_the_script(self):
        s = SKILL()
        self.assertIn('apply_slide_review.py" "<run dir>/findings.json" --run "<run dir>/run.json" --prepare "<work dir>"', s)
        self.assertNotIn("write `<work dir>/slide_findings_<N>.json`: that slide's findings", s)

    def test_retry_adds_the_refusal_line(self):
        s = SKILL()
        self.assertIn("Refused: <the line apply_slide_review.py printed for that file>", s)
        self.assertNotIn("re-dispatch the reviews it names once, with the same text", s)



class SameScenarioConflicts(unittest.TestCase):
    """S-4: r5b stage V recorded statements about another scenario as conflicts and added a premise no source states;
    verify.md asked it to record "two meetings describing different flows"."""

    def test_off_scenario_statements_are_not_conflicts(self):
        v = VERIFY()
        self.assertNotIn("two meetings describing different flows", v)
        self.assertIn("A statement that the claim's `context` or the source itself places in another scenario, process or "
                      "attribute is not a conflict: it may go in `evidence`, never in `conflicts`. When you cannot tell "
                      "whether it is the same item, record it.", v)    # final review I2: never hide a real conflict
        self.assertIn("Never add a premise about the scenario that neither the claim's `context` nor a source states.", v)


class SourceLinks(unittest.TestCase):
    """T-3: in step 6b the main session re-typed the whole findings.json to add "(not in inventory)" — 191 s (r5a),
    about 193 s (r5b), and once it changed evidence it may not touch."""

    def _findings(self):
        return [{"id": "C01", "source": "notes/a.md", "evidence": "e1",
                 "conflicts": [{"span": "x", "source": "notes/b.md", "date": "2026-01-01"},
                               {"span": "y", "source": "notes/a.md", "date": "2026-01-02"}], "sources": []},
                {"id": "C02", "source": "", "evidence": "e2", "conflicts": [], "sources": []}]

    def test_sources_from_source_and_conflicts_unresolved_marked(self):
        out = SL.link(self._findings(), {})
        self.assertEqual(out[0]["sources"], [{"name": "notes/a.md (not in inventory)", "link": "", "folder": ""},
                                             {"name": "notes/b.md (not in inventory)", "link": "", "folder": ""}])
        self.assertEqual(out[1]["sources"], [])

    def test_resolved_sources_take_the_inventory_link(self):
        out = SL.link(self._findings(), {"notes/b.md": {"name": "B notes", "link": "https://x.test/b", "folder": "Team/B"}})
        self.assertEqual(out[0]["sources"][1], {"name": "B notes", "link": "https://x.test/b", "folder": "Team/B"})

    def test_nothing_but_sources_changes(self):
        before = self._findings()
        after = SL.link(json.loads(json.dumps(before)), {})
        for b, a in zip(before, after):
            self.assertEqual({k: v for k, v in b.items() if k != "sources"}, {k: v for k, v in a.items() if k != "sources"})

    def test_cli_rewrites_in_place(self):
        d = Path(tempfile.mkdtemp())
        (d / "findings.json").write_text(json.dumps(self._findings()))
        (d / "links.json").write_text(json.dumps({"notes/a.md": {"link": "https://x.test/a", "folder": "Team"}}))
        r = subprocess.run([sys.executable, "-I", str(DECK / "source_links.py"), str(d / "findings.json"),
                            "--links", str(d / "links.json")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"findings": 2, "sources": 2, "resolved": 1})
        f = json.loads((d / "findings.json").read_text())[0]
        self.assertEqual(f["sources"][0], {"name": "notes/a.md", "link": "https://x.test/a", "folder": "Team"})

    def test_both_skills_run_it_in_6b(self):
        for skill in (DECK, DOC):
            s = (skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill.name):
                self.assertIn('source_links.py" "<run dir>/findings.json"', s)
                self.assertIn("`<work dir>/source_links.json`", s)
                self.assertNotIn("note (in your own working notes, not in any file) each finding's resolved links", s)


class MainSessionTime(unittest.TestCase):
    """T-6: T6's hand-copied source list (151 entries, ~2.1 min) was read by no stage-V worker (0 read_document calls
    in 6 r5 workers); figures ran before a foreground stage E (96 s)."""

    def test_source_list_removed(self):
        self.assertNotIn("`sources`: `[{title, chars}]`", SKILL())
        self.assertNotIn("a source listed in `brain_context.json`'s `sources`", VERIFY())

    def test_stage_e_runs_in_the_background_while_figures_are_read(self):
        s = SKILL()
        self.assertIn("in the background (`run_in_background: true` where the Agent tool offers it)", s)
        self.assertIn("While the stage-E workers run, the main session does step 3", s)


class SmallDeckChunks(unittest.TestCase):
    """T-5: with chunks of 4 the stride dealt the figure claim and the cutoff claim of r5a into one chunk."""

    def test_auto_size(self):
        self.assertEqual([C.auto_size(n) for n in (1, 20, 21, 80, 81, 450)], [3, 3, 4, 4, 6, 6])

    def test_skill_states_it(self):
        self.assertIn("chunks of 3 claims for up to 20 claims, 4 for up to 80, 6 beyond", SKILL())


if __name__ == "__main__":
    unittest.main()


class ReviewFilesKeepTheirSlide(unittest.TestCase):
    """Final review C1: the on-slide check captured the loop's slide number by reference, so a duplicate in any
    review file but the last listed was checked against the last file's slide and the whole set was refused."""

    def test_duplicate_in_the_first_listed_file_is_accepted(self):
        d = Path(tempfile.mkdtemp())
        fs = [_f("C01", "s1-sh5-p1"), _f("C02", "s1-sh6-p1"), _f("C03", "s2-sh5-p1")]
        (d / "findings.json").write_text(json.dumps(fs))
        (d / "run.json").write_text(json.dumps({"comments": "major"}))
        (d / "slide_review_1.json").write_text(json.dumps([{"id": "C01", "action": "keep", "reason": "r"},
                                                           {"id": "C02", "action": "duplicate_of", "of": "C01", "reason": "same"}]))
        (d / "slide_review_2.json").write_text(json.dumps([{"id": "C03", "action": "keep", "reason": "r"}]))
        self.assertEqual(SR.apply(d / "findings.json", d / "run.json", [d / "slide_review_1.json", d / "slide_review_2.json"]), [])


class FinalReviewFixes(unittest.TestCase):
    def test_i3_figures_wait_for_stage_e_dispatch_and_chunking_waits_for_workers(self):
        s = SKILL()
        self.assertNotIn("Open those files with the Read tool; never unzip", s)
        self.assertIn("never run `chunk_claims.py` while a stage-E worker is still running", s)

    def test_i1_baseline_document_gets_its_link(self):
        for skill in (DECK, DOC):
            with self.subTest(skill=skill.name):
                self.assertIn("add the baseline document to `<work dir>/source_links.json`",
                              (skill / "SKILL.md").read_text(encoding="utf-8"))
