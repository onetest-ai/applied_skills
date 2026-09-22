from __future__ import annotations

import importlib.util
import tempfile
import tomllib
import unittest
from pathlib import Path

SK = Path(__file__).resolve().parent.parent / "skills"
_spec = importlib.util.spec_from_file_location("brain_doctor", SK / "knowledge-pipeline" / "brain_doctor.py")
D = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(D)

HAND_EDITED = '''version = 1

# my notes — keep me
[paths]
parsed = "parsed"   # trailing comment

[sources.roots.docs]
path = "docs"
include = ["**/*.pdf"]
'''


class CatalogueTests(unittest.TestCase):
    def test_every_entry_downloadable_and_one_recommended_per_language(self):
        for e in D.CATALOGUE:
            self.assertTrue(e["file"].startswith("ggml-") and e["file"].endswith(".bin"), e)
            self.assertIn(D.HF_BASE + e["file"], D.download_command(e))
            self.assertIn(str(D.SHARED_MODEL_DIR), D.download_command(e))
        rec = [e["name"] for e in D.CATALOGUE if e["recommended"]]
        self.assertEqual(sorted(rec), ["small", "small.en"])


class DiscoveryTests(unittest.TestCase):
    def test_find_models_lists_ggml_files_once(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a"; b = Path(td) / "b"; a.mkdir(); b.mkdir()
            (a / "ggml-small.en.bin").write_bytes(b"x" * 10)
            (a / "notes.txt").write_text("no")
            (b / "ggml-small.en.bin").symlink_to(a / "ggml-small.en.bin")
            found = D.find_models([a, b, Path(td) / "missing"])
        self.assertEqual([(m["file"], m["bytes"]) for m in found], [("ggml-small.en.bin", 10)])


class SetVideoKeysTests(unittest.TestCase):
    def test_appends_section_and_leaves_rest_byte_identical(self):
        out = D.set_video_keys(HAND_EDITED, {"whisper_model": "~/.cache/brain/whisper/ggml-small.en.bin",
                                             "language": "en"})
        self.assertTrue(out.startswith(HAND_EDITED))
        self.assertEqual(tomllib.loads(out)["video"],
                         {"whisper_model": "~/.cache/brain/whisper/ggml-small.en.bin", "language": "en"})

    def test_replaces_existing_keys_in_place_only(self):
        src = HAND_EDITED + '\n[video]\nwhisper_model = "old.bin"  # was\nother = 1\n\n[deployment]\ntarget = "local"\n'
        out = D.set_video_keys(src, {"whisper_model": "new.bin", "language": "auto"})
        data = tomllib.loads(out)
        self.assertEqual(data["video"], {"whisper_model": "new.bin", "other": 1, "language": "auto"})
        self.assertEqual(data["deployment"], {"target": "local"})
        self.assertTrue(out.startswith(HAND_EDITED))
        self.assertIn('[deployment]\ntarget = "local"\n', out)

    def test_cli_writes_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "brain.toml"; cfg.write_text(HAND_EDITED)
            model = Path(td) / "ggml-base.bin"; model.write_bytes(b"m")
            self.assertEqual(D.main(["set-whisper-model", "--config", str(cfg), "--model", str(model)]), 0)
            self.assertEqual(tomllib.loads(cfg.read_text())["video"]["whisper_model"], str(model.resolve()))

    def test_cli_stores_the_resolved_path_of_a_cwd_relative_model(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "proj").mkdir(); (root / "models").mkdir()
            cfg = root / "proj" / "brain.toml"; cfg.write_text(HAND_EDITED)
            (root / "models" / "ggml-base.bin").write_bytes(b"m")
            old = os.getcwd(); os.chdir(root)
            try:
                self.assertEqual(D.main(["set-whisper-model", "--config", str(cfg),
                                         "--model", "models/ggml-base.bin"]), 0)
            finally:
                os.chdir(old)
            stored = tomllib.loads(cfg.read_text())["video"]["whisper_model"]
            self.assertTrue(Path(stored).is_absolute(), stored)
            self.assertEqual(stored, str((root / "models" / "ggml-base.bin").resolve()))
            # and brain.toml's own resolution finds the same file
            self.assertTrue(Path(D.configured_video(str(cfg))["whisper_model"]).is_file())

    def test_top_level_config_and_json_survive_the_subcommand(self):
        import contextlib
        import io
        import json
        self.assertEqual(D.main(["--config", "/does/not/exist.toml", "whisper-models"]), 2)
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "brain.toml"; cfg.write_text(HAND_EDITED)
            model = Path(td) / "ggml-base.bin"; model.write_bytes(b"m")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(D.main(["--config", str(cfg), "--json", "whisper-models"]), 0)
            self.assertIn("catalogue", json.loads(buf.getvalue()))
            self.assertEqual(D.main(["--config", str(cfg), "set-whisper-model", "--model", str(model)]), 0)
            self.assertIn("whisper_model", tomllib.loads(cfg.read_text())["video"])

    def test_set_whisper_model_without_any_config_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            model = Path(td) / "ggml-base.bin"; model.write_bytes(b"m")
            self.assertEqual(D.main(["set-whisper-model", "--model", str(model)]), 2)

    def test_cli_refuses_missing_model_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "brain.toml"; cfg.write_text(HAND_EDITED)
            self.assertEqual(D.main(["set-whisper-model", "--config", str(cfg), "--model", "/nope.bin"]), 1)
            self.assertEqual(cfg.read_text(), HAND_EDITED)

    def test_whisper_models_missing_config_exits_2(self):
        self.assertEqual(D.main(["whisper-models", "--config", "/does/not/exist.toml"]), 2)

    def test_set_whisper_model_unparsable_config_exits_2_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "brain.toml"; cfg.write_text("version = [")
            model = Path(td) / "ggml-base.bin"; model.write_bytes(b"m")
            self.assertEqual(D.main(["set-whisper-model", "--config", str(cfg), "--model", str(model)]), 2)
            self.assertEqual(cfg.read_text(), "version = [")
