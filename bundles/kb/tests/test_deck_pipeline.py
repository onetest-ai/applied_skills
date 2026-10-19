"""deck-fact-check end to end, with canned stage-E and stage-V worker outputs: every documented command, run exactly as
SKILL.md runs it (python -I, from /), composes into a run whose final self-check prints `clean` (spec §7 A8)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

try:
    import pptx  # noqa: F401
    import pymupdf  # noqa: F401
    from render_pages import soffice_bin
    READY = soffice_bin() is not None
except ImportError:
    READY = False


def run(script, *args):
    r = subprocess.run([sys.executable, "-I", str(DECK / script), *map(str, args)],
                       capture_output=True, text=True, cwd="/", timeout=300)
    return r


@unittest.skipUnless(READY, "needs python-pptx, PyMuPDF and LibreOffice")
class EndToEnd(unittest.TestCase):
    def test_full_run_is_clean(self):
        import deck_fixtures as F
        d = Path(tempfile.mkdtemp())
        draft = F.full_deck(d)
        work, rundir = d / "run" / "work", d / "run"
        out_deck = d / "full — fact-checked.pptx"
        page = d / "full — findings.html"

        r = run("pptx_sections.py", draft, "--out", work)
        self.assertEqual(r.returncode, 0, r.stderr)
        secs = json.loads((work / "sections.json").read_text())
        self.assertEqual([s["section"] for s in secs], ["Slide 1: Delivery plan", "Slide 2: North region — FY2025", "Slide 3"])
        self.assertEqual([f["figure"] for s in secs for f in s["figures"]], [3])

        # stage E (canned): one claim per checkable body line and the data row; the image slide has no text
        body = {p["text"]: p for p in secs[0]["paragraphs"]}
        row = secs[1]["tables"][0]["rows"][1]
        claims = [
            {"claim_id": "B1-C01", "p_id": body["Uptime held at 99.9% all year."]["p_id"],
             "s_id": body["Uptime held at 99.9% all year."]["sentences"][0]["s_id"], "section": secs[0]["section"],
             "quote": "Uptime held at 99.9% all year.", "claim": "Uptime held at 99.9% all year.",
             "unresolved": ["period", "scope"], "type": "NUM", "questions": ["what was service uptime"]},
            {"claim_id": "B1-C02", "p_id": body["Go-live is set for Q2 2027."]["p_id"],
             "s_id": body["Go-live is set for Q2 2027."]["sentences"][0]["s_id"], "section": secs[0]["section"],
             "quote": "Go-live is set for Q2 2027.", "claim": "Go-live is set for Q2 2027.", "type": "TIME",
             "questions": ["when is go-live planned"]},
            {"claim_id": "B1-C03", "p_id": row["r_id"], "s_id": row["s_id"], "section": secs[1]["section"],
             "quote": "Uptime | 99.9%", "claim": "Uptime in the North region was 99.9% in FY2025.", "type": "NUM",
             "questions": ["what was north region uptime in fy2025"]},
        ]
        batches = json.loads((work / "batches.json").read_text())
        self.assertEqual(len(batches), 1)
        (work / "claims_batch_1.json").write_text(json.dumps(claims))
        (work / "coverage_batch_1.json").write_text(json.dumps([{"section_id": "s03", "reason": "no checkable statement"}]))
        pic = next(f for s in secs for f in s["figures"])["elements"][0]   # a figure claim names its element
        self_pid = f"s3-sh{pic['shape_id']}"
        (work / "claims_figures.json").write_text(json.dumps([
            {"claim_id": "I01", "p_id": self_pid, "s_id": "s3", "section": "Figure", "quote": "network diagram",
             "claim": "The slide 3 picture shows the Northwind network.", "type": "TOPO", "kind": "figure", "figure": 3,
             "questions": ["what does the northwind network connect"]}]))

        r = run("chunk_claims.py", work, "--scope", "all", "--chunk-size", "15")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        chunk = json.loads((work / "chunk_1.json").read_text())
        self.assertEqual(chunk["claims"][2]["claim"], "Uptime in the North region was 99.9% in FY2025.")

        # stage V (canned)
        def f(c, verdict, severity, **kw):
            base = {"id": c["claim_id"], "p_id": c["p_id"], "section": c["section"], "quote": c["quote"],
                    "claim": c["claim"], "type": c["type"], "verdict": verdict, "severity": severity,
                    "confidence": "High", "evidence": "The ops report states 99.1% for FY2025.",
                    "trail": "name check: none", "conflicts": [], "fix": "Use 99.1%.", "source": "ops-report.md",
                    "sources": []}
            return {**base, **kw}
        cs = chunk["claims"]
        (work / "findings_chunk_1.json").write_text(json.dumps([
            f(cs[0], "Misleading", "Major", fix="State the period and region."),
            f(cs[1], "Verified", "Minor", evidence="The plan states go-live in Q2 2027.", fix=""),
            f(cs[2], "Incorrect", "Blocker"),
            f(cs[3], "No Evidence", "Minor", evidence="The Brain holds no network diagram.", fix="needs owner input",
              anchor="drawing", figure=3)]))

        findings = rundir / "findings.json"
        r = run("merge_findings.py", work, "--out", findings, "--scope", "all", "--comments", "major")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        merged = json.loads(findings.read_text())
        self.assertEqual([x["id"] for x in merged], ["C01", "C02", "C03", "I01"])
        self.assertEqual(merged[2]["claim"], "Uptime in the North region was 99.9% in FY2025.")

        r = run("pptx_annotate.py", draft, out_deck, "--findings", findings, "--select", "major")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(set(json.loads(r.stdout)["written"]), {"C01", "C03"})

        r = run("findings_report.py", findings, "--out", page, "--document", "full.pptx", "--brain-version", "v1",
                "--run", rundir / "run.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Checked as: Uptime in the North region was 99.9% in FY2025.", page.read_text())

        r = run("baseline.py", findings, "--document", "full.pptx", "--brain-version", "v1")
        self.assertEqual(r.returncode, 0, r.stderr)

        r = run("fact_check_invariants.py", findings, out_deck, "--original", draft)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "clean"), r.stdout + r.stderr)
        self.assertEqual(json.loads((rundir / "run.json").read_text())["source_sha256"],
                         hashlib.sha256(draft.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
