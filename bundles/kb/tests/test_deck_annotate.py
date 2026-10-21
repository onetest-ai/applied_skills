"""deck-fact-check annotator and deck invariants (spec §4.2, §4.4, §7 L2): native legacy comments pinned to the
claim's shape, and a checker that is shown to fail on every defect it exists to catch."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_plugin_structure import KB_ROOT

DECK = KB_ROOT / "skills" / "deck-fact-check"
sys.path.insert(0, str(DECK))

try:
    import pptx  # noqa: F401
    HAVE_PPTX = True
except ImportError:
    HAVE_PPTX = False

if HAVE_PPTX:
    import annotate_cli
    import deck_fixtures as F
    import fact_check_invariants as I
    import pptx_annotate as A
    from render_pages import soffice_bin
    HAVE_SOFFICE = soffice_bin() is not None
else:
    HAVE_SOFFICE = False


def finding(fid, p_id, section, quote, verdict, severity, **kw):
    f = {"id": fid, "p_id": p_id, "section": section, "quote": quote, "claim": quote, "type": "NUM",
         "verdict": verdict, "severity": severity, "confidence": "High",
         "evidence": "The ops report states 99.1% for FY2025.", "trail": "t", "conflicts": [],
         "fix": "Use 99.1%.", "why": "the ops report states 99.1%", "suggested": "Uptime was 99.1% in FY2025.",
         "source": "ops-report.md", "sources": []}
    f.update(kw)
    return f


S1, S2 = "Slide 1: Delivery plan", "Slide 2: North region — FY2025"
FINDINGS = [
    finding("C01", "s1-sh3-p2", S1, "Uptime held at 99.9% all year.", "Incorrect", "Major"),
    finding("C02", "s1-sh3-p1", S1, "Go-live is set for Q2 2027.", "Misleading", "Minor"),
    finding("C03", "s2-sh3-r2", S2, "Uptime | 99.9%", "Incorrect", "Blocker",
            evidence='The source says "R&D <core> uptime" was 99.1% — São Paulo region.'),
    finding("C04", "s1-sh3-p3", S1, "The integration team owns cut-over.", "Verified", "Minor"),
    finding("C05", "s1-sh3-p1", S1, "Go-live is set for Q2 2027.", "No Evidence", "Minor"),
    finding("I01", "s3", "Figure", "slide 3 picture", "Outdated", "Major", anchor="drawing", figure=3,
            conflicts=[{"span": "x", "source": "y", "date": "2025"}]),
]
COVERAGE = [{"heading": "Slide 3", "reason": "no checkable statement"}]


@unittest.skipUnless(HAVE_PPTX, "needs python-pptx")
class Annotate(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.src = F.full_deck(self.d)
        self.sha = hashlib.sha256(self.src.read_bytes()).hexdigest()
        self.out = self.d / "full — fact-checked.pptx"

    def select(self, policy="major"):
        return [f for f in FINDINGS if I.wants_comment(f, policy)]

    def run_annotate(self, policy="major"):
        written, skipped = A.annotate(self.src, self.out, self.select(policy))
        return written, skipped, A.annotate_cli.mark_destinations(FINDINGS, written, label="Deck comment")

    def test_a2_comment_set_equals_the_policy(self):
        for policy, want in (("major", {"C01", "C03", "I01"}), ("all", {"C01", "C02", "C03", "I01"})):
            with self.subTest(policy=policy):
                written, skipped, _ = self.run_annotate(policy)
                self.assertEqual((set(written), skipped), (want, []))
                got = {I.comment_owner(c["text"], FINDINGS) for c in I.read_pptx_comments(self.out)}
                self.assertEqual(got, want)

    def test_a3_pins_sit_on_the_claims_slide_inside_its_shape(self):
        self.run_annotate()
        boxes = I.pptx_boxes(self.src)
        by_id = {I.comment_owner(c["text"], FINDINGS): c for c in I.read_pptx_comments(self.out)}
        self.assertEqual((by_id["C01"]["slide"], by_id["C03"]["slide"], by_id["I01"]["slide"]), (1, 2, 3))
        for fid, key in (("C01", (1, 3)), ("C03", (2, 3))):
            box, c = boxes[key], by_id[fid]
            with self.subTest(fid=fid):
                self.assertEqual((c["x"], c["y"]), (A.to_pos(box["x"]), A.to_pos(box["y"])))
                self.assertLessEqual(box["x"], c["x"] * I.EMU_PER_POS + I.EMU_PER_POS)
        self.assertEqual(A.to_pos(914400), 576)                       # the spike: 1 in = 576 units

    def test_a4_text_is_the_shared_comment_and_escapes_round_trip(self):
        A.annotate(self.src, self.out, self.select(), fields=I.COMMENT_FIELDS)   # Brain line on: it holds the escapes
        by_id = {I.comment_owner(c["text"], FINDINGS): c["text"] for c in I.read_pptx_comments(self.out)}
        c03 = next(f for f in FINDINGS if f["id"] == "C03")
        self.assertEqual(by_id["C03"], I.comment_text(c03, I.COMMENT_FIELDS))
        self.assertIn('"R&D <core> uptime"', by_id["C03"])
        self.assertIn("São Paulo", by_id["C03"])
        self.assertTrue(by_id["C01"].startswith('[Incorrect]\n"Uptime held at 99.9% all year."\n'))

    def test_a5_rerun_on_its_own_output_adds_nothing(self):
        self.run_annotate()
        again = self.d / "again.pptx"
        written, skipped = A.annotate(self.out, again, self.select())
        self.assertEqual((set(written), skipped), ({"C01", "C03", "I01"}, []))
        self.assertEqual(len(I.read_pptx_comments(again)), 3)
        self.assertEqual(again.read_bytes(), self.out.read_bytes())

    def test_a6_original_is_untouched(self):
        self.run_annotate()
        self.assertEqual(hashlib.sha256(self.src.read_bytes()).hexdigest(), self.sha)
        self.assertEqual(I.read_pptx_comments(self.src), [])

    def test_a7_a_reviewers_comment_is_kept(self):
        reviewed = self.d / "reviewed.pptx"
        with patch.object(A, "AUTHOR", "Reviewer"), patch.object(A, "comment_text", lambda f, fields=None: "Please check the dates."):
            A.annotate(self.src, reviewed, [finding("X1", "s1-sh3-p1", S1, "q", "Incorrect", "Major")])
        A.annotate(reviewed, self.out, self.select())
        comments = I.read_pptx_comments(self.out)
        self.assertIn("Please check the dates.", [c["text"] for c in comments])
        self.assertEqual(len(comments), 4)
        with zipfile.ZipFile(self.out) as z:
            authors = z.read("ppt/commentAuthors.xml").decode()
            self.assertEqual(len(re.findall(r"<p:cmAuthor\b", authors)), 2)
            self.assertRegex(authors, r'name="Fact Checker · Brain"[^>]*lastIdx="3"')
            slide1 = [n for n in z.namelist() if n.startswith("ppt/comments/")]
            self.assertEqual(len(slide1), 3)              # slide 1 reuses the reviewer's part; slides 2, 3 get one
        ours = [c for c in comments if c["text"] != "Please check the dates."]
        self.assertEqual(sorted(int(c["idx"]) for c in ours), [1, 2, 3])

    def test_a_shape_that_is_not_in_the_deck_is_skipped(self):
        written, skipped = A.annotate(self.src, self.out, [finding("C09", "s1-sh99-p1", S1, "q", "Incorrect", "Major")])
        self.assertEqual((written, skipped), ([], ["C09"]))

    def test_empty_and_prefixed_relationship_parts_are_supported(self):
        from lxml import etree
        for xml in (f'<Relationships xmlns="{I.PKG_REL_NS}"/>',
                    f'<r:Relationships xmlns:r="{I.PKG_REL_NS}"/>'):
            root = etree.fromstring(A._add_rel(xml, I.COMMENTS_REL, "../comments/comment1.xml").encode())
            rel = root.find(f"{{{I.PKG_REL_NS}}}Relationship")
            self.assertIsNotNone(rel)
            self.assertEqual(rel.get("Target"), "../comments/comment1.xml")

    def test_prefixed_existing_comments_and_authors_are_extended(self):
        partial = self.d / "partial.pptx"
        A.annotate(self.src, partial, self.select()[:1])
        rewritten = self.d / "prefixed.pptx"
        with zipfile.ZipFile(partial) as zin, zipfile.ZipFile(rewritten, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.startswith("ppt/comments/") or info.filename == "ppt/commentAuthors.xml":
                    data = data.replace(b"xmlns:p=", b"xmlns:x=").replace(b"<p:", b"<x:").replace(b"</p:", b"</x:")
                zout.writestr(info, data)
        A.annotate(rewritten, self.out, self.select())
        self.assertEqual(len(I.read_pptx_comments(self.out)), 3)
        self.assertEqual(len({c["text"] for c in I.read_pptx_comments(self.out)}), 3)

    def test_package_parts_are_wired(self):
        self.run_annotate()
        with zipfile.ZipFile(self.out) as z:
            names = set(z.namelist())
            types = z.read("[Content_Types].xml").decode()
            pres = z.read("ppt/_rels/presentation.xml.rels").decode()
            self.assertIn("ppt/commentAuthors.xml", names)
            self.assertIn(A.CT_AUTHORS, types)
            self.assertIn(I.AUTHORS_REL, pres)
            for part in (n for n in names if n.startswith("ppt/comments/")):
                self.assertIn(f'PartName="/{part}"', types)
        from pptx import Presentation
        Presentation(str(self.out))                       # python-pptx still opens it

    @unittest.skipUnless(HAVE_SOFFICE, "needs LibreOffice")
    def test_a1_libreoffice_opens_the_copy_and_reads_every_comment(self):
        self.run_annotate()
        out = self.d / "fodp"
        subprocess.run([soffice_bin(), "--headless", "--convert-to", "fodp", "--outdir", str(out), str(self.out)],
                       check=True, capture_output=True, timeout=180)
        flat = next(out.glob("*.fodp")).read_text(encoding="utf-8")
        self.assertEqual(flat.count("<officeooo:annotation "), 3)
        for fid in ("C01", "C03", "I01"):
            self.assertIn(next(f["quote"] for f in FINDINGS if f["id"] == fid), flat)

    def test_cli_select_writes_and_marks_deck_comments(self):
        fj = self.d / "findings.json"
        fj.write_text(json.dumps(FINDINGS))
        r = subprocess.run([sys.executable, "-I", str(DECK / "pptx_annotate.py"), str(self.src), str(self.out),
                            "--select", "major", "--findings", str(fj)], capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(set(json.loads(r.stdout)["written"]), {"C01", "C03", "I01"})
        dest = {f["id"]: f["destination"] for f in json.loads(fj.read_text())}
        self.assertEqual(dest, {"C01": "Deck comment", "C02": "log only", "C03": "Deck comment", "C04": "count only",
                                "C05": "log only", "I01": "Deck comment"})

    def test_cli_refuses_to_overwrite_the_draft(self):
        fj = self.d / "findings.json"
        fj.write_text(json.dumps(FINDINGS))
        r = subprocess.run([sys.executable, str(DECK / "pptx_annotate.py"), str(self.src), str(self.src),
                            "--select", "major", "--findings", str(fj)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("pptx_annotate.py: refusing to overwrite the original draft", r.stderr)


@unittest.skipUnless(HAVE_PPTX, "needs python-pptx")
class DeckInvariants(unittest.TestCase):
    """A8 and the mutations: check_run is clean on a good deck and names each injected defect."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.src = F.full_deck(self.d)
        self.sha = hashlib.sha256(self.src.read_bytes()).hexdigest()
        self.out = self.d / "full — fact-checked.pptx"
        written, _ = A.annotate(self.src, self.out, [f for f in FINDINGS if I.wants_comment(f, "major")])
        self.findings = annotate_cli.mark_destinations(FINDINGS, written, label="Deck comment")

    def check(self, findings=None, out=None, coverage=COVERAGE, original=None):
        return I.check_run(findings or self.findings, out or self.out, original_sha256=self.sha,
                           original_path=original or self.src, coverage=coverage, run={"comments": "major"})

    def mutate(self, part_pred, fn):
        bad = self.d / "bad.pptx"
        with zipfile.ZipFile(self.out) as zin, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as zout:
            for i in zin.infolist():
                data = zin.read(i.filename)
                if part_pred(i.filename):
                    data = fn(data.decode("utf-8")).encode("utf-8")
                zout.writestr(i, data)
        return bad

    def comments_part(self, slide):
        with zipfile.ZipFile(self.out) as z:
            return next(t for typ, t in I._rels(z, I.pptx_slide_parts(self.out)[slide - 1]).values()
                        if typ == I.COMMENTS_REL)

    def assert_flags(self, violations, needle):
        self.assertTrue(any(needle in v for v in violations), violations)

    def test_a8_clean(self):
        self.assertEqual(self.check(), [])

    def test_headings_are_the_readers_section_names(self):
        self.assertEqual(I.doc_view(self.src).headings(), [S1, S2, "Slide 3"])
        self.assertTrue(I.doc_view(self.src).has_images())

    def test_m1_dropped_required_comment(self):
        part = self.comments_part(1)
        bad = self.mutate(lambda n: n == part, lambda x: re.sub(r"<p:cm [^>]*>(?:(?!</p:cm>).)*Uptime held at 99.9% all year..*?</p:cm>", "", x, flags=re.S))
        self.assert_flags(self.check(out=bad), "C01: approved comment is missing from the document")

    def test_m2_unapproved_comment(self):
        part = self.comments_part(1)
        extra = '<p:cm authorId="0" dt="2026-01-01T00:00:00.000" idx="9"><p:pos x="10" y="10"/><p:text>[Incorrect]\nUnapproved statement</p:text></p:cm>'
        bad = self.mutate(lambda n: n == part, lambda x: x.replace("</p:cmLst>", extra + "</p:cmLst>"))
        self.assert_flags(self.check(out=bad), "comment in the document was never approved")

    def test_m3_pin_on_another_slide(self):
        p1, p2 = self.comments_part(1), self.comments_part(2)
        with zipfile.ZipFile(self.out) as z:
            c01 = re.search(r"<p:cm [^>]*>(?:(?!</p:cm>).)*Uptime held at 99.9% all year..*?</p:cm>", z.read(p1).decode(), re.S).group(0)
        step1 = self.mutate(lambda n: n == p1, lambda x: x.replace(c01, ""))
        shutil.copyfile(step1, self.d / "step1.pptx")
        self.out = self.d / "step1.pptx"
        bad = self.mutate(lambda n: n == p2, lambda x: x.replace("</p:cmLst>", c01 + "</p:cmLst>"))
        self.assert_flags(self.check(out=bad), "C01: comment is on slide 2, its claim is on slide 1")

    def test_m4_pin_outside_its_shape(self):
        part = self.comments_part(2)
        bad = self.mutate(lambda n: n == part, lambda x: re.sub(r'<p:pos x="\d+" y="\d+"/>', '<p:pos x="5" y="5"/>', x))
        self.assert_flags(self.check(out=bad), "C03: comment pin is outside the box of the shape")

    def test_m5_word_cap(self):
        part = self.comments_part(1)
        bad = self.mutate(lambda n: n == part, lambda x: x.replace('Suggested: "Uptime was 99.1% in FY2025."', "Suggested: " + "word " * 100))
        self.assert_flags(self.check(out=bad), "comment is over 90 words")

    def test_m6_header_verdict_changed(self):
        part = self.comments_part(1)
        bad = self.mutate(lambda n: n == part, lambda x: x.replace("[Incorrect]", "[Misleading]"))
        self.assert_flags(self.check(out=bad), "C01: comment category does not match the finding")

    def test_m7_comment_on_a_verified_claim(self):
        fs = [dict(f) for f in self.findings]
        c04 = next(f for f in fs if f["id"] == "C04")
        c04["destination"] = "Deck comment"
        part = self.comments_part(1)
        extra = ('<p:cm authorId="0" dt="2026-01-01T00:00:00.000" idx="9"><p:pos x="288" y="1008"/>'
                 '<p:text>[Verified · Minor · C04] §Delivery plan</p:text></p:cm>')
        bad = self.mutate(lambda n: n == part, lambda x: x.replace("</p:cmLst>", extra + "</p:cmLst>"))
        self.assert_flags(self.check(findings=fs, out=bad), "C04: Verified claim must not carry a comment")

    def test_m8_output_overwrites_the_original(self):
        self.assert_flags(self.check(original=self.out), "output would overwrite the original document")

    def test_m9_uncovered_slide(self):
        self.assert_flags(self.check(coverage=[]), "heading not covered: Slide 3")

    def test_slide_coverage_does_not_require_a_title_removed_by_the_visible_reader(self):
        findings = [{**f, "section": f["section"].split(":", 1)[0]} for f in self.findings]
        problems = self.check(findings=findings)
        self.assertFalse(any("heading not covered" in p for p in problems), problems)

    def test_unknown_suffix_has_no_view(self):
        with self.assertRaises(ValueError):
            I.doc_view(self.d / "draft.pdf")


if __name__ == "__main__":
    unittest.main()
