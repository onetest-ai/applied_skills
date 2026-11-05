"""Coverage gate (r2 plan, R8): a long meeting whose last third (or any third) produced nothing is re-read before it is
marked done, or the extractor says why nothing was there. Thirds are by text offset, never by timestamp: one long
turn can hold a third of a meeting."""
import json
import textwrap

from conftest import run_cli
from taska_lib import config as C, coverage as CV, store as S

EV = "2026-03-02_meetings_cv000001"
FILLER = "the dock schedule and the yard plan were discussed again in some detail by everyone present today "
LINES = ["Nora will send the forklift certification list to the client by Thursday morning",
         "Ravi needs to book the warehouse site visit and confirm the date with Opal",
         "Opal will share the crane inspection report with the team next week"]


def _project(tmp_path, n_filler=40):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    turns = []
    for i, line in enumerate(LINES):   # one work line at the start of each third
        turns.append({"speaker": "Nora Quill", "ts": f"00:{i * 10:02d}:00", "text": line})
        turns.append({"speaker": "Ravi Tamsin", "ts": f"00:{i * 10 + 1:02d}:00", "text": FILLER * n_filler})
    S.write_evidence(cfg.store, {"id": EV, "kind": "meeting", "at": "2026-03-02T10:00:00", "title": "Daily sync",
                                 "source": "meetings", "turns": turns})
    return cfg


def _note(cfg, quote, mid):
    S.append(cfg.store, [{"type": "note", "mid": mid, "by": "agent", "evidence": EV, "date": "2026-03-02", "node": "E01",
                          "quote": quote, "text": "x"}])


def _tokens(tmp_path, *parts):
    args = []
    for p in parts:
        args += ["--reread", run_cli(tmp_path, "reread", "--evidence", EV, "--part", str(p))["token"]]
    return args


def _left(tmp_path):
    return ["--leftovers", run_cli(tmp_path, "leftovers", "--evidence", EV)["token"]]


def _ack(tmp_path, *reread, check=False):
    return run_cli(tmp_path, "done", "--evidence", EV, "--ack", "status talk", "--quote", LINES[1], "--quote", LINES[2],
                   *reread, *_left(tmp_path), check=check)


