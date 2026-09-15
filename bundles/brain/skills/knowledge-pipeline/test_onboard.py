from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "onboard.py"


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


if __name__ == "__main__":
    unittest.main()
