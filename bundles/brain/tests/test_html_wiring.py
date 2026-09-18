"""HTML must be reachable end to end: registered, parsed, and documented."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
ONBOARD = SKILLS / "knowledge-pipeline" / "onboard.py"


class WiringTests(unittest.TestCase):
    def test_narrative_ext_includes_html(self):
        spec = importlib.util.spec_from_file_location("onboard", ONBOARD)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertIn(".html", m.NARRATIVE_EXT)
        self.assertIn(".htm", m.NARRATIVE_EXT)

    def test_scaffolded_config_includes_html(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "brain"
            docs = Path(td) / "docs"; docs.mkdir()
            r = subprocess.run([sys.executable, str(ONBOARD), "scaffold",
                                "--project", str(proj), "--goal", "g", "--docs", str(docs)],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('"**/*.html"', (proj / "brain.toml").read_text())

    def test_visual_parse_skill_documents_all_three_providers(self):
        text = (SKILLS / "visual-parse" / "SKILL.md").read_text()
        for token in ("Playwright", "Claude in Chrome", "html_segments.js",
                      "html_capture.py", "degraded"):
            self.assertIn(token, text, f"visual-parse SKILL.md missing {token!r}")

    def test_visual_parse_skill_states_the_security_position(self):
        text = (SKILLS / "visual-parse" / "SKILL.md").read_text().lower()
        self.assertIn("file://", text)
        self.assertIn("untrusted", text)


if __name__ == "__main__":
    unittest.main()
