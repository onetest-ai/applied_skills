"""onboard: three read-only(-ish) helpers `scribe:onboard` drives.

- `coverage`: per-section Brain + raw evidence for a task file that may not
  be registered yet (`enabled: false`, or not even loaded by
  `validate_all`/`load_instances` — a project with OTHER broken tasks must
  not block previewing this one). Reads the file directly and never writes
  state: no `work/<task>/fingerprint.json`, no `out/<out>/_src/state.json`.
  This is a preview for onboarding step 4, not a real `fingerprint` run.

- `sections_from_example`: propose template sections from an example
  document's own heading structure, so a user who already has a document
  they like doesn't have to invent section ids from scratch.

- `guard_brain`: the loop-guard half of task 3's Brain-side `exclude` glob —
  onboarding's step 7 calls this once the output path is chosen, so the
  Brain never re-ingests Scribe's own published output as if it were source
  material. Edits `brain.toml` as text, surgically (never touches another
  line), because `tomllib` is read-only and the file may carry hand-written
  comments/formatting `tomllib` would discard on a round trip.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from scribe_lib import brain as brain_mod
from scribe_lib import parsing
from scribe_lib.config import (
    Config,
    ScribeError,
    load_templates,
    parse_frontmatter,
    parse_template_ref,
    resolve_instance_inputs,
    substitute_params,
)
from scribe_lib.fingerprint import _raw_hits

# --------------------------------------------------------------------- coverage --


def coverage(config: Config, task_file: Path) -> dict[str, Any]:
    """`{"task", "sections": [{"section", "brain_hits", "raw_hits",
    "top_sources", "numbers_available", "no_evidence"}, ...]}`.

    Loads `task_file`'s frontmatter directly — never through
    `load_instances`/`validate_all` (a broken sibling task, or this task not
    yet being `enabled: true`/registered, must not block a preview) — and
    runs the same tag+untagged Brain search fingerprint.py's retrieval uses,
    plus a raw-index lookup if `work/<task>/raw/raw.sqlite` already exists
    (from a prior `gather-raw`; coverage never runs `gather-raw` itself).
    Writes nothing.
    """
    task_file = Path(task_file)
    if not task_file.is_file():
        raise ScribeError(f"task file not found: {task_file}")
    fm, _ = parse_frontmatter(task_file)
    task_id = fm.get("id")
    if not task_id:
        raise ScribeError(f"{task_file}: task instance missing 'id'")

    templates = load_templates(config)
    template_id, template_version = parse_template_ref(fm.get("template", ""))
    template = templates.get(template_id)
    if template is None or template.get("version") != template_version:
        raise ScribeError(f"{task_file}: unknown template '{fm.get('template')}'")

    resolved = resolve_instance_inputs(fm, template)
    params = fm.get("params") or {}
    sections_spec = [
        substitute_params(sec, params) for sec in (template.get("output") or {}).get("sections") or []
    ]

    tag_field = (resolved.get("brain") or {}).get("tags")
    tags: list[str | None]
    if isinstance(tag_field, list) and tag_field:
        tags = list(tag_field)
    elif isinstance(tag_field, str) and tag_field:
        tags = [tag_field]
    else:
        tags = [None]
    searches: list[tuple[str | None, str]] = [(tag, f"tag:{tag}") for tag in tags if tag is not None]
    searches.append((None, "untagged"))

    raw_db = config.work_dir / task_id / "raw" / "raw.sqlite"

    out_sections: list[dict[str, Any]] = []
    for sec in sections_spec:
        sid = sec["id"]
        queries = sec.get("queries") or []

        brain_hit_map: dict[str, dict[str, Any]] = {}
        for q in queries:
            for tag, _via in searches:
                for h in brain_mod.search(config, q, config.top_k, tag):
                    cid = str(h["chunk_id"])
                    existing = brain_hit_map.get(cid)
                    if existing is None or (h.get("score") or 0) > (existing.get("score") or 0):
                        brain_hit_map[cid] = h

        raw_hits: list[dict[str, Any]] = []
        if raw_db.is_file():
            seen_raw: set[tuple[str, str]] = set()
            for q in queries:
                for h in _raw_hits(raw_db, q, config.top_k):
                    key = (h["path"], h["locator"])
                    if key not in seen_raw:
                        seen_raw.add(key)
                        raw_hits.append(h)

        top_sources: list[str] = []
        for h in brain_hit_map.values():
            src = h.get("source")
            if src and src not in top_sources:
                top_sources.append(src)
        for h in raw_hits:
            src = h.get("path")
            if src and src not in top_sources:
                top_sources.append(src)
        top_sources = top_sources[:3]

        numbers_available = "numbers" in (sec.get("lanes") or []) and bool(sec.get("list_metrics"))
        brain_hits = len(brain_hit_map)
        raw_hit_count = len(raw_hits)

        out_sections.append(
            {
                "section": sid,
                "brain_hits": brain_hits,
                "raw_hits": raw_hit_count,
                "top_sources": top_sources,
                "numbers_available": numbers_available,
                "no_evidence": brain_hits == 0 and raw_hit_count == 0,
            }
        )

    return {"task": task_id, "sections": out_sections}


# ---------------------------------------------------------- sections from example --


def _kebab(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug or "section"


_HEADING_RE = re.compile(r"^(#{1,2})\s+(.+?)\s*$", re.MULTILINE)


def _markdown_headings(text: str) -> list[tuple[int, str]]:
    """`[(level, title), ...]` for literal `#`/`##` Markdown headings — the
    shape `parsing.extract` returns for `.md`/`.txt`/`.vtt`/... inputs
    (`parse_one` never adds the `# SOURCE:`/`# method:` preamble itself —
    that is `parse_corpus.main`'s CLI-output convention, not `parse_one`'s —
    so there is no preamble H1 to strip here)."""
    return [(len(m.group(1)), m.group(2).strip()) for m in _HEADING_RE.finditer(text or "")]


def _pdf_headings(pdf_path: Path) -> list[tuple[int, str]]:
    """`[(level, title), ...]` inferred from font size: PyMuPDF's plain-text
    extraction (what `parse_corpus.parse_pdf_pymupdf` uses) discards heading
    structure, so this reads `get_text("dict")` spans directly. The largest
    distinct font size in the document is level 1, the next-largest is level
    2; everything else (body text) is ignored. Returns `[]` when the
    document uses fewer than two distinct sizes (no headings distinguishable
    from body text)."""
    import pymupdf  # heavy import; local so a caller that never touches a
    # pdf/docx/pptx example never pays for it.

    doc = pymupdf.open(pdf_path)
    try:
        lines: list[tuple[float, str]] = []
        for page in doc:
            d = page.get_text("dict")
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    if not text:
                        continue
                    size = max((span.get("size", 0.0) for span in line.get("spans", [])), default=0.0)
                    lines.append((round(size, 1), text))
    finally:
        doc.close()

    if not lines:
        return []
    sizes = sorted({s for s, _ in lines}, reverse=True)
    if len(sizes) < 2:
        return []
    level1_size, level2_size = sizes[0], sizes[1]
    out: list[tuple[int, str]] = []
    for size, text in lines:
        if size == level1_size:
            out.append((1, text))
        elif size == level2_size:
            out.append((2, text))
    return out


def _office_headings(config: Config, example: Path) -> list[tuple[int, str]]:
    """docx/pptx -> pdf via the SAME `soffice` discovery `parse_corpus.py`
    uses (`parse_office_pymupdf`), then `_pdf_headings` on the result.
    `parse_one`'s own docx/pptx path (plain `get_text()`) is text-only —
    heading structure survives only in per-span font size, which plain text
    extraction throws away, so this duplicates the soffice-conversion half
    of `parse_office_pymupdf` rather than reusing its (lossy, for this
    purpose) final text output."""
    parse_corpus = parsing.load_parse_corpus(config)
    soffice = parse_corpus._soffice()
    if not soffice:
        raise ScribeError("need LibreOffice (soffice) on PATH to read headings from a pptx/docx example")

    tmp = Path(tempfile.mkdtemp(prefix="scribe_onboard_pdf_"))
    profile = Path(tempfile.mkdtemp(prefix="scribe_onboard_soffice_"))
    try:
        result = subprocess.run(
            [
                soffice,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp),
                str(example),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        pdf_path = tmp / f"{example.stem}.pdf"
        if result.returncode != 0 or not pdf_path.is_file():
            raise ScribeError(f"soffice could not convert {example.name} to pdf for heading extraction")
        return _pdf_headings(pdf_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)


def sections_from_example(config: Config, example: Path) -> list[dict[str, str]]:
    """`[{"id", "title"}, ...]` — one per H2 heading in `example`'s own
    structure, falling back to H1s when it has no H2s. `.md`/`.txt`/other
    plain-text formats go through `parsing.extract` (Markdown headings read
    literally); `.pdf`/`.docx`/`.pptx` go through font-size heading
    detection (see `_pdf_headings`/`_office_headings`) since text-only
    extraction discards heading levels for those formats."""
    example = Path(example)
    if not example.is_file():
        raise ScribeError(f"example document not found: {example}")

    ext = example.suffix.lower()
    if ext in (".docx", ".pptx", ".doc", ".ppt"):
        headings = _office_headings(config, example)
    elif ext == ".pdf":
        headings = _pdf_headings(example)
    else:
        text, _method = parsing.extract(config, example)
        headings = _markdown_headings(text or "")

    h2 = [(lvl, t) for lvl, t in headings if lvl == 2]
    chosen = h2 if h2 else [(lvl, t) for lvl, t in headings if lvl == 1]

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for _lvl, title in chosen:
        title = title.strip()
        if not title:
            continue
        base = _kebab(title)
        sid, n = base, 2
        while sid in seen:
            sid = f"{base}-{n}"
            n += 1
        seen.add(sid)
        out.append({"id": sid, "title": title})
    return out


# --------------------------------------------------------------------- guard_brain --


def guard_brain(brain_toml: Path, out_root: Path, *, require: bool = False) -> dict[str, Any]:
    """For every `[sources.roots.<key>]` in `brain_toml` whose resolved
    `path` contains `out_root`, ensure an `exclude` entry
    `"<out_root relative to that root>/**"` exists — inserting a fresh
    `exclude = [...]` line right after that root's `include` line, or
    appending to its existing `exclude` array. Surgical text edit only:
    reads with `tomllib` (read-only) to find which roots match and what
    their current `exclude` already holds, then edits the ORIGINAL lines in
    place — every other line, key, comment and root section is byte-for-byte
    untouched. Idempotent: a glob already present is left alone.

    `require=True` raises `ScribeError` (a `ValueError`) when `out_root`
    matches no configured root at all — "nothing to guard" is a normal,
    non-fatal outcome by default (a project's Brain may not source from
    anywhere near this task's output), but onboarding's step 7 wants to know
    when it explicitly expected a match.
    """
    brain_toml = Path(brain_toml)
    if not brain_toml.is_file():
        raise ScribeError(f"brain.toml not found: {brain_toml}")
    out_root = Path(out_root).resolve()

    with brain_toml.open("rb") as fh:
        raw = tomllib.load(fh)
    roots_cfg = ((raw.get("sources") or {}).get("roots")) or {}

    matches: list[tuple[str, str, list[str]]] = []  # (key, glob, existing exclude list)
    for key, spec in roots_cfg.items():
        if not isinstance(spec, dict):
            continue
        raw_path = os.path.expandvars(os.path.expanduser(str(spec.get("path", ""))))
        if not raw_path:
            continue
        root = Path(raw_path)
        if not root.is_absolute():
            root = brain_toml.parent / root
        root = root.resolve()
        try:
            rel = out_root.relative_to(root)
        except ValueError:
            continue
        glob = f"{rel.as_posix()}/**"
        existing = list(spec.get("exclude") or [])
        matches.append((key, glob, existing))

    if not matches:
        if require:
            raise ScribeError(f"guard-brain: {out_root} is not inside any [sources.roots.*] path in {brain_toml}")
        return {"changed": False, "roots": [], "glob": None}

    lines = brain_toml.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = False
    for key, glob, existing in matches:
        if glob in existing:
            continue  # already guarded

        header = f"[sources.roots.{key}]"
        start = next((i for i, ln in enumerate(lines) if ln.strip() == header), None)
        if start is None:
            continue
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if lines[i].lstrip().startswith("["):
                end = i
                break

        exclude_idx = None
        include_idx = None
        for i in range(start, end):
            stripped = lines[i].strip()
            if stripped.startswith("exclude"):
                exclude_idx = i
            elif stripped.startswith("include"):
                include_idx = i

        if exclude_idx is not None:
            line = lines[exclude_idx]
            had_nl = line.endswith("\n")
            body = line[:-1] if had_nl else line
            if existing:
                new_body = re.sub(r"\]\s*$", f', "{glob}"]', body, count=1)
            else:
                new_body = re.sub(r"\[\s*\]\s*$", f'["{glob}"]', body, count=1)
            lines[exclude_idx] = new_body + ("\n" if had_nl else "")
        else:
            insert_at = (include_idx + 1) if include_idx is not None else end
            lines.insert(insert_at, f'exclude = ["{glob}"]\n')
        changed = True

    if changed:
        brain_toml.write_text("".join(lines), encoding="utf-8")

    return {"changed": changed, "roots": sorted(k for k, _, _ in matches), "glob": matches[0][1]}


__all__ = ["coverage", "sections_from_example", "guard_brain"]
