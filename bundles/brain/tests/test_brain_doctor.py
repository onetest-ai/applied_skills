from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SK = Path(__file__).resolve().parent.parent / "skills"
_spec = importlib.util.spec_from_file_location("brain_doctor", SK / "knowledge-pipeline" / "brain_doctor.py")
D = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(D)


def _tree(root: Path, files: list[str]) -> None:
    for f in files:
        p = root / f; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")


def _config(root: Path, include: list[str], extra: str = "") -> Path:
    cfg = root / "brain.toml"
    cfg.write_text('version = 1\n[sources.roots.docs]\npath = "docs"\nmode = "import"\n'
                   f"include = {json.dumps(include)}\n{extra}")
    return cfg


def _by_name(checks):
    return {c["name"]: c for c in checks}


class DoctorRequirementTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.root = Path(self.t.name)
        # every python/sqlite check passes; system tools are decided by `which`
        self.p1 = patch.object(D, "check_python_deps", return_value=(True, "all importable"))
        self.p2 = patch.object(D, "check_sqlite_vec", return_value=(True, "vec v0"))
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop(); self.t.cleanup()

    def test_pdf_only_corpus_requires_no_system_tool(self):
        _tree(self.root / "docs", ["a.pdf"])
        cfg = _config(self.root, ["**/*.pdf"])
        with patch.object(D, "which", return_value=None), patch.object(D, "soffice_path", return_value=None):
            checks = D.run_checks(D.scan_corpus(config=str(cfg)), config=str(cfg))
        c = _by_name(checks)
        self.assertFalse(c["ffmpeg"]["required"]); self.assertFalse(c["whisper-cli"]["required"])
        self.assertFalse(c["soffice"]["required"])
        self.assertEqual(D.exit_code(checks), 0)

    def test_video_without_sidecar_requires_ffmpeg_and_whisper(self):
        _tree(self.root / "docs", ["m/a.mp4", "m/a.vtt", "m/b.mp4"])
        cfg = _config(self.root, ["**/*.mp4", "**/*.vtt"])
        with patch.object(D, "which", return_value=None):
            checks = D.run_checks(D.scan_corpus(config=str(cfg)), config=str(cfg))
        c = _by_name(checks)
        self.assertTrue(c["ffmpeg"]["required"]); self.assertFalse(c["ffmpeg"]["ok"])
        self.assertIn("2 video file(s)", c["ffmpeg"]["why"])
        self.assertTrue(c["whisper-cli"]["required"])
        self.assertIn("1 of 2 video(s)", c["whisper-cli"]["why"])
        self.assertEqual(D.exit_code(checks), 1)

    def test_every_video_has_sidecar_so_whisper_is_optional(self):
        _tree(self.root / "docs", ["a.mp4", "a.VTT"])
        cfg = _config(self.root, ["**/*.mp4", "**/*.VTT"])
        with patch.object(D, "which", side_effect=lambda n: f"/bin/{n}" if n in ("ffmpeg", "ffprobe") else None):
            checks = D.run_checks(D.scan_corpus(config=str(cfg)), config=str(cfg))
        c = _by_name(checks)
        self.assertFalse(c["whisper-cli"]["required"]); self.assertTrue(c["ffmpeg"]["ok"])
        self.assertEqual(D.exit_code(checks), 0)

    def test_whisper_ok_needs_binary_and_configured_model_file(self):
        _tree(self.root / "docs", ["a.mp4"])
        model = self.root / "ggml-small.en.bin"; model.write_bytes(b"m")
        cfg = _config(self.root, ["**/*.mp4"], '[video]\nwhisper_model = "ggml-small.en.bin"\n')
        with patch.object(D, "which", side_effect=lambda n: f"/bin/{n}"):
            c = _by_name(D.run_checks(D.scan_corpus(config=str(cfg)), config=str(cfg)))
        self.assertTrue(c["whisper-cli"]["ok"])
        model.unlink()
        with patch.object(D, "which", side_effect=lambda n: f"/bin/{n}"):
            c = _by_name(D.run_checks(D.scan_corpus(config=str(cfg)), config=str(cfg)))
        self.assertFalse(c["whisper-cli"]["ok"]); self.assertIn("file not found", c["whisper-cli"]["detail"])

    def test_include_gap_is_reported(self):
        _tree(self.root / "docs", ["a.pdf", "rec/standup.mp4"])
        cfg = _config(self.root, ["**/*.pdf"])
        scan = D.scan_corpus(config=str(cfg))
        self.assertEqual(scan["include_gaps"], ["docs:rec/standup.mp4"])
        self.assertIn("not matched by any include glob", D.format_report([], scan["include_gaps"]))

    def test_no_config_means_everything_optional(self):
        with patch.object(D, "which", return_value=None), patch.object(D, "soffice_path", return_value=None):
            checks = D.run_checks(D.scan_corpus())
        self.assertTrue(all(not c["required"] for c in checks if c["name"] not in ("python deps", "sqlite-vec")))

    def test_need_flag_forces_a_requirement(self):
        with patch.object(D, "which", return_value=None):
            c = _by_name(D.run_checks(D.scan_corpus(), need=("evals",)))
        self.assertTrue(c["node"]["required"])


class DoctorCliTests(unittest.TestCase):
    def test_unreadable_config_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "brain.toml"; bad.write_text("version = [")
            self.assertEqual(D.main(["--config", str(bad)]), 2)

    def test_json_output_shape(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(D, "check_python_deps", return_value=(True, "ok")), \
             patch.object(D, "check_sqlite_vec", return_value=(True, "ok")), \
             patch("sys.stdout") as out:
            code = D.main(["--corpus", td, "--json"])
        payload = json.loads("".join(call.args[0] for call in out.write.call_args_list))
        self.assertEqual(code, 0)
        self.assertEqual(set(payload), {"ok", "checks", "include_gaps"})
        self.assertEqual(set(payload["checks"][0]), {"name", "ok", "required", "why", "detail", "install"})


class SofficeCandidatesPinnedTests(unittest.TestCase):
    def test_candidates_match_render_pages_and_parse_corpus(self):
        def tuple_in(path):
            src = path.read_text()
            m = re.search(r"for c in \((.*?)\):", src, re.S)
            return tuple(re.findall(r'"([^"]+)"', m.group(1)))
        self.assertEqual(D.SOFFICE_CANDIDATES, tuple_in(SK / "visual-parse" / "render_pages.py"))
        self.assertEqual(D.SOFFICE_CANDIDATES, tuple_in(SK / "corpus-taxonomy-extraction" / "parse_corpus.py"))
