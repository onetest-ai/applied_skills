"""The fact-check pipeline ships in two skills, each installable on its own (Cowork has no cross-skill imports), so
the format-free files are copies that must stay byte-identical (spec §5, R5; the chunking.py precedent): a deck
finding is graded by exactly the rules a Word finding is. Change doc-fact-check/ and deck-fact-check/ together."""
from __future__ import annotations

import unittest

from test_plugin_structure import KB_ROOT

SKILLS = KB_ROOT / "skills"
DOC, DECK = SKILLS / "doc-fact-check", SKILLS / "deck-fact-check"
VISUAL_PARSE = KB_ROOT.parent / "brain" / "skills" / "visual-parse"
SHARED = ("section_io.py", "chunk_claims.py", "merge_findings.py", "fact_check_invariants.py", "findings_report.py",
          "baseline.py", "run_clock.py", "annotate_cli.py", "review_bundle.py", "source_links.py", "extract.md", "verify.md")
FORMAT_SPECIFIC = {"doc-fact-check": {"sections.py", "annotate.py", "SKILL.md"},
                   "deck-fact-check": {"pptx_sections.py", "pptx_annotate.py", "render_pages.py", "apply_slide_review.py",
                                       "slide_review.md", "SKILL.md"}}


class SharedCopies(unittest.TestCase):
    def test_d1_shared_files_are_byte_identical(self):
        for name in SHARED:
            with self.subTest(file=name):
                self.assertEqual((DOC / name).read_bytes(), (DECK / name).read_bytes(),
                                 f"{name} copies drifted: change doc-fact-check/ and deck-fact-check/ together")

    def test_d2_render_pages_is_byte_identical_with_visual_parse(self):
        self.assertEqual((VISUAL_PARSE / "render_pages.py").read_bytes(), (DECK / "render_pages.py").read_bytes(),
                         "render_pages.py drifted from bundles/brain/skills/visual-parse/: change both together")

    def test_every_file_is_either_shared_or_named_format_specific(self):
        # a new file must be put on one side of the line on purpose, or it would silently escape the drift test
        for skill in (DOC, DECK):
            files = {p.name for p in skill.iterdir() if p.is_file() and p.suffix in (".py", ".md")}
            with self.subTest(skill=skill.name):
                self.assertEqual(files - set(SHARED), FORMAT_SPECIFIC[skill.name])


if __name__ == "__main__":
    unittest.main()
