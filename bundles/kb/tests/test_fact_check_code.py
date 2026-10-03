"""The python block shipped in doc-fact-check/SKILL.md is executed, not just read."""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    import docx  # noqa: F401
    HAVE_DOCX = hasattr(docx.Document(), "comments")
except Exception:  # missing, or python-docx < 1.2
    HAVE_DOCX = False

from test_plugin_structure import KB_ROOT, read_text


def load_helpers():
    import importlib
    mod = importlib.import_module("annotate")  # conftest puts every skill dir on sys.path
    return {k: getattr(mod, k) for k in ("split_run", "isolate", "paragraphs", "mark_destinations", "annotate")}


def _tiny_png() -> bytes:
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = zlib.compress(b"\x00\xff\xff\xff")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


@unittest.skipUnless(HAVE_DOCX, "needs python-docx >= 1.2")
class TestReferenceCode(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.h = load_helpers()

    def _doc(self, build):
        from docx import Document
        d = Document()
        build(d)
        p = self.tmp / "in.docx"
        d.save(p)
        return p

    def _finding(self, **kw):
        base = dict(id="C01", verdict="Incorrect", severity="Major", section="1",
                    evidence="e", fix="f", source="s")
        base.update(kw)
        return base

    def test_every_table_cell_is_visited(self):
        def build(d):
            t = d.add_table(rows=12, cols=8)
            for i, row in enumerate(t.rows):
                for j, c in enumerate(row.cells):
                    c.text = f"cell-{i}-{j}"
        from docx import Document
        doc = Document(self._doc(build))
        seen = [p.text for p in self.h["paragraphs"](doc)]
        for i in range(12):
            for j in range(8):
                self.assertIn(f"cell-{i}-{j}", seen)

    def test_merged_cell_is_visited_once(self):
        def build(d):
            t = d.add_table(rows=1, cols=3)
            a = t.cell(0, 0).merge(t.cell(0, 1))
            a.text = "merged"
            t.cell(0, 2).text = "solo"
        from docx import Document
        doc = Document(self._doc(build))
        seen = [p.text for p in self.h["paragraphs"](doc)]
        self.assertEqual(seen.count("merged"), 1)
        self.assertEqual(seen.count("solo"), 1)

    def test_comment_covers_only_the_quoted_words(self):
        def build(d):
            p = d.add_paragraph("Alpha ")
            p.add_run("Revenue was 40 million in 2025")
            p.add_run(" and more.")
        src = self._doc(build)
        dst = self.tmp / "out.docx"
        self.h["annotate"](str(src), str(dst), [self._finding(quote="40 million in")])
        import re, zipfile
        xml = zipfile.ZipFile(dst).read("word/document.xml").decode()
        body = re.search(r"commentRangeStart.*?commentRangeEnd", xml, re.S).group(0)
        self.assertEqual("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", body)), "40 million in")

    def test_missing_quote_writes_no_comment(self):
        src = self._doc(lambda d: d.add_paragraph("nothing to see"))
        dst = self.tmp / "out.docx"
        self.h["annotate"](str(src), str(dst), [self._finding(quote="absent words")])
        from docx import Document
        self.assertEqual(len(list(Document(dst).comments)), 0)

    def test_rerun_does_not_duplicate_and_c1_is_not_c10(self):
        src = self._doc(lambda d: d.add_paragraph("one two three four"))
        a, b = self.tmp / "a.docx", self.tmp / "b.docx"
        f10 = self._finding(id="C10", quote="one two")
        f1 = self._finding(id="C1", quote="three four")
        self.h["annotate"](str(src), str(a), [f10])
        self.h["annotate"](str(a), str(b), [f10, f1])
        from docx import Document
        self.assertEqual(len(list(Document(b).comments)), 2)

    def test_paragraphs_come_out_in_document_order(self):
        def build(d):
            d.add_paragraph("before")
            t = d.add_table(rows=1, cols=1)
            t.cell(0, 0).text = "in-table"
            d.add_paragraph("after")
        from docx import Document
        doc = Document(self._doc(build))
        seen = [p.text for p in self.h["paragraphs"](doc) if p.text]
        self.assertEqual(seen, ["before", "in-table", "after"])

    def test_figure_number_counts_drawings_in_document_order(self):
        import io
        from docx import Document
        from docx.shared import Pt
        png = _tiny_png()
        def build(d):
            d.add_paragraph("intro")
            t = d.add_table(rows=1, cols=1)
            t.cell(0, 0).paragraphs[0].add_run().add_picture(io.BytesIO(png), width=Pt(10))   # figure 1
            d.add_paragraph().add_run().add_picture(io.BytesIO(png), width=Pt(10))            # figure 2
        src = self._doc(build)
        dst = self.tmp / "out.docx"
        self.h["annotate"](str(src), str(dst), [self._finding(id="I2", anchor="drawing", figure=2)])
        from docx import Document
        doc = Document(dst)
        anchored = [p for p in doc.element.body.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
                    if p.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}commentRangeStart")]
        self.assertEqual(len(anchored), 1)
        in_table = any(a.tag.endswith("}tc") for a in anchored[0].iterancestors())
        self.assertFalse(in_table, "figure 2 is the body drawing, not the one inside the table")

    def test_mark_destinations_sets_values_without_mutating_input(self):
        findings = [self._finding(id="C01"), self._finding(id="C02"),
                    self._finding(id="C03", verdict="Verified", severity="—")]
        before = [dict(f) for f in findings]
        out = self.h["mark_destinations"](findings, ["C01"])
        self.assertEqual([f["destination"] for f in out], ["Word comment", "log only", "count only"])
        self.assertEqual(findings, before)
        self.assertNotIn("destination", findings[0])

    def test_comment_tile_equals_comments_really_in_the_saved_docx(self):
        import re, zipfile
        from findings_report import render_html
        def build(d):
            d.add_paragraph("Alpha beta gamma delta")
            d.add_paragraph("Epsilon zeta eta theta")
        src = self._doc(build)
        dst = self.tmp / "out.docx"
        findings = [self._finding(id="C01", quote="beta gamma"), self._finding(id="C02", quote="absent words"),
                    self._finding(id="C03", quote="zeta eta"),
                    self._finding(id="C04", verdict="Verified", severity="—", quote="Alpha")]
        written, skipped = self.h["annotate"](str(src), str(dst), findings[:3])   # only approved findings are annotated
        self.assertEqual((written, skipped), (["C01", "C03"], ["C02"]))
        marked = self.h["mark_destinations"](findings, written)
        html = render_html(marked, document="in.docx", brain_version="v1", run_date="2026-01-01")
        xml = zipfile.ZipFile(dst).read("word/comments.xml").decode()
        real = len(re.findall(r"<w:comment ", xml))
        self.assertEqual(real, 2)
        self.assertRegex(html, rf'data-stat="comments">{real}<')


    # --- review9 findings 2, 6 ---
    def _add_textbox(self, doc, text):
        """Append a paragraph holding a w:txbxContent text box that contains `text`."""
        from docx.oxml import parse_xml
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        xml = (f'<w:p {W}><w:r><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml"><w:txbxContent>'
               f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:txbxContent></v:shape></w:pict></w:r></w:p>')
        body = doc.element.body
        body.insert(0, parse_xml(xml))   # text box first, so document order would pick it first

    def _comment_paragraph_texts(self, path):
        from docx import Document
        from docx.oxml.ns import qn
        doc = Document(path)
        out = []
        for s in doc.element.body.iter(qn("w:commentRangeStart")):
            p = next(a for a in s.iterancestors() if a.tag == qn("w:p"))
            out.append((p.xpath("string(.)"), any(a.tag == qn("w:txbxContent") for a in p.iterancestors())))
        return out

    def test_quote_in_body_and_textbox_anchors_in_the_body(self):
        from docx import Document
        d = Document()
        d.add_paragraph("Body says revenue was 40 million today.")
        self._add_textbox(d, "Box says revenue was 40 million today.")
        src, dst = self.tmp / "tb.docx", self.tmp / "tb-out.docx"
        d.save(src)
        written, _ = self.h["annotate"](str(src), str(dst), [self._finding(quote="40 million")])
        self.assertEqual(written, ["C01"])
        (text, in_box), = self._comment_paragraph_texts(dst)
        self.assertFalse(in_box)
        self.assertTrue(text.startswith("Body says"))

    def test_textbox_is_the_fallback_when_the_body_lacks_the_quote(self):
        from docx import Document
        d = Document()
        d.add_paragraph("Nothing relevant here.")
        self._add_textbox(d, "Only the box mentions 40 million.")
        src, dst = self.tmp / "tb2.docx", self.tmp / "tb2-out.docx"
        d.save(src)
        log = []
        written, _ = self.h["annotate"](str(src), str(dst), [self._finding(quote="40 million")])
        self.assertEqual(written, ["C01"])
        (text, in_box), = self._comment_paragraph_texts(dst)
        self.assertTrue(in_box)

    def test_textbox_fallback_is_logged_as_anchored_textbox(self):
        import contextlib, io
        from docx import Document
        d = Document()
        d.add_paragraph("Nothing relevant here.")
        self._add_textbox(d, "Only the box mentions 40 million.")
        src, dst = self.tmp / "tb3.docx", self.tmp / "tb3-out.docx"
        d.save(src)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            self.h["annotate"](str(src), str(dst), [self._finding(quote="40 million")])
        self.assertIn("anchored: textbox", buf.getvalue())

    def test_id_mentioned_in_another_comments_body_does_not_suppress_it(self):
        src = self._doc(lambda d: d.add_paragraph("one two three four"))
        a, b = self.tmp / "a6.docx", self.tmp / "b6.docx"
        f2 = self._finding(id="C02", quote="one two", fix="Align with the C01 item, see [Incorrect · Major · C01] · C01] too")
        f1 = self._finding(id="C01", quote="three four")
        self.h["annotate"](str(src), str(a), [f2])
        written, _ = self.h["annotate"](str(a), str(b), [f2, f1])
        self.assertEqual(written, ["C02", "C01"])
        from docx import Document
        self.assertEqual(len(list(Document(b).comments)), 2)


@unittest.skipUnless(HAVE_DOCX, "needs python-docx >= 1.2")
class AnnotateCli(unittest.TestCase):
    SCRIPT = KB_ROOT / "skills" / "doc-fact-check" / "annotate.py"
    BASE = dict(section="1", evidence="e (2026-01-01, f.md)", fix="x", source="f.md")

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.d = Path(self._td.name)
        from docx import Document
        doc = Document()
        doc.add_paragraph("Revenue was 40 million in 2025.")
        self.src = self.d / "draft.docx"
        doc.save(str(self.src))
        self.out = self.d / "out.docx"

    def _write(self, name, data):
        import json
        path = self.d / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def _run(self, approved, findings, out=None, draft=None, pre=()):
        import subprocess
        import sys
        return subprocess.run(
            [sys.executable, *pre, str(self.SCRIPT), str(draft or self.src), str(out or self.out),
             "--approved", str(approved), "--findings", str(findings)],
            capture_output=True, text=True)

    def _good(self):
        approved = [dict(id="C01", verdict="Incorrect", severity="Major", quote="40 million", **self.BASE)]
        findings = approved + [dict(id="C02", verdict="Verified", severity="Minor", quote="2025", **self.BASE)]
        return self._write("approved.json", approved), self._write("findings.json", findings)

    def test_cli_annotates_marks_destinations_and_reports(self):
        import json
        a, f = self._good()
        r = self._run(a, f)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"written": ["C01"], "skipped": []})
        dest = {x["id"]: x["destination"] for x in json.loads(f.read_text())}
        self.assertEqual(dest, {"C01": "Word comment", "C02": "count only"})

    def test_missing_python_docx_stops_with_the_step_0_message(self):
        a, f = self._good()
        r = self._run(a, f, pre=("-S", "-I"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("doc-fact-check needs Python with python-docx ≥ 1.2", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(self.out.exists())

    def test_malformed_json_exits_2_and_writes_nothing(self):
        a, f = self._good()
        bad = self._write("bad.json", "{not json")
        for approved, findings in ((bad, f), (a, bad)):
            r = self._run(approved, findings)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("annotate.py: ", r.stderr)
            self.assertFalse(self.out.exists())

    def test_approved_finding_without_quote_or_anchor_exits_2(self):
        import json
        _, f = self._good()
        before = f.read_text()
        a = self._write("a2.json", [dict(id="C01", verdict="Incorrect", severity="Major", **self.BASE)])
        r = self._run(a, f)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("quote", r.stderr)
        self.assertFalse(self.out.exists())
        self.assertEqual(f.read_text(), before)
        self.assertIsInstance(json.loads(before), list)

    def test_drawing_finding_without_an_integer_figure_exits_2(self):
        _, f = self._good()
        before = f.read_text()
        drawing = dict(id="I01", verdict="Incorrect", severity="Major", anchor="drawing", **self.BASE)
        for extra in ({}, {"figure": 0}, {"figure": "1"}, {"figure": True}, {"figure": 1.0}):
            with self.subTest(extra=extra):
                a = self._write("a_fig.json", [{**drawing, **extra}])
                r = self._run(a, f)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn(f'annotate.py: {a}: I01: anchor "drawing" needs an integer figure >= 1', r.stderr)
                self.assertFalse(self.out.exists())
                self.assertEqual(f.read_text(), before)

    def _words_finding(self, total):
        """An approved finding whose written comment is exactly `total` words (11 are header, labels, fix and source)."""
        base = {**self.BASE, "evidence": " ".join(["w"] * (total - 11))}
        return dict(id="C12", verdict="Incorrect", severity="Major", quote="40 million", **base)

    def test_comment_header_names_only_the_last_heading(self):
        # A full heading path spent ~20 of the 60 words, so annotate refused and the main session rewrote approved.json.
        from annotate import comment_text
        long = "Platform - Baseline (excerpt for tests) > 5.7 Other sources and external parties"
        text = comment_text(dict(id="C16", verdict="Misleading", severity="Minor", **{**self.BASE, "section": long}))
        self.assertTrue(text.startswith("[Misleading · Minor · C16] §5.7 Other sources and external parties\n"), text)
        self.assertNotIn("Baseline", text)

    def test_comment_over_the_word_limit_exits_2_naming_id_and_count_and_writes_nothing(self):
        _, f = self._good()
        before = f.read_text()
        a = self._write("a_long.json", [self._words_finding(70)])
        r = self._run(a, f)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(f"annotate.py: {a}: C12: comment would be 70 words (limit 60); shorten its evidence or fix", r.stderr)
        self.assertFalse(self.out.exists())
        self.assertEqual(f.read_text(), before)

    def test_comment_of_exactly_the_word_limit_is_written(self):
        import json
        _, f = self._good()
        a = self._write("a_exact.json", [self._words_finding(60)])
        r = self._run(a, f)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"written": ["C12"], "skipped": []})
        self.assertTrue(self.out.exists())

    def test_skill_md_tells_the_agent_to_write_evidence_and_fix_for_the_comment(self):
        skill = (KB_ROOT / "skills" / "doc-fact-check" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Each row's `evidence` and `fix` are written for the comment, so that the whole comment, "
                      "header and Source line included, is at most 60 words; `annotate.py` refuses a longer one and names it.", skill)

    def test_approved_finding_missing_a_comment_key_exits_2_naming_it(self):
        _, f = self._good()
        good = dict(id="C01", verdict="Incorrect", severity="Major", quote="40 million", **self.BASE)
        for key in ("verdict", "severity", "section", "evidence", "fix", "source"):
            for bad in (None, "", " ") + ((3,) if key != "section" else ()):   # section: any non-empty value
                with self.subTest(key=key, bad=bad):
                    row = {k: v for k, v in good.items() if k != key}
                    if bad is not None:
                        row[key] = bad
                    a = self._write("a_key.json", [row])
                    r = self._run(a, f)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn(f"annotate.py: {a}: C01: ", r.stderr)
                    self.assertIn(f"`{key}`", r.stderr)
                    self.assertFalse(self.out.exists())

    def test_section_may_be_any_non_empty_value(self):
        _, f = self._good()
        a = self._write("a_sec.json", [dict(id="C01", verdict="Incorrect", severity="Major", quote="40 million",
                                            **{**self.BASE, "section": 4})])
        r = self._run(a, f)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_list_or_idless_input_exits_2(self):
        _, f = self._good()
        for data in ({"findings": []}, [{"quote": "x"}], ["C01"]):
            r = self._run(self._write("a3.json", data), f)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertFalse(self.out.exists())

    def test_refuses_to_overwrite_the_draft(self):
        a, f = self._good()
        before = self.src.read_bytes()
        r = self._run(a, f, out=self.src)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.src.read_bytes(), before)

    def test_missing_input_file_exits_2(self):
        a, f = self._good()
        r = self._run(a, f, draft=self.d / "nope.docx")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(self.out.exists())

    def test_skill_md_ships_no_python_block_and_documents_the_command(self):
        skill = (KB_ROOT / "skills" / "doc-fact-check" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("```python", skill)
        self.assertIn('python "<skill dir>/annotate.py" "<draft dir>/<name>.docx" "<draft dir>/<name> \u2014 fact-checked.docx" '
                      '--approved "<work dir>/approved.json" --findings "<run dir>/findings.json"', skill)
        self.assertIn("a JSON array of the approved finding objects, with the keys listed above", skill)


if __name__ == "__main__":
    unittest.main()
