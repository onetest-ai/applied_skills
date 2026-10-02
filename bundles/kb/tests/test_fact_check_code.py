"""The python block shipped in fact-check/SKILL.md is executed, not just read."""
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
    text = read_text(KB_ROOT / "skills" / "fact-check" / "SKILL.md")
    code = text.split("```python", 1)[1].split("```", 1)[0]
    ns: dict = {}
    exec(compile(code, "SKILL.md:python", "exec"), ns)
    return ns


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


if __name__ == "__main__":
    unittest.main()
