from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _tools
import video_capture as V

FAKE_WHISPER = """#!{py}
import sys, json, pathlib
args = sys.argv[1:]
of = args[args.index("-of") + 1]
pathlib.Path(of + ".vtt").write_text("WEBVTT\\n\\n00:00:01.000 --> 00:00:02.000\\nhello from asr\\n")
pathlib.Path(of).parent.joinpath("..", "calls.json").resolve().write_text(json.dumps(args))
"""


class ResolveModelTests(unittest.TestCase):
    def test_flag_wins_then_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "brain.toml"
            cfg.write_text('version = 1\n[video]\nwhisper_model = "models/ggml-base.bin"\nlanguage = "en"\n')
            self.assertEqual(V.resolve_model("/x.bin", str(cfg)), ("/x.bin", "en"))
            self.assertEqual(V.resolve_model(None, str(cfg)), (str(Path(td).resolve() / "models" / "ggml-base.bin"), "en"))
            self.assertEqual(V.resolve_model(None, None), (None, "auto"))


@_tools.require_tool("ffmpeg", "ffprobe")
class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        corpus = r / "c"; corpus.mkdir()
        self.video = _tools.make_synthetic_video(corpus / "talk.mp4", audio=True)
        self.work = r / "video"
        self.assertEqual(V.main(["probe", "--video", str(self.video), "--rel-to", str(corpus),
                                 "--work", str(self.work)]), 0)
        self.probe = self.work / "talk" / "probe.json"
        self.bin = r / "bin"; self.bin.mkdir()
        exe = self.bin / "whisper-cli"; exe.write_text(FAKE_WHISPER.format(py=sys.executable))
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        self.model = r / "ggml-small.en.bin"; self.model.write_bytes(b"m")
        self.env = patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.t.cleanup()

    def test_probe_chose_asr(self):
        self.assertEqual(json.loads(self.probe.read_text())["transcript"], "asr")

    def test_transcribe_writes_vtt_and_cache_key(self):
        self.assertEqual(V.main(["transcribe", "--probe", str(self.probe), "--model", str(self.model)]), 0)
        vtt = self.work / "talk" / "transcript.vtt"
        self.assertEqual(V.read_cues(vtt)[0]["text"], "hello from asr")
        asr = json.loads((self.work / "talk" / "asr.json").read_text())
        self.assertEqual((asr["model"], asr["language"]), ("ggml-small.en.bin", "auto"))
        mtime = vtt.stat().st_mtime_ns
        self.assertEqual(V.main(["transcribe", "--probe", str(self.probe), "--model", str(self.model)]), 0)
        self.assertEqual(vtt.stat().st_mtime_ns, mtime)  # cache hit

    def test_no_model_exits_pointing_to_doctor(self):
        with patch("sys.stderr") as err:
            code = V.main(["transcribe", "--probe", str(self.probe)])
        self.assertEqual(code, 1)
        self.assertIn("whisper-models", "".join(c.args[0] for c in err.write.call_args_list))

    def test_missing_binary_exits_3(self):
        with patch.dict(os.environ, {"PATH": "/nonexistent"}):
            self.assertEqual(V.main(["transcribe", "--probe", str(self.probe), "--model", str(self.model)]), 3)
