"""A slide review is given the slide's page image. Hidden slides are not in the PDF, so page numbers are not slide numbers once a slide is
hidden: with slide 2 hidden, p02 is slide 3. --prepare prints the right image for each slide; building the name from the slide number
showed two reviewers (slides 35 and 50 of a deck with hidden slide 33) the wrong slide."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))
import apply_slide_review as SR  # noqa: E402


def work_dir(hidden, pages=5, slug="deck"):
    d = Path(tempfile.mkdtemp())
    (d / "stats.json").write_text(json.dumps({"hidden_slides": hidden}))
    (d / "render" / slug).mkdir(parents=True)
    (d / "render" / slug / "pages.json").write_text(json.dumps(
        {"slug": slug, "pages": [{"page": i, "image": f"{slug}/p{i:02d}.png"} for i in range(1, pages + 1)]}))
    return d


class SlideImages(unittest.TestCase):
    def test_without_hidden_slides_the_page_is_the_slide(self):
        self.assertEqual(SR.slide_images(work_dir([]), [3, 5]), {"3": "render/deck/p03.png", "5": "render/deck/p05.png"})

    def test_a_slide_after_a_hidden_one_is_one_page_earlier(self):
        d = work_dir([2])
        self.assertEqual(SR.slide_images(d, [1, 4]), {"1": "render/deck/p01.png", "4": "render/deck/p03.png"})

    def test_several_hidden_slides_shift_each_later_slide_by_the_number_before_it(self):
        d = work_dir([2, 3, 9], pages=8)
        self.assertEqual(SR.slide_images(d, [4, 10]), {"4": "render/deck/p02.png", "10": "render/deck/p07.png"})

    def test_a_missing_render_gives_no_images_and_no_crash(self):
        d = Path(tempfile.mkdtemp())
        (d / "stats.json").write_text(json.dumps({"hidden_slides": []}))
        self.assertEqual(SR.slide_images(d, [3]), {})

    def test_cli_prints_the_slides_and_their_images(self):
        d = work_dir([2])
        finding = {"id": "C01", "p_id": "s04-sh1-p1", "quote": "q", "claim": "c", "verdict": "Incorrect", "severity": "Major", "evidence": "e",
                   "conflicts": [], "fix": "f", "confidence": "High", "why": "w", "suggested": "s", "source": "x", "sources": [], "destination": ""}
        (d / "findings.json").write_text(json.dumps([finding]))
        (d / "run.json").write_text(json.dumps({"comments": "major"}))
        r = subprocess.run([sys.executable, "-I", str(DECK / "apply_slide_review.py"), str(d / "findings.json"), "--run", str(d / "run.json"),
                            "--prepare", str(d)], capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"slides": [4], "images": {"4": "render/deck/p03.png"}})

    def test_the_skill_tells_the_worker_to_use_the_printed_image_not_a_name_built_from_the_slide_number(self):
        text = (DECK / "SKILL.md").read_text(encoding="utf-8")
        para = next(p for p in text.split("\n") if "Slide review. Brief:" in p)
        self.assertIn("under `images`", para)
        self.assertNotIn("<the slide's page image>", para)


if __name__ == "__main__":
    unittest.main()
