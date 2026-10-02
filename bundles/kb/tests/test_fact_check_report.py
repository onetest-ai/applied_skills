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

    CHIP_IDS = ["f-all", "f-finding", "f-major", "f-incorrect", "f-misleading", "f-outdated",
                "f-controversial", "f-noev", "f-verified"]

    def test_chip_ids_in_reference_order(self):
        html = self.render()
        bar = re.search(r'<div class="chips" role="group" aria-label="Filter claims">(.*?)<span class="shown" id="shown"></span>',
                        html, re.S).group(1)
        self.assertEqual(re.findall(r'<button class="chip" id="([^"]+)"', bar), self.CHIP_IDS)
        self.assertEqual(re.findall(r'data-f="([^"]+)"', bar),
                         ["all", "finding", "major", "incorrect", "misleading", "outdated",
                          "controversial", "no-evidence", "verified"])
        self.assertEqual(re.findall(r'aria-pressed="([^"]+)">([^<]+)</button>', bar),
                         [("true", "All"), ("false", "All findings"), ("false", "Blocker + Major"),
                          ("false", "Incorrect"), ("false", "Misleading"), ("false", "Outdated"),
                          ("false", "Controversial"), ("false", "No Evidence"), ("false", "Verified")])

    def test_zero_count_chips_still_present_and_script_disables_them(self):
        html = self.render([f("V1", "Verified", "—", "count only")])
        for cid in self.CHIP_IDS:
            self.assertIn(f'id="{cid}"', html)
        self.assertIn("disabled", html.split("<script>")[1])

    def test_no_figure_only_or_in_document_chip(self):
        html = self.render()
        self.assertNotIn("Figure only", html)
        self.assertNotIn('data-f="figure"', html)
        self.assertNotIn('data-f="in-document"', html)

    def test_ledger_headers_exact(self):
        html = self.render()
        self.assertEqual(re.findall(r"<th>(.*?)</th>", html),
                         ["ID", "Where", "Claim", "Verdict", "Severity", "Conf.", "Brain evidence (dated)", "Source", "Action"])

    def test_section_headings(self):
        html = self.render()
        self.assertEqual(re.findall(r"<h2>(.*?)</h2>", html), ["Verdicts", "Blocker and Major findings", "Claim ledger"])
        self.assertEqual(re.findall(r"<h1>(.*?)</h1>", self.render(title="My Title")), ["My Title"])

    def _tags(self, html, id):
        return set(re.search(rf'<tr data-tags="([^"]*)"><td class="mono">{id}<', html).group(1).split())

    def test_row_tags_for_each_verdict(self):
        cases = [("Incorrect", "Blocker", "Word comment", {"incorrect", "finding", "major", "comment"}),
                 ("Misleading", "Major", "log only", {"misleading", "finding", "major"}),
                 ("Outdated", "Minor", "Word comment", {"outdated", "finding", "comment"}),
                 ("Controversial", "Minor", "log only", {"controversial", "finding"}),
                 ("No Evidence", "Minor", "log only", {"no-evidence", "finding"}),
                 ("Verified", "—", "count only", {"verified"})]
        for i, (v, sev, dest, expected) in enumerate(cases):
            html = self.render([f(f"C{i:02d}", v, sev, dest)])
            self.assertEqual(self._tags(html, f"C{i:02d}"), expected, v)

    def test_rows_carry_verdict_severity_comment_and_figure_tags(self):
        html = self.render()
        self.assertEqual(self._tags(html, "C01"), {"incorrect", "finding", "major", "comment"})
        self.assertIn("no-evidence finding", html)
        self.assertIn("figure", self._tags(html, "I01"))
        self.assertNotIn("finding", self._tags(html, "C04"))

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

    def test_missing_destination_everywhere_shows_na_not_zero(self):
        rows = [{k: v for k, v in r.items() if k != "destination"} for r in FINDINGS]
        html = self.render(rows)
        self.assertRegex(html, r'data-stat="comments">n/a<')
        self.assertIn("destination missing in findings.json", html)

    def test_cli_warns_on_stderr_when_destination_is_missing(self):
        tmp = Path(tempfile.mkdtemp())
        src, out = tmp / "findings.json", tmp / "findings.html"
        src.write_text(json.dumps([{k: v for k, v in r.items() if k != "destination"} for r in FINDINGS]))
        r = subprocess.run([sys.executable, str(SKILL_DIR / "findings_report.py"), str(src), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("destination", r.stderr)
        self.assertRegex(out.read_text(), r'data-stat="comments">n/a<')

    def test_destination_present_keeps_numeric_count_and_no_warning(self):
        tmp = Path(tempfile.mkdtemp())
        src, out = tmp / "findings.json", tmp / "findings.html"
        src.write_text(json.dumps(FINDINGS))
        r = subprocess.run([sys.executable, str(SKILL_DIR / "findings_report.py"), str(src), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.stderr, "")
        self.assertRegex(out.read_text(), r'data-stat="comments">3<')
        self.assertNotIn("n/a", self.render())

    def test_zero_comments_with_destination_key_is_still_a_number(self):
        html = self.render([f("C03", "No Evidence", "Minor", "log only")])
        self.assertRegex(html, r'data-stat="comments">0<')

    def test_source_folder_sits_on_its_own_line(self):
        from findings_report import CSS
        self.assertRegex(CSS, r'\.cap\{[^}]*display:block')


class TestReview9Report(unittest.TestCase):
    def test_footer_does_not_say_newer_sources_win(self):
        from findings_report import render_html
        html = render_html([f("C01", "Incorrect", "Major", "Word comment", "x")])
        self.assertNotIn("Newer sources win", html)
        self.assertIn("Disagreements between sources are reported with both values and both citations, "
                      "never resolved silently.", html)

    def test_unrecognised_findings_object_exits_cleanly(self):
        for payload in ({}, {"other": 1}):
            with tempfile.TemporaryDirectory() as t:
                src = Path(t) / "findings.json"
                src.write_text(json.dumps(payload))
                r = subprocess.run([sys.executable, str(SKILL_DIR / "findings_report.py"), str(src),
                                    "--out", str(Path(t) / "o.html")], capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn('findings.json: expected a list, or an object with "findings" or "claims"', r.stderr)
                self.assertNotIn("Traceback", r.stderr)


class TestSkillWritesTheReportLocally(unittest.TestCase):
    def _text(self):
        return read_text(SKILL_DIR / "SKILL.md")

    def test_findings_page_is_a_local_html_file_never_an_artifact(self):
        step9 = self._text().split("### 9.", 1)[1].split("### 10.", 1)[0]
        self.assertIn("findings_report.py", step9)
        self.assertIn("findings.html", step9)
        self.assertIn("never publish it as an Artifact", step9)
        self.assertNotIn("If an Artifact tool is available", step9)

    def test_step9_defines_the_findings_json_schema(self):
        step9 = self._text().split("### 9.", 1)[1].split("### 10.", 1)[0]
        for key in ("id", "p_id", "section", "quote", "type", "verdict", "severity", "confidence", "evidence",
                    "fix", "source", "sources", "destination"):
            self.assertIn(f"`{key}`", step9, key)
        for value in ("Word comment", "log only", "count only"):
            self.assertIn(f'"{value}"', step9, value)
        self.assertIn("after step 8", step9)



class CoverageLineTests(unittest.TestCase):
    FAST = {"mode": "fast", "scope": "risk", "sections_total": 9, "claims_extracted": 40, "claims_verified": 12}
    DEEP = {"mode": "deep", "scope": "all", "sections_total": 9, "claims_extracted": 40, "claims_verified": 40}

    def test_fast_line(self):
        from findings_report import coverage_line
        self.assertEqual(coverage_line(self.FAST),
                         "Fast scan — 12 of 40 claims checked (high-risk only). Run a Deep check before sign-off.")

    def test_deep_line(self):
        from findings_report import coverage_line
        self.assertEqual(coverage_line(self.DEEP), "Deep check — all 9 sections checked; 40 claims verified.")

    def test_zero_risk_fast_line(self):
        from findings_report import coverage_line
        self.assertEqual(coverage_line(dict(self.FAST, claims_verified=0)),
                         "Fast scan — 0 of 40 claims checked (high-risk only). Run a Deep check before sign-off.")

    def test_page_banner_equals_cli_line(self):
        from findings_report import coverage_line, render_html
        html = render_html(FINDINGS, document="x.docx", brain_version="v1", run_date="2026-01-01", run=self.FAST)
        self.assertIn(f'<p class="coverage" data-mode="fast">{coverage_line(self.FAST)}</p>', html)
        d = Path(tempfile.mkdtemp()); (d / "run.json").write_text(json.dumps(self.FAST))
        r = subprocess.run([sys.executable, str(SKILL_DIR / "findings_report.py"), "--coverage-line", str(d / "run.json")],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, coverage_line(self.FAST)))

    def test_no_run_means_no_banner(self):
        from findings_report import render_html
        html = render_html(FINDINGS, document="x.docx", brain_version="v1", run_date="2026-01-01")
        self.assertNotIn('class="coverage"', html)

    def test_cli_run_flag_renders_banner(self):
        from findings_report import coverage_line
        d = Path(tempfile.mkdtemp())
        (d / "run.json").write_text(json.dumps(self.DEEP)); (d / "f.json").write_text(json.dumps(FINDINGS))
        r = subprocess.run([sys.executable, str(SKILL_DIR / "findings_report.py"), str(d / "f.json"),
                            "--out", str(d / "o.html"), "--run", str(d / "run.json")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(coverage_line(self.DEEP), (d / "o.html").read_text(encoding="utf-8"))

    def test_findings_required_without_coverage_line(self):
        r = subprocess.run([sys.executable, str(SKILL_DIR / "findings_report.py"), "--out", "x.html"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
