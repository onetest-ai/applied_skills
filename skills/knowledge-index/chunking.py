"""Shared heading-aware Markdown chunker — one source of truth for chunk = section.

Used by knowledge-index (RAG chunks) AND corpus-taxonomy-extraction/to_obsidian
(vault notes) so a retrieval chunk is exactly the note a human sees. Keep the copies
in the two skills identical.

sections(md, max_chars) -> list[(title, body)]
  Split at Markdown headings / '[page N]' markers; oversized sections split by
  paragraph; titles derived from the first substantive line when a heading is
  missing or useless ('Page N'). Falls back to paragraph-merge for heading-less docs.
"""
import re

def strip_preamble(md):
    return re.sub(r"\A# SOURCE:.*\n(# method:.*\n)?\n?", "", md)

def derive_title(title, body):
    t = (title or "").strip()
    if t and not re.match(r"(?i)^page \d+$", t) and len(re.sub(r"\W", "", t)) >= 3:
        return t[:70]
    for ln in body.splitlines():
        s = re.sub(r"\s+", " ", re.sub(r"[#>*_`|<>!\[\]()-]+", " ", ln)).strip()
        if len(re.sub(r"\W", "", s)) >= 6:
            return " ".join(s.split()[:10])[:70]
    return t or "Section"

def sections(md, max_chars=1600):
    _s = md.strip()
    if _s.startswith(("{", "[")):
        raise ValueError(
            f"sections() input appears to be JSON, not Markdown. "
            f"Convert with extraction_to_md.py first. First 80 chars: {_s[:80]!r}"
        )
    md = strip_preamble(md)
    heading = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
    blocks, title, buf = [], None, []
    for ln in md.splitlines():
        m = heading.match(ln)
        if m:
            if title is not None or "\n".join(buf).strip():
                blocks.append((title, "\n".join(buf).strip()))
            title, buf = re.sub(r"\[page (\d+)\]", r"Page \1", m.group(1)).strip(), []
        else:
            buf.append(ln)
    if title is not None or "\n".join(buf).strip():
        blocks.append((title, "\n".join(buf).strip()))
    blocks = [(t, b) for t, b in blocks if b or t]
    if not blocks:
        blocks = [(None, md.strip())]
    out = []
    for t, b in blocks:
        if len(b) <= max_chars:
            out.append((derive_title(t, b), b)); continue
        paras = [p for p in re.split(r"\n\s*\n", b) if p.strip()]
        cur, part = "", 1
        for p in paras:
            if len(cur) + len(p) + 2 > max_chars and cur:
                base = derive_title(t, cur)
                out.append((f"{base} (part {part})", cur.strip())); cur = p; part += 1
            else:
                cur = (cur + "\n\n" + p) if cur else p
        if cur.strip():
            base = derive_title(t, cur)
            out.append((f"{base} (part {part})" if part > 1 else base, cur.strip()))
    return out
