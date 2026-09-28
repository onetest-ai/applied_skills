"""Claim parser — the one claim model, shared by base, pack, check-file, merge and accept.

A section body (the Markdown under one `## Title {#id}` heading, or an agent's
drafted `work/<task>/sections/<sid>.md`) is a sequence of **blocks**:

  - `bullet`  -- one `- ...` list item (each item is its own claim)
  - `para`    -- a blank-line-delimited paragraph (its lines joined with a space)
  - `code`    -- a fenced ```` ``` ```` block (e.g. a mermaid diagram) — never a claim
  - `not_modeled` -- a paragraph/bullet whose text starts with "Not modeled:"

A **claim** is a `bullet` or `para` block. Every claim block carries:
  - `tags`: the citation tags found in it, verbatim as written (`"[RAG:123]"`, ...)
  - `claim_id` / `sup_ref` / `origin`: parsed from a trailing
    `<!-- c:xxxxxxxx( sup=c:yyyyyyyy)?( origin=human|human_modified)? -->`
    comment, if the text already has one (present in base.md / carried claims;
    absent on a freshly drafted claim until `merge` assigns one). `origin`
    marks a human-authored claim; it is written only by
    `base` (docx recovery) and `merge`, never trusted from an agent's draft.
  - `superseded` / `superseded_date`: set when the text starts with the
    `**Superseded (<date>):**` marker
  - `normalized`: text with tags, the claim-id comment and the superseded
    marker stripped, whitespace collapsed, lowercased — used for id
    assignment (`assign_claim_id`) and cross-version similarity matching

`text` on every block is the content with the trailing id comment removed but
tags/marker intact — this is what gets re-emitted (with a possibly new id
comment appended) when a claim's id is (re)assigned.

**Tag bodies are unescaped on read.** A docx round trip
(pandoc docx -> gfm) backslash-escapes Markdown punctuation, so a tag can come
back as `[FILE:a \\_ b.docx#Page 1 \\> Page 2]`. `tags_in`/`parse_tag` return, and
`text`/`content` carry, every tag with its body unescaped
(`unescape_tag_body`), so an already-published file with escaped tags is read
as its original tag and anything re-emitted from `text` (merge) is written
unescaped. `raw` stays byte-exact.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any

# A tag body may contain backslash escapes (`\]` included) from a docx round trip.
TAG_RE = re.compile(r"\[(RAG|MART|GRAPH|FILE|TASK):((?:\\.|[^\]\\])+)\]")
HUMAN_ORIGINS = ("human", "human_modified")
CLAIM_ID_RE = re.compile(
    r"<!--\s*c:([0-9a-f]{8})(?:\s+sup=c:([0-9a-f]{8}))?(?:\s+origin=(human|human_modified))?\s*-->\s*$"
)
# Every ASCII punctuation escape except `\]`: a tag body can never hold a bare
# `]` (it would end the tag), so `\]` stays escaped to keep the tag parseable.
_MD_ESCAPE_RE = re.compile(r"\\([!-/:-@\[\\^-`{-~])")
SUPERSEDED_RE = re.compile(r"^\*\*Superseded \(([^)]+)\):\*\*\s*")
# Any HTML comment, anywhere (claim-id comments, origin markers, ...): never
# part of what a reader sees, so never part of what counts as a "visible change".
_COMMENT_ANY_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Typographic characters pandoc's Markdown-smart writer substitutes for their
# plain ASCII originals (curly quotes, guillemet-style low quotes, en/em
# dashes, the ellipsis glyph, non-breaking space) — folded away wherever text
# is compared to a human, or hashed for a claim id, so a smart-typography
# round trip through a docx is never mistaken for a human edit (defect: a
# rendered docx turns `Bain's` into `Bain’s`, and a pre-fold `normalize_text`
# read every such claim as `human_modified`). `visible_text` and
# `normalize_text` both call `fold_typography` so they cannot drift apart.
_TYPOGRAPHY = str.maketrans({
    "‘": "'", "’": "'", "‚": "'",   # ' ' ,
    "“": '"', "”": '"', "„": '"',   # " " „
    "–": "-", "—": "-",                   # – —
    "…": "...",                                 # …
    " ": " ",                                   # nbsp
})
_QUOTES = _TYPOGRAPHY  # back-compat alias; prefer fold_typography
NOT_MODELED_RE = re.compile(r"^Not modeled:", re.IGNORECASE)
FENCE_RE = re.compile(r"^```(\S*)\s*$")
# A draft claim matches an unmatched base claim at this normalized-text similarity.
MATCH_RATIO = 0.9


def unescape_tag_body(value: str) -> str:
    """Undo Markdown backslash escapes (`\\_` `\\>` `\\*` `\\#` `\\[` `\\(` `\\)`
    `\\\\` `\\``, any ASCII punctuation per CommonMark except `\\]`) inside a tag body."""
    return _MD_ESCAPE_RE.sub(r"\1", value)


def unescape_tags(text: str) -> str:
    """`text` with every tag's body unescaped; everything outside tags untouched."""
    return TAG_RE.sub(lambda m: f"[{m.group(1)}:{unescape_tag_body(m.group(2))}]", text)


def tags_in(text: str) -> list[str]:
    """`["[RAG:123]", "[FILE:a.md#L1]", ...]` — tags in order, no de-dup, bodies unescaped."""
    return [f"[{kind}:{unescape_tag_body(value)}]" for kind, value in TAG_RE.findall(text)]


def parse_tag(tag: str) -> tuple[str, str]:
    """`"[FILE:path#loc]"` -> `("FILE", "path#loc")` (body unescaped). Raises ValueError if malformed."""
    m = TAG_RE.fullmatch(tag)
    if not m:
        raise ValueError(f"not a recognised citation tag: {tag!r}")
    return m.group(1), unescape_tag_body(m.group(2))


def base_claim_origin(block: dict[str, Any]) -> str | None:
    """Origin of a claim in a BASE or PUBLISHED document (base.md, `_src/vNNN.md`,
    next.md): the comment's `origin=`, else `"human"` for a claim with no id at
    all — in those documents an id-less claim can only be a human addition
    recovered from an edited docx by a pre-Task-8 `base` (merge ids every
    drafted claim). Never call this on an agent's draft."""
    if block.get("origin"):
        return block["origin"]
    if is_claim(block) and not block.get("claim_id"):
        return "human"
    return None


def fold_typography(text: str) -> str:
    """Fold curly quotes, en/em dashes, the ellipsis glyph and nbsp to the
    plain ASCII a person would not notice the difference from. Shared by
    `visible_text` and `normalize_text` so they cannot drift apart.

    Runs of two or three ASCII hyphens (`--`/`---`, the literal source
    spelling of an en/em dash that pandoc's smart typography would render as
    a single dash glyph) collapse to one `-` too, so a claim written either
    way compares equal to its rendered-and-recovered self."""
    text = re.sub(r"-{2,3}", "-", text)
    return text.translate(_TYPOGRAPHY)


def visible_text(md: str) -> str:
    """What a human actually sees, for the noop test (spec A9): strip every
    HTML comment (claim-id/origin comments included — a re-minted id or a
    flipped `origin=` is not a visible change), unescape Markdown backslash
    escapes, fold curly quotes to straight ones, collapse whitespace runs to
    one space, strip. Two Markdown strings with the same `visible_text` read
    identically to a person; only that equality may back a "nothing changed"
    verdict."""
    text = _COMMENT_ANY_RE.sub("", md)
    text = fold_typography(_MD_ESCAPE_RE.sub(r"\1", text))
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(content: str) -> str:
    """Strip tags + unescape Markdown backslash escapes + fold typography
    (curly quotes/dashes/ellipsis/nbsp -> plain ASCII, same fold as
    `visible_text` — a smart-typography docx round trip must never register
    as a human edit) + collapse whitespace + lowercase. `content` should
    already have any trailing claim-id comment and leading bullet marker
    removed."""
    stripped = TAG_RE.sub("", content)
    stripped = SUPERSEDED_RE.sub("", stripped.strip())
    stripped = fold_typography(_MD_ESCAPE_RE.sub(r"\1", stripped))
    return re.sub(r"\s+", " ", stripped).strip().lower()


def assign_claim_id(task_id: str, section_id: str, normalized: str) -> str:
    """`sha256(task|section|normalized)[:8]` — per the contract, verbatim."""
    payload = f"{task_id}|{section_id}|{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _make_block(kind: str, raw: str) -> dict[str, Any]:
    text = raw
    claim_id: str | None = None
    sup_ref: str | None = None
    origin: str | None = None
    m = CLAIM_ID_RE.search(text)
    if m:
        claim_id, sup_ref, origin = m.group(1), m.group(2), m.group(3)
        text = CLAIM_ID_RE.sub("", text).rstrip()
    if kind != "code":
        text = unescape_tags(text)

    content = text
    if kind == "bullet" and content.lstrip().startswith("- "):
        content = content.lstrip()[2:]

    superseded = False
    superseded_date = None
    sup_m = SUPERSEDED_RE.match(content.strip())
    if sup_m and kind != "code":
        superseded = True
        superseded_date = sup_m.group(1)

    is_not_modeled = kind != "code" and bool(NOT_MODELED_RE.match(content.strip()))
    if is_not_modeled:
        kind = "not_modeled"

    tags = tags_in(content) if kind != "code" else []
    normalized = normalize_text(content) if kind != "code" else ""

    return {
        "kind": kind,
        "raw": raw,
        "text": text,
        "content": content,
        "tags": tags,
        "claim_id": claim_id,
        "sup_ref": sup_ref,
        "origin": origin,
        "superseded": superseded,
        "superseded_date": superseded_date,
        "normalized": normalized,
    }


def parse_blocks(body: str) -> list[dict[str, Any]]:
    """Split a section body into blocks, in document order."""
    lines = body.splitlines()
    blocks: list[dict[str, Any]] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence_m = FENCE_RE.match(line)
        if fence_m:
            j = i + 1
            while j < n and lines[j].strip() != "```" and not lines[j].startswith("```"):
                j += 1
            end = j + 1 if j < n else j
            raw = "\n".join(lines[i:end])
            blocks.append(_make_block("code", raw))
            i = end
            continue
        j = i
        group: list[str] = []
        while j < n and lines[j].strip() and not FENCE_RE.match(lines[j]):
            group.append(lines[j])
            j += 1
        is_bullet_group = bool(group) and all(gl.strip().startswith("- ") for gl in group)
        if is_bullet_group:
            for gl in group:
                blocks.append(_make_block("bullet", gl.strip()))
        else:
            blocks.append(_make_block("para", " ".join(gl.strip() for gl in group)))
        i = j
    return blocks


def is_claim(block: dict[str, Any]) -> bool:
    return block["kind"] in ("bullet", "para")


def claim_comment(claim_id: str, sup_ref: str | None = None, origin: str | None = None) -> str:
    """`<!-- c:xxxx( sup=c:yyyy)?( origin=...)? -->`."""
    return (
        f"<!-- c:{claim_id}"
        + (f" sup=c:{sup_ref}" if sup_ref else "")
        + (f" origin={origin}" if origin else "")
        + " -->"
    )


def render_claim(
    block: dict[str, Any], claim_id: str | None, sup_ref: str | None = None, origin: str | None = None
) -> str:
    """Re-emit a claim block's `text` (or `raw`, for a non-claim block) with a
    (possibly new) trailing `<!-- c:xxxx( sup=c:yyyy)?( origin=...)? -->` comment."""
    if block["kind"] == "code":
        return block["raw"]
    body = block["text"]
    if claim_id is None:
        return body
    return f"{body} {claim_comment(claim_id, sup_ref, origin)}"


def render_blocks(rendered: list[tuple[dict[str, Any], str]]) -> str:
    """`[(block, rendered_text), ...]` -> the section body text, joining
    consecutive bullets without a blank line and everything else with one."""
    lines: list[str] = []
    prev_kind: str | None = None
    for block, text in rendered:
        if lines and not (prev_kind == "bullet" and block["kind"] == "bullet"):
            lines.append("")
        lines.append(text)
        prev_kind = block["kind"]
    return ("\n".join(lines)).strip() + "\n" if lines else ""


def claim_key(block: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """`(normalized text, sorted tags)` — a claim is the same claim only when both
    are equal (spec A5): same text with different tags is a re-cite, not a keep."""
    return (block["normalized"], tuple(sorted(block["tags"])))


def mint_claim_id(task_id: str, section_id: str, normalized: str, used: set[str]) -> str:
    """`assign_claim_id`, re-hashed as `normalized#2`, `#3`, ... while the id is
    already taken in this section (two claims never share an id)."""
    cid, n = assign_claim_id(task_id, section_id, normalized), 1
    while cid in used:
        n += 1
        cid = assign_claim_id(task_id, section_id, f"{normalized}#{n}")
    return cid


def match_claims(
    base: list[dict[str, Any]], draft: list[dict[str, Any]], task_id: str, section_id: str
) -> list[dict[str, Any]]:
    """Match a section's drafted claim blocks to its base claim blocks — the one
    claim-continuity rule every command uses.

    Returns one `{"block", "claim_id", "category", "base"}` per draft CLAIM block,
    in draft order (non-claim blocks are skipped). A base claim is matched at most
    once:
      1. the draft block's own `<!-- c:x -->` id, if `x` is an unmatched base
         claim's id; any other agent-written id (invented, copied from elsewhere,
         already used) is ignored;
      2. else the unmatched base claim with the highest `SequenceMatcher` ratio
         over `normalized`, ties broken by base claim id ascending, accepted at
         `>= MATCH_RATIO`;
      3. else no base: a fresh id (`mint_claim_id`, never one already used in this section).
    A matched base claim with no id (a legacy human addition, before ids were
    persisted) gets a fresh id too. Category: `superseded` (the block became
    superseded this version), `kept` (equal `claim_key`), `recited` (equal text,
    different tags), `reworded` (ratio match, different text), `added` (no base).
    Base claims absent from every result's `base` were dropped."""
    unmatched: dict[str, dict[str, Any]] = {}
    for idx, b in enumerate(x for x in base if is_claim(x)):
        unmatched[b["claim_id"] or f"~noid{idx:06d}"] = b
    used = {k for k in unmatched if not k.startswith("~")}
    out: list[dict[str, Any]] = []
    for blk in (d for d in draft if is_claim(d)):
        key = blk.get("claim_id")
        if not (key and key in unmatched):
            ranked = sorted(
                (
                    (difflib.SequenceMatcher(None, blk["normalized"], b["normalized"]).ratio(), k)
                    for k, b in unmatched.items()
                ),
                key=lambda t: (-t[0], t[1]),
            )
            key = ranked[0][1] if ranked and ranked[0][0] >= MATCH_RATIO else None
        match = unmatched.pop(key) if key else None
        if match is not None:
            cid = match["claim_id"] or mint_claim_id(task_id, section_id, blk["normalized"], used)
            if blk["superseded"] and not match["superseded"]:
                cat = "superseded"
            elif claim_key(blk) == claim_key(match):
                cat = "kept"
            elif blk["normalized"] == match["normalized"]:
                cat = "recited"
            else:
                cat = "reworded"
        else:
            cid = mint_claim_id(task_id, section_id, blk["normalized"], used)
            cat = "added"
        used.add(cid)
        out.append({"block": blk, "claim_id": cid, "category": cat, "base": match})
    return out
