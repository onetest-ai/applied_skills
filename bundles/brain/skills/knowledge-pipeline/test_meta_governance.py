from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import brain_sync as B  # noqa: E402


def _project(td: str, goal: str = "", audience: str = ""):
    """Build a <project>/schema/knowledge.sqlite layout; goal.txt/brain.toml optional."""
    proj = Path(td)
    (proj / "schema").mkdir(parents=True, exist_ok=True)
    if goal:
        (proj / "goal.txt").write_text(goal, encoding="utf-8")
    if audience:
        (proj / "brain.toml").write_text(f'[project]\naudience = "{audience}"\n', encoding="utf-8")
    return str(proj / "schema" / "knowledge.sqlite")


class WriteMetaTests(unittest.TestCase):
    def test_write_meta_populates_and_reports_no_drift(self):
        with tempfile.TemporaryDirectory() as td:
            db = _project(td, goal="optimize call center", audience="ops managers")
            c = sqlite3.connect(db)
            goal, audience, drift = B.write_meta(c, db)
            self.assertEqual(goal, "optimize call center")
            self.assertEqual(audience, "ops managers")
            self.assertIsNone(drift)
            self.assertEqual(B.read_meta(c, "goal"), "optimize call center")
            c.close()

    def test_write_meta_detects_goal_drift(self):
        with tempfile.TemporaryDirectory() as td:
            db = _project(td, goal="goal one")
            c = sqlite3.connect(db)
            B.write_meta(c, db)                       # seeds "goal one"
            (Path(td) / "goal.txt").write_text("goal two", encoding="utf-8")
            _, _, drift = B.write_meta(c, db)          # re-seed with a changed goal
            self.assertEqual(drift, "goal one")
            self.assertEqual(B.read_meta(c, "goal"), "goal two")
            c.close()

    def test_read_meta_empty_when_no_table(self):
        with tempfile.TemporaryDirectory() as td:
            db = _project(td)
            c = sqlite3.connect(db)
            self.assertEqual(B.read_meta(c, "goal"), "")  # no meta table yet
            c.close()


class EnforceGoalTests(unittest.TestCase):
    def test_empty_goal_with_require_exits_3(self):
        with self.assertRaises(SystemExit) as cm:
            B.enforce_goal("", None, require=True, context="seed")
        self.assertEqual(cm.exception.code, 3)

    def test_empty_goal_without_require_warns_and_returns_false(self):
        self.assertFalse(B.enforce_goal("", None, require=False, context="seed"))

    def test_present_goal_returns_true(self):
        self.assertTrue(B.enforce_goal("a goal", None, require=True, context="apply"))

    def test_drift_does_not_block_present_goal(self):
        self.assertTrue(B.enforce_goal("new", "old", require=True, context="apply"))


class AboutCommandTests(unittest.TestCase):
    def test_about_prints_goal_and_audience(self):
        with tempfile.TemporaryDirectory() as td:
            db = _project(td, goal="the goal", audience="the audience")
            c = sqlite3.connect(db); B.write_meta(c, db); c.commit(); c.close()
            r = subprocess.run([sys.executable, str(HERE / "brain_sync.py"), "about", "--db", db],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("the goal", r.stdout)
            self.assertIn("the audience", r.stdout)

    def test_about_flags_unset_goal(self):
        with tempfile.TemporaryDirectory() as td:
            db = _project(td)                          # no goal.txt
            sqlite3.connect(db).close()                # empty store, no meta table
            r = subprocess.run([sys.executable, str(HERE / "brain_sync.py"), "about", "--db", db],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ungoverned", r.stdout)


class SeedGateTests(unittest.TestCase):
    def test_seed_requires_goal_fails_up_front(self):
        with tempfile.TemporaryDirectory() as td:
            db = _project(td)                       # no goal.txt -> empty goal
            parsed = Path(td) / "parsed"; parsed.mkdir()
            r = subprocess.run([sys.executable, str(HERE / "brain_sync.py"), "seed",
                                "--db", db, "--parsed", str(parsed), "--require-goal"],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("ungoverned", r.stderr)
            # gate fired BEFORE work: no store was created
            self.assertFalse(Path(db).exists())


if __name__ == "__main__":
    unittest.main()
