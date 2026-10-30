"""The evidence file the extracting agent reads never hides text: Claude Code's Read shows only the first ~2000
characters of a line, so a transcript stored as one 108,000-character line was read as its first 2% (Oct 21 readout,
13 of 21 key items missed). render_md keeps every word, in lines short enough to be read whole. Invented data only."""
import re

from taska_lib import store as S

SENT = "Nora will send the yard plan to Opal by Friday so the dock team can start. "


def _item(**kw):
    return {"id": "2026-03-04_meetings_aaaa0001", "date": "2026-03-04", "at": "2026-03-04T10:00:00", "title": "Depot review", "kind": "meeting",
            "source": "meetings", **kw}


def _words(s):
    return re.findall(r"\S+", s)


def test_a_long_text_is_split_into_readable_lines_and_keeps_every_word():
    text = SENT * 800                                  # ~60,000 characters, one paragraph
    md = S.render_md(_item(text=text))
    assert max(len(l) for l in md.splitlines()) <= S.MAX_LINE
    assert _words(md.split("\n\n", 1)[1]) == _words(text)


def test_a_long_turn_is_split_and_every_part_says_who_and_when():
    md = S.render_md(_item(turns=[{"ts": "00:05:00", "speaker": "Nora Quill", "text": SENT * 200}]))
    body = [l for l in md.split("\n\n", 1)[1].splitlines() if l]
    assert len(body) > 1 and max(len(l) for l in body) <= S.MAX_LINE
    assert all(l.startswith("[00:05:00] Nora Quill: ") for l in body)
    assert _words(" ".join(l.split(": ", 1)[1] for l in body)) == _words(SENT * 200)


def test_text_without_sentence_ends_is_split_between_words():
    text = "word " * 5000
    md = S.render_md(_item(text=text))
    assert max(len(l) for l in md.splitlines()) <= S.MAX_LINE and _words(md.split("\n\n", 1)[1]) == _words(text)


def test_a_short_turn_is_written_as_before():
    md = S.render_md(_item(turns=[{"ts": "00:01:00", "speaker": "Opal Brennick", "text": SENT.strip()}]))
    assert "[00:01:00] Opal Brennick: " + SENT.strip() in md.splitlines()


def test_rerender_rewrites_every_md_from_its_json_and_never_touches_the_json(tmp_path):
    S.ensure(tmp_path)
    x = _item(text=SENT * 800)
    S.write_evidence(tmp_path, x)
    md, js = S.P(tmp_path, "evidence", x["id"] + ".md"), S.P(tmp_path, "evidence", x["id"] + ".json")
    md.write_text("# old\n\n" + SENT * 800)               # as written before this fix: one very long line
    before = js.read_bytes()
    assert S.rerender(tmp_path) == 1 and S.rerender(tmp_path) == 0
    assert md.read_text() == S.render_md(x) and js.read_bytes() == before


def test_no_line_of_the_file_is_too_long_headers_included():
    md = S.render_md(_item(text="short", meta={"context": SENT * 40, "participants": ["Nora Quill"] * 300}))
    assert max(len(l) for l in md.splitlines()) <= S.MAX_LINE
    assert all(l.startswith("context: ") for l in md.splitlines() if "yard plan" in l)
