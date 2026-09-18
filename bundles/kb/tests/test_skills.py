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


class TestReportSkill(unittest.TestCase, SkillContractMixin):
    def test_report_contract(self):
        self.assert_skill(
            "report",
            required_tokens=["_shared/authoring.md", "verifier", "docs/kb/",
                             "Table of Contents", "Sources"],
        )


class TestModeSkill(unittest.TestCase, SkillContractMixin):
    def test_mode_contract(self):
        self.assert_skill("mode", required_tokens=[
            "state.json", "ambient", "on", "off", "status",  # preserved
            "Cowork",                                         # limitation noted
        ])


class TestSkillsNameNoServer(unittest.TestCase):
    """kb must not hardcode an MCP server name: users register Brains under any name."""

    FORBIDDEN = ("mcp__brain__", "mcp__plugin_brain_brain__")

    def test_no_skill_hardcodes_a_server_name(self):
        from test_plugin_structure import KB_ROOT, read_text
        for name in ("ask", "brief", "challenge", "explore", "report"):
            text = read_text(KB_ROOT / "skills" / name / "SKILL.md")
            for token in self.FORBIDDEN:
                self.assertNotIn(token, text, f"{name}: hardcodes {token!r}")

    def test_answer_skills_defer_to_the_discovery_contract(self):
        from test_plugin_structure import KB_ROOT, read_text
        for name in ("ask", "brief", "challenge", "explore", "report"):
            text = read_text(KB_ROOT / "skills" / name / "SKILL.md")
            self.assertIn("Brain Discovery", text,
                          f"{name}: must defer to the doctrine Brain Discovery contract")

    def test_every_description_opens_with_use_when(self):
        """The description is how a model decides whether to invoke a skill, so the
        trigger leads. `Use to` / `Use at` / capability-first openers do not qualify."""
        from test_plugin_structure import KB_ROOT, read_text, parse_frontmatter
        for path in sorted((KB_ROOT / "skills").glob("*/SKILL.md")):
            desc = parse_frontmatter(read_text(path)).get("description", "")
            self.assertTrue(desc.startswith("Use when "),
                            f"{path.parent.name}: description must open with 'Use when ', got {desc[:40]!r}")

    def test_no_skill_declares_allowed_tools(self):
        """`allowed-tools` is pre-approval, not capability, and it cannot name a server
        whose name varies per user. kb declares none and lets permissions govern."""
        from test_plugin_structure import KB_ROOT, read_text, parse_frontmatter
        for path in sorted((KB_ROOT / "skills").glob("*/SKILL.md")):
            fm = parse_frontmatter(read_text(path))
            self.assertNotIn("allowed-tools", fm,
                             f"{path.parent.name}: must not declare allowed-tools")


CONTRACT_START = "<!-- BRAIN-CONTRACT:START -->"
CONTRACT_END = "<!-- BRAIN-CONTRACT:END -->"


def _contract(text):
    """The delimited canonical block, or '' when absent."""
    if CONTRACT_START not in text or CONTRACT_END not in text:
        return ""
    return text.split(CONTRACT_START, 1)[1].split(CONTRACT_END, 1)[0]


class TestBrainContractIsInlined(unittest.TestCase):
    """A rule that only applies when a sibling file resolves is not a rule.

    The Agent Skills format documents same-directory references only, so
    `../_shared/*.md` may not resolve at runtime. Every skill that must obey the
    contract carries it verbatim; this test is what keeps the copies identical.
    """

    def _canonical(self):
        from test_plugin_structure import KB_ROOT, read_text
        block = _contract(read_text(KB_ROOT / "skills" / "_shared" / "doctrine.md"))
        self.assertTrue(block.strip(), "doctrine.md must delimit the canonical contract block")
        return block

    def test_every_answering_skill_inlines_the_contract(self):
        from test_plugin_structure import KB_ROOT, read_text
        canonical = self._canonical()
        for name in ("ask", "brief", "challenge", "explore", "report"):
            text = read_text(KB_ROOT / "skills" / name / "SKILL.md")
            self.assertEqual(_contract(text), canonical,
                             f"{name}: inlined contract differs from doctrine.md")

    def test_verifier_inlines_the_resolution_rules(self):
        from test_plugin_structure import KB_ROOT, read_text
        text = read_text(KB_ROOT / "agents" / "verifier.md")
        for token in ("tool surface", "never blend", "project instructions"):
            self.assertIn(token, text, f"verifier missing {token!r}")

    def test_no_skill_depends_on_a_parent_directory_reference_for_its_rules(self):
        """Depth may live in _shared/; the rules may not."""
        from test_plugin_structure import KB_ROOT, read_text
        for name in ("ask", "brief", "challenge", "explore", "report"):
            text = read_text(KB_ROOT / "skills" / name / "SKILL.md")
            head = text.split(CONTRACT_START, 1)[0]
            self.assertNotIn("Follow `../_shared/doctrine.md`", head,
                             f"{name}: still defers its rules to a parent-directory file")


if __name__ == "__main__":
    unittest.main()
