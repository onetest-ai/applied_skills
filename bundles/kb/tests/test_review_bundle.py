"""review_bundle.py (spec F2, eval 5): the run's extraction and findings travel with the deliverables, so a reviewer
can open what the checker saw, even when the run happened in a sandbox."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from test_plugin_structure import KB_ROOT

SKILLS = KB_ROOT / "skills"
SCRIPT = SKILLS / "doc-fact-check" / "review_bundle.py"
COMMAND = 'python "<skill dir>/review_bundle.py" "<work dir>" "<run dir>" --out "<draft dir>/<name> — review data.zip"'


def make_run(root: Path) -> tuple[Path, Path]:
    run = root / "run"
    work = run / "work"
    (work / "media").mkdir(parents=True)
    (work / "render" / "deck").mkdir(parents=True)
    for name in ("sections.json", "stats.json", "batches.json", "batch_1.json", "claims_batch_1.json",
                 "coverage_batch_1.json", "chunks.json", "chunk_1.json", "findings_chunk_1.json",
                 "claims_figures.json", "brain_context.json", "timings.json"):
        (work / name).write_text(json.dumps({"name": name}))
    (work / "media" / "slide_03.png").write_bytes(b"\x89PNG fake")
    (work / "render" / "deck" / "p01.png").write_bytes(b"\x89PNG big render")
    for name in ("findings.json", "run.json", "coverage.json"):
        (run / name).write_text("[]")
    (run / "baseline.md").write_text("# baseline\n")
    return work, run


def bundle(work, run, out, *extra):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), str(work), str(run), "--out", str(out), *extra],
                          capture_output=True, text=True, cwd="/")


class ReviewBundle(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.work, self.run = make_run(self.d)

    def test_holds_extraction_claims_findings_and_slide_images(self):
        out = self.d / "deck — review data.zip"
        r = bundle(self.work, self.run, out)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = set(zipfile.ZipFile(out).namelist())
        for n in ("work/sections.json", "work/stats.json", "work/batches.json", "work/batch_1.json",
                  "work/claims_batch_1.json", "work/chunk_1.json", "work/findings_chunk_1.json",
                  "work/claims_figures.json", "work/media/slide_03.png",
                  "findings.json", "run.json", "coverage.json", "baseline.md"):
            self.assertIn(n, names)
        self.assertFalse(any(n.startswith("work/render/") for n in names), "the full render is left out (size)")
        self.assertIn("README.txt", names)

    def test_is_deterministic(self):
        a, b = self.d / "a.zip", self.d / "b.zip"
        bundle(self.work, self.run, a)
        bundle(self.work, self.run, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_records_when_each_worker_file_was_written(self):
        """The zip stamps every entry 1980 (so it is reproducible), which erases when each worker finished; work/file_times.json keeps it
        (UTC, to the second), so a run's trace of stage and worker times can be drawn from the bundle alone."""
        import os
        (self.work / "slide_review_3.json").write_text("[]")
        for name, when in (("claims_batch_1.json", 1_790_000_000), ("findings_chunk_1.json", 1_790_000_600), ("slide_review_3.json", 1_790_000_900)):
            os.utime(self.work / name, (when, when))
        out = self.d / "t.zip"
        self.assertEqual(bundle(self.work, self.run, out).returncode, 0)
        times = json.loads(zipfile.ZipFile(out).read("work/file_times.json"))
        self.assertEqual(times["work/claims_batch_1.json"], "2026-09-21T14:13:20+00:00")
        self.assertEqual(times["work/findings_chunk_1.json"], "2026-09-21T14:23:20+00:00")
        self.assertIn("work/slide_review_3.json", times)
        self.assertFalse(any(n.startswith("work/media/") for n in times), "images are not workers")

    def test_refuses_anything_but_a_zip_and_a_missing_work_dir(self):
        r = bundle(self.work, self.run, self.d / "deck.pptx")
        self.assertEqual(r.returncode, 2)
        self.assertIn("must end in .zip", r.stderr)
        r = bundle(self.d / "nope", self.run, self.d / "x.zip")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.d / "x.zip").exists())

    def test_both_skills_deliver_it_in_step_10(self):
        for skill in ("doc-fact-check", "deck-fact-check"):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            step10 = text[text.index("### 10."):text.index("## Rules")]
            with self.subTest(skill=skill):
                self.assertIn(COMMAND, step10)
                self.assertIn("review data.zip", step10.split(COMMAND)[1])


class InstallFallback(unittest.TestCase):
    """Spec F4: a locked-down Python refuses a plain pip install; step 0 says exactly what to do."""

    def test_step_0_names_the_externally_managed_fallback(self):
        for skill, pkgs in (("doc-fact-check", '"python-docx>=1.2"'), ("deck-fact-check", 'python-pptx "pymupdf<2"')):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            step0 = text[text.index("### 0. Resolve"):text.index("### 0b.")]
            with self.subTest(skill=skill):
                self.assertIn("externally-managed-environment", step0)
                self.assertIn(f"python -m pip install --break-system-packages {pkgs}", step0)


if __name__ == "__main__":
    unittest.main()
