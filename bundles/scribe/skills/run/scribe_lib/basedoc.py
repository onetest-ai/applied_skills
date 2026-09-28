"""base: recover the next drafting base from the latest published version.

If the published docx's sha256 still matches `state.published.docx_sha256`
(no human edit since the last publish), the base is just the previous
`_src/vNNN.md` copied verbatim — it is already in the canonical Document
Markdown format (`## <Title> {#<section-id>}` headings, `<!-- c:xxxxxxxx -->`
claim-id comments), so nothing needs reconstructing. One exception: a claim
that version's lineage marks `origin: human|human_modified` but whose comment
lacks `origin=` (published before origin was persisted in the comment) gets it added
(`_restore_legacy_origins`).

If the docx sha differs but every recovered section reads identically to the
previous published version once comments/escapes/quotes/whitespace are
normalized away (`claims.visible_text` — spec A9, PoC finding I1: a Word
"open and save" round-trip changes bytes, not visible content), this is not a
human edit either: fall back to the previous `_src` text verbatim, same as
the unedited path (`base_edited: false`, no `human_added`/`human_modified`).

Otherwise a human edited the published file directly (spec A8), and the
base has to be recovered from it:
  1. `pandoc -f docx -t gfm --wrap=none --track-changes=accept` to Markdown:
     tracked insertions are accepted, tracked deletions and Word comments
     dropped.
  2. Footnote -> tag: a citation tag was rendered (by `render`) as a
     footnote whose text begins with the tag, e.g. `RAG:123 — <source label>`
     (tag = the footnote text up to the first " — "). Each `[^N]` reference is
     replaced by ` [<tag>]` at its point of use; footnote *definitions* are
     dropped once absorbed. Tag bodies are unescaped (pandoc's gfm writer
     backslash-escapes `_`, `>`, `*`, ... in footnote text).
  3. Headings -> section ids: pandoc's docx round-trip does not preserve the
     `{#section-id}` attribute, so a level-2 heading is matched to a section by
     TITLE (params substituted; case- and whitespace-insensitive). Every H2
     must be a template section title or the script-owned "Changes in this
     version", and every section of the previous version must still have its
     heading; otherwise `base` REFUSES: it returns `{"status": "failed",
     "reason": "section_heading_changed", "headings": [unmatched titles]}`
     (plus `"missing": [titles]` for deleted headings), removes any old
     base.md/base.json, and writes nothing. Dropping the text would lose a
     human's content; a missing section would fail `accept` every night.
     Before that, structure the base cannot hold is refused the same way
     with `{"status": "failed", "reason": "unsupported_structure", "detail":
     [...]}`: any heading not at level 2 (except the one H1 title) and any
     text before the first section (`_structure_problems`). This runs before
     the re-save check, so such an edit is never read as a plain re-save.
  4. Claims matched per section against the previous `_src/vNNN.md` (parsed by
     `{#id}`), on `claims.normalize_text` — tags stripped, so a long tag can
     never make an edit look unchanged (PoC I2); see `_carry_claims`:
       - exact normalized match -> carried unchanged, previous origin kept;
       - else best ratio >= 0.6 against an unmatched previous claim (ties by
         id) -> `human_modified`, id kept, however small the change;
         `origin=human_modified` (a previously `human` claim stays `human`);
       - else -> `human_added`, a fresh id (`claims.mint_claim_id`, unique in
         the section) and `origin=human`. The id is minted here, not in merge,
         because a carried section is copied byte-for-byte from base.md;
       - a previous claim left unmatched -> a `human_deleted` tombstone, which
         `merge` uses to drop the claim if the agent drafts it again.
     A previous id-less claim (a legacy human addition) matched gets a fresh id.
  5. Diagrams come from the previous `_src`, never from the docx (Word cannot
     edit mermaid source; the round trip yields `<figure><img>`, PoC I9):
     recovered images/figures/code units are dropped and every previous fenced
     block is re-inserted after the claim that preceded it (at the end if that
     claim is gone).

Writes `work/<task>/base.md` (canonical format, `{#id}` headings) and
`work/<task>/base.json`:

    {
      "base_version": <int> | null,       # null only when the task has never published
      "base_edited": bool,
      "human_added":    [{"section", "claim_id", "text"}],
      "human_modified": [{"section", "claim_id", "text"}],
      "human_deleted":  [{"section", "claim_id", "normalized"}]
    }

`text` fields are normalized (whitespace-collapsed) and truncated to 200
chars — base.json is a diagnostic/audit artifact, not the base content
itself (that's base.md).
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from scribe_lib import claims
from scribe_lib.config import Config, ScribeError, read_state, sha256_file, substitute_params

_TEXT_TRUNCATE = 200

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_ID_HEADING_RE = re.compile(r"^#{1,6}\s+.*\{#([\w-]+)\}\s*$")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:\s?(.*)$")
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\]")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _docx_to_gfm(path: Path) -> str:
    proc = subprocess.run(
        # --track-changes=accept: a tracked insertion is kept, a tracked
        # deletion and every Word comment are dropped (spec A8).
        ["pandoc", "-f", "docx", "-t", "gfm", "--wrap=none", "--track-changes=accept", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ScribeError(f"pandoc failed converting {path}: {proc.stderr.strip()}")
    return proc.stdout


def _extract_footnotes(gfm: str) -> tuple[str, dict[str, str]]:
    """Split gfm into (body without footnote definitions, {num: tag})."""
    lines = gfm.splitlines()
    defs: dict[str, str] = {}
    body_lines: list[str] = []
    i = 0
    while i < len(lines):
        m = _FOOTNOTE_DEF_RE.match(lines[i])
        if m:
            num, rest = m.group(1), m.group(2)
            j = i + 1
            while j < len(lines) and (lines[j].startswith("    ") or not lines[j].strip()):
                if lines[j].strip():
                    rest += " " + lines[j].strip()
                j += 1
            defs[num] = claims.unescape_tag_body(rest.split(" — ", 1)[0].strip())
            i = j
            continue
        body_lines.append(lines[i])
        i += 1
    return "\n".join(body_lines), defs


def _reinsert_tags(body: str, defs: dict[str, str]) -> str:
    def _sub(m: re.Match[str]) -> str:
        tag = defs.get(m.group(1))
        return f" [{tag}]" if tag else m.group(0)

    return _FOOTNOTE_REF_RE.sub(_sub, body)


def split_sections(text: str) -> list[tuple[int, str, str]]:
    """[(heading level, title, section body)] split at every Markdown heading."""
    out: list[tuple[int, str, str]] = []
    level: int | None = None
    title: str | None = None
    buf: list[str] = []
    for ln in text.splitlines():
        m = _HEADING_RE.match(ln)
        if m:
            if title is not None:
                out.append((level, title, "\n".join(buf).strip()))
            level = len(m.group(1))
            title = m.group(2).strip()
            buf = []
        else:
            buf.append(ln)
    if title is not None:
        out.append((level, title, "\n".join(buf).strip()))
    return out


def split_by_section_id(text: str) -> dict[str, str]:
    """{section id: body} from a canonical `## Title {#id}`-headed document."""
    out: dict[str, str] = {}
    sid: str | None = None
    buf: list[str] = []
    for ln in text.splitlines():
        m = _ID_HEADING_RE.match(ln)
        if m:
            if sid is not None:
                out[sid] = "\n".join(buf).strip()
            sid = m.group(1)
            buf = []
        else:
            buf.append(ln)
    if sid is not None:
        out[sid] = "\n".join(buf).strip()
    return out


def _unit(block: dict[str, Any], with_ids: bool) -> dict[str, Any]:
    """A `claims.parse_blocks` claim or `Not modeled:` block as a unit:
    `{kind: "para"|"bullet", text, claim_id, sup_ref, origin}`. `text` is the
    block's `content` (tags unescaped, id comment and bullet marker removed),
    whitespace-collapsed."""
    kind = block["kind"]
    if kind == "not_modeled":
        kind = "bullet" if block["raw"].lstrip().startswith("- ") else "para"
    unit = {"kind": kind, "text": _normalize(block["content"]), "claim_id": None, "sup_ref": None, "origin": None}
    if with_ids:
        unit.update(claim_id=block["claim_id"], sup_ref=block["sup_ref"], origin=claims.base_claim_origin(block))
    return unit


def _section_items(text: str, with_ids: bool) -> list[dict[str, Any]]:
    """`text`'s blocks in order: a unit per claim/`Not modeled:` block, and a
    `{"kind": "code", "raw": ...}` item per fenced code block (carried verbatim,
    never a unit or a claim)."""
    return [
        {"kind": "code", "raw": b["raw"]} if b["kind"] == "code" else _unit(b, with_ids)
        for b in claims.parse_blocks(text)
    ]


def _strip_table_delimiter_rows(text: str) -> str:
    """`text` with every GFM table delimiter/alignment row dropped."""
    return "\n".join(ln for ln in text.splitlines() if not _TABLE_DELIM_LINE_RE.match(ln))


def _resave_units(text: str, with_ids: bool) -> list[str]:
    """`claims.visible_text` of every claim/prose unit in `text`, in order,
    comments/escapes/quotes/whitespace normalized and table delimiter rows
    dropped first — code/diagram units are skipped entirely, never compared:
    a docx round trip cannot recover a fenced diagram's source text (it comes
    back as an `<img>`/`<figure>` placeholder, `_DIAGRAM_PLACEHOLDER_RE`;
    the edited-recovery path sources it from the previous `_src` instead,
    `_carry_claims`), so its absence/rewrite must never by itself make a
    plain re-save look edited. Used only by the resave-detection check in
    `base_task`."""
    items = _section_items(_strip_table_delimiter_rows(text), with_ids)
    out: list[str] = []
    for u in items:
        if u["kind"] == "code":
            continue
        vt = claims.visible_text(u["text"])
        if _DIAGRAM_PLACEHOLDER_RE.match(vt.strip()):
            continue
        out.append(vt)
    return out


def parse_units(text: str, with_ids: bool) -> list[dict[str, Any]]:
    """[{kind: "para"|"bullet", text, claim_id, sup_ref, origin}] — the paragraphs/
    bullets in `text`, parsed by `claims.parse_blocks` (the one claim parser):
    each `- ` item is its own bullet unit, fenced code blocks (e.g. a mermaid
    diagram) are never units. `with_ids=True` captures the trailing
    `<!-- c:xxxxxxxx( sup=c:yyyyyyyy)?( origin=...)? -->` comment (used for the
    previous version's units; an id-less previous claim gets `origin: "human"`,
    see `claims.base_claim_origin`); `with_ids=False` ignores it (used for
    freshly recovered docx text, which never carries those comments).
    """
    return [u for u in _section_items(text, with_ids) if u["kind"] != "code"]


def _is_not_modeled(unit: dict[str, Any]) -> bool:
    return bool(claims.NOT_MODELED_RE.match(unit["text"]))


def _is_image(unit: dict[str, Any]) -> bool:
    """A recovered unit that is a rendered diagram/image or raw HTML figure —
    what a docx round trip makes of a fenced diagram. Never a claim."""
    return unit["text"].lstrip().startswith(_IMAGE_PREFIXES)


def _carry_claims(
    new_items: list[dict[str, Any]], prev_items: list[dict[str, Any]], task_id: str, sid: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Match one section's recovered docx units against the previous version's
    (spec A8, PoC I2). Returns `(items to render, human_added, human_modified,
    human_deleted)`.

    Matching is on `claims.normalize_text` (tags stripped, so a long tag can
    never make an edit look unchanged): first every exact normalized match
    (carried unchanged, previous origin kept), then, for the rest, the
    unmatched previous claim with the best ratio >= `_MODIFIED_RATIO` (ties by
    claim id) — `human_modified`, id kept, whatever the size of the change.
    No match -> `human_added` with a fresh id, minted unique within the
    section (`claims.mint_claim_id`). A previous claim left unmatched was
    deleted by a person -> a `human_deleted` tombstone.

    Diagrams come from the previous version, never the docx (Word cannot edit
    mermaid source, PoC I9): recovered images/figures and code units are
    dropped, and each previous fenced block is re-inserted after the claim
    that preceded it there (first, if none did; at the end, if that claim is
    gone)."""
    new_claims: list[dict[str, Any]] = []
    kept_items: list[dict[str, Any]] = []
    for u in new_items:
        if u["kind"] == "code" or _is_image(u):
            continue
        u["sup_ref"], u["origin"], u["claim_id"] = None, None, None
        kept_items.append(u)
        if not _is_not_modeled(u):  # a `Not modeled:` line is not a claim: no id, no origin
            new_claims.append(u)

    # Previous claims, and each previous fence's anchor (index of the claim before it).
    prev_claims: list[dict[str, Any]] = []
    fences_after: dict[int, list[dict[str, Any]]] = {}
    for p in prev_items:
        if p["kind"] == "code":
            fences_after.setdefault(len(prev_claims) - 1, []).append(p)
        elif not _is_not_modeled(p):
            prev_claims.append(p)

    norm_new = [claims.normalize_text(u["text"]) for u in new_claims]
    norm_prev = [claims.normalize_text(p["text"]) for p in prev_claims]
    prev_key = {i: p["claim_id"] or f"~noid{i:06d}" for i, p in enumerate(prev_claims)}
    unmatched = sorted(prev_key, key=lambda i: prev_key[i])  # tie order: claim id ascending
    match: dict[int, int] = {}
    for ni, n in enumerate(norm_new):
        pi = next((i for i in unmatched if norm_prev[i] == n), None)
        if pi is not None:
            match[ni] = pi
            unmatched.remove(pi)
    for ni, n in enumerate(norm_new):
        if ni in match or not unmatched:
            continue
        ratio, _, pi = min(
            ((difflib.SequenceMatcher(None, n, norm_prev[i]).ratio(), prev_key[i], i) for i in unmatched),
            key=lambda t: (-t[0], t[1]),
        )
        if ratio >= _MODIFIED_RATIO:
            match[ni] = pi
            unmatched.remove(pi)

    used = {p["claim_id"] for p in prev_claims if p["claim_id"]}
    human_added: list[dict[str, Any]] = []
    human_modified: list[dict[str, Any]] = []
    for ni, u in enumerate(new_claims):
        pi = match.get(ni)
        prev = prev_claims[pi] if pi is not None else None
        u["claim_id"] = (prev or {}).get("claim_id") or claims.mint_claim_id(task_id, sid, norm_new[ni], used)
        used.add(u["claim_id"])
        entry = {"claim_id": u["claim_id"], "text": u["text"][:_TEXT_TRUNCATE]}
        if prev is None:
            u["origin"] = "human"
            human_added.append(entry)
            continue
        u["sup_ref"] = prev.get("sup_ref")
        if norm_new[ni] == norm_prev[pi]:
            u["origin"] = prev.get("origin")
        else:
            u["origin"] = "human" if prev.get("origin") == "human" else "human_modified"
            human_modified.append(entry)

    human_deleted = [
        {"section": sid, "claim_id": prev_claims[i]["claim_id"], "normalized": norm_prev[i]}
        for i in sorted(unmatched)
        if prev_claims[i]["claim_id"]
    ]

    # Re-insert the previous version's fences.
    matched_prev_of = {id(new_claims[ni]): pi for ni, pi in match.items()}
    placed: set[int] = set()
    out: list[dict[str, Any]] = list(fences_after.get(-1, []))
    placed.add(-1)
    for u in kept_items:
        out.append(u)
        pi = matched_prev_of.get(id(u))
        if pi is not None and pi in fences_after:
            out.extend(fences_after[pi])
            placed.add(pi)
    for anchor in sorted(fences_after):
        if anchor not in placed:
            out.extend(fences_after[anchor])
    return out, human_added, human_modified, human_deleted


