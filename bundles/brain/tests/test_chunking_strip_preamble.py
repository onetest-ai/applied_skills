"""The `# fidelity:` header parse_corpus.py now writes must not seed the heading
hierarchy. strip_preamble() exists precisely to remove the whole preamble before
section_records() ever sees a `#` line — regression guard for the whole-branch
review finding where a live `# fidelity: full` H1 leaked into parent_heading/
breadcrumb_path for every parsed document, not just HTML ones.
"""
import chunking  # noqa: E402  (conftest puts every skill dir on sys.path)


def test_three_line_preamble_is_stripped_before_first_heading():
    md = (
        "# SOURCE: decks/q3-review.html\n"
        "# method: playwright+dom\n"
        "# fidelity: full\n"
        "\n"
        "## [page 1]\n\n"
        "Five9 FCR was 72% in Q3.\n"
    )
    records = chunking.section_records(md)
    first = records[0]
    assert first["title"] != "fidelity: full"
    assert first["parent_heading"] == ""
    assert first["breadcrumb_path"] == "Page 1"


def test_two_line_preamble_still_stripped():
    md = "# SOURCE: notes.md\n# method: passthrough\n\n## Section\n\nbody text here.\n"
    records = chunking.section_records(md)
    assert records[0]["parent_heading"] == ""