def test_coverage_counts_events_per_third_by_text_offset(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    c = CV.coverage(S.evidence(cfg.store)[EV], S.events(cfg.store))
    assert [p["events"] for p in c["parts"]] == [1, 0, 0] and c["empty_parts"] == [2, 3]


def test_done_refuses_until_acknowledged(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "coverage" and r["empty_parts"] == [2, 3]
    assert EV not in S.processed(S.events(cfg.store))
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "parts 2 and 3 are status talk, no new work",
                "--quote", LINES[1], "--quote", LINES[2], *_tokens(tmp_path, 2, 3), *_left(tmp_path))
    assert r["status"] == "ok"
    p = [e for e in S.events(cfg.store) if e["type"] == "processed"][-1]
    assert "coverage acknowledged: parts 2 and 3 are status talk" in p["note"]


def test_ack_without_a_quote_from_each_gap_part_is_refused(tmp_path):
    # Oct 31: five coverage refusals, five --ack "Middle part…" with no re-read in between (trajectory.py)
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "status talk", check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [2, 3] and "--quote" in r["hint"]
    assert EV not in S.processed(S.events(cfg.store))


def test_a_quote_from_the_wrong_part_names_the_missing_part(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "status talk", "--quote", LINES[1], "--quote", LINES[0],
                check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [3]
    unsaid = "these eight words were never said in this meeting"
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "status talk", "--quote", LINES[1], "--quote", unsaid, check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [3] and r["quotes_not_found"] == [unsaid]
    assert r["quotes_found"] == {LINES[1]: 2}


def test_part_of_locates_a_quote_by_text_offset(tmp_path):
    cfg = _project(tmp_path)
    item = S.evidence(cfg.store)[EV]
    assert [CV.part_of(item, q) for q in LINES] == [1, 2, 3] and CV.part_of(item, "never said here") == 0


def test_full_coverage_passes(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(LINES):
        _note(cfg, line, f"n{i}")
    assert run_cli(tmp_path, "done", "--evidence", EV)["status"] == "ok"


def test_short_meeting_is_not_gated(tmp_path):
    cfg = _project(tmp_path, n_filler=1)
    assert run_cli(tmp_path, "done", "--evidence", EV)["status"] == "ok"


def test_extract_says_what_to_do_when_done_answers_coverage():
    from pathlib import Path
    ext = " ".join((Path(__file__).resolve().parent.parent / "skills" / "run" / "EXTRACT.md").read_text().split())
    assert '"status": "coverage"' in ext and "--ack" in ext and "re-read" in ext and "--quote" in ext


def test_a_long_meeting_with_a_thin_third_is_gated(tmp_path):
    # Oct 31: a 20,000-word readout produced 13, 1 and 3 events per third; "no empty third" let it through
    cfg = _project(tmp_path, n_filler=260)                       # ~80,000 characters: a long meeting
    for i, line in enumerate(LINES):
        _note(cfg, line, f"n{i}")
    _note(cfg, LINES[0], "n4")
    c = CV.coverage(S.evidence(cfg.store)[EV], S.events(cfg.store))
    assert [p["events"] for p in c["parts"]] == [2, 1, 1] and c["empty_parts"] == [] and c["thin_parts"] == [2, 3]
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "coverage" and r["thin_parts"] == [2, 3]


def test_a_long_meeting_with_two_events_per_third_passes(tmp_path):
    cfg = _project(tmp_path, n_filler=260)
    for i, line in enumerate(LINES):
        _note(cfg, line, f"n{i}")
        _note(cfg, line, f"m{i}")
    assert CV.coverage(S.evidence(cfg.store)[EV], S.events(cfg.store))["thin_parts"] == []
    assert run_cli(tmp_path, "done", "--evidence", EV)["status"] == "ok"


def test_a_short_meeting_is_never_thin(tmp_path):
    cfg = _project(tmp_path, n_filler=40)
    for i, line in enumerate(LINES):
        _note(cfg, line, f"n{i}")
    assert CV.coverage(S.evidence(cfg.store)[EV], S.events(cfg.store))["thin_parts"] == []


def test_a_short_quote_or_the_title_does_not_acknowledge_a_part(tmp_path):
    # review: one word per part, or the title (full_text ends with it, so it always lands in the last part), passed
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "x", "--quote", "forklift", "--quote", "Daily sync", check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [2, 3]
    assert r["quotes_too_short"] == ["forklift", "Daily sync"]
    assert CV.part_of(S.evidence(cfg.store)[EV], "Daily sync " * 4) == 0


def test_a_quote_present_in_two_parts_counts_for_the_first(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    everywhere = "the dock schedule and the yard plan were discussed again in some detail"
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "x", "--quote", everywhere, check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [2, 3] and r["quotes_found"] == {everywhere: 1}


def test_the_refusal_says_where_each_part_starts(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert [s["part"] for s in r["starts"]] == [1, 2, 3] and r["starts"][0] == {"part": 1, "ts": "00:00:00", "speaker": "Nora Quill"}
    assert [s["ts"] for s in r["starts"]] == ["00:00:00", "00:01:00", "00:20:00"]   # the long filler turns hold the cuts
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "x", "--quote", LINES[1], check=False)
    assert r["status"] == "ack_unquoted" and [s["part"] for s in r["starts"]] == [1, 2, 3]


def _words(n, tag):
    return " ".join(f"{tag}{i}" for i in range(n))


def _item(counts, tag="w"):
    turns = [{"ts": f"00:{i:02d}:00", "speaker": f"Sam{i}", "text": _words(c, f"{tag}{i}x")} for i, c in enumerate(counts)]
    return {"id": "ev1", "kind": "meeting", "title": "Dock sync", "date": "2026-03-02", "source": "meetings", "turns": turns}


def test_sections_cut_at_turns_near_3000_words():
    secs = CV.sections(_item([2000, 2000, 2000]))
    assert [s["n"] for s in secs] == [1, 2, 3] and [s["start_ts"] for s in secs] == ["00:00:00", "00:01:00", "00:02:00"]
    assert [s["words"] for s in secs] == [2000, 2000, 2000]
    secs = CV.sections(_item([700] * 6, tag="wide"))
    assert [s["words"] for s in secs] == [2800, 1400]
    assert secs[0]["start"] == 0 and secs[0]["end"] == secs[1]["start"]
    _, end = CV._text_end(_item([700] * 6, tag="wide"))
    assert secs[-1]["end"] == end


def test_a_turn_without_sentence_ends_is_cut_into_word_chunks():
    secs = CV.sections(_item([500, 4000, 500]))
    assert [s["words"] for s in secs] == [500, 3000, 1500]


def test_a_text_only_meeting_cuts_at_sentences():
    text = " ".join(f"{_words(40, f's{i}x')}." for i in range(150))
    item = {"id": "ev2", "kind": "meeting", "title": "Plan", "date": "2026-03-02", "source": "meetings", "text": text}
    secs = CV.sections(item)
    assert [s["words"] for s in secs] == [3000, 3000] and all(s["start_ts"] == "" for s in secs)
    assert secs[1]["start"] == secs[0]["end"]


def test_a_meeting_of_30000_characters_or_fewer_is_one_section():
    item = _item([2000, 2000])
    n, end = CV._text_end(item)
    assert 3000 < sum(len(t["text"].split()) for t in item["turns"]) and n <= CV.SECTIONS_FROM == 30000
    assert CV.sections(item) == [{"n": 1, "start": 0, "end": end, "start_ts": "00:00:00", "words": 4000}]
    assert len(CV.sections(_item([2000, 2000, 2000]))) == 3


def test_section_of_takes_the_section_of_the_first_character():
    item = _item([2000, 2000, 2000])
    assert [CV.section_of(item, _words(10, f"w{i}x")) for i in range(3)] == [1, 2, 3]
    assert CV.section_of(item, "never said anywhere in it") == 0 and CV.section_of(item, "Dock sync " * 4) == 0


def test_section_lines_map_to_the_rendered_md():
    item = _item([2000, 2000, 2000])
    lines = S.render_md(item).splitlines()
    rows = CV.section_lines(lines, item, CV.sections(item))
    assert [lines[r["first_line"] - 1][:10] for r in rows] == ["[00:00:00]", "[00:01:00]", "[00:02:00]"]
    assert rows[0]["last_line"] == rows[1]["first_line"] - 1 and rows[-1]["last_line"] == len(lines)


def _starts_with(item, words=3000, token_of=None):
    lines = S.render_md(item).splitlines()
    full = CV.norm(S.full_text(item))
    rows = CV.section_lines(lines, item, CV.sections(item, words))
    for r in rows:
        first = full[r["start"]:].split()[0]
        assert first in CV.norm(lines[r["first_line"] - 1]).split()
        assert r["first_line"] == 1 or first not in CV.norm(lines[r["first_line"] - 2]).split()
    return lines, rows


def test_a_repeated_ts_and_speaker_across_a_boundary_maps_to_the_right_line():
    item = _item([2000, 2000, 2000])
    for t in item["turns"]:
        t["ts"], t["speaker"] = "00:05:00", "Sam"
    lines, rows = _starts_with(item)
    assert [r["n"] for r in rows] == [1, 2, 3] and rows[1]["first_line"] > rows[0]["first_line"] + 2
    assert lines[rows[1]["first_line"] - 2] == "" and rows[0]["last_line"] == rows[1]["first_line"] - 1


def test_a_section_starting_on_a_bc_turn_maps_to_the_next_rendered_line():
    item = _item([2000, 2000, 50, 2000])
    item["turns"][2]["bc"] = True
    lines, rows = _starts_with(item)
    assert len(rows) == 3 and rows[2]["first_line"] is not None
    assert lines[rows[2]["first_line"] - 1].startswith("[00:03:00] Sam3:")


def test_two_speakers_at_one_ts_map_to_their_own_turn():
    item = _item([2000, 2000, 2000])
    for t in item["turns"]:
        t["ts"] = "00:05:00"
    lines, rows = _starts_with(item)
    assert [lines[r["first_line"] - 1].split(": ")[0] for r in rows] == ["[00:05:00] Sam0", "[00:05:00] Sam1", "[00:05:00] Sam2"]


def test_a_text_only_meeting_of_7000_words_maps_every_section():
    text = " ".join(f"{_words(40, f's{i}x')}." for i in range(175))
    item = {"id": "ev2", "kind": "meeting", "title": "Plan", "date": "2026-03-02", "source": "meetings", "text": text}
    lines, rows = _starts_with(item)
    assert len(rows) == 3 and all(r["first_line"] for r in rows) and rows[-1]["last_line"] == len(lines)


def test_a_stale_md_gets_no_line_numbers_rather_than_a_guess(monkeypatch):
    item = _item([2000, 2000, 2000])
    monkeypatch.setattr(S, "MAX_LINE", 300)
    stale = S.render_md(item).splitlines()
    monkeypatch.setattr(S, "MAX_LINE", 600)
    rows = CV.section_lines(stale, item, CV.sections(item))
    assert [(r["first_line"], r["last_line"]) for r in rows] == [(None, None)] * 3


def _monologue(words=3000, keys=("Nora will send the crane list", "Ravi will book the yard visit", "Opal will share the gate report")):
    body = [f"talk{i}" for i in range(words)]
    for k, key in enumerate(keys):
        body[words * (2 * k + 1) // 6] = key + "."
    return {"id": "ev4", "kind": "meeting", "title": "Readout", "date": "2026-03-02", "source": "meetings",
            "turns": [{"ts": "00:00:00", "speaker": "Nora", "text": " ".join(body)}]}, keys


def _line_of(lines, quote):
    return next(i + 1 for i, x in enumerate(lines) if quote in x)


def test_a_3000_word_monologue_gives_each_part_its_own_lines():
    item, keys = _monologue()
    lines = S.render_md(item).splitlines()
    ranges = [CV.part_lines(lines, item, p) for p in (1, 2, 3)]
    assert all(f is not None and f <= l for f, l in ranges)
    assert ranges[0][0] == lines.index("") + 2 and ranges[2][1] == len(lines)
    assert ranges[0][1] <= ranges[1][0] <= ranges[0][1] + 1 and ranges[1][1] <= ranges[2][0] <= ranges[1][1] + 1
    for key in keys:
        p = CV.part_of(item, key)
        assert ranges[p - 1][0] <= _line_of(lines, key) <= ranges[p - 1][1]


def test_every_key_line_of_a_part_is_inside_its_range():
    item = _item([300] * 12)
    lines = S.render_md(item).splitlines()
    ranges = {p: CV.part_lines(lines, item, p) for p in (1, 2, 3)}
    for t in item["turns"]:
        for w in t["text"].split()[::25]:
            p = CV.part_of(item, " ".join(t["text"].split()[t["text"].split().index(w):][:8]))
            n = next(i + 1 for i, x in enumerate(lines) if f" {w} " in f" {x} ")
            assert ranges[p][0] <= n <= ranges[p][1], (w, p, n, ranges[p])


def test_a_text_only_meeting_gives_each_part_lines():
    text = " ".join(f"{_words(40, f's{i}x')}." for i in range(60))
    item = {"id": "ev2", "kind": "meeting", "title": "Plan", "date": "2026-03-02", "source": "meetings", "text": text}
    lines = S.render_md(item).splitlines()
    ranges = [CV.part_lines(lines, item, p) for p in (1, 2, 3)]
    assert all(f is not None and f <= l for f, l in ranges) and ranges[0][0] < ranges[2][0]
    p = CV.part_of(item, _words(40, "s30x")[:60])
    assert ranges[p - 1][0] <= _line_of(lines, "s30x0 ") <= ranges[p - 1][1]


def test_a_stale_md_gives_a_part_no_lines(monkeypatch):
    item, _keys = _monologue()
    monkeypatch.setattr(S, "MAX_LINE", 300)
    stale = S.render_md(item).splitlines()
    monkeypatch.setattr(S, "MAX_LINE", 600)
    assert CV.part_lines(stale, item, 2) == (None, None)


def test_thin_parts_start_at_30000_chars():
    def made(chars):
        item = {"id": "ev3", "kind": "meeting", "title": "T", "turns": [
            {"ts": f"00:0{i}:00", "speaker": "A", "text": ("abcdefgh " * 20 + f"turn{i}marker " + "abcdefgh " * (chars // 27))[:chars // 3]} for i in range(3)]}
        return item
    def thin(item, counts):
        evs = []
        for i, c in enumerate(counts):
            evs += [{"type": "note", "by": "agent", "evidence": "ev3", "quote": item["turns"][i]["text"][170:230]}] * c
        return CV.coverage(item, evs)["thin_parts"]
    big, small = made(35000), made(25000)
    assert thin(big, [2, 1, 2]) == [2] and thin(small, [2, 1, 2]) == []


def test_an_ack_with_quotes_but_no_reread_is_refused(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = _ack(tmp_path)
    assert r["status"] == "reread" and r["parts_without_reread"] == [2, 3] and "reread --evidence" in r["hint"]
    assert [s["part"] for s in r["starts"]] == [1, 2, 3]
    assert EV not in S.processed(S.events(cfg.store))


def test_a_reread_token_lets_the_ack_through(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    assert _ack(tmp_path, *_tokens(tmp_path, 2, 3), check=True)["status"] == "ok"
    assert EV in S.processed(S.events(cfg.store))


def test_a_token_from_before_an_apply_is_stale(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    old = _tokens(tmp_path, 2, 3)
    _note(cfg, LINES[0], "n2")
    r = _ack(tmp_path, *old)
    assert r["status"] == "reread" and r["parts_without_reread"] == [2, 3]
    assert _ack(tmp_path, *_tokens(tmp_path, 3))["parts_without_reread"] == [2]


def test_a_token_for_part_one_does_not_cover_part_three(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = _ack(tmp_path, *_tokens(tmp_path, 1, 2))
    assert r["status"] == "reread" and r["parts_without_reread"] == [3]


def test_reread_answers_the_part_as_lines_of_the_md(tmp_path):
    cfg = _project(tmp_path)
    r = run_cli(tmp_path, "reread", "--evidence", EV, "--part", "2")
    assert set(r) == {"status", "evidence", "part", "starts", "md", "first_line", "last_line", "token"}
    assert r["status"] == "ok" and r["part"] == 2 and r["starts"]["part"] == 2 and len(r["token"]) == 12
    lines = S.P(cfg.store, "evidence", EV + ".md").read_text().split("\n")
    assert r["md"].endswith(f"{EV}.md") and r["first_line"] <= _line_of(lines, LINES[1]) <= r["last_line"]
    assert _line_of(lines, LINES[0]) < r["first_line"] and _line_of(lines, LINES[2]) > r["last_line"]


def test_reread_on_a_stale_md_says_to_render_it(tmp_path):
    cfg = _project(tmp_path)
    token = run_cli(tmp_path, "reread", "--evidence", EV, "--part", "2")["token"]
    S.P(cfg.store, "evidence", EV + ".md").write_text("# Daily sync\nold layout\n\none line\n")
    r = run_cli(tmp_path, "reread", "--evidence", EV, "--part", "2")
    assert r["first_line"] is None and r["last_line"] is None and "render-evidence" in r["hint"]
    assert r["token"] == token


def test_reread_refuses_an_unknown_evidence_or_part(tmp_path):
    _project(tmp_path)
    assert run_cli(tmp_path, "reread", "--evidence", "nope", "--part", "1", check=False)["status"] == "error"
    assert run_cli(tmp_path, "reread", "--evidence", EV, "--part", "4", check=False)["status"] == "error"


def test_the_agent_flow_reread_apply_reread_done(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    assert run_cli(tmp_path, "done", "--evidence", EV, check=False)["status"] == "coverage"
    old = _tokens(tmp_path, 2, 3)
    _note(cfg, LINES[0], "n2")
    stale = _ack(tmp_path, *old)
    assert stale["status"] == "reread" and stale["parts_without_reread"] == [2, 3]
    assert "changes with every apply" in stale["hint"]
    assert EV not in S.processed(S.events(cfg.store))
    assert _ack(tmp_path, *_tokens(tmp_path, 2, 3), check=True)["status"] == "ok"
    assert EV in S.processed(S.events(cfg.store))


def _sentences(n, per=40, tag="s"):
    return " ".join(f"{_words(per, f'{tag}{i}x')}." for i in range(n))


def _one_turn(sentences=225, speaker="?"):
    return {"id": "ev5", "kind": "meeting", "title": "Readout", "date": "2026-03-02", "source": "meetings",
            "turns": [{"ts": "00:07:00", "speaker": speaker, "text": _sentences(sentences)}]}


def test_a_single_long_turn_is_cut_at_sentence_ends():
    item = _one_turn()
    secs = CV.sections(item)
    full = CV.norm(S.full_text(item))
    assert [s["n"] for s in secs] == [1, 2, 3] and all(s["words"] <= 3000 for s in secs)
    assert all(s["start_ts"] == "00:07:00" for s in secs)
    assert secs[0]["start"] == 0 and secs[0]["end"] == secs[1]["start"] and secs[1]["end"] == secs[2]["start"]
    assert [full[s["start"]:].split()[0] for s in secs] == ["s0x0", "s75x0", "s150x0"]
    assert [full[:s["start"]].split()[-1] for s in secs[1:]] == ["s74x39", "s149x39"]


def _body(lines, row):
    return CV.norm(" ".join(l.split(": ", 1)[1] for l in lines[row["first_line"] - 1:row["last_line"]]))


def test_a_single_long_turn_maps_to_increasing_rendered_lines():
    item = _one_turn()
    lines = S.render_md(item).splitlines()
    full = CV.norm(S.full_text(item))
    secs = CV.sections(item)
    rows = CV.section_lines(lines, item, secs)
    assert all(r["first_line"] is not None and r["first_line"] <= r["last_line"] for r in rows)
    assert all(a["first_line"] < b["first_line"] and a["last_line"] <= b["first_line"] for a, b in zip(rows, rows[1:]))
    assert rows[-1]["last_line"] == len(lines)
    for r in rows:
        first_words = CV.norm(lines[r["first_line"] - 1].split(": ", 1)[1])
        assert full[r["start"]:r["start"] + 40] in first_words
        assert full[r["start"]:r["end"]].strip() in _body(lines, r)


def test_a_boundary_inside_a_rendered_line_puts_that_line_in_both_ranges():
    item = _one_turn()
    lines = S.render_md(item).splitlines()
    rows = CV.section_lines(lines, item, CV.sections(item))
    shared = [a["last_line"] == b["first_line"] for a, b in zip(rows, rows[1:])]
    assert any(shared)
    for a, b in zip(rows, rows[1:]):
        assert b["first_line"] - 1 <= a["last_line"] <= b["first_line"]


def test_a_boundary_at_a_line_start_shares_no_line():
    item = _item([2000, 2000, 2000])
    lines = S.render_md(item).splitlines()
    rows = CV.section_lines(lines, item, CV.sections(item))
    assert all(a["last_line"] == b["first_line"] - 2 or a["last_line"] == b["first_line"] - 1 for a, b in zip(rows, rows[1:]))
    assert all(a["last_line"] < b["first_line"] for a, b in zip(rows, rows[1:]))


def test_a_20000_word_turn_without_punctuation_is_cut_into_chunks():
    item = {"id": "ev6", "kind": "meeting", "title": "Readout", "date": "2026-03-02", "source": "meetings",
            "turns": [{"ts": "00:01:00", "speaker": "?", "text": _words(20000, "u")}]}
    secs = CV.sections(item)
    assert [s["words"] for s in secs] == [3000] * 6 + [2000]
    assert all(a["end"] == b["start"] for a, b in zip(secs, secs[1:])) and secs[0]["start"] == 0
    assert all(s["start_ts"] == "00:01:00" for s in secs)
    assert CV.section_of(item, _words(10, "u").replace("u0", "u9000"), secs) == 0
    lines = S.render_md(item).splitlines()
    rows = CV.section_lines(lines, item, secs)
    assert all(r["first_line"] and r["last_line"] for r in rows)
    assert all(CV.norm(S.full_text(item))[r["start"]:r["end"]].strip() in _body(lines, r) for r in rows)


def test_a_4000_word_sentence_is_cut_into_chunks():
    item = {"id": "ev7", "kind": "meeting", "title": "Readout", "date": "2026-03-02", "source": "meetings",
            "turns": [{"ts": "00:01:00", "speaker": "Nora", "text": _sentences(100, tag="a") + " " + _words(4000, "big") + ". " + _sentences(100, tag="b")}]}
    secs = CV.sections(item)
    assert all(s["words"] <= 3000 for s in secs) and sum(s["words"] for s in secs) == 4000 + 8000
    assert all(a["end"] == b["start"] for a, b in zip(secs, secs[1:]))
    assert CV.section_of(item, _words(10, "big"), secs) == 3 and CV.section_of(item, _words(10, "b50x"), secs) == len(secs)


def test_a_small_meeting_with_a_4000_word_turn_stays_one_section():
    item = {"id": "ev8", "kind": "meeting", "title": "Sync", "date": "2026-03-02", "source": "meetings",
            "turns": [{"ts": "00:01:00", "speaker": "Nora", "text": " ".join(["ab."] * 4000)}]}
    n, _ = CV._text_end(item)
    assert n <= CV.SECTIONS_FROM and len(CV.sections(item)) == 1


def test_section_of_a_quote_from_the_middle_of_a_single_long_turn():
    item = _one_turn()
    secs = CV.sections(item)
    quote = _words(10, "s100x")
    assert CV.section_of(item, quote, secs) == 2 and CV.section_of(item, _words(10, "s200x")) == 3
    assert CV.section_of(item, _words(10, "s10x")) == 1


def test_a_mixed_meeting_cuts_short_turns_and_splits_the_long_one():
    item = _item([500, 700])
    item["turns"].append({"ts": "00:09:00", "speaker": "Lee", "text": _sentences(150, tag="lg")})
    item["turns"].append({"ts": "00:10:00", "speaker": "Kit", "text": _words(400, "end")})
    secs = CV.sections(item)
    full = CV.norm(S.full_text(item))
    assert all(s["words"] <= 3000 for s in secs) and sum(s["words"] for s in secs) == 500 + 700 + 6000 + 400
    assert secs[0]["start_ts"] == "00:00:00" and [s["start_ts"] for s in secs[1:3]] == ["00:09:00", "00:09:00"]
    assert all(a["end"] == b["start"] for a, b in zip(secs, secs[1:]))
    assert full[secs[1]["start"]:].split()[0].startswith("lg")
    assert CV.section_of(item, _words(10, "lg100x"), secs) == 2
    assert CV.section_of(item, _words(10, "end"), secs) == len(secs)