def _render_units(units: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    prev_kind: str | None = None
    for u in units:
        if u["kind"] == "code":
            if lines:
                lines.append("")
            lines.append(u["raw"])
            prev_kind = "code"
            continue
        tag = (
            " " + claims.claim_comment(u["claim_id"], u.get("sup_ref"), u.get("origin"))
            if u["claim_id"]
            else ""
        )
        if lines and (u["kind"] != prev_kind or u["kind"] == "para"):
            lines.append("")
        if u["kind"] == "bullet":
            lines.append(f"- {u['text']}{tag}")
        else:
            lines.append(f"{u['text']}{tag}")
        prev_kind = u["kind"]
    return "\n".join(lines)


# A recovered claim matching an unmatched previous claim at or above this
# normalized-text ratio is that claim, human-modified; below it, a human
# addition (spec A8: the threshold only separates modified from added).
_MODIFIED_RATIO = 0.6
_IMAGE_PREFIXES = ("![", "<img", "<figure")
_CHANGES_HEADING = "changes in this version"

_ID_ONLY_COMMENT_RE = re.compile(r"<!--\s*c:([0-9a-f]{8})(\s+sup=c:[0-9a-f]{8})?\s*-->\s*$")
# A GFM table delimiter/alignment row (`| --- | :---: |`) — pandoc is free to
# change dash counts and add/drop alignment colons on its own round trip; the
# row never renders as reader-visible text, so it must never make a plain
# re-save look edited (review fix round 1, Important 1).
_TABLE_DELIM_LINE_RE = re.compile(r"^\s*\|?(?:\s*:?-{1,}:?\s*\|)*\s*:?-{1,}:?\s*\|?\s*$")
# What a rendered fenced diagram (a mermaid ```` ```mermaid ```` fence,
# `render.py` converts it to an embedded PNG before pandoc sees it) comes
# back as after a docx round trip: pandoc's gfm writer emits an HTML
# `<figure>...<img .../>...</figure>` block for a captioned image, or a bare
# `<img>`/Markdown `![alt](path)` for an uncaptioned one. None of these are a
# fenced code block, so `claims.parse_blocks` would otherwise read one as an
# ordinary prose unit with no counterpart in the previous `_src` (whose
# section still holds the literal ```` ```mermaid ```` fence, a "code" unit
# `_resave_units` already skips) — review fix round 1, Important 1.
_DIAGRAM_PLACEHOLDER_RE = re.compile(
    r"^(?:<figure\b.*</figure>|<img\b[^>]*/?>|!\[[^\]]*\]\([^)]*\)(?:\{[^}]*\})?)\s*$", re.DOTALL
)


def _restore_legacy_origins(text: str, lineage_path: Path) -> str:
    """A version published before origin was persisted in the comment recorded a human_modified claim's
    origin only in its lineage (from that run's base.json), not in the claim
    comment. Put it back into the comment of each such claim — matched by
    claim id, only where the comment has no `origin=` yet — so the status
    survives from here on. Id-less human additions need nothing here (see
    `claims.base_claim_origin`). No lineage file, or nothing to restore ->
    `text` unchanged."""
    if not lineage_path.is_file():
        return text
    try:
        nodes = json.loads(lineage_path.read_text(encoding="utf-8")).get("nodes") or []
    except (OSError, ValueError):
        return text
    origins = {
        n["claim_id"]: n["origin"]
        for n in nodes
        if n.get("type") == "claim" and n.get("claim_id") and n.get("origin") in claims.HUMAN_ORIGINS
    }
    if not origins:
        return text

    def _fix(line: str) -> str:
        m = _ID_ONLY_COMMENT_RE.search(line)
        if not m or m.group(1) not in origins:
            return line
        return line[: m.start()] + f"<!-- c:{m.group(1)}{m.group(2) or ''} origin={origins[m.group(1)]} -->"

    return "\n".join(_fix(ln) for ln in text.split("\n"))


def _src_dir(config: Config, instance: dict[str, Any]) -> Path:
    return config.out_root / instance["out"] / "_src"


def _published_docx_path(config: Config, instance: dict[str, Any]) -> Path:
    return config.out_root / instance["out"] / f"{instance['title']}.docx"


_TEXT_BEFORE_SECTIONS = "text before the first section"


def _structure_problems(body: str) -> list[str]:
    """What in the recovered docx body the base cannot hold: every heading
    that is not level 2, except a first H1 (the document title) that comes
    before any section, and `_TEXT_BEFORE_SECTIONS` when anything but blank
    lines precedes the first H2. Each offending heading as `"H<n>: <title>"`."""
    problems: list[str] = []
    seen_h2 = seen_title = text_before = False
    for ln in body.splitlines():
        m = _HEADING_RE.match(ln)
        if not m:
            if not seen_h2 and ln.strip():
                text_before = True
            continue
        level, title = len(m.group(1)), m.group(2).strip()
        if level == 2:
            seen_h2 = True
        elif level == 1 and not seen_h2 and not seen_title:
            seen_title = True
        else:
            problems.append(f"H{level}: {title}")
    if text_before:
        problems.insert(0, _TEXT_BEFORE_SECTIONS)
    return problems


def _base_result(version: int | None, edited: bool = False, **lists: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "base_version": version,
        "base_edited": edited,
        "human_added": lists.get("human_added", []),
        "human_modified": lists.get("human_modified", []),
        "human_deleted": lists.get("human_deleted", []),
    }


def _heading_key(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def base_task(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
) -> dict[str, Any]:
    state = read_state(config, instance)
    work_dir = config.work_dir / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    base_md_path = work_dir / "base.md"
    base_json_path = work_dir / "base.json"

    def _write(text: str, result: dict[str, Any]) -> dict[str, Any]:
        base_md_path.write_text(text, encoding="utf-8")
        base_json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    def _refuse(failed: dict[str, Any]) -> dict[str, Any]:
        base_md_path.unlink(missing_ok=True)
        base_json_path.unlink(missing_ok=True)
        return failed

    version = state.get("version")
    if not state or not version:
        return _write("", _base_result(None))

    src_dir = _src_dir(config, instance)
    prev_src = src_dir / f"v{version:03d}.md"
    docx_path = _published_docx_path(config, instance)
    published_sha = (state.get("published") or {}).get("docx_sha256")
    # Restored once, for every path (review fix round 1, Important 3):
    # `_carry_claims` reads `origin` straight off the previous units, so an
    # unrestored text would silently drop a legacy `human_modified` origin the
    # moment the next visible change arrives as a docx edit.
    prev_text_restored = (
        _restore_legacy_origins(prev_src.read_text(encoding="utf-8"), src_dir / f"v{version:03d}.lineage.json")
        if prev_src.is_file()
        else ""
    )

    edited = bool(docx_path.is_file() and published_sha and sha256_file(docx_path) != published_sha)
    if not edited:
        return _write(prev_text_restored, _base_result(version))

    gfm = _docx_to_gfm(docx_path)
    body, footnote_defs = _extract_footnotes(gfm)
    body = _reinsert_tags(body, footnote_defs)

    params = instance.get("params") or {}
    sections_spec = [substitute_params(s, params) for s in (template.get("output") or {}).get("sections") or []]
    title_to_sid = {_heading_key(s["title"]): s["id"] for s in sections_spec}
    prev_sections = split_by_section_id(prev_text_restored)

    # Structure the base cannot represent is refused loudly, never dropped
    # (fix round 1): a heading at any level but 2 (other than the one H1 title)
    # and any text before the first section. Dropped silently, such content
    # vanished, turned the claims after it into tombstones, or made the edit
    # read as a plain re-save — so this runs before the re-save check.
    problems = _structure_problems(body)
    if problems:
        return _refuse({"status": "failed", "reason": "unsupported_structure", "detail": problems})

    docx_sections: dict[str, str] = {}
    unmatched_headings: list[str] = []
    for level, title, sec_body in split_sections(body):
        if level != 2:
            continue
        sid = title_to_sid.get(_heading_key(title))
        if sid is not None:
            docx_sections[sid] = sec_body
        elif _heading_key(title) != _CHANGES_HEADING:  # script-owned, on every rendered docx
            unmatched_headings.append(title)

    # A8 / I2: a renamed or deleted section heading is refused loudly, never
    # dropped — keeping the text out of the base would lose a human's content,
    # and a missing section would fail `accept` every night. A section absent
    # from the previous version too (new in the template) is not "deleted".
    missing = [s["title"] for s in sections_spec if s["id"] not in docx_sections and s["id"] in prev_sections]
    if unmatched_headings or missing:
        failed: dict[str, Any] = {"status": "failed", "reason": "section_heading_changed", "headings": unmatched_headings}
        if missing:
            failed["missing"] = missing
        return _refuse(failed)

    # A9 / I1: a Word "open and save" (pandoc/Word re-serializes punctuation,
    # quotes, whitespace, table delimiter rows; a diagram round-trips as an
    # image, never as recoverable fence text) changes the docx's bytes
    # without a human changing anything a reader would see. If every
    # section's claim/prose units read identically to the previous published
    # version once comments/escapes/quotes/whitespace/table-delimiter noise
    # are normalized away (code/diagram units skipped — `_resave_units`),
    # this is not a human edit — fall back to the previous `_src` text
    # verbatim rather than recording a spurious human_added/human_modified
    # claim from round-trip noise.
    if all(
        _resave_units(docx_sections.get(sec["id"], ""), False) == _resave_units(prev_sections.get(sec["id"], ""), True)
        for sec in sections_spec
    ):
        return _write(prev_text_restored, _base_result(version))

    lists: dict[str, list[dict[str, Any]]] = {"human_added": [], "human_modified": [], "human_deleted": []}
    out_lines: list[str] = []
    for sec in sections_spec:
        sid = sec["id"]
        if sid not in docx_sections:
            continue
        new_items = _section_items(docx_sections[sid], with_ids=False)
        prev_items = _section_items(prev_sections.get(sid, ""), with_ids=True)
        assigned, added, modified, deleted = _carry_claims(new_items, prev_items, task_id, sid)
        for entry in added + modified:
            entry["section"] = sid
        lists["human_added"] += added
        lists["human_modified"] += modified
        lists["human_deleted"] += deleted
        out_lines += [f"## {sec['title']} {{#{sid}}}", "", _render_units(assigned), ""]

    base_md = ("\n".join(out_lines).strip() + "\n") if out_lines else ""
    return _write(base_md, _base_result(version, True, **lists))
