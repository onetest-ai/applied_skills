import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

import decisions as D
import taxo_io
import taxonomy_merge as TM
import taxonomy_review as R
from taxo_fixtures import tagged_store, taxonomy, write_json

CTE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"


def cli(*args):
    return subprocess.run([sys.executable, str(CTE / "taxonomy_review.py"), *args], text=True, capture_output=True)


class ProvisionalTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tdir = os.path.join(self.td.name, "taxonomy")
        self.v0 = os.path.join(self.tdir, "taxonomy_v0.json")
        write_json(self.v0, taxonomy())                       # a draft: no "version"
        self.db = os.path.join(self.td.name, "k.sqlite")
        tagged_store(self.db, taxonomy())

    def tearDown(self):
        self.td.cleanup()

    def test_adopt_provisional_copies_bytes_and_writes_marker(self):
        r = cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["provisional"])
        self.assertEqual(open(os.path.join(self.tdir, "current.json"), "rb").read(), open(self.v0, "rb").read())
        self.assertTrue(taxo_io.is_provisional(self.tdir))
        marker = json.load(open(taxo_io.provisional_path(self.tdir)))
        self.assertEqual(marker["version"], 0)

    def test_health_context_is_titled_first_build_while_provisional(self):
        cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        ctx = R._context("health", taxonomy(), [], {}, 0, {}, provisional=True)
        self.assertEqual(ctx["title"], "First-build review")
        self.assertIn("everything else is kept", ctx["subtitle"])
        self.assertEqual(R._context("health", taxonomy(), [], {}, 0, {})["title"], "Health review")

    def test_applying_a_review_clears_the_marker(self):
        cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        cur = os.path.join(self.tdir, "current.json")
        path, rev = R.build_plan("browse", cur, db=self.db)
        dp = D.default_path(self.tdir)
        D.append(dp, {"review_id": rev["review_id"], "action": "propose", "item_id": "p-1",
                      "op": {"type": "remove", "node": "Transform", "disposition": "demote"},
                      "reviewer": "t", "surface": "browser"})
        D.append(dp, {"review_id": rev["review_id"], "action": "submit", "reviewer": "t", "surface": "browser"})
        res = TM.apply_review(path)
        self.assertEqual(res["status"], "applied")
        self.assertTrue(res["provisional_cleared"])
        self.assertFalse(taxo_io.is_provisional(self.tdir))
