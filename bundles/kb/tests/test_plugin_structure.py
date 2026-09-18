from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parents[1]          # .../bundles/kb
REPO_ROOT = KB_ROOT.parents[1]                          # .../bundles/kb -> bundles -> repo root


def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML-frontmatter parser: top-level `key: value` pairs only."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


class TestPluginManifest(unittest.TestCase):
    def test_plugin_json_exists_and_names_kb(self):
        manifest = load_json(KB_ROOT / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "kb")
        self.assertIn("description", manifest)
        self.assertIn("version", manifest)
        # kb ships no MCP server of its own (it consumes the Brain's)
        self.assertNotIn("mcpServers", manifest)

    def test_marketplace_lists_kb(self):
        # REPO_ROOT must be the actual repo root (holds the marketplace file)
        self.assertTrue((REPO_ROOT / ".claude-plugin" / "marketplace.json").exists(),
                        f"REPO_ROOT {REPO_ROOT} is not the repo root")
        market = load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
        names = {p["name"] for p in market["plugins"]}
        self.assertIn("kb", names)
        kb_entry = next(p for p in market["plugins"] if p["name"] == "kb")
        self.assertEqual(kb_entry["source"], "./bundles/kb")

    def test_readme_exists(self):
        self.assertTrue((KB_ROOT / "README.md").is_file())


class TestDoctrineAndVerifier(unittest.TestCase):
    def test_doctrine_covers_truth_rules(self):
        text = read_text(KB_ROOT / "skills" / "_shared" / "doctrine.md")
        for token in ["[RAG:", "[MART:", "[GRAPH:", "not modeled", "source_file"]:
            self.assertIn(token, text, f"doctrine missing {token!r}")

    def test_verifier_agent_frontmatter(self):
        text = read_text(KB_ROOT / "agents" / "verifier.md")
        fm = parse_frontmatter(text)
        self.assertEqual(fm.get("name"), "verifier")
        self.assertIn("description", fm)
        # A `tools:` allowlist would pin the verifier to specific MCP server names and
        # leave it blind to any other Brain. It must inherit MCP tools instead.
        self.assertNotIn("tools", fm, "verifier must not pin a tools allowlist")
        # Read-only is enforced by the platform, not by prose.
        self.assertIn("disallowedTools", fm)
        for tool in ("Write", "Edit", "NotebookEdit", "Bash", "Task", "SlashCommand"):
            self.assertIn(tool, fm["disallowedTools"], f"verifier may still use {tool}")
        # No hardcoded server names anywhere in the agent.
        self.assertNotIn("mcp__brain__", text)
        self.assertNotIn("mcp__plugin_brain_brain__", text)

    def test_doctrine_states_discovery_contract(self):
        text = read_text(KB_ROOT / "skills" / "_shared" / "doctrine.md")
        # Identify a Brain by its tool surface, never by server name.
        for token in ("Brain Discovery", "tool surface", "search_knowledge", "get_metric"):
            self.assertIn(token, text, f"doctrine missing {token!r}")
        # The old rename-to-`brain` convention must be gone.
        self.assertNotIn("mcp__brain__", text)
        self.assertNotIn("mcp__plugin_brain_brain__", text)
        # One invocation binds to one Brain.
        self.assertIn("never blend", text.lower())


class TestMarketplacePlugins(unittest.TestCase):
    def test_marketplace_has_brain_and_kb(self):
        market = load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
        names = {p["name"] for p in market["plugins"]}
        self.assertIn("brain", names)
        self.assertIn("kb", names)

    def test_no_root_plugin_json(self):
        # the repo root is no longer a plugin; only the marketplace file remains
        self.assertFalse((REPO_ROOT / ".claude-plugin" / "plugin.json").exists())

    def test_brain_plugin_manifest_names_brain(self):
        manifest = load_json(REPO_ROOT / "bundles" / "brain" / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "brain")


class TestCoworkCleanup(unittest.TestCase):
    def test_no_brain_cowork_tree(self):
        self.assertFalse((REPO_ROOT / "cowork" / "brain-cowork").exists(),
                         "cowork/brain-cowork must be removed")

    def test_marketplace_has_no_brain_cowork(self):
        market = load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
        names = {p["name"] for p in market["plugins"]}
        self.assertNotIn("brain-cowork", names)
        self.assertEqual(names, {"brain", "kb"})


