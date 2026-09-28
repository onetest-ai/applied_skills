import re

from chunking import section_records

MARK = re.compile(r"^\s*<!--\s*speaker:.*?-->\s*$", re.M)


def bodies(md: str, n: int) -> list[str]:
    return [r["body"] for r in section_records(md, n)]


def test_marker_never_split_off_alone():
    md = "## 17:15 — A (cue 75)\n\n<!-- speaker: A -->\n\n" + "word " * 400
    out = bodies(md, 1200)
    assert all(MARK.sub("", b).strip() for b in out)
    assert out[0].startswith("<!-- speaker: A -->")


def test_marker_with_nothing_after_is_dropped():
    assert bodies("## t\n\n<!-- speaker: A -->\n\n## u\n\ntext", 1200) == ["text"]


def test_long_body_without_markers_splits_as_before():
    md = "## t\n\n" + "\n\n".join(["para " * 50] * 10)
    out = bodies(md, 1200)
    assert len(out) > 1 and all(len(b) <= 1200 for b in out)
