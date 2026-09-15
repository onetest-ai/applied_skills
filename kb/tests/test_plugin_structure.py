from __future__ import annotations

import json
import unittest
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parents[1]          # .../kb
REPO_ROOT = KB_ROOT.parent                             # repo root


def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


class TestPluginManifest(unittest.TestCase):
    def test_plugin_json_exists_and_names_kb(self):
        manifest = load_json(KB_ROOT / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "kb")
        self.assertIn("description", manifest)
        self.assertIn("version", manifest)
        # kb ships no MCP server of its own (it consumes the Brain's)
        self.assertNotIn("mcpServers", manifest)

    def test_marketplace_lists_kb(self):
        market = load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
        names = {p["name"] for p in market["plugins"]}
        self.assertIn("kb", names)
        kb_entry = next(p for p in market["plugins"] if p["name"] == "kb")
        self.assertEqual(kb_entry["source"], "./kb")

    def test_readme_exists(self):
        self.assertTrue((KB_ROOT / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
