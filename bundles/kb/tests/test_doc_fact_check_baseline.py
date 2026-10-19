"""baseline.py: the rerun baseline and its sources sidecar (spec §4.6)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import baseline as B

F = [{"id": "C01", "p_id": "p3", "section": "Overview > 1 Scope", "quote": "Portal | calls 3 services",
      "type": "NUM", "verdict": "Incorrect", "severity": "Major", "confidence": "High",
      "evidence": "The design note states four services (2026-09-01).",
      "trail": "get_metric(name=service_count) not_modeled; search_knowledge → chunk 123456789012345678",
      "conflicts": [], "fix": "Say four services.", "source": "design-note.md", "sources": [],
      "destination": "Word comment"}]


class Baseline(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "findings.json").write_text(json.dumps(F))
        (self.dir / "run.json").write_text(json.dumps({"mode": "fast", "source_sha256": "ab" * 32,
                                                       "claims_verified": 1}))

    def test_header_fields(self):
        md, _ = B.write_baseline(self.dir / "findings.json", "Draft.docx", "kv-7", today="2026-10-06")
        text = md.read_text()
        for line in ("- source_sha256: " + "ab" * 32, "- mode: fast", "- knowledge_version: kv-7",
                     "- claims: 1", "- date: 2026-10-06"):
            self.assertIn(line, text)

    def test_table_escapes_pipes_and_keeps_trail(self):
        md, _ = B.write_baseline(self.dir / "findings.json", "Draft.docx", "kv-7", today="2026-10-06")
        row = next(l for l in md.read_text().splitlines() if l.startswith("| C01 "))
        self.assertIn("Portal \\| calls 3 services", row)
        self.assertIn("123456789012345678", row)

    def test_sources_sidecar(self):
        _, src = B.write_baseline(self.dir / "findings.json", "Draft.docx", "kv-7", today="2026-10-06")
        data = json.loads(src.read_text())
        self.assertIn({"tag": "RAG:123456789012345678", "kind": "narrative", "chunk_id": "123456789012345678",
                       "source_file": "design-note.md", "finding": "C01"}, data)
        self.assertIn({"tag": "MART:service_count", "kind": "metric", "metric": "service_count", "grain": None,
                       "source_file": "design-note.md", "finding": "C01"}, data)

    def test_conflicts_column(self):  # I4
        self.assertIn("conflicts", B.COLUMNS)
        rows = [dict(F[0], conflicts=[{"span": "four services", "source": "a.md", "date": "2026-01-01"},
                                      {"span": "five | services", "source": "b.md", "date": "2026-02-01"}])]
        (self.dir / "findings.json").write_text(json.dumps(rows))
        md, _ = B.write_baseline(self.dir / "findings.json", "Draft.docx", "kv-7", today="2026-10-06")
        lines = md.read_text().splitlines()
        header = next(l for l in lines if l.startswith("| id "))
        self.assertIn("| conflicts |", header)
        row = next(l for l in lines if l.startswith("| C01 "))
        self.assertIn("| four services — a.md, 2026-01-01; five \\| services — b.md, 2026-02-01 |", row)

    def test_missing_run_json_is_an_error(self):
        (self.dir / "run.json").unlink()
        with self.assertRaises(FileNotFoundError):
            B.write_baseline(self.dir / "findings.json", "Draft.docx", "kv-7")


if __name__ == "__main__":
    unittest.main()
