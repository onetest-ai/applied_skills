"""PyMuPDF 2.0 (June 2026 major) pulls in pymupdf-layout, pymupdf4llm and onnxruntime, and in Cowork's Linux sandbox the deck reader died with a
segmentation fault (exit 139) after the skill's unpinned `pip install python-pptx pymupdf`; the run only finished once the model installed
`pymupdf<2`. The deck code uses pymupdf.open, Matrix and VersionBind only, which 1.x has (the deck and fact-check tests pass on 1.28)."""
import re
from pathlib import Path

KB = Path(__file__).resolve().parents[1]


def install_lines():
    text = (KB / "skills" / "deck-fact-check" / "SKILL.md").read_text(encoding="utf-8")
    return re.findall(r"pip install [^`)]*pymupdf[^`)]*", text)


def test_the_deck_skill_installs_a_pinned_pymupdf():
    lines = install_lines()
    assert lines, "the deck skill must say how to install its dependencies"
    for l in lines:
        assert '"pymupdf<2"' in l, f"unpinned PyMuPDF install: {l!r}"


def test_no_other_doc_tells_the_user_to_install_an_unpinned_pymupdf():
    for p in [*(KB / "skills").rglob("*.md"), KB / "README.md"]:
        if p.exists():
            assert not re.search(r"pip install[^`\n]*python-pptx pymupdf(?!<)", p.read_text(encoding="utf-8")), f"{p.relative_to(KB)}: unpinned install"
