import os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

ONB = Path(__file__).resolve().parent.parent / "skills" / "knowledge-pipeline" / "onboard.py"


def make(td, provisional):
    os.makedirs(os.path.join(td, "schema")); os.makedirs(os.path.join(td, "taxonomy"))
    db = os.path.join(td, "schema", "knowledge.sqlite")
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, title TEXT, text TEXT);"
                    "CREATE TABLE chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT);"
                    "CREATE TABLE chunk_verdicts(chunk_id INTEGER PRIMARY KEY, verdict TEXT, taxonomy_version INT);"
                    "INSERT INTO chunks VALUES(1,'a','t','x'),(2,'a','t','y'),(3,'a','t','z');"
                    "INSERT INTO chunk_topics VALUES(1,'c','C','intent_l1');"
                    "INSERT INTO chunk_verdicts VALUES(2,'no_topic',0);")
    c.commit(); c.close()
    if provisional:
        open(os.path.join(td, "taxonomy", "PROVISIONAL"), "w").write("{}")
    return db


def verify(db):
    return subprocess.run([sys.executable, str(ONB), "verify", "--db", db], text=True, capture_output=True)


class VerifyProvisionalTests(unittest.TestCase):
    def test_provisional_fails(self):
        with tempfile.TemporaryDirectory() as td:
            r = verify(make(td, True))
            self.assertEqual(r.returncode, 1)
            self.assertIn("PROVISIONAL", r.stdout)

    def test_coverage_excludes_no_topic(self):
        with tempfile.TemporaryDirectory() as td:
            r = verify(make(td, False))
            self.assertIn("1/3 chunks", r.stdout)          # chunk 3 only; chunk 2 is no-topic
            self.assertIn("1 no-topic", r.stdout)
            self.assertNotIn("PROVISIONAL", r.stdout)
