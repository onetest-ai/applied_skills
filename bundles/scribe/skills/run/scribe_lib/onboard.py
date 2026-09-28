"""onboard: three read-only(-ish) helpers `scribe:onboard` drives.

- `coverage`: per-section Brain + raw evidence for a task file that may not
  be registered yet (`enabled: false`, or not even loaded by
  `validate_all`/`load_instances` — a project with OTHER broken tasks must
  not block previewing this one). Reads the file directly and never writes
  state: no `work/<task>/fingerprint.json`, no `out/<out>/_src/state.json`.
  This is a preview for onboarding step 4, not a real `fingerprint` run.
  Shares its retrieval with `fingerprint.py` via `fingerprint.retrieve_section`
  (task 13 review, Important) — no copy, no reach into a private helper.

- `sections_from_example`: propose template sections from an example
  document's own heading structure, so a user who already has a document
  they like doesn't have to invent section ids from scratch. docx/pptx go
  through `pandoc` (heading styles / slide titles survive the conversion to
  Markdown); pdf falls back to a font-size heuristic only when the pdf
  carries no outline/bookmark structure of its own.

- `guard_brain`: the loop-guard half of task 3's Brain-side `exclude` glob —
  onboarding's step 7 calls this once the output path is chosen, so the
  Brain never re-ingests Scribe's own published output as if it were source
  material. Edits `brain.toml` as text, surgically (never touches another
  line), because `tomllib` is read-only and the file may carry hand-written
  comments/formatting `tomllib` would discard on a round trip. Conservative
  and self-verifying (task 13 review, Critical): it only ever appends a
  brand-new `exclude` line at the END of a table (never inside another
  key's value, however many lines that value spans), only ever extends an
  existing `exclude` array when that array is a single line with no inline
  comment, and re-parses its own edit before writing a single byte.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from scribe_lib import brain as brain_mod
from scribe_lib import fingerprint as fingerprint_mod
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

# --------------------------------------------------------------------- coverage --


def coverage(config: Config, task_file: Path) -> dict[str, Any]:
    """`{"task", "sections": [{"section", "brain_hits", "raw_hits",
    "top_sources", "numbers_available", "no_evidence"}, ...]}`.

    Loads `task_file`'s frontmatter directly — never through
    `load_instances`/`validate_all` (a broken sibling task, or this task not
    yet being `enabled: true`/registered, must not block a preview) — and
    runs the SAME tag+untagged Brain search `fingerprint.py`'s retrieval
    uses (`fingerprint.retrieve_section`, shared — task 13 review), plus a
    raw-index lookup if `work/<task>/raw/raw.sqlite` already exists (from a
    prior `gather-raw`; coverage never runs `gather-raw` itself). Writes
    nothing.

    `numbers_available` only calls the Brain's `list_metrics` (read-only,
    cached once per call) when some section actually declares a `numbers`/
    `numbers?` lane — most sections never do, and a Brain whose store has no
    `facts` table at all must not make every OTHER section's preview fail.
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

    tags = fingerprint_mod.resolve_tags(resolved)
    searches = fingerprint_mod.build_searches(tags)
    raw_db = config.work_dir / task_id / "raw" / "raw.sqlite"

    _metrics_cache: list[dict[str, Any]] | None = None

    def _has_metrics() -> bool:
        nonlocal _metrics_cache
        if _metrics_cache is None:
            _metrics_cache = brain_mod.list_metrics(config)
        return bool(_metrics_cache)

    out_sections: list[dict[str, Any]] = []
    for sec in sections_spec:
        sid = sec["id"]
        queries = sec.get("queries") or []

        brain_hit_map, _via, raw_hit_map = fingerprint_mod.retrieve_section(
            config, queries, searches, raw_db, include_raw=True
        )
        raw_hits = list(raw_hit_map.values())

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

        lanes = [str(l).rstrip("?") for l in (sec.get("lanes") or [])]
        numbers_available = "numbers" in lanes and _has_metrics()
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
    """`[(level, title), ...]` for literal `#`/`##` Markdown headings."""
    return [(len(m.group(1)), m.group(2).strip()) for m in _HEADING_RE.finditer(text or "")]


