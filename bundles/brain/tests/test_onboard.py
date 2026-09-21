from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "knowledge-pipeline"
SCRIPT = HERE / "onboard.py"

# Import onboard module directly so we can test internal helpers without subprocess.
_spec = importlib.util.spec_from_file_location("onboard", SCRIPT)
_onboard = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_onboard)  # type: ignore[union-attr]


class OnboardSourceConfigTests(unittest.TestCase):
    def test_scaffold_writes_portable_brain_toml_and_registry_runbook(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "mother sources" / "docs"; docs.mkdir(parents=True)
            reporting = root / "mother sources" / "reporting"; reporting.mkdir()
            project = root / "brain"
            result = subprocess.run([
                sys.executable, str(SCRIPT), "scaffold",
                "--project", str(project), "--goal", "test goal",
                "--docs", str(docs), "--reporting", str(reporting),
                "--docs-mode", "mirror", "--reporting-mode", "import",
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = (project / "brain.toml").read_text()
            self.assertIn('path = "../mother sources/docs"', config)
            self.assertIn('[sources.roots.docs]\npath = "../mother sources/docs"\nmode = "mirror"', config)
            self.assertIn('[sources.roots.reporting]\npath = "../mother sources/reporting"\nmode = "import"', config)
            self.assertIn('[sources.roots.incoming]\npath = ".incoming"\nmode = "managed"', config)
            self.assertTrue((project / ".incoming").is_dir())
            plan = (project / "BRAIN.md").read_text()
            self.assertIn("./brain source init", plan)
            self.assertIn("./brain source plan", plan)
            self.assertIn("./brain source import", plan)

    def test_scaffold_records_audience_in_brain_toml_and_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"; docs.mkdir()
            project = root / "brain"
            result = subprocess.run([
                sys.executable, str(SCRIPT), "scaffold",
                "--project", str(project), "--goal", "test goal",
                "--audience", "ops managers", "--docs", str(docs),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = (project / "brain.toml").read_text()
            self.assertIn('[project]\naudience = "ops managers"', config)
            # [project] must precede the [sources...] sections and be valid TOML.
            self.assertLess(config.index("[project]"), config.index("[sources.roots.incoming]"))
            import tomllib
            parsed = tomllib.loads(config)
            self.assertEqual(parsed["project"]["audience"], "ops managers")
            plan = (project / "BRAIN.md").read_text()
            self.assertIn("ops managers", plan)
            self.assertIn("Audience", plan)

    def test_run_script_includes_fact_intake_stage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"; docs.mkdir()
            project = root / "brain"
            result = subprocess.run([
                sys.executable, str(SCRIPT), "scaffold",
                "--project", str(project), "--goal", "test goal",
                "--docs", str(docs),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = (project / "BRAIN.md").read_text()
            self.assertIn("fact_prep.py", plan)
            self.assertIn("fact_write.py", plan)
            self.assertIn("--apply", plan)
            # fact intake runs after narrative indexing/classify, before verify.
            self.assertLess(plan.index("classify_write.py"), plan.index("fact_prep.py"))
            self.assertLess(plan.index("fact_write.py"), plan.index("verify --db"))

    def test_run_script_reviews_taxonomy_and_builds_from_current(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "docs"; docs.mkdir()
            project = root / "brain"
            result = subprocess.run([sys.executable, str(SCRIPT), "scaffold", "--project", str(project),
                                     "--goal", "g", "--docs", str(docs)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = (project / "BRAIN.md").read_text()
            self.assertIn("work/consolidated.json", plan)
            self.assertIn("taxonomy_review.py\" plan --mode draft", plan)
            self.assertIn("skip if taxonomy/current.json already exists", plan)
            self.assertIn("taxonomy_merge.py\" --review", plan)
            self.assertLess(plan.index("taxonomy_merge.py\" --review"), plan.index("build_graph.py"))
            for line in plan.splitlines():
                if "build_graph.py" in line or "classify_prep.py" in line:
                    self.assertIn("current.json", line)
                    self.assertNotIn("taxonomy_v0.json", line)

    def test_scaffold_does_not_overwrite_existing_brain_toml(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project = root / "brain"; project.mkdir()
            config = project / "brain.toml"; config.write_text("version = 1\n# custom\n")
            docs = root / "docs"; docs.mkdir()
            result = subprocess.run([
                sys.executable, str(SCRIPT), "scaffold", "--project", str(project),
                "--goal", "test", "--docs", str(docs),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), "version = 1\n# custom\n")


class NarrativeExtTests(unittest.TestCase):
    """NARRATIVE_EXT must route .vtt and .srt files into the narrative lane."""

    def _make_corpus(self, tmp: Path, exts: list) -> Path:
        docs = tmp / "docs"
        docs.mkdir()
        for ext in exts:
            (docs / f"file{ext}").write_text("dummy")
        return docs

    def test_vtt_file_classified_as_narrative(self):
        with tempfile.TemporaryDirectory() as td:
            docs = self._make_corpus(Path(td), [".vtt"])
            narrative, reporting, other = _onboard._scan_docs(docs)
            self.assertEqual(len(narrative), 1, f"Expected 1 narrative file, got {narrative}")
            self.assertEqual(len(other), 0, f"Expected no 'other' files, got {other}")

    def test_srt_file_classified_as_narrative(self):
        with tempfile.TemporaryDirectory() as td:
            docs = self._make_corpus(Path(td), [".srt"])
            narrative, reporting, other = _onboard._scan_docs(docs)
            self.assertEqual(len(narrative), 1, f"Expected 1 narrative file, got {narrative}")
            self.assertEqual(len(other), 0, f"Expected no 'other' files, got {other}")

    def test_mixed_corpus_vtt_srt_with_pdf_all_narrative(self):
        with tempfile.TemporaryDirectory() as td:
            docs = self._make_corpus(Path(td), [".vtt", ".srt", ".pdf"])
            narrative, reporting, other = _onboard._scan_docs(docs)
            self.assertEqual(len(narrative), 3, f"Expected 3 narrative files, got {narrative}")
            self.assertEqual(len(other), 0, f"Expected no 'other' files, got {other}")


class BrainNameTests(unittest.TestCase):
    def _scaffold(self, project, *extra):
        return subprocess.run([
            sys.executable, str(SCRIPT), "scaffold",
            "--project", str(project), "--goal", "optimize call-center operations",
            *extra,
        ], text=True, capture_output=True)

    def test_scaffold_records_a_brain_name(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "brain"
            result = self._scaffold(project, "--name", "ACME Contact Centre")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((project / "name.txt").read_text().strip(),
                             "ACME Contact Centre")
            plan = (project / "BRAIN.md").read_text()
            self.assertIn("ACME Contact Centre", plan)
            self.assertIn("meta", plan,
                          "BRAIN.md must show how the name reaches the meta table")
            # Regression guard: the --name branch interpolates an extra "step 9" block into
            # the plan text before the outer textwrap.dedent() runs. If that block's lines
            # don't carry the same indentation as the rest of the literal, the outer dedent's
            # common-prefix computation collapses to "" and the ENTIRE document — heading,
            # prose, code fences — ends up indented four spaces, which Markdown then renders
            # as one indented code block. A substring assertion alone can't see this class of
            # bug, so assert directly that no body line is four-space indented and that the
            # heading survives at column 0.
            self.assertIn("\n# Brain build plan", plan)
            indented_lines = [line for line in plan.splitlines() if line.startswith("    ")]
            self.assertEqual(indented_lines, [],
                             f"BRAIN.md must not be indented as a whole: {indented_lines[:3]!r}")

    def test_scaffold_without_a_name_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "brain"
            result = self._scaffold(project)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((project / "BRAIN.md").is_file())
            self.assertEqual((project / "name.txt").read_text().strip(), "")


if __name__ == "__main__":
    unittest.main()
