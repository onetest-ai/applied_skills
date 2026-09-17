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


class TestExploreSkill(unittest.TestCase, SkillContractMixin):
    def test_explore_contract(self):
        self.assert_skill(
            "explore",
            required_tokens=[
                "../_shared/doctrine.md", "health",
                "find_related_content", "get_taxonomy", "chunk_id",
            ],
        )


class TestChallengeSkill(unittest.TestCase, SkillContractMixin):
    def test_challenge_contract(self):
        self.assert_skill(
            "challenge",
            required_tokens=[
                "../_shared/doctrine.md", "health",
                "get_metric", "get_evidence", "not modeled", "grain",
            ],
        )


class TestAuthoringShared(unittest.TestCase):
    def test_authoring_pipeline_documented(self):
        from test_plugin_structure import KB_ROOT, read_text
        text = read_text(KB_ROOT / "skills" / "_shared" / "authoring.md")
        for token in ["gather", "verify", "verifier", "human", "sources.json", "source_file"]:
            self.assertIn(token, text, f"authoring.md missing {token!r}")


class TestBriefSkill(unittest.TestCase, SkillContractMixin):
    def test_brief_contract(self):
        self.assert_skill(
            "brief",
            required_tokens=["_shared/authoring.md", "verifier", "docs/kb/", "Sources"],
        )

    def test_brief_does_not_allow_write(self):
        path = KB_ROOT / "skills" / "brief" / "SKILL.md"
        fm = parse_frontmatter(read_text(path))
        self.assertNotIn("Write", fm.get("allowed-tools", ""))


class TestReportSkill(unittest.TestCase, SkillContractMixin):
    def test_report_contract(self):
        self.assert_skill(
            "report",
            required_tokens=["_shared/authoring.md", "verifier", "docs/kb/",
                             "Table of Contents", "Sources"],
        )

    def test_report_does_not_allow_write(self):
        path = KB_ROOT / "skills" / "report" / "SKILL.md"
        fm = parse_frontmatter(read_text(path))
        self.assertNotIn("Write", fm.get("allowed-tools", ""))


class TestModeSkill(unittest.TestCase, SkillContractMixin):
    def test_mode_contract(self):
        self.assert_skill("mode", required_tokens=["state.json", "ambient", "on", "off", "status"])


class TestConnectSkill(unittest.TestCase, SkillContractMixin):
    def test_connect_contract(self):
        self.assert_skill("connect", required_tokens=[
            "health", "mcp-config", "brain",   # CLI branch preserved
            "connector", "Entra",              # Cowork branch added
        ])


if __name__ == "__main__":
    unittest.main()
