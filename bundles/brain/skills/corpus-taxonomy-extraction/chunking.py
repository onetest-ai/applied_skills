"""Shared heading-aware Markdown chunker — one source of truth for chunk = section.

Used by knowledge-index (RAG chunks) AND corpus-taxonomy-extraction/to_obsidian
(vault notes) so a retrieval chunk is exactly the note a human sees. Keep the copies
in the two skills identical.

section_records(md, max_chars) -> list[dict]  (title, body, parent_heading, breadcrumb_path, ord)
sections(md, max_chars) -> list[(title, body)] (compatibility wrapper)
  Split at Markdown headings / '[page N]' markers; oversized sections split by
  paragraph; titles derived from the first substantive line when a heading is
  missing or useless ('Page N'). Falls back to paragraph-merge for heading-less docs.
"""
import re

# Bump when a change here alters the records for UNCHANGED Markdown: knowledge_index
# folds it into each document's cache key so a re-index re-chunks every document
# (surviving chunks keep their id and embedding hash, so nothing is re-embedded).
CHUNKER_VERSION = "2"   # 2: image-only sections folded into the next record

def strip_preamble(md):
    return re.sub(r"\A# SOURCE:.*\n(# method:.*\n)?(# fidelity:.*\n)?\n?", "", md)

def derive_title(title, body):
    t = (title or "").strip()
    if t and not re.match(r"(?i)^page \d+$", t) and len(re.sub(r"\W", "", t)) >= 3:
        return t[:70]
    for ln in body.splitlines():
        s = re.sub(r"\s+", " ", re.sub(r"[#>*_`|<>!\[\]()-]+", " ", ln)).strip()
        if len(re.sub(r"\W", "", s)) >= 6:
            return " ".join(s.split()[:10])[:70]
    return t or "Section"

_SPEAKER_ONLY = re.compile(r"^(\s*<!--\s*speaker:.*?-->\s*)+$")
_IMAGE_ONLY = re.compile(r"^(\s*<!--\s*image:.*?-->\s*)+$")
_IMAGE = re.compile(r"<!--\s*image:.*?-->")


def _fold_image_only(recs):
    """Drop records whose body is only an image marker, moving the marker onto the next
    record that has none (the index already hands the last page image to following
    sections, so image assignment is unchanged). Every record keeps `ord` = its position
    BEFORE the drop: chunk ids are sha256(source, ord), so only the empty chunks' ids go.
    A trailing image-only record (nothing after it to carry the image) is kept."""
    for i, r in enumerate(recs):
        r["ord"] = i
    last_real = max((i for i, r in enumerate(recs) if not _IMAGE_ONLY.match(r["body"])), default=-1)
    out, carry = [], None
    for i, r in enumerate(recs):
        if i < last_real and _IMAGE_ONLY.match(r["body"]):
            carry = _IMAGE.findall(r["body"])[-1]
            continue
        if carry and not _IMAGE.search(r["body"]):
            r["body"] = f"{carry}\n\n{r['body']}"
        carry = None
        out.append(r)
    return out


def section_records(md, max_chars=1600):
    _s = md.strip()
    if _s.startswith("{\"") or _s.startswith("[{"):
        raise ValueError(
            f"sections() input appears to be JSON, not Markdown. "
            f"Convert with extraction_to_md.py first. First 80 chars: {_s[:80]!r}"
        )
    md = strip_preamble(md)
    heading = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
    blocks, title, buf, level = [], None, [], 0
    # Stack of (level, title) for the currently-open headings, outermost first. On a
    # heading of level L, every entry with level >= L is popped before pushing — so
    # consecutive same-level headings (e.g. two ## sections in a row) become SIBLINGS,
    # not parent/child. A bare `hierarchy[:level-1]` slice (the old code) instead kept
    # whatever title last occupied that slot, so every ## section after the first
    # inherited the FIRST ## title ever seen as its parent_heading/breadcrumb_path.
    stack = []
    breadcrumb, parent = "", ""
    for ln in md.splitlines():
        m = heading.match(ln)
        if m:
            if title is not None or "\n".join(buf).strip():
                blocks.append((title, "\n".join(buf).strip(), parent, breadcrumb))
            level = len(ln) - len(ln.lstrip("#"))
            title = re.sub(r"\[page (\d+)\]", r"Page \1", m.group(1)).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            titles = [t for _, t in stack]
            breadcrumb = " > ".join(titles)
            parent = titles[-2] if len(titles) > 1 else ""
            buf = []
        else:
            buf.append(ln)
    if title is not None or "\n".join(buf).strip():
        blocks.append((title, "\n".join(buf).strip(), parent, breadcrumb))
    # Title-only sections (heading with no body text) are intentionally dropped.
    # knowledge_index.py removes orphan chunk rows automatically on the next re-index
    # via its per-source old_ids-minus-new_ids cascade (chunks, chunk_topics, graph_edges).
    # The only residual risk: if classify_write.py ran before re-indexing, its chunk_topics
    # rows for dropped sections remain until classify_write runs again on the updated DB.
    blocks = [(t, b, p, bc) for t, b, p, bc in blocks if b and not _SPEAKER_ONLY.match(b)]
    if not blocks:
        blocks = [(None, md.strip(), "", "")]
    out = []
    for t, b, p, bc in blocks:
        if len(b) <= max_chars:
            out.append({"title": derive_title(t, b), "body": b, "parent_heading": p, "breadcrumb_path": bc}); continue
        paras = [para for para in re.split(r"\n\s*\n", b) if para.strip()]
        cur, part = "", 1
        for para in paras:
            if len(cur) + len(para) + 2 > max_chars and cur and not _SPEAKER_ONLY.match(cur):
                base = derive_title(t, cur)
                out.append({"title": f"{base} (part {part})", "body": cur.strip(), "parent_heading": p, "breadcrumb_path": bc}); cur = para; part += 1
            else:
                cur = (cur + "\n\n" + para) if cur else para
        if cur.strip() and not _SPEAKER_ONLY.match(cur):
            base = derive_title(t, cur)
            out.append({"title": f"{base} (part {part})" if part > 1 else base, "body": cur.strip(), "parent_heading": p, "breadcrumb_path": bc})
    return _fold_image_only(out)


def sections(md, max_chars=1600):
    return [(record["title"], record["body"]) for record in section_records(md, max_chars)]
