from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = KB_ROOT / "hooks" / "scripts"


def build_fixture_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT);
        CREATE TABLE graph_nodes(id TEXT PRIMARY KEY, label TEXT, kind TEXT, parent TEXT);
        CREATE TABLE facts(family TEXT, metric TEXT, grain TEXT, entity TEXT, month TEXT, value REAL, source_file TEXT);
        INSERT INTO chunks(id, source, ord, title, text) VALUES (1,'a.md',0,'t','body');
        INSERT INTO graph_nodes(id,label,kind,parent) VALUES ('n1','L1','intent',NULL);
        INSERT INTO facts VALUES ('f','aht','branch','A','2025-06',12.0,'x.xlsx');
        """
    )
    con.commit()
    con.close()


def run_script(script: Path, env_extra=None, stdin=""):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["sh", str(script)], input=stdin, env=env,
        capture_output=True, text=True,
    )


class TestHealthLine(unittest.TestCase):
    def test_reports_lane_counts_when_db_present(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "knowledge.sqlite"
            build_fixture_db(db)
            res = run_script(SCRIPTS / "health-line.sh", {"BRAIN_DB": str(db)})
            self.assertEqual(res.returncode, 0)
            out = res.stdout.lower()
            self.assertIn("brain", out)
            self.assertIn("chunks", out)
            self.assertIn("1", res.stdout)   # one chunk

    def test_reports_sqlite3_unavailable_when_db_found_but_sqlite3_missing(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "knowledge.sqlite"
            build_fixture_db(db)

            sh_path = shutil.which("sh")
            self.assertIsNotNone(sh_path, "sh must be resolvable to build the fixture PATH")

            bin_dir = Path(d) / "bin"
            bin_dir.mkdir()
            (bin_dir / "sh").symlink_to(sh_path)

            res = run_script(
                SCRIPTS / "health-line.sh",
                {"BRAIN_DB": str(db), "PATH": str(bin_dir)},
            )
            self.assertEqual(res.returncode, 0)
            out = res.stdout.lower()
            self.assertIn("sqlite3", out)
            self.assertIn("unavailable", out)

    def test_quiet_message_when_no_db(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_script(
                SCRIPTS / "health-line.sh",
                {
                    "BRAIN_DB": str(Path(d) / "missing.sqlite"),
                    "CLAUDE_PROJECT_DIR": d,
                },
            )
            self.assertEqual(res.returncode, 0)   # never breaks the session
            self.assertIn("kb:connect", res.stdout)


class TestAmbientReminder(unittest.TestCase):
    def _run_with_state(self, ambient: bool):
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d) / ".claude" / "kb"
            state_dir.mkdir(parents=True)
            (state_dir / "state.json").write_text(
                '{"ambient": %s}' % ("true" if ambient else "false"),
                encoding="utf-8",
            )
            return run_script(
                SCRIPTS / "ambient-reminder.sh",
                {"CLAUDE_PROJECT_DIR": d},
            )

    def test_emits_reminder_when_on(self):
        res = self._run_with_state(True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Brain", res.stdout)
        self.assertIn("not modeled", res.stdout.lower())

    def test_silent_when_off(self):
        res = self._run_with_state(False)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")

    def test_silent_when_no_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_script(SCRIPTS / "ambient-reminder.sh", {"CLAUDE_PROJECT_DIR": d})
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.stdout.strip(), "")


class TestHooksManifest(unittest.TestCase):
    def test_hooks_json_wires_both_events(self):
        data = json.loads((KB_ROOT / "hooks" / "hooks.json").read_text())
        hooks = data["hooks"]
        self.assertIn("SessionStart", hooks)
        self.assertIn("UserPromptSubmit", hooks)
        blob = json.dumps(data)
        self.assertIn("health-line.sh", blob)
        self.assertIn("ambient-reminder.sh", blob)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", blob)  # portable pathing


if __name__ == "__main__":
    unittest.main()
