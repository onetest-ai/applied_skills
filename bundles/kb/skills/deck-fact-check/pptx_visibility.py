"""Locate fully painted text in the rendered slide, rather than trusting its PDF text layer."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata


def normalized(text: str) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", text).casefold())


def _claim_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub("[‐‑‒–—−]", "-", text)
    return re.sub(r"[^\w%$€£¥=<>+./,:-]+", "", text)


@dataclass(frozen=True)
class Glyph:
    text: str
    x: float
    y: float
    visible: bool


def _painted_glyphs(page):
    import pymupdf
    matrix = pymupdf.Matrix(2, 2)
    original = page.get_pixmap(matrix=matrix, alpha=False)
    with pymupdf.open() as background, pymupdf.open() as text_only:
        for clone in (background, text_only):
            clone.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
            clone[0].add_redact_annot(clone[0].rect, fill=False, cross_out=False)
        background[0].apply_redactions(images=0, graphics=0, text=0)
        behind = background[0].get_pixmap(matrix=matrix, alpha=False)
        background[0].add_redact_annot(background[0].rect, fill=False, cross_out=False)
        background[0].apply_redactions(images=1, graphics=2, text=0)
        empty = background[0].get_pixmap(matrix=matrix, alpha=True)
        text_only[0].apply_redactions(images=1, graphics=2, text=1)
        ink = text_only[0].get_pixmap(matrix=matrix, alpha=True)
    actual, under, mask, blank = original.samples, behind.samples, ink.samples, empty.samples

    def visible(box):
        expected = painted = 0
        for y in range(max(0, math.floor(box[1] * 2)), min(original.height, math.ceil(box[3] * 2))):
            for x in range(max(0, math.floor(box[0] * 2)), min(original.width, math.ceil(box[2] * 2))):
                mi = y * ink.stride + x * 4
                if max(abs(mask[mi + c] - blank[mi + c]) for c in range(4)) < 16:
                    continue
                expected += 1
                oi, bi = y * original.stride + x * 3, y * behind.stride + x * 3
                painted += max(abs(actual[oi + c] - under[bi + c]) for c in range(3)) >= 2
        return expected > 0 and painted / expected >= 0.95

    return visible


class RenderedText:
    def __init__(self, page):
        import pymupdf
        is_painted = _painted_glyphs(page)
        self.glyphs: list[Glyph] = []
        painted = []
        bounds = page.rect + (-0.1, -0.1, 0.1, 0.1)
        for span in page.get_texttrace():
            span_visible = span["type"] in (0, 1) and span["opacity"] > 0
            text = []
            previous_box = None
            for code, glyph_id, origin, box in span["chars"]:
                char = chr(code)
                if glyph_id == -1 and previous_box is not None:
                    box = previous_box
                else:
                    previous_box = box
                rect = pymupdf.Rect(box)
                visible = span_visible and bounds.contains(rect) and (char.isspace() or is_painted(box))
                text.append(char if visible else " ")
                self.glyphs.extend(Glyph(c, box[0], box[1], visible) for c in _claim_text(char))
            painted.append("".join(text))
        self.text = "\n".join(painted)
        self.search_text = "".join(g.text for g in self.glyphs)
        self.width = page.rect.width

    def _matches(self, text: str) -> list[list[Glyph]]:
        needle = _claim_text(text)
        if not needle:
            return []
        found, offset = [], 0
        while (start := self.search_text.find(needle, offset)) >= 0:
            offset = start + 1
            glyphs = self.glyphs[start:start + len(needle)]
            found.append(glyphs)
        return found

    def locations(self, text: str, scale: float = 1) -> list[tuple[float, float]]:
        return [(glyphs[0].x * scale, glyphs[0].y * scale) for glyphs in self._matches(text)
                if all(g.visible for g in glyphs)]

    def contains(self, text: str, pos: dict, slide_width: int, preceding: str = "") -> bool:
        scale = slide_width / self.width

        def anchored(glyphs):
            x, y = glyphs[0].x * scale, glyphs[0].y * scale
            return (pos["x"] - 91440 <= x <= pos["x"] + pos["w"] + 91440
                    and pos["y"] - 91440 <= y <= pos["y"] + pos["h"] + 91440)

        candidates = [glyphs for glyphs in self._matches(text) if anchored(glyphs)]
        if candidates:
            return any(all(g.visible for g in glyphs) for glyphs in candidates)
        if preceding:
            length = len(_claim_text(text))
            return any(anchored(glyphs) and all(g.visible for g in glyphs[-length:])
                       for glyphs in self._matches(preceding + text))
        return False
