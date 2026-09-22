from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "knowledge-pipeline"
_spec = importlib.util.spec_from_file_location("onboard", HERE / "onboard.py")
O = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(O)


class OnboardDoctorTests(unittest.TestCase):
    def test_scan_prints_doctor_report_with_video_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "standup.mp4").write_bytes(b"x")
            buf = io.StringIO()
            with redirect_stdout(buf):
                O.cmd_scan(Namespace(docs=td))
        out = buf.getvalue()
        self.assertIn("== brain doctor ==", out)
        self.assertRegex(out, r"ffmpeg\s+REQUIRED — 1 video file")

    def test_scaffold_includes_video_globs_and_plan_has_step_0(self):
        with tempfile.TemporaryDirectory() as td:
            docs = Path(td) / "docs"; docs.mkdir()
            proj = Path(td) / "brain"
            r = subprocess.run([sys.executable, str(HERE / "onboard.py"), "scaffold", "--project", str(proj),
                                "--goal", "g", "--docs", str(docs)], text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            cfg = (proj / "brain.toml").read_text()
            for ext in ("mp4", "mov", "mkv", "webm", "m4v"):
                self.assertIn(f'"**/*.{ext}"', cfg)
            plan = (proj / "BRAIN.md").read_text()
            self.assertIn("brain_doctor.py", plan)
            self.assertIn("video_capture.py", plan)
            self.assertLess(plan.index("brain_doctor.py"), plan.index("# 1a"))
            # sidecar consumption is automatic (manifest-gated) — the plan names no flag for it
            self.assertNotIn("consume-video", plan)
            self.assertIn("video-lane doc in parsed/", plan)
