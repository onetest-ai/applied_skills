"""wait_for.py: the documented way to wait for stage-E and stage-V workers, so the model never improvises `sleep`, `ls` or `until` loops
(the shell blocks `sleep`, and every other improvised command shape asks the user for approval)."""
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import wait_for as W
from test_plugin_structure import KB_ROOT

SCRIPT = KB_ROOT / "skills" / "doc-fact-check" / "wait_for.py"


def run(d, *args):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), str(d), *args], capture_output=True, text=True)


class WaitFor(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def put(self, name, text="[]"):
        (self.d / name).write_text(text)

    def test_ready_when_enough_complete_files_exist(self):
        for i in (1, 2):
            self.put(f"findings_chunk_{i}.json")
        r = run(self.d, "--glob", "findings_chunk_*.json", "--count", "2", "--timeout", "5")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("ready"), r.stdout)

    def test_waiting_after_the_timeout_says_to_run_it_again_and_still_exits_zero(self):
        self.put("findings_chunk_1.json")
        r = run(self.d, "--glob", "findings_chunk_*.json", "--count", "3", "--timeout", "1", "--interval", "0.1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("waiting: findings_chunk_*.json 1 of 3", r.stdout)
        self.assertIn("run this command again", r.stdout)

    def test_a_half_written_json_file_is_not_counted(self):
        self.put("findings_chunk_1.json")
        self.put("findings_chunk_2.json", '[{"id": ')
        self.assertEqual(W.count(self.d, "findings_chunk_*.json"), 1)

    def test_an_empty_file_is_not_counted(self):
        self.put("claims_batch_1.json", "")
        self.assertEqual(W.count(self.d, "claims_batch_*.json"), 0)

    def test_every_glob_must_reach_the_count(self):
        for i in (1, 2):
            self.put(f"claims_batch_{i}.json")
        self.put("coverage_batch_1.json")
        r = run(self.d, "--glob", "claims_batch_*.json", "--glob", "coverage_batch_*.json", "--count", "2", "--timeout", "1", "--interval", "0.1")
        self.assertTrue(r.stdout.startswith("waiting"), r.stdout)
        self.assertIn("coverage_batch_*.json 1 of 2", r.stdout)

    def test_a_file_that_appears_during_the_wait_is_seen(self):
        threading.Timer(0.3, lambda: self.put("findings_chunk_1.json", json.dumps([1]))).start()
        t0 = time.monotonic()
        r = run(self.d, "--glob", "findings_chunk_*.json", "--count", "1", "--timeout", "10", "--interval", "0.1")
        self.assertTrue(r.stdout.startswith("ready"), r.stdout)
        self.assertLess(time.monotonic() - t0, 8)

    def test_the_timeout_is_capped_below_a_tool_call_limit(self):
        self.assertEqual(W.clamp_timeout(9999), W.MAX_TIMEOUT)
        self.assertLess(W.MAX_TIMEOUT, 180)
        self.assertEqual(W.clamp_timeout(0), 1)


if __name__ == "__main__":
    unittest.main()
