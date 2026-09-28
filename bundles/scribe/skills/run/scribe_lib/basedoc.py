"""base: recover the next drafting base from the latest published version.

If the published docx's sha256 still matches `state.published.docx_sha256`
(no human edit since the last publish), the base is just the previous
`_src/vNNN.md` copied verbatim — it is already in the canonical Document
Markdown format (`## <Title> {#<section-id>}` headings, `<!-- c:xxxxxxxx -->`
claim-id comments), so nothing needs reconstructing. One exception: a claim
that version's lineage marks `origin: human|human_modified` but whose comment
lacks `origin=` (published before origin was persisted in the comment) gets it added
(`_restore_legacy_origins`).

If the docx sha differs, a human edited the published file directly, and the
base has to be recovered from it:
  1. `pandoc -f docx -t gfm --wrap=none` to Markdown.
  2. Footnote -> tag: a citation tag was rendered (by `render`) as a
     footnote whose text begins with the tag, e.g. `RAG:123 — <source label>`
     (tag = the footnote text up to the first " — "). Each `[^N]` reference is
     replaced by ` [<tag>]` at its point of use; footnote *definitions* are
     dropped once absorbed.
  3. Headings -> section ids: pandoc's docx round-trip does not preserve the
     `{#section-id}` attribute (Word bookmarks aren't literal text), so a
     level-2 heading is matched to a section by TITLE against the task's
     template (case-insensitive). An unmatched heading is dropped from the
     base and listed under `unmatched_headings` in base.json — nothing else
     in this task's contract says what to do with it, and silently keeping an
     unrecognised heading out of the drafting base is the safer default.
  4. Claim ids carried by comparing each new paragraph/bullet in a section
     against the SAME section's units in the previous `_src/vNNN.md` (parsed
     by section id, via `split_by_section_id` — that file's headings DO carry
     `{#id}`, since we write it ourselves) using `difflib.SequenceMatcher`:
     ratio >= 0.9 -> carried unchanged; 0.6 <= ratio < 0.9 -> carried but
     recorded `human_modified`; below 0.6 (or no previous units left to match)
     -> recorded `human_added`. Each previous unit matches at most once
     (greedy, highest ratio first per new unit).
  5. Origin (a human-authored claim keeps that status until
     a human changes it again). It is persisted in the claim comment,
     `<!-- c:xxxxxxxx origin=human|human_modified -->`:
       - carried unchanged (>= 0.9) -> the previous unit's origin, read from
         its comment (a previous id-less unit is a legacy human addition,
         from before origin was persisted in the comment: it gets a fresh id
         and `origin=human`);
       - `human_modified` -> `origin=human_modified` (a previously `human`
         claim edited by a human again stays `human`);
       - `human_added` -> a fresh id `sha256(task|section|normalized)[:8]`
         and `origin=human`. The id is minted here, not in merge, because a
         section that is not stale is copied byte-for-byte from base.md into
         the next version — base.md is where it must already be persisted.
  Tag bodies recovered from footnotes are unescaped: pandoc's gfm writer
  backslash-escapes `_`, `>`, `*`, ... in footnote text.

Writes `work/<task>/base.md` (canonical format, `{#id}` headings) and
`work/<task>/base.json`:

    {
      "base_version": <int> | null,       # null only when the task has never published
      "base_edited": bool,
      "human_added":    [{"section", "claim_id", "text"}],
      "human_modified": [{"section", "claim_id", "text"}],
      "unmatched_headings": [...]          # present only in the docx-recovery path, only if non-empty
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
from scribe_lib.config import Config, ScribeError, read_state, sha256_file

_TEXT_TRUNCATE = 200

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_ID_HEADING_RE = re.compile(r"^#{1,6}\s+.*\{#([\w-]+)\}\s*$")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:\s?(.*)$")
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\]")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _docx_to_gfm(path: Path) -> str:
    proc = subprocess.run(
        ["pandoc", "-f", "docx", "-t", "gfm", "--wrap=none", str(path)],
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


def _split_claim_comment(text: str) -> tuple[str | None, str | None, str | None, str]:
    """(claim_id, sup_ref, origin, text without the comment, tags unescaped)."""
    m = claims.CLAIM_ID_RE.search(text)
    if m:
        return m.group(1), m.group(2), m.group(3), _normalize(claims.unescape_tags(text[: m.start()]))
    return None, None, None, _normalize(claims.unescape_tags(text))


def parse_units(text: str, with_ids: bool) -> list[dict[str, Any]]:
    """[{kind: "para"|"bullet", text, claim_id, sup_ref, origin}] — paragraphs/bullets in `text`.

    A paragraph whose every non-blank line starts with `- ` becomes one
    "bullet" unit per line; anything else becomes one "para" unit (its lines
    joined with a space). `with_ids=True` strips + captures a trailing
    `<!-- c:xxxxxxxx( sup=c:yyyyyyyy)?( origin=...)? -->` comment (used for the
    previous version's units; an id-less previous unit gets `origin: "human"`,
    see `claims.base_claim_origin`);
    `with_ids=False` just normalizes (used for freshly recovered docx text,
    which never carries those comments).
    """
    out: list[dict[str, Any]] = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        lines = [ln for ln in para.splitlines() if ln.strip()]
        bullet = bool(lines) and all(ln.strip().startswith("- ") for ln in lines)
        raws = [ln.strip()[2:].strip() for ln in lines] if bullet else [" ".join(ln.strip() for ln in lines)]
        for raw in raws:
            if with_ids:
                claim_id, sup_ref, origin, norm = _split_claim_comment(raw)
                if not claim_id and not claims.NOT_MODELED_RE.match(norm):
                    origin = "human"
            else:
                claim_id, sup_ref, origin, norm = None, None, None, _normalize(raw)
            out.append(
                {"kind": "bullet" if bullet else "para", "text": norm, "claim_id": claim_id,
                 "sup_ref": sup_ref, "origin": origin}
            )
    return out


def _is_not_modeled(unit: dict[str, Any]) -> bool:
    return bool(claims.NOT_MODELED_RE.match(unit["text"]))


def _fresh_id(task_id: str, sid: str, unit: dict[str, Any]) -> str:
    return claims.assign_claim_id(task_id, sid, claims.normalize_text(unit["text"]))


def _carry_claims(
    new_units: list[dict[str, Any]], prev_units: list[dict[str, Any]], task_id: str, sid: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pool = list(range(len(prev_units)))
    human_added: list[dict[str, Any]] = []
    human_modified: list[dict[str, Any]] = []
    for u in new_units:
        u["sup_ref"], u["origin"] = None, None
        if _is_not_modeled(u):
            # A `Not modeled:` line is not a claim: no id, no origin.
            u["claim_id"] = None
            continue
        best_idx, best_ratio = None, 0.0
        for idx in pool:
            ratio = difflib.SequenceMatcher(None, u["text"], prev_units[idx]["text"]).ratio()
            if ratio > best_ratio:
                best_ratio, best_idx = ratio, idx
        if best_idx is not None and best_ratio >= 0.9:
            prev = prev_units[best_idx]
            pool.remove(best_idx)
            u["claim_id"] = prev["claim_id"] or _fresh_id(task_id, sid, u)
            u["sup_ref"] = prev.get("sup_ref")
            u["origin"] = prev.get("origin")
        elif best_idx is not None and best_ratio >= 0.6:
            prev = prev_units[best_idx]
            pool.remove(best_idx)
            u["claim_id"] = prev["claim_id"] or _fresh_id(task_id, sid, u)
            u["sup_ref"] = prev.get("sup_ref")
            u["origin"] = "human" if prev.get("origin") == "human" else "human_modified"
            human_modified.append({"claim_id": u["claim_id"], "text": u["text"][:_TEXT_TRUNCATE]})
        else:
            u["claim_id"] = _fresh_id(task_id, sid, u)
            u["origin"] = "human"
            human_added.append({"claim_id": u["claim_id"], "text": u["text"][:_TEXT_TRUNCATE]})
    return new_units, human_added, human_modified


def _render_units(units: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    prev_kind: str | None = None
    for u in units:
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


_ID_ONLY_COMMENT_RE = re.compile(r"<!--\s*c:([0-9a-f]{8})(\s+sup=c:[0-9a-f]{8})?\s*-->\s*$")


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

    version = state.get("version")
    if not state or not version:
        base_md_path.write_text("", encoding="utf-8")
        result = {"base_version": None, "base_edited": False, "human_added": [], "human_modified": []}
        base_json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    src_dir = _src_dir(config, instance)
    prev_src = src_dir / f"v{version:03d}.md"
    docx_path = _published_docx_path(config, instance)
    published_sha = (state.get("published") or {}).get("docx_sha256")

    edited = bool(docx_path.is_file() and published_sha and sha256_file(docx_path) != published_sha)

    if not edited:
        text = prev_src.read_text(encoding="utf-8") if prev_src.is_file() else ""
        text = _restore_legacy_origins(text, src_dir / f"v{version:03d}.lineage.json")
        base_md_path.write_text(text, encoding="utf-8")
        result = {"base_version": version, "base_edited": False, "human_added": [], "human_modified": []}
        base_json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    gfm = _docx_to_gfm(docx_path)
    body, footnote_defs = _extract_footnotes(gfm)
    body = _reinsert_tags(body, footnote_defs)

    sections_spec = (template.get("output") or {}).get("sections") or []
    title_to_sid = {s["title"].strip().casefold(): s["id"] for s in sections_spec}

    docx_sections: dict[str, str] = {}
    unmatched_headings: list[str] = []
    for level, title, sec_body in split_sections(body):
        if level != 2:
            continue
        sid = title_to_sid.get(title.casefold())
        if sid is None:
            unmatched_headings.append(title)
            continue
        docx_sections[sid] = sec_body

    prev_sections = split_by_section_id(prev_src.read_text(encoding="utf-8")) if prev_src.is_file() else {}

    human_added_all: list[dict[str, Any]] = []
    human_modified_all: list[dict[str, Any]] = []
    out_lines: list[str] = []
    for sec in sections_spec:
        sid = sec["id"]
        if sid not in docx_sections:
            continue
        new_units = parse_units(docx_sections[sid], with_ids=False)
        prev_units = parse_units(prev_sections.get(sid, ""), with_ids=True)
        assigned, added, modified = _carry_claims(new_units, prev_units, task_id, sid)
        for a in added:
            a["section"] = sid
        for m in modified:
            m["section"] = sid
        human_added_all.extend(added)
        human_modified_all.extend(modified)
        out_lines.append(f"## {sec['title']} {{#{sid}}}")
        out_lines.append("")
        out_lines.append(_render_units(assigned))
        out_lines.append("")

    base_md = ("\n".join(out_lines).strip() + "\n") if out_lines else ""
    base_md_path.write_text(base_md, encoding="utf-8")

    result: dict[str, Any] = {
        "base_version": version,
        "base_edited": True,
        "human_added": human_added_all,
        "human_modified": human_modified_all,
    }
    if unmatched_headings:
        result["unmatched_headings"] = unmatched_headings
    base_json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
