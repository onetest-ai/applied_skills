from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

VP = Path(__file__).resolve().parent.parent / "skills" / "visual-parse" / "vision_prep.py"


def _render_dir(root: Path, slug: str, medium: str | None, dropped_page: int | None = None) -> Path:
    d = root / "assets" / slug; d.mkdir(parents=True)
    pages = []
    for n in (1, 2):
        (d / f"p{n:02d}.png").write_bytes(b"png")
        p = {"page": n, "image": f"{slug}/p{n:02d}.png", "img_sha": f"{slug}-{n}", "flagged": True}
        if n == dropped_page:
            p["dropped"] = "no-content"
        pages.append(p)
    doc = {"doc": slug, "slug": slug, "pages": pages}
    if medium:
        doc["medium"] = medium
    (d / "pages.json").write_text(json.dumps(doc))
    return d


def _prep(*dirs, out: Path):
    args = [sys.executable, str(VP), "--out", str(out)]
    for d in dirs:
        args += ["--render-dir", str(d)]
    subprocess.run(args, check=True, capture_output=True, text=True)
    batches = [json.loads(p.read_text()) for p in sorted(out.glob("batch_*.json"))]
    return (out / "instructions.md").read_text(), [i for b in batches for i in b]


class VisionPrepVideoTests(unittest.TestCase):
    def test_deck_instructions_unchanged_and_no_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            instr, _ = _prep(_render_dir(root, "deck", None), out=root / "v")
        self.assertNotIn("no-content", instr)
        self.assertTrue(instr.startswith("# Transcribe each slide/page image to FAITHFUL structured Markdown"))

    def test_video_gets_gate_and_images_exist_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            instr, items = _prep(_render_dir(root, "rec__standup", "video"), out=root / "v")
            self.assertIn("<!-- no-content -->", instr)
            self.assertIn("never what might have been said", instr)
            self.assertEqual(len(items), 2)
            for it in items:
                self.assertTrue(Path(it["image"]).is_file(), it["image"])  # contract: value, not just key

    def test_dropped_pages_are_not_batched(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, items = _prep(_render_dir(root, "rec__standup", "video", dropped_page=1), out=root / "v")
        self.assertEqual([i["page"] for i in items], [2])

    def test_every_item_carries_its_medium(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, items = _prep(_render_dir(root, "deck", None), out=root / "v")
        self.assertEqual({i["medium"] for i in items}, {"document"})

    def test_mixed_deck_and_video_gate_is_scoped_to_video_items(self):
        # A deck slide of team photos must never be answered <!-- no-content --> (that
        # answer is cached forever in page_render and emitted empty by vision_assemble).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            instr, items = _prep(_render_dir(root, "m__standup", None),
                                 _render_dir(root, "m__standup--mp4", "video"), out=root / "v")
        by_medium = {}
        for it in items:
            by_medium.setdefault(it["medium"], []).append(it["img_sha"])
        self.assertEqual(sorted(by_medium["document"]), ["m__standup-1", "m__standup-2"])
        self.assertEqual(sorted(by_medium["video"]), ["m__standup--mp4-1", "m__standup--mp4-2"])
        self.assertIn("<!-- no-content -->", instr)
        self.assertIn("ONLY to items whose `medium` is `video`", instr)
        self.assertIn("Never answer `<!-- no-content -->` for an item whose `medium` is `document`", instr)
