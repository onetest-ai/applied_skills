"""Regression: consecutive ``##`` sections must be SIBLINGS, not parent/child.

`section_records` used `hierarchy = hierarchy[: level - 1]` before pushing the new
title — for a level-2 heading that keeps `hierarchy[:1]`, i.e. the FIRST `##` title
ever seen, so every later `##` section inherited it as `parent_heading` and as the
head of `breadcrumb_path`. In the live store this hit 8,270 of 8,271 transcript
chunks after the first (every parser but preamble-stripped ones emits only `##`
sections). Fix: keep a stack of (level, title); on a heading of level L, pop every
entry with level >= L, then push. Consecutive same-level headings become siblings.
"""
import chunking  # noqa: E402  (conftest puts every skill dir on sys.path)


def test_consecutive_level2_sections_are_siblings_not_nested():
    md = (
        "## First cue\n\nAnn: hello.\n\n"
        "## Second cue\n\nBob: hi there.\n\n"
        "## Third cue\n\nAnn: bye.\n"
    )
    records = chunking.section_records(md)
    assert [r["title"] for r in records] == ["First cue", "Second cue", "Third cue"]
    # Every ## section is top-level among siblings: no parent, breadcrumb == own title.
    for r in records:
        assert r["parent_heading"] == ""
        assert r["breadcrumb_path"] == r["title"]


def test_real_hierarchy_h1_h2_h3_produces_expected_breadcrumbs():
    md = (
        "# Doc Title\n\n"
        "## Section A\n\nintro text.\n\n"
        "### Sub A1\n\nbody one.\n\n"
        "## Section B\n\nintro two.\n\n"
        "### Sub B1\n\nbody two.\n"
    )
    records = chunking.section_records(md)
    by_title = {r["title"]: r for r in records}

    assert by_title["Section A"]["parent_heading"] == "Doc Title"
    assert by_title["Section A"]["breadcrumb_path"] == "Doc Title > Section A"

    assert by_title["Sub A1"]["parent_heading"] == "Section A"
    assert by_title["Sub A1"]["breadcrumb_path"] == "Doc Title > Section A > Sub A1"

    # Section B is a sibling of Section A (pops Sub A1 AND Section A, level >= 2).
    assert by_title["Section B"]["parent_heading"] == "Doc Title"
    assert by_title["Section B"]["breadcrumb_path"] == "Doc Title > Section B"

    assert by_title["Sub B1"]["parent_heading"] == "Section B"
    assert by_title["Sub B1"]["breadcrumb_path"] == "Doc Title > Section B > Sub B1"


def test_title_and_body_text_are_unchanged_by_the_stack_fix():
    md = "## First\n\nbody one.\n\n## Second\n\nbody two.\n"
    records = chunking.section_records(md)
    assert records[0]["title"] == "First" and records[0]["body"] == "body one."
    assert records[1]["title"] == "Second" and records[1]["body"] == "body two."
