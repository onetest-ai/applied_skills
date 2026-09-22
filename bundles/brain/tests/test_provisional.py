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

    def _adopt_failing_at(self, target, exc):
        """Run adopt --provisional in-process with `target` (a taxonomy_review attribute path) raising."""
        from unittest import mock
        with mock.patch(target, side_effect=exc):
            with self.assertRaises(type(exc)):
                R.main(["adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional"])

    def test_marker_survives_a_failure_writing_current_json(self):
        self._adopt_failing_at("taxonomy_review.atomic_write_bytes", OSError("disk full"))
        self.assertTrue(taxo_io.is_provisional(self.tdir))                       # blocked, not deployable
        self.assertFalse(os.path.exists(os.path.join(self.tdir, "current.json")))

    def test_marker_survives_a_failure_committing_the_store(self):
        import sqlite3
        self._adopt_failing_at("taxonomy_review.GM.write_version", sqlite3.OperationalError("database is locked"))
        self.assertTrue(taxo_io.is_provisional(self.tdir))
        # current.json was already written: an adopted draft is never left without its marker
        self.assertTrue(os.path.exists(os.path.join(self.tdir, "current.json")))

    def test_rerunning_adopt_after_a_failure_completes_it(self):
        self._adopt_failing_at("taxonomy_review.atomic_write_bytes", OSError("disk full"))
        r = cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(open(os.path.join(self.tdir, "current.json"), "rb").read(), open(self.v0, "rb").read())
        self.assertTrue(taxo_io.is_provisional(self.tdir))

    def test_refused_adopt_writes_no_marker(self):
        write_json(os.path.join(self.tdir, "current.json"), taxonomy(version=3))   # a ratified, different current
        r = cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(taxo_io.is_provisional(self.tdir))

    def test_health_context_is_titled_first_build_while_provisional(self):
        cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        ctx = R._context("health", taxonomy(), [], {}, 0, {}, provisional=True)
        self.assertEqual(ctx["title"], "First-build review")
        self.assertIn("everything else is kept", ctx["subtitle"])
        self.assertEqual(R._context("health", taxonomy(), [], {}, 0, {})["title"], "Health review")

    def test_health_subtitle_counts_fit_problems(self):
        problems = {"sparse": [{"node": "a"}, {"node": "b"}], "misplaced": [{"node": "c"}],
                    "overloaded": [{"node": "d"}], "near_duplicate": [{"members": []}]}
        ctx = R._context("health", taxonomy(), [], {}, 3, problems)
        self.assertEqual(ctx["subtitle"], "v3 · 5 problems · 0 fixes proposed")

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

    def _submitted_remove_review(self):
        cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        cur = os.path.join(self.tdir, "current.json")
        path, rev = R.build_plan("browse", cur, db=self.db)
        dp = D.default_path(self.tdir)
        D.append(dp, {"review_id": rev["review_id"], "action": "propose", "item_id": "p-1",
                      "op": {"type": "remove", "node": "Transform", "disposition": "demote"},
                      "reviewer": "t", "surface": "browser"})
        D.append(dp, {"review_id": rev["review_id"], "action": "submit", "reviewer": "t", "surface": "browser"})
        return path

    def test_marker_is_cleared_before_applied_is_logged_and_recovery_clears_it(self):
        path = self._submitted_remove_review()
        real_append = TM.D.append

        def crash_on_applied(p, rec):
            if rec.get("action") == "applied":
                raise RuntimeError("crash before logging applied")
            return real_append(p, rec)

        TM.D.append = crash_on_applied
        try:
            with self.assertRaises(RuntimeError):
                TM.apply_review(path)
        finally:
            TM.D.append = real_append
        self.assertFalse(taxo_io.is_provisional(self.tdir))       # cleared before `applied`
        # a crash before the clear (after the version was written) leaves the marker: recovery clears it
        taxo_io.write_provisional(self.tdir, 0, "x")
        res = TM.apply_review(path)
        self.assertTrue(res["recovered"])
        self.assertTrue(res["provisional_cleared"])
        self.assertFalse(taxo_io.is_provisional(self.tdir))

    def test_apply_without_review_clears_the_marker(self):
        cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        props = os.path.join(self.td.name, "props")
        write_json(os.path.join(props, "result_0.json"),
                   {"proposals": [{"name": "Brand New Area", "level": "L1", "parent": None, "evidence": "x"}]})
        r = subprocess.run([sys.executable, str(CTE / "taxonomy_merge.py"), "--taxonomy",
                            os.path.join(self.tdir, "current.json"), "--proposals", props, "--apply",
                            "--without-review"], text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.tdir, "taxonomy_v1.json")))
        self.assertFalse(taxo_io.is_provisional(self.tdir))

    def test_without_review_with_nothing_to_apply_still_clears_the_marker(self):
        # The user explicitly chose to skip review; with no usable proposals there is no version to
        # write, but their instruction is the human decision the marker waits for. A dry run keeps it.
        cli("adopt", "--taxonomy", self.v0, "--db", self.db, "--provisional")
        cur = os.path.join(self.tdir, "current.json")
        before = open(cur, "rb").read()
        props = os.path.join(self.td.name, "props")
        write_json(os.path.join(props, "result_0.json"),      # a duplicate of an existing L1: nothing to add
                   {"proposals": [{"name": "Billing & Payments", "level": "L1", "parent": None, "evidence": "x"}]})
        merge = [sys.executable, str(CTE / "taxonomy_merge.py"), "--taxonomy", cur, "--proposals", props]
        r = subprocess.run(merge, text=True, capture_output=True)                     # dry run
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(taxo_io.is_provisional(self.tdir))
        r = subprocess.run(merge + ["--apply", "--without-review"], text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing to apply", r.stdout)
        self.assertIn("PROVISIONAL", r.stdout)
        self.assertFalse(taxo_io.is_provisional(self.tdir))
        self.assertFalse(os.path.exists(os.path.join(self.tdir, "taxonomy_v1.json")))
        self.assertEqual(open(cur, "rb").read(), before)                           # current.json untouched