def _pandoc_headings(example: Path, fmt: str) -> list[tuple[int, str]]:
    """docx/pptx -> Markdown via `pandoc -f <fmt> -t gfm`, then read literal
    `#`/`##` headings from the result. `pandoc` reads BOTH formats' native
    heading structure directly — a docx's "Heading 1"/"Heading 2" paragraph
    styles become `#`/`##`, and a pptx's per-slide title placeholder becomes
    one `##` per slide (pandoc's own convention, verified empirically: a
    3-slide deck built from `# `/`## ` Markdown of either level comes back
    as three `##` headings, one per slide) — so this never needs to guess
    heading levels from rendered font size the way a flattened pdf text
    dump would (task 13 review, Important: the old approach mistook body
    text for a heading whenever a document used only one heading level)."""
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise ScribeError(f"need pandoc on PATH to read headings from a {fmt} example")
    result = subprocess.run(
        [pandoc, "-f", fmt, "-t", "gfm", str(example)],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        raise ScribeError(f"pandoc could not read {example.name}: {(result.stderr or '').strip()}")
    return _markdown_headings(result.stdout)


def _pdf_headings(pdf_path: Path) -> list[tuple[int, str]]:
    """`[(level, title), ...]` for a standalone pdf (never a pandoc/soffice
    intermediate — docx/pptx go through `_pandoc_headings` instead). Prefers
    the pdf's own outline/bookmarks (`get_toc()`) when it has one — real
    structure, not a guess. Only when that's empty (no structure exists —
    task 13 review's ruling) does this fall back to a font-size heuristic:
    the largest distinct span size is level 1, the next-largest is level 2,
    everything else is treated as body text. That heuristic still
    misclassifies body text as a heading for a pdf that uses only one
    heading size and has no outline — accepted for this fallback-of-
    -last-resort path only; it is never reached for docx/pptx."""
    import pymupdf  # heavy import; local so a caller that never touches a pdf pays nothing.

    doc = pymupdf.open(pdf_path)
    try:
        toc = doc.get_toc()
        if toc:
            out: list[tuple[int, str]] = []
            for entry in toc:
                level, title = entry[0], entry[1]
                title = str(title).strip()
                if title:
                    out.append((min(int(level), 2), title))
            return out

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
    out = []
    for size, text in lines:
        if size == level1_size:
            out.append((1, text))
        elif size == level2_size:
            out.append((2, text))
    return out


def sections_from_example(config: Config, example: Path) -> list[dict[str, str]]:
    """`[{"id", "title"}, ...]` — one per H2 heading in `example`'s own
    structure, falling back to H1s when it has no H2s. `.docx`/`.pptx` go
    through `pandoc` (heading styles / slide titles); `.pdf` through its own
    outline or, lacking one, a font-size heuristic; everything else
    (`.md`/`.txt`/...) through `parsing.extract` (`parse_one`'s passthrough
    for those formats preserves literal Markdown headings verbatim, and
    never emits the `# SOURCE:`/`# method:` preamble itself — that's
    `parse_corpus.main`'s CLI-output convention, not `parse_one`'s — so
    there is no preamble H1 to strip here)."""
    example = Path(example)
    if not example.is_file():
        raise ScribeError(f"example document not found: {example}")

    ext = example.suffix.lower()
    if ext in (".docx", ".doc"):
        headings = _pandoc_headings(example, "docx")
    elif ext in (".pptx", ".ppt"):
        headings = _pandoc_headings(example, "pptx")
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

_ROOT_HEADER_RE = re.compile(
    r'^\[sources\.roots\.(?:"(?P<dq>(?:[^"\\]|\\.)*)"|\'(?P<sq>[^\']*)\'|(?P<bare>[A-Za-z0-9_-]+))\]\s*(#.*)?$'
)


def _find_root_table(lines: list[str], key: str) -> tuple[int, int] | None:
    """`(start, end)` line-index range of `[sources.roots.<key>]`'s table
    body — `end` is the index of the next `[`-starting header line, or EOF.
    The header itself may be bare, single- or double-quoted, and may carry a
    trailing `# comment` (task 13 review: an exact-string header match
    silently skipped both of those)."""
    for i, line in enumerate(lines):
        m = _ROOT_HEADER_RE.match(line.rstrip("\n"))
        if not m:
            continue
        found = m.group("dq") or m.group("sq") or m.group("bare")
        if found == key:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^\s*\[", lines[j]):
                    end = j
                    break
            return i, end
    return None


def _find_key_span(lines: list[str], start: int, end: int, key: str) -> tuple[int, int] | None:
    """`(first_line, last_line)` inclusive line-index span of `key = ...`
    inside `lines[start:end]`, tracking `[`/`]` depth so a multi-line array
    value is captured in full rather than just its first line (task 13
    review, Critical: inserting relative to only the first line of another
    key's multi-line array value corrupted the file). `None` if `key` is not
    assigned in this range."""
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(start, end):
        if key_re.match(lines[i]):
            depth = 0
            j = i
            while j < end:
                depth += lines[j].count("[") - lines[j].count("]")
                if depth <= 0:
                    return i, j
                j += 1
            return i, end - 1
    return None


def _unchanged_except_excludes(before: dict[str, Any], after: dict[str, Any], keys: set[str]) -> bool:
    """Whether `after` (the edited config, re-parsed) differs from `before`
    (the original, parsed once up front) ONLY in the `exclude` list of each
    root in `keys` — every other root, every other key of THOSE roots, and
    everything outside `[sources.roots]` must compare exactly equal."""
    b_top = {k: v for k, v in before.items() if k != "sources"}
    a_top = {k: v for k, v in after.items() if k != "sources"}
    if b_top != a_top:
        return False
    b_sources = before.get("sources") or {}
    a_sources = after.get("sources") or {}
    b_sources_rest = {k: v for k, v in b_sources.items() if k != "roots"}
    a_sources_rest = {k: v for k, v in a_sources.items() if k != "roots"}
    if b_sources_rest != a_sources_rest:
        return False
    b_roots = b_sources.get("roots") or {}
    a_roots = a_sources.get("roots") or {}
    if set(b_roots) != set(a_roots):
        return False
    for key, b_spec in b_roots.items():
        a_spec = a_roots.get(key, {})
        if key in keys:
            b_rest = {k: v for k, v in b_spec.items() if k != "exclude"}
            a_rest = {k: v for k, v in a_spec.items() if k != "exclude"}
            if b_rest != a_rest:
                return False
        elif b_spec != a_spec:
            return False
    return True


def _load_source_registry(brain_skills: Path):
    """Brain's `source_registry.py`, loaded dynamically (it's a plain
    script, not an importable package) — or `None` if it isn't where
    `brain_skills` says. Verification-only use: `guard_brain` never edits
    anything through it, only re-parses its own text with it."""
    path = Path(brain_skills) / "knowledge-pipeline" / "source_registry.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("scribe_onboard_source_registry", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guard_brain(
    brain_toml: Path,
    out_root: Path,
    *,
    require: bool = False,
    brain_skills: Path | None = None,
) -> dict[str, Any]:
    """For every `[sources.roots.<key>]` in `brain_toml` whose resolved
    `path` contains `out_root`, ensure an `exclude` entry
    `"<out_root relative to that root>/**"` exists. Conservative and
    self-verifying (task 13 review, Critical/Important — controller ruling):

    - locates a root's table by its header, tolerant of quoted keys and a
      trailing comment (`_find_root_table`); the table ends at the next `[`
      header or EOF.
    - a root with NO `exclude` key gets a brand-new `exclude = [...]` line
      inserted at the END of its table body — never inside another key's
      value, however many lines that value spans.
    - a root whose `exclude` already contains the glob (per `tomllib`) is
      left alone (`changed: false`).
    - a root whose `exclude` exists but lacks the glob is only edited when
      that array is a SINGLE line with no inline comment after it;
      otherwise nothing is written and the result carries
      `{"changed": false, "manual": "<line to add>"}` instead of guessing.
    - before writing anything, the FULL edited text is re-parsed with
      `tomllib` (refusing, file untouched, if it doesn't parse), checked
      that every matched root's `exclude` now contains its glob, and
      checked that every other key/value (every other root in full, and
      every OTHER key of a matched root) is unchanged versus the ORIGINAL
      parse (`_unchanged_except_excludes`). Only then is the file replaced.
      When `brain_skills` is given (the real Brain plugin's skills dir — the
      CLI always passes `config.brain_skills`), the same edited text is also
      re-parsed with brain's own `source_registry.load_config` on a temp
      copy, as an extra check with the actual consumer of this file.

    `require=True` raises `ScribeError` (a `ValueError`) when `out_root`
    matches no configured root at all — "nothing to guard" is a normal,
    non-fatal outcome by default, but onboarding's step 7 wants to know when
    it explicitly expected a match. `out_root == root` itself (nothing to
    scope the exclude to) is treated the same as no match.
    """
    brain_toml = Path(brain_toml)
    if not brain_toml.is_file():
        raise ScribeError(f"brain.toml not found: {brain_toml}")
    out_root = Path(out_root).resolve()

    original_text = brain_toml.read_text(encoding="utf-8")
    with brain_toml.open("rb") as fh:
        before = tomllib.load(fh)
    roots_cfg = ((before.get("sources") or {}).get("roots")) or {}

    matches: list[tuple[str, str, dict[str, Any]]] = []  # (key, glob, spec)
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
        rel_str = rel.as_posix()
        if rel_str == ".":
            continue  # out_root == root: the whole root would be scribe's; nothing to scope to
        matches.append((key, f"{rel_str}/**", spec))

    if not matches:
        if require:
            raise ScribeError(f"guard-brain: {out_root} is not inside any [sources.roots.*] path in {brain_toml}")
        return {"changed": False, "roots": [], "glob": None}

    roots_matched = sorted(k for k, _, _ in matches)
    primary_glob = matches[0][1]

    lines = original_text.splitlines(keepends=True)
    edited = False
    manual: str | None = None
    for key, glob, spec in matches:
        existing = list(spec.get("exclude") or [])
        if glob in existing:
            continue  # already guarded

        table = _find_root_table(lines, key)
        if table is None:
            continue
        start, end = table

        if "exclude" not in spec:
            last_content = start
            for j in range(start + 1, end):
                if lines[j].strip():
                    last_content = j
            lines.insert(last_content + 1, f'exclude = ["{glob}"]\n')
            edited = True
            continue

        span = _find_key_span(lines, start, end, "exclude")
        if span is None:
            manual = f'    "{glob}",'
            continue
        line_start, line_end = span
        if line_start != line_end:
            manual = f'    "{glob}",'
            continue
        line = lines[line_start]
        had_nl = line.endswith("\n")
        body = line[:-1] if had_nl else line
        close_idx = body.rfind("]")
        if close_idx == -1 or body[close_idx + 1 :].strip():
            manual = f'    "{glob}",'
            continue
        if existing:
            new_body = re.sub(r"\]\s*$", f', "{glob}"]', body, count=1)
        else:
            new_body = re.sub(r"\[\s*\]\s*$", f'["{glob}"]', body, count=1)
        lines[line_start] = new_body + ("\n" if had_nl else "")
        edited = True

    if not edited:
        result: dict[str, Any] = {"changed": False, "roots": roots_matched, "glob": primary_glob}
        if manual:
            result["manual"] = manual
        return result

    new_text = "".join(lines)

    try:
        after = tomllib.loads(new_text)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a hard refusal, file untouched
        raise ScribeError(f"guard-brain: edited brain.toml would not parse ({exc}); nothing written") from exc

    after_roots = ((after.get("sources") or {}).get("roots")) or {}
    touched_keys = {k for k, _, _ in matches}
    for key, glob, _spec in matches:
        after_exclude = (after_roots.get(key) or {}).get("exclude") or []
        if glob not in after_exclude:
            raise ScribeError(
                f"guard-brain: verification failed — {glob!r} missing from [sources.roots.{key}] after edit; nothing written"
            )
    if not _unchanged_except_excludes(before, after, touched_keys):
        raise ScribeError(
            "guard-brain: verification failed — the edit changed more than the guarded roots' exclude lists; nothing written"
        )

    if brain_skills is not None:
        source_registry = _load_source_registry(brain_skills)
        if source_registry is not None:
            tmp_fd, tmp_name = tempfile.mkstemp(suffix=".toml", prefix="scribe_guard_brain_")
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as tf:
                    tf.write(new_text)
                sr_cfg = source_registry.load_config(tmp_path)
                for key, glob, _spec in matches:
                    sr_exclude = (sr_cfg.get("roots", {}).get(key) or {}).get("exclude") or []
                    if glob not in sr_exclude:
                        raise ScribeError(
                            f"guard-brain: source_registry.load_config verification failed for "
                            f"[sources.roots.{key}]; nothing written"
                        )
            finally:
                tmp_path.unlink(missing_ok=True)

    brain_toml.write_text(new_text, encoding="utf-8")
    return {"changed": True, "roots": roots_matched, "glob": primary_glob}


__all__ = ["coverage", "sections_from_example", "guard_brain"]
