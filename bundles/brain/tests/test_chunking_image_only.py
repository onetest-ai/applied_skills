"""A section whose body is only an image marker is not emitted as its own chunk.

Visual-lane pages render as `## pNN · title` + `<!-- image: … -->`, then the vision
transcription under its own sub-headings — so the page heading used to become an
EMPTY chunk (title only, classified from the title alone). The same happened when an
oversized page split off the marker as "(part 1)". The marker now moves onto the next
record (so the page image still attaches to the page's text, exactly as the index's
image inheritance assigned it before), and every surviving record keeps its `ord` —
chunk ids are sha256(source, ord), so nothing but the empty chunks changes id.
"""
from chunking import section_records

IMG = "<!-- image: deck/p01.png -->"


def page(n, title, body):
    return f"## p{n:02d} · {title}\n{IMG.replace('p01', f'p{n:02d}')}\n\n{body}\n"


def test_image_only_page_heading_is_folded_into_its_first_subsection():
    md = page(1, "Quarterly snapshot", "#### Quarterly snapshot\n\nNPS held at +5 across regions.") + \
         page(2, "Drivers", "#### Drivers\n\nDelivery timeliness led the drivers.")
    recs = section_records(md, 1600)
    assert [r["title"] for r in recs] == ["Quarterly snapshot", "Drivers"]
    assert [r["ord"] for r in recs] == [1, 3]              # gaps: the empty records' ords
    assert recs[0]["body"] == "<!-- image: deck/p01.png -->\n\nNPS held at +5 across regions."
    assert recs[1]["body"].startswith("<!-- image: deck/p02.png -->")
    # the child keeps its place under the page heading
    assert recs[0]["breadcrumb_path"] == "p01 · Quarterly snapshot > Quarterly snapshot"
    assert recs[0]["parent_heading"] == "p01 · Quarterly snapshot"


def test_marker_only_part_one_is_folded_into_part_two():
    big = "word " * 400                                   # one paragraph > max_chars
    recs = section_records(page(3, "Overview", big), 1200)
    assert len(recs) == 1
    assert recs[0]["ord"] == 1
    assert recs[0]["title"].endswith("(part 2)")           # unchanged title: no re-embed
    assert recs[0]["body"].startswith("<!-- image: deck/p03.png -->\n\nword")


def test_child_with_its_own_image_keeps_it():
    md = "## p01 · A\n<!-- image: deck/p01.png -->\n\n### sub\n<!-- image: deck/p01b.png -->\n\ntext here\n"
    recs = section_records(md, 1600)
    assert [r["body"] for r in recs] == ["<!-- image: deck/p01b.png -->\n\ntext here"]


def test_trailing_image_only_section_is_kept():
    md = "## a\n\nreal text\n\n## p09 · Closing\n<!-- image: deck/p09.png -->\n"
    recs = section_records(md, 1600)
    assert [r["body"] for r in recs] == ["real text", "<!-- image: deck/p09.png -->"]
    assert [r["ord"] for r in recs] == [0, 1]


def test_documents_without_image_only_sections_are_unchanged():
    md = ("# Guide\n\nIntro text.\n\n## Setup\n\nInstall it.\n\n### Details\n\n"
          "<!-- image: g/p01.png -->\n\nSee the figure.\n\n## Use\n\n" + "para " * 50 + "\n\n" + "more " * 300)
    recs = section_records(md, 1200)
    assert [r["ord"] for r in recs] == list(range(len(recs)))
    assert [r["body"] for r in recs][:3] == ["Intro text.", "Install it.",
                                            "<!-- image: g/p01.png -->\n\nSee the figure."]


def test_reindex_of_an_existing_store_drops_only_the_empty_chunks(tmp_path, monkeypatch):
    """An existing store built by the old chunker: a re-index removes the empty page
    chunks (and their tags) and re-embeds nothing — every other chunk keeps its id."""
    import chunking
    import knowledge_index as K

    calls = []
    def fake_embed(_model, texts):
        calls.append(len(texts))
        return [[1.0] + [0.0] * 383 for _ in texts]
    monkeypatch.setattr(K, "embed", fake_embed)

    corpus = tmp_path / "parsed"; corpus.mkdir()
    (corpus / "deck.pdf.md").write_text(
        page(1, "Snapshot", "#### Snapshot\n\nNPS held at +5.") + page(2, "Drivers", "#### Drivers\n\nDelivery led."))
    con = K.connect(str(tmp_path / "k.sqlite")); K._ensure_schema(con, 384)
    con.execute("CREATE TABLE IF NOT EXISTS chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT)")

    # the old chunker: no folding, contiguous ords, previous cache-key version
    def old_fold(recs):
        for i, r in enumerate(recs):
            r["ord"] = i
        return recs
    monkeypatch.setattr(chunking, "_fold_image_only", old_fold)
    monkeypatch.setattr(K, "CHUNKER_VERSION", "1")
    K.index_docs(con, "m", str(corpus), ["deck.pdf.md"], 384, 1600)
    before = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT id, title, text, image FROM chunks")}
    empty = [cid for cid, (_, text, _) in before.items() if not text]
    assert len(empty) == 2
    con.executemany("INSERT INTO chunk_topics VALUES(?, 't', 'Topic', 'intent')", [(c,) for c in before])
    calls.clear()

    monkeypatch.undo(); monkeypatch.setattr(K, "embed", fake_embed)
    K.index_docs(con, "m", str(corpus), ["deck.pdf.md"], 384, 1600)
    after = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT id, title, text, image FROM chunks")}
    assert calls == []                                             # nothing re-embedded
    assert set(after) == set(before) - set(empty)                  # only the empty ones went
    assert all(after[c] == before[c] for c in after)               # same title, text, image
    assert {r[0] for r in con.execute("SELECT chunk_id FROM chunk_topics")} == set(after)
