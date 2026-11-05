"""evals/pages.py: paginated transcript views with exact offsets, and delivery read from a role's tool results.
Invented data only (Harbor Logistics)."""
import json
import sys
from dataclasses import dataclass, field

import pytest

from conftest import TASKA_ROOT

sys.path.insert(0, str(TASKA_ROOT / "evals"))
import pages as G  # noqa: E402

SENT = ("Nora confirmed the berth plan for Zoë and the café crew at Søndre gate, so the next step is the crane schedule. "
        "Ravi said the hauliers' access list is still pending — naïve estimates won't do. ")


def one_line(n_chars=108_288):
    body = "".join(f"Point {k}. {SENT}" for k in range(n_chars // len(SENT) + 1))
    head = "# Readout\n\n[00:00:05] Nora Quill: "
    return head + body[: n_chars - len(head) - 1] + "\n"


def multi_turn():
    lines = [f"[00:{m:02d}:{s:02d}] {'Nora Quill' if (m + s) % 2 else 'Ravi Tamsin'}: {SENT * 3}" for m in range(60) for s in (0, 30)]
    return "# Yard sync\n\n" + "\n".join(lines) + "\n"


@pytest.mark.parametrize("source", [one_line(), multi_turn()], ids=["108288-one-line", "many-turns"])
def test_pages_tile_the_source_exactly(source):
    pages = G.paginate(source)
    assert pages[0].start == 0
    assert pages[-1].end == len(source)
    assert all(a.end == b.start for a, b in zip(pages, pages[1:]))
    assert all(p.text == source[p.start:p.end] for p in pages)
    assert "".join(p.text for p in pages) == source
    assert all(p.end - p.start <= G.PAGE_CHARS for p in pages)


def test_the_single_line_case_renders_short_lines_and_rebuilds_byte_for_byte(tmp_path):
    src = tmp_path / "2026-03-02_meetings_ab12cd34.md"
    src.write_text(one_line(), encoding="utf-8")
    m = G.write_views(src, tmp_path / "views")
    d = tmp_path / "views" / "2026-03-02_meetings_ab12cd34"
    assert len(m["pages"]) == 3 and m["chars"] == 108_288
    for p in (m["pages"][0], m["pages"][len(m["pages"]) // 2], m["pages"][-1]):     # beginning, middle and final page
        body = (d / p["file"]).read_text(encoding="utf-8")
        assert max(len(l) for l in body.split("\n")) <= G.LINE_CHARS and p["unbreakable_lines"] == 0
        assert G.unrender(body, p["wraps"]) == one_line()[p["start"]:p["end"]]
    assert G.read_view(d) == src.read_text(encoding="utf-8")
    assert m["speaker_quality"][0].startswith("one speaker tag (Nora Quill) holds 100%")
    assert "turn(s) over 20000 characters" in m["speaker_quality"][1]
    assert m["pages"][1]["continues_turn"] is True and m["pages"][0]["turns"] == ["t0001", "t0001"]


def test_pages_break_at_turn_boundaries_when_there_are_turns():
    source = multi_turn()
    starts = {t["start"] for t in G.turns(source)}
    assert all(p.start in starts for p in G.paginate(source)[1:])


def test_a_quote_across_a_page_boundary_maps_to_both_pages(tmp_path):
    src = tmp_path / "2026-03-02_meetings_ab12cd34.md"
    src.write_text(one_line(), encoding="utf-8")
    m = G.write_views(src, tmp_path / "views")
    source = src.read_text(encoding="utf-8")
    b = m["pages"][0]["end"]
    first, last = source.rfind(" ", 0, b - 30) + 1, source.find(" ", b + 30)
    quote = source[first:last]
    hit = G.locate(m, source, quote.upper().replace(" ", "  "))
    assert hit["pages"] == [1, 2] and (hit["start"], hit["end"]) == (first, last)
    assert G.locate(m, source, "words nobody said in this readout") is None


def test_a_tampered_page_is_refused(tmp_path):
    src = tmp_path / "2026-03-02_meetings_ab12cd34.md"
    src.write_text(multi_turn(), encoding="utf-8")
    G.write_views(src, tmp_path / "views")
    p = tmp_path / "views" / "2026-03-02_meetings_ab12cd34" / "p01.md"
    p.write_text(p.read_text().replace("berth", "birth", 1))
    with pytest.raises(ValueError, match="p01.md: page text differs"):
        G.read_view(p.parent)


@dataclass
class Call:
    name: str
    input: dict
    output: str
    is_error: bool = False
    ts: str = field(default="")


def _numbered(body, first=1, last=None):
    lines = body.split("\n")
    last = last or len(lines)
    return "\n".join(f"{k}\t{lines[k - 1]}" for k in range(first, last + 1))


def test_delivery_separates_available_requested_whole_and_truncated(tmp_path):
    src = tmp_path / "2026-03-02_meetings_ab12cd34.md"
    src.write_text(one_line(), encoding="utf-8")
    m = G.write_views(src, tmp_path / "views")
    d = tmp_path / "views" / m["evidence"]
    b1, b2, b3 = ((d / p["file"]).read_text(encoding="utf-8") for p in m["pages"])
    n2 = len(b2.split("\n"))
    calls = [Call("Read", {"file_path": str(d / "p01.md")}, _numbered(b1)),
             Call("Read", {"file_path": str(d / "p02.md"), "limit": "20"}, _numbered(b2, 1, 20)),
             Call("Read", {"file_path": str(d / "p02.md"), "offset": "21"}, _numbered(b2, 21, n2)),
             Call("Read", {"file_path": str(d / "p03.md")}, "File content (31000 tokens) exceeds maximum allowed tokens (25000).", True)]
    out = G.delivery(calls, m, str(d))
    assert [(r["requested"], r["whole"], r["truncated"]) for r in out["pages"]] == [(True, True, False), (True, True, False), (True, False, True)]
    assert out["missing"] == [3] and out["all_whole"] is False
    cut = _numbered(b1).replace(b1.split("\n")[3], b1.split("\n")[3][:100] + "…")
    assert G.delivery([Call("Read", {"file_path": str(d / "p01.md")}, cut)], m, str(d))["pages"][0]["whole"] is False
    assert G.delivery([], m, str(d))["pages"][0] == {"page": 1, "file": "p01.md", "available": True, "requested": False, "whole": False,
                                                       "lines_returned": 0, "lines": len(b1.split("\n")), "truncated": False}
    whole_by_cat = G.delivery([Call("Bash", {"command": f"cat {d / 'p03.md'}"}, b3)], m, str(d))
    assert whole_by_cat["pages"][2]["whole"] is True


def test_the_cli_writes_views_that_rebuild_their_sources(tmp_path, capsys):
    src = tmp_path / "2026-03-02_meetings_ab12cd34.md"
    src.write_text(one_line(50_000), encoding="utf-8")
    assert G.main(["write", "--source", str(src), "--out", str(tmp_path / "v")]) == 0
    assert json.loads(capsys.readouterr().out)[0]["pages"] == 2


def test_a_page_size_under_two_lines_is_refused():
    with pytest.raises(ValueError, match="below two lines"):
        G.paginate("x" * 10, page_chars=100)


def test_a_crlf_transcript_and_an_unbreakable_token_round_trip(tmp_path):
    src = tmp_path / "2026-03-02_meetings_ab12cd34.md"
    body = "# Sync\r\n[00:01] Nora Quill: " + "z" * 700 + " more words" * 500 + "\r\n[00:02] Ravi Tamsin: ok\r\n"
    src.write_bytes(body.encode())
    m = G.write_views(src, tmp_path / "v")
    d = tmp_path / "v" / m["evidence"]
    assert G.read_view(d) == body
    lines = (d / "p01.md").read_bytes().decode().split("\n")
    assert m["pages"][0]["unbreakable_lines"] == 1 and sorted(len(l) for l in lines)[-2] <= G.LINE_CHARS
