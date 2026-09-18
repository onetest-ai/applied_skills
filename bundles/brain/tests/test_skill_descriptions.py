"""Every skill's description must lead with its invocation trigger.

The description is the only thing a model sees when deciding whether to invoke a
skill, so it opens with `Use when <trigger>` and puts the capability after.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"


def _description(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        if line.startswith("description:"):
            return line.partition(":")[2].strip()
    return ""


class SkillDescriptionTests(unittest.TestCase):
    def test_every_description_opens_with_use_when(self):
        offenders = []
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            desc = _description(path)
            if not desc.startswith("Use when "):
                offenders.append(f"{path.parent.name}: {desc[:50]!r}")
        self.assertEqual(offenders, [], "descriptions must open with 'Use when '")

    def test_every_skill_has_a_description(self):
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            self.assertTrue(_description(path),
                            f"{path.parent.name}: no description in frontmatter")


if __name__ == "__main__":
    unittest.main()
