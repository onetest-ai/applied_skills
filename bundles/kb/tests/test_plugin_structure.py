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
        # read-only: no Write/Edit in the tools allowlist
        self.assertIn("tools", fm)
        self.assertNotIn("Write", fm["tools"])
        self.assertNotIn("Edit", fm["tools"])
        # The verifier's steps CALL the Brain MCP tools — it must actually be granted
        # them (Read+Grep alone cannot re-resolve a citation). Guard both namespaces.
        for tool in ("health", "get_evidence", "get_metric", "get_taxonomy"):
            self.assertIn(f"mcp__brain__{tool}", fm["tools"], f"verifier missing mcp__brain__{tool}")
            self.assertIn(f"mcp__plugin_brain_brain__{tool}", fm["tools"], f"verifier missing plugin-namespace {tool}")


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
        for token in ("marketplace", "connector", "brain", "Entra", "/kb:connect"):
            self.assertIn(token, text, f"cowork-setup.md missing {token!r}")

    def test_readme_links_cowork_doc(self):
        readme = read_text(KB_ROOT / "README.md")
        self.assertIn("docs/cowork-setup.md", readme)


if __name__ == "__main__":
    unittest.main()
