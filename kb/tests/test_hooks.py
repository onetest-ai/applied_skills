from __future__ import annotations

import os
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


if __name__ == "__main__":
    unittest.main()
