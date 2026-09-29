"""The Brain's side of the Scribe loop guard — one predicate for every ingest path.

Scribe stamps every artifact it publishes with a machine-readable marker: a
``scribe-task`` custom property in docx/pptx/xlsx ``docProps/custom.xml``, a
``scribe-task=<id>`` PDF keyword, or a ``<!-- scribe:...`` first line in a
``.md``/``.markdown``/``.txt`` file. Every place that walks a Brain root —
``source_registry.iter_files`` (registration) and ``parse_corpus`` (parsing) — must
skip marked files and a root's ``exclude`` globs through THIS module, or Scribe's
output re-enters the Brain as its own evidence (or, in strict source mode, the
unregistered parsed document blocks every ``brain_sync``).

The same module also gates hidden paths: ``skip_reason`` returns ``"hidden"`` (checked
before exclude/marker) for a dot-file, a dot-dir anywhere in the relative path, an
AppleDouble sidecar, an Office lock file, or an OS-hidden file — see ``is_hidden``.

Exists twice and the copies must stay byte-identical
(``corpus-taxonomy-extraction/`` and ``knowledge-pipeline/``), like ``chunking.py``.
"""
from __future__ import annotations

import re
import stat
from pathlib import Path, PurePosixPath

SCRIBE_MARKER = "scribe-task"


def is_hidden(path: Path, rel: str) -> bool:
    """True when ``rel`` is hidden: any path component (dir or file, relative to the
    scanned root) starts with ``"."`` (dot-files, dot-dirs, macOS AppleDouble
    ``._x.pdf``), the file name starts with ``"~$"`` (Office lock/owner files), or the
    OS marks the file hidden (macOS/BSD ``st_flags & UF_HIDDEN``, Windows
    ``st_file_attributes & FILE_ATTRIBUTE_HIDDEN``, both only when present). Never
    raises — an unreadable/missing path is simply not hidden by this check."""
    try:
        parts = PurePosixPath(rel).parts
        if any(part.startswith(".") for part in parts):
            return True
        if parts and parts[-1].startswith("~$"):
            return True
        st = Path(path).stat()
        uf_hidden = getattr(stat, "UF_HIDDEN", None)
        if uf_hidden is not None and (getattr(st, "st_flags", 0) & uf_hidden):
            return True
        hidden_attr = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", None)
        if hidden_attr is not None and (getattr(st, "st_file_attributes", 0) & hidden_attr):
            return True
    except Exception:
        return False
    return False


def is_scribe_artifact(path: Path) -> bool:
    """True for a file scribe generated (spec: Artifacts -> scribe marker). Never raises."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix in (".docx", ".pptx", ".xlsx"):
            import zipfile
            with zipfile.ZipFile(path) as z:
                if "docProps/custom.xml" not in z.namelist():
                    return False
                return f'name="{SCRIBE_MARKER}"' in z.read("docProps/custom.xml").decode("utf-8", "replace")
        if suffix == ".pdf":
            import pymupdf  # PyMuPDF, already a brain dependency
            with pymupdf.open(path) as d:
                return f"{SCRIBE_MARKER}=" in ((d.metadata or {}).get("keywords") or "")
        if suffix in (".md", ".markdown", ".txt"):
            with path.open("r", encoding="utf-8", errors="replace") as h:
                return h.readline().lstrip().startswith("<!-- scribe:")
    except Exception:
        return False
    return False


def glob_match(rel: str, pattern: str) -> bool:
    """Match a root-relative POSIX path against an ``exclude`` glob (``**`` spans dirs)."""
    if hasattr(PurePosixPath, "full_match"):
        return PurePosixPath(rel).full_match(pattern)
    rx = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.fullmatch(rx, rel) is not None


def skip_reason(path: Path, rel: str, excludes=()) -> str | None:
    """``"hidden"`` / ``"excluded"`` / ``"scribe_marker"`` when the file must not be
    ingested, else None. ``"hidden"`` is checked first: a hidden path should never
    reach exclude-glob or marker logic (or register/parse) at all."""
    if is_hidden(path, rel):
        return "hidden"
    if any(glob_match(rel, g) for g in excludes):
        return "excluded"
    if is_scribe_artifact(path):
        return "scribe_marker"
    return None