class TestCoworkDoc(unittest.TestCase):
    def test_cowork_doc_exists_and_covers_setup(self):
        doc = KB_ROOT / "docs" / "cowork-setup.md"
        self.assertTrue(doc.is_file(), "bundles/kb/docs/cowork-setup.md missing")
        text = read_text(doc)
        for token in ("marketplace", "connector", "brain", "Entra", "/kb:ask"):
            self.assertIn(token, text, f"cowork-setup.md missing {token!r}")

    def test_readme_links_cowork_doc(self):
        readme = read_text(KB_ROOT / "README.md")
        self.assertIn("docs/cowork-setup.md", readme)

    def test_cowork_doc_does_not_require_a_connector_name(self):
        text = read_text(KB_ROOT / "docs" / "cowork-setup.md").lower()
        self.assertNotIn("name the connector `brain`", text)
        self.assertNotIn("disable", text,
                         "cowork doc must not tell users to disable a Brain connector")
        self.assertIn("any name", text)


class TestNoHardcodedBrainNamespace(unittest.TestCase):
    """The whole point of the multi-Brain work: kb names no MCP server."""

    def test_kb_tree_is_free_of_server_names(self):
        offenders = []
        for sub in ("skills", "agents"):
            for path in sorted((KB_ROOT / sub).rglob("*.md")):
                text = read_text(path)
                for token in ("mcp__brain__", "mcp__plugin_brain_brain__"):
                    if token in text:
                        offenders.append(f"{path.relative_to(KB_ROOT)}: {token}")
        self.assertEqual(offenders, [], "hardcoded MCP server names remain")

    def test_no_skill_grants_itself_tools(self):
        offenders = [
            path.parent.name
            for path in sorted((KB_ROOT / "skills").glob("*/SKILL.md"))
            if "allowed-tools" in read_text(path)
        ]
        self.assertEqual(offenders, [], "skills still declare allowed-tools")


_CONTRACT_START = "<!-- BRAIN-CONTRACT:START -->"
_CONTRACT_END = "<!-- BRAIN-CONTRACT:END -->"


def _extract_contract(text: str) -> str:
    """The delimited canonical block, or '' when absent."""
    if _CONTRACT_START not in text or _CONTRACT_END not in text:
        return ""
    return text.split(_CONTRACT_START, 1)[1].split(_CONTRACT_END, 1)[0]


class TestConnectSkillRemoved(unittest.TestCase):
    """Registering a Brain is adding an MCP server — platform plumbing, not a skill."""

    def test_connect_skill_is_gone(self):
        self.assertFalse((KB_ROOT / "skills" / "connect").exists())

    def test_nothing_references_the_deleted_skill(self):
        offenders = []
        candidates = [KB_ROOT / "README.md", REPO_ROOT / "README.md"]
        for sub in ("skills", "agents", "docs", "hooks"):
            root = KB_ROOT / sub
            if root.exists():
                candidates.extend(sorted(root.rglob("*")))
        for path in candidates:
            if path.is_file() and path.suffix in (".md", ".sh") and "kb:connect" in read_text(path):
                offenders.append(str(path))
        self.assertEqual(offenders, [], "stale /kb:connect references remain")

    def test_contract_pin_is_a_tiebreak_that_stops_on_a_genuine_miss(self):
        """A Brain named in the request must outrank the pin; the pin only stops
        discovery when it names a Brain that matches no reachable candidate."""
        text = read_text(KB_ROOT / "skills" / "_shared" / "doctrine.md")
        block = _extract_contract(text)
        self.assertTrue(block.strip(), "doctrine.md must delimit the canonical contract block")
        for token in ("project instructions", "CLAUDE.md"):
            self.assertIn(token, block, f"contract missing {token!r}")
        self.assertIn(
            "fall through to discovery", block,
            "contract must keep the pin's stop-on-unreachable behavior",
        )
        self.assertIn(
            "wins over the pin", block,
            "contract must state that a request-named Brain outranks the pin",
        )
        # Override must be resolved before Pinned in the numbered list.
        override_idx = block.find("**Override.**")
        pinned_idx = block.find("**Pinned.**")
        self.assertGreater(override_idx, -1, "contract missing an Override step")
        self.assertGreater(pinned_idx, -1, "contract missing a Pinned step")
        self.assertLess(override_idx, pinned_idx,
                        "Override must be resolved before Pinned")


if __name__ == "__main__":
    unittest.main()
