"""The findings page is a local file rendered deterministically from the findings table."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT, read_text

SKILL_DIR = KB_ROOT / "skills" / "fact-check"
sys.path.insert(0, str(SKILL_DIR))


def f(id, verdict, severity, destination, quote="", **kw):
    return dict(id=id, verdict=verdict, severity=severity, destination=destination, quote=quote,
                where=kw.pop("where", "§1"), evidence=kw.pop("evidence", "Brain: x (2026-01-01)"),
                fix="Fix it", sources=kw.pop("sources", [{"name": "f.vtt", "link": "https://tenant.example/site/f.vtt"}]), **kw)


FINDINGS = [
    f("C01", "Incorrect", "Blocker", "Word comment", "40 million", evidence="get_metric: x=28% (2024-Q4)"),
    f("C02", "Misleading", "Major", "Word comment", "green"),
    f("C03", "No Evidence", "Minor", "log only", "about 30%"),
    f("C04", "Verified", "—", "count only", "fine"),
    f("I01", "Misleading", "Major", "Word comment", "edge A <-> B", type="figure"),
]


class TestFindingsReport(unittest.TestCase):
    def render(self, findings=FINDINGS, **kw):
        from findings_report import render_html
        return render_html(findings, document=kw.pop("document", "draft.docx"), brain_version=kw.pop("brain_version", "v1"),
                           run_date=kw.pop("run_date", "2026-09-30"), **kw)

    def test_every_claim_is_a_ledger_row(self):
        html = self.render()
        self.assertEqual(len(re.findall(r'<tr data-tags="[^"]*">', html)), len(FINDINGS))

    def test_header_carries_title_eyebrow_lede_and_meta(self):
        html = self.render(title="Platform Fact-Check", eyebrow="Commerce + Integration", lede="The draft checked claim by claim.",
                           brain_name="Example Brain", output_name="draft — fact-checked.docx")
        self.assertIn("<h1>Platform Fact-Check</h1>", html)
        self.assertIn('<span class="eyebrow">Commerce + Integration</span>', html)
        self.assertIn('<p class="lede">The draft checked claim by claim.</p>', html)
        self.assertRegex(html, r'Brain <b>Example Brain</b>')
        self.assertRegex(html, r'Knowledge <b>v1</b>')
        self.assertRegex(html, r'Output <b>draft — fact-checked\.docx</b>')

    def test_header_omits_empty_optional_fields(self):
        html = self.render()
        self.assertNotIn("<span class=\"eyebrow\">", html)
        self.assertNotIn('<p class="lede">', html)
        self.assertNotRegex(html, r'Brain <b>')
        self.assertNotRegex(html, r'Output <b>')

    def test_four_stat_tiles_with_correct_counts(self):
        html = self.render()
        self.assertRegex(html, r'data-stat="claims">5<')
        self.assertRegex(html, r'data-stat="verified"[^>]*>1<')
        self.assertRegex(html, r'data-stat="major">3<')
        self.assertRegex(html, r'data-stat="comments">3<')

    def test_major_tile_subtitle_breaks_down_blocker_and_major(self):
        html = self.render()
        self.assertIn("1 Blocker, 2 Major", html)

    def test_major_tile_carries_alert_class_only_when_nonzero(self):
        html = self.render()
        self.assertRegex(html, r'<div class="tile alert"><div class="k">blocker \+ major')
        html_none = self.render([f("V1", "Verified", "—", "count only")])
        self.assertNotRegex(html_none, r'<div class="tile alert">')

    def test_one_bar_per_verdict_including_zero_count_ones(self):
        html = self.render()
        for verdict in ("Incorrect", "Misleading", "Outdated", "Controversial", "No Evidence", "Verified"):
            self.assertEqual(len(re.findall(rf'<div class="vfill s-{verdict.lower().replace(" ", "-")}"', html)), 1)

    def test_majors_panel_lists_every_blocker_or_major_finding(self):
        html = self.render()
        self.assertEqual(len(re.findall(r'<span class="mid">', html)), 3)
        self.assertIn('<span class="mid">C01</span>', html)
        self.assertIn('<span class="mid">I01</span>', html)
        self.assertNotIn('<span class="mid">C03</span>', html)

    def test_majors_panel_shows_a_note_when_none_are_major(self):
        html = self.render([f("V1", "Verified", "—", "count only")])
        self.assertIn('<p class="note">None.</p>', html)

    def test_chips_are_data_driven_and_omit_zero_count_verdicts(self):
        html = self.render()
        for label in ("All", "All findings", "Blocker + Major", "In document", "Incorrect", "Misleading",
                      "No Evidence", "Verified", "Figure only"):
            self.assertIn(f'">{label}<', html)
        self.assertNotIn('data-f="outdated"', html)
        self.assertNotIn('data-f="controversial"', html)

    def test_figure_chip_omitted_when_no_figure_findings(self):
        html = self.render([f("C01", "Incorrect", "Major", "Word comment")])
        self.assertNotIn("Figure only", html)

    def test_rows_carry_verdict_severity_comment_and_figure_tags(self):
        html = self.render()
        c01_tags = re.search(r'<tr data-tags="([^"]*)"><td class="mono">C01<', html).group(1).split()
        self.assertEqual(set(c01_tags), {"incorrect", "finding", "major", "in-document"})
        self.assertIn("no-evidence finding", html)
        i01_tags = re.search(r'<tr data-tags="([^"]*)"><td class="mono">I01<', html).group(1).split()
        self.assertIn("figure", i01_tags)
        verified_tags = re.search(r'<tr data-tags="([^"]*)"><td class="mono">C04<', html).group(1).split()
        self.assertNotIn("finding", verified_tags)

    def test_untrusted_text_is_escaped(self):
        html = self.render([f("C01", "Incorrect", "Major", "Word comment", '<script>alert(1)</script>')])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_page_has_no_external_network_resources(self):
        html = self.render()
        self.assertNotRegex(html, r'<(script|link|img|style)[^>]+(src|href)="https?://')
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("fonts.gstatic.com", html)

    def test_page_links_only_the_cited_sources(self):
        html = self.render()
        self.assertIn('href="https://tenant.example/site/f.vtt"', html)

    def test_javascript_urls_in_sources_are_not_linked(self):
        html = self.render([f("C01", "Incorrect", "Major", "Word comment", "q",
                              sources=[{"name": "x", "link": "javascript:alert(1)"}])])
        self.assertNotIn("javascript:", html)

    def test_source_may_be_a_plain_string(self):
        html = self.render([f("C01", "Incorrect", "Major", "Word comment", "q", sources=["plain.md"])])
        self.assertIn("plain.md", html)

    def test_cli_writes_a_local_file(self):
        tmp = Path(tempfile.mkdtemp())
        src, out = tmp / "findings.json", tmp / "findings.html"
        src.write_text(json.dumps(FINDINGS))
        r = subprocess.run([sys.executable, str(SKILL_DIR / "findings_report.py"), str(src), "--out", str(out),
                            "--document", "draft.docx", "--brain-version", "v1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.read_text().startswith("<!doctype html>"))
        self.assertIn(str(out), r.stdout)


class TestSkillWritesTheReportLocally(unittest.TestCase):
    def _text(self):
        return read_text(SKILL_DIR / "SKILL.md")

    def test_findings_page_is_a_local_html_file_never_an_artifact(self):
        step9 = self._text().split("### 9.", 1)[1].split("### 10.", 1)[0]
        self.assertIn("findings_report.py", step9)
        self.assertIn("findings.html", step9)
        self.assertIn("never publish it as an Artifact", step9)
        self.assertNotIn("If an Artifact tool is available", step9)


if __name__ == "__main__":
    unittest.main()
