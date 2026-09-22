import unittest

import _tools


class MissingToolsReportTests(unittest.TestCase):
    def test_reason_format_names_tool_and_install(self):
        r = _tools.skip_reason("ffmpeg")
        self.assertTrue(r.startswith("MISSING TOOL: ffmpeg "))
        self.assertIn("brew install ffmpeg", r)
        self.assertIn("brain_doctor.py", r)

    def test_report_groups_by_tool_and_ignores_other_skips(self):
        reasons = [
            "Skipped: " + _tools.skip_reason("ffmpeg"),
            _tools.skip_reason("ffmpeg"),
            _tools.skip_reason("whisper-cli"),
            "Skipped: needs a network",
        ]
        lines = _tools.missing_tools_report(reasons)
        self.assertIn("WARNING: 3 tests skipped", lines[0])
        self.assertTrue(any(l.split()[0] == "ffmpeg" and "2 tests" in l for l in lines[1:]))
        self.assertTrue(any(l.split()[0] == "whisper-cli" and "1 test " in l for l in lines[1:]))
        self.assertEqual(len(lines), 3)

    def test_no_tool_skips_means_no_report(self):
        self.assertEqual(_tools.missing_tools_report(["Skipped: other"]), [])
