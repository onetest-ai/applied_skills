from __future__ import annotations

import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT, read_text, parse_frontmatter


class SkillContractMixin:
    def assert_skill(self, name, required_tokens):
        path = KB_ROOT / "skills" / name / "SKILL.md"
        self.assertTrue(path.is_file(), f"missing skill {name}")
        text = read_text(path)
        fm = parse_frontmatter(text)
        self.assertIn("description", fm, f"{name}: no description")
        for token in required_tokens:
            self.assertIn(token, text, f"{name}: missing {token!r}")


class TestAskSkill(unittest.TestCase, SkillContractMixin):
    def test_ask_contract(self):
        self.assert_skill(
            "ask",
            required_tokens=[
                "_shared/doctrine.md",   # references the doctrine
                "health",                # detects the brain first
                "search_knowledge",
                "get_metric",
                "get_taxonomy",
                "get_evidence",
                "not modeled",
                "[RAG:",
            ],
        )


if __name__ == "__main__":
    unittest.main()
