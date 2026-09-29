"""render: work/<task>/next.md (or --md PATH) -> work/<task>/render/{<title>.docx,.pdf}.

Three passes over the Document Markdown source, in this order:

1. **Strip comments.** The header `<!-- scribe: ... -->` line and every
   trailing `<!-- c:xxxxxxxx( sup=c:yyyyyyyy)? -->` claim-id comment are
   plain single-line HTML comments (never spanning a fenced code block —
   Mermaid's `-->` arrow syntax never matches, since it lacks the leading
   `<!--`), so a per-line, non-fenced-only regex strip is enough; no DOTALL
   scan that could eat a mermaid block.
2. **Escape `<`/`>` outside tags.** A literal angle-bracketed word in claim
   text (e.g. `<Customer Name>`) is backslash-escaped so pandoc's Markdown
   reader treats it as literal text instead of dropping it as raw inline
   HTML (m3) — see `_escape_angles_outside_tags`.
3. **Tags -> pandoc footnotes.** Each `[RAG:...]`/`[MART:...]`/`[GRAPH:...]`/
   `[FILE:...]`/`[TASK:...]` becomes `^[<TAG BODY> ¦ <source label>]` (`¦` =
   `claims.FOOTNOTE_SEP`, U+00A6 BROKEN BAR), where `<TAG BODY>` is
   `KIND:value` verbatim (no brackets). `_tags_to_footnotes` never refuses a
   tag body — even one containing that glyph, e.g. a `[FILE:]` locator that
   is a raw document's heading breadcrumb (Minor, F3 re-review: this used to
   raise `ScribeError`, which would block that task's publish every night
   the tag recurred). C1 (F3 review, Critical 1): the separator used to be
   `" — "` (an em dash), which a VTT `[FILE:]` locator legitimately contains
   (`00:17 — Speaker (cue 1) > 03:26 — ...`), so `basedoc._extract_footnotes`
   splitting the recovered footnote text on the first `" — "` truncated
   every such tag to its first timestamp on a docx round trip, collapsing
   distinct cues of the same file into duplicate tags. `basedoc` now
   recovers the tag body primarily by matching the footnote text against
   the previous version's own tag bodies (present regardless of which
   separator rendered it, or itself contains one) — exact for such a tag —
   falling back to splitting on `" ¦ "`, and only as a last resort on the
   legacy `" — "` for a docx rendered before this fix. Several tags on one
   claim -> several footnotes, one per tag, in order.
4. **Mermaid fences -> images.** A ` ```mermaid ` fence is written to
   `render/diagrams/<section>-<n>.mmd` (n = 1-based, per section id seen so
   far this render; the doc's own H1 before any `## Title {#id}` heading
   counts as section "doc"), rendered to a same-named `.png` via
   `npx -y` and the pinned `MERMAID_CLI`, and replaced by
   `![<section> diagram](diagrams/<...>.png)`. On failure the original fence
   is kept as a plain code block and the failure is recorded (never raises —
   `accept`'s `diagrams_render` check is what should catch this).

Then `pandoc <processed>.md -f markdown-smart -o render/<title>.docx
--reference-doc=<template's reference.docx> -M scribe-task=<task id> -M
title=<title> --resource-path=<render dir>` — `-f markdown-smart` turns off
pandoc's smart-typography reader extension (on by default for `.md`), so the
published docx keeps the source's straight quotes/hyphens/`...` verbatim
instead of pandoc silently curling `Bain's` into `Bain’s`, which is
otherwise recovered from the docx and read as a human edit by `base`
(`claims.normalize_text` folds curly-vs-straight anyway, belt-and-suspenders,
but the docx should not diverge from the source in the first place)
(confirmed empirically: `-M scribe-task=...` lands as a genuine custom
property in `docProps/custom.xml`, `-M title=...` as `dc:title` in
`docProps/core.xml` — pandoc 3.11), then `soffice --headless --convert-to pdf
--outdir render <title>.docx`.

Deviation from the brief's literal mermaid-cli flags, documented here and in
the task report: `-w 1600` is rejected ("unknown option '-w'") by
`MERMAID_CLI` (`@mermaid-js/mermaid-cli@12.0.0`, pinned so `npx -y` always
resolves the same CLI) — that version replaced `-w`/`-H` with a single
`--size <px>` option. `-b white --size 1600` is the same intent (bounded
render size, white background) with the current flag name.

Writes `render/render.json`:
    {"docx": "<title>.docx", "pdf": "<title>.pdf",
     "diagrams": [{"diagram": "<section>-<n>", "ok": bool, "error"?: str}, ...],
     "ok": bool, "errors": [str, ...]}
`ok` is true only if both the docx and pdf exist (mermaid failures alone do
not fail the render — they degrade to a code block, per the brief; `accept`'s
`diagrams_render` check is where a failed diagram should block acceptance).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from scribe_lib import brain as brain_mod
from scribe_lib import claims
from scribe_lib.config import Config, ScribeError

_COMMENT_RE = re.compile(r"\s*<!--.*?-->")
_ID_HEADING_RE = re.compile(r"^#{1,6}\s+.*\{#([\w-]+)\}\s*$")

_MERMAID_TIMEOUT = 120
_SOFFICE_TIMEOUT = 180

# Pinned so `npx -y` resolves the same CLI (and flag surface) on every machine
# and every run, rather than "whatever's currently latest" — see the module
# docstring's note on `-w`/`--size` for what an unpinned version already broke
# once. `doctor.py` imports this constant rather than repeating the literal.
MERMAID_CLI = "@mermaid-js/mermaid-cli@12.0.0"


def _rag_label(config: Config, chunk_id: str) -> str:
    ev = brain_mod.evidence(config, chunk_id)
    if ev.get("status") != "ok":
        return "not modeled"
    source = str(ev.get("source") or "")
    name = source.rsplit("__", 1)[-1]
    if name.endswith(".md"):
        name = name[:-3]
    section = ev.get("section")
    return f"{name}, {section}" if section else (name or chunk_id)


def _file_label(value: str) -> str:
    path, _, locator = value.partition("#")
    name = path.rsplit("/", 1)[-1] or path
    return f"{name}, {locator}" if locator else name


def _task_label(instances: dict[str, Any], value: str) -> str:
    task_id, _, claim_ref = value.partition("#")
    inst = instances.get(task_id) or {}
    title = inst.get("title", task_id)
    return f"{title} {claim_ref}" if claim_ref else str(title)


def _footnote_label(config: Config, instances: dict[str, Any], kind: str, value: str) -> str:
    if kind == "RAG":
        return _rag_label(config, value)
    if kind == "FILE":
        return _file_label(value)
    if kind == "TASK":
        return _task_label(instances, value)
    # MART -> "metric@grain", GRAPH -> node label: the tag's own value IS the label.
    return value


# Characters pandoc's Markdown reader could turn into markup inside a footnote
# (emphasis, math, sub/superscript, code, links, raw HTML; `@` is left alone —
# without --citeproc a citation renders as its literal text). Backslash-escaping them
# keeps a tag body literal in the docx, so `base` recovers it exactly.
_PANDOC_SPECIAL_RE = re.compile(r"([\\*_\[\]<>$^~`])")


def _md_literal(text: str) -> str:
    return _PANDOC_SPECIAL_RE.sub(r"\\\1", text)


def _escape_unescaped_angles(s: str) -> str:
    """Backslash-escape a literal `<`/`>` in `s`, but leave one that is
    ALREADY escaped (immediately preceded by a backslash) exactly alone.

    Review fix round 1, Important #3: a published claim round-trips through
    `base_task`'s docx recovery (`pandoc -f docx -t gfm`), whose gfm WRITER
    itself backslash-escapes a literal `<`/`>` it finds in the docx's plain
    text — that is a correct, ordinary part of turning visible text back
    into Markdown source, not something this function put there. The first
    cut of this function escaped indiscriminately, so a SECOND render pass
    over that already-escaped text (`\\<Customer Name\\>`) turned `\\<` into
    `\\\\<` — an escaped backslash followed by a bare, unescaped `<` — and
    every further round trip grew another backslash. Scanning left to right
    and copying a backslash together with whatever it escapes, verbatim and
    unmodified, makes this function idempotent: it escapes a `<`/`>` exactly
    once, the first time it sees an unescaped one, and never touches one
    that already carries an escape (from this function, from `_md_literal`,
    or from pandoc's own gfm writer)."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            out.append(s[i : i + 2])
            i += 2
            continue
        out.append("\\" + ch if ch in "<>" else ch)
        i += 1
    return "".join(out)


def _escape_angles_outside_tags(line: str) -> str:
    """Backslash-escape a literal, unescaped `<`/`>` in claim text,
    everywhere EXCEPT inside a `[RAG:...]`/`[MART:...]`/`[GRAPH:...]`/
    `[FILE:...]`/`[TASK:...]` tag (converted separately by
    `_tags_to_footnotes`, which already escapes its own tag body/label).

    A word like `<Customer Name>` is a literal token in the claim, not
    Markdown — left unescaped, pandoc's Markdown reader treats it as raw
    inline HTML and drops it from the rendered docx/pdf entirely (m3): the
    word silently disappears rather than surviving as text. Applied per line,
    before `_tags_to_footnotes`, so it never touches a tag's own brackets
    (`TAG_RE` matches `[`/`]`, not `<`/`>`) or the footnote text
    `_tags_to_footnotes`/`_md_literal` go on to produce. See
    `_escape_unescaped_angles` for why an ALREADY-escaped `<`/`>` (e.g. from
    a prior render -> base docx round trip) must be left alone.
    """
    out: list[str] = []
    pos = 0
    for m in claims.TAG_RE.finditer(line):
        out.append(_escape_unescaped_angles(line[pos : m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_escape_unescaped_angles(line[pos:]))
    return "".join(out)


def _tags_to_footnotes(text: str, config: Config, instances: dict[str, Any]) -> str:
    def _sub(m: re.Match[str]) -> str:
        kind, value = m.group(1), claims.unescape_tag_body(m.group(2))
        tag_body = f"{kind}:{value}"
        # Minor (F3 re-review): a `[FILE:]` locator is a raw document's
        # heading breadcrumb, and a source heading can legitimately contain
        # `claims.FOOTNOTE_SEP` itself — this used to raise ScribeError here,
        # which would block that task's publish every night. Write the
        # footnote regardless: `basedoc._recover_tag_body`'s primary
        # recovery path matches the footnote text against the previous
        # version's own known tag bodies (a prefix match, independent of
        # which separator is inside the body), which is exact for a tag
        # body that itself contains the separator.
        label = _footnote_label(config, instances, kind, value)
        return f"^[{_md_literal(tag_body)} {claims.FOOTNOTE_SEP} {_md_literal(label)}]"

    return claims.TAG_RE.sub(_sub, text)


def _strip_line_comments(line: str) -> str:
    return _COMMENT_RE.sub("", line)


def _run_mermaid(mmd_path: Path, png_path: Path) -> tuple[bool, str | None]:
    cmd = [
        "npx", "-y", MERMAID_CLI,
        "-i", str(mmd_path), "-o", str(png_path),
        "-b", "white", "--size", "1600",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_MERMAID_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"mmdc timed out after {_MERMAID_TIMEOUT}s"
    except FileNotFoundError:
        return False, "npx not found on PATH"
    if proc.returncode != 0 or not png_path.is_file():
        detail = (proc.stderr or proc.stdout or "mmdc failed").strip()
        return False, detail[:2000]
    return True, None


def _stamp_pdf(pdf_path: Path, task_id: str) -> None:
    import pymupdf

    with pymupdf.open(pdf_path) as d:
        meta = dict(d.metadata or {})
        meta["keywords"] = f"scribe-task={task_id}"
        d.set_metadata(meta)
        d.saveIncr()


def _reference_docx(config: Config) -> Path:
    for tdir in config.templates_dirs:
        candidate = tdir / "reference.docx"
        if candidate.is_file():
            return candidate
    raise ScribeError(
        "no reference.docx found under any templates_dir "
        f"({[str(d) for d in config.templates_dirs]}) — generate one with "
        "`pandoc -o reference.docx --print-default-data-file reference.docx`"
    )


def _process_markdown(
    text: str, config: Config, instances: dict[str, Any], diagrams_dir: Path
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Strip comments, convert tags to footnotes, extract mermaid diagrams.

    Returns (processed markdown text, diagrams, errors)."""
    lines = text.splitlines()
    out_lines: list[str] = []
    diagrams: list[dict[str, Any]] = []
    errors: list[str] = []
    current_section = "doc"
    section_counts: dict[str, int] = {}

    i, n = 0, len(lines)
    while i < n:
        raw_line = lines[i]
        fence_m = claims.FENCE_RE.match(raw_line)
        if fence_m:
            lang = fence_m.group(1)
            j = i + 1
            body_lines: list[str] = []
            while j < n and not claims.FENCE_RE.match(lines[j]):
                body_lines.append(lines[j])
                j += 1
            closed = j < n
            if lang == "mermaid":
                section_counts[current_section] = section_counts.get(current_section, 0) + 1
                name = f"{current_section}-{section_counts[current_section]}"
                mmd_path = diagrams_dir / f"{name}.mmd"
                mmd_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
                png_path = diagrams_dir / f"{name}.png"
                ok, err = _run_mermaid(mmd_path, png_path)
                if ok:
                    out_lines.append(f"![{current_section} diagram](diagrams/{name}.png)")
                    diagrams.append({"diagram": name, "ok": True})
                else:
                    out_lines.append(raw_line)
                    out_lines.extend(body_lines)
                    if closed:
                        out_lines.append(lines[j])
                    diagrams.append({"diagram": name, "ok": False, "error": err})
                    errors.append(f"mermaid diagram {name} failed: {err}")
            else:
                out_lines.append(raw_line)
                out_lines.extend(body_lines)
                if closed:
                    out_lines.append(lines[j])
            i = j + 1 if closed else j
            continue

        heading_m = _ID_HEADING_RE.match(raw_line)
        if heading_m:
            current_section = heading_m.group(1)

        line = _strip_line_comments(raw_line)
        line = _escape_angles_outside_tags(line)
        line = _tags_to_footnotes(line, config, instances)
        out_lines.append(line)
        i += 1

    processed = "\n".join(out_lines).strip("\n") + "\n"
    return processed, diagrams, errors


def render_task(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    instances: dict[str, Any],
    md_path: str | Path | None = None,
) -> dict[str, Any]:
    work_dir = config.work_dir / task_id
    src_path = Path(md_path) if md_path else work_dir / "next.md"
    if not src_path.is_file():
        raise ScribeError(f"no markdown to render at {src_path}")
    text = src_path.read_text(encoding="utf-8")

    render_dir = work_dir / "render"
    diagrams_dir = render_dir / "diagrams"
    if diagrams_dir.is_dir():
        shutil.rmtree(diagrams_dir)
    diagrams_dir.mkdir(parents=True, exist_ok=True)

    processed, diagrams, errors = _process_markdown(text, config, instances, diagrams_dir)

    tmp_md = render_dir / "_render.md"
    tmp_md.write_text(processed, encoding="utf-8")

    title = instance["title"]
    docx_path = render_dir / f"{title}.docx"
    pdf_path = render_dir / f"{title}.pdf"

    reference_docx = _reference_docx(config)
    pandoc_cmd = [
        "pandoc", str(tmp_md),
        "-f", "markdown-smart",
        "-o", str(docx_path),
        f"--reference-doc={reference_docx}",
        "-M", f"scribe-task={task_id}",
        "-M", f"title={title}",
        f"--resource-path={render_dir}",
    ]
    try:
        proc = subprocess.run(pandoc_cmd, capture_output=True, text=True, timeout=_SOFFICE_TIMEOUT)
        if proc.returncode != 0:
            errors.append(f"pandoc failed: {proc.stderr.strip()}")
    except FileNotFoundError:
        errors.append("pandoc not found on PATH")
    except subprocess.TimeoutExpired:
        errors.append(f"pandoc timed out after {_SOFFICE_TIMEOUT}s")

    if docx_path.is_file():
        soffice_cmd = [
            "soffice", "--headless", "--convert-to", "pdf",
            "--outdir", str(render_dir), str(docx_path),
        ]
        try:
            proc = subprocess.run(
                soffice_cmd, capture_output=True, text=True, timeout=_SOFFICE_TIMEOUT
            )
            if proc.returncode != 0 and not pdf_path.is_file():
                errors.append(f"soffice failed: {(proc.stderr or proc.stdout).strip()}")
            elif pdf_path.is_file():
                try:
                    _stamp_pdf(pdf_path, task_id)
                except Exception as exc:
                    # The loop guard depends on every published pdf carrying the
                    # scribe-task marker — an unmarked pdf is worse than no pdf,
                    # since it would silently re-enter the corpus once moved out
                    # of an excluded dir. Remove it so the is_file() gate below
                    # fails exactly like a missing pdf (ok=False, exit 1).
                    errors.append(f"pdf marker stamp failed: {exc}")
                    pdf_path.unlink(missing_ok=True)
        except FileNotFoundError:
            errors.append("soffice not found on PATH")
        except subprocess.TimeoutExpired:
            errors.append(f"soffice timed out after {_SOFFICE_TIMEOUT}s")
    else:
        errors.append("skipped pdf conversion: docx was not produced")

    ok = docx_path.is_file() and pdf_path.is_file()
    if not ok:
        if not docx_path.is_file():
            errors.append(f"missing {docx_path}")
        if not pdf_path.is_file():
            errors.append(f"missing {pdf_path}")

    result = {
        "docx": docx_path.name if docx_path.is_file() else None,
        "pdf": pdf_path.name if pdf_path.is_file() else None,
        "diagrams": diagrams,
        "ok": ok,
        "errors": errors,
    }
    (render_dir / "render.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
