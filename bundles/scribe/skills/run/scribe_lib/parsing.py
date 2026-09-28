"""Shared text extraction — the ONE code path both raw-file *selection*
(`select_raw_files` in `config.py`, used by `plan`/`delta`/`gather-raw`) and
raw-file *parsing* (`gather-raw`'s full output, in `raw.py`) go through.

Fix round 1 (task-2-review.md, item 1): `select_raw_files`'s `match` check
used to fall back to a best-effort UTF-8 decode of a candidate file's raw
bytes when its path didn't match. That is fine for the raw-replay fixtures
this bundle's own tests use (plain `.txt`) but is close to useless for a
binary office format — a raw `.xlsx`/`.docx`/`.pdf` decoded as UTF-8-with-
errors-ignored is mostly garbage, so an alias that is genuinely IN the
document (e.g. a term appearing dozens of times inside a workbook's cells)
almost never survives that decode as a clean substring. `match` must run on
the file's actual PARSED text, using the same `parse_corpus.parse_one` that
`gather-raw` uses to produce its final Markdown output — not a second,
cheaper extraction that could disagree with it. Consequence: a file `plan`/
`delta`/`gather-raw` select must be one `parse_one` can actually turn into
text; `select_raw_files` and `gather_raw_task` both call `extract` here
instead of each parsing independently.

Loads `parse_corpus.py`/`chunking.py` from `<brain_skills>/...` via
`importlib.util.spec_from_file_location` (they are plain scripts, not
packages), cached once per process. `extract` additionally caches each
file's `(text, method)` by `(path, mtime_ns, size)`, so within one `scribe.py`
invocation a file already parsed for match-checking (`select_raw_files`) is
never re-parsed for `gather-raw`'s output, and vice versa — parsing an office
document usually means an LibreOffice subprocess conversion, worth avoiding
twice in the same run.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from scribe_lib.config import Config, ScribeError

XLSX_MAX_MB = 20.0
SAMPLE_ROWS = 8
MERGE_CUES = 1

_PARSE_CORPUS_MODULE = None
_PARSE_CORPUS_PATH: Path | None = None
_CHUNKING_MODULE = None
_CHUNKING_PATH: Path | None = None
_TEXT_CACHE: dict[tuple[str, int, int], tuple[str | None, str]] = {}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ScribeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_parse_corpus(config: Config):
    global _PARSE_CORPUS_MODULE, _PARSE_CORPUS_PATH
    path = config.brain_skills / "corpus-taxonomy-extraction" / "parse_corpus.py"
    if not path.is_file():
        raise ScribeError(f"parse_corpus.py not found under {path}")
    if _PARSE_CORPUS_MODULE is None or _PARSE_CORPUS_PATH != path:
        _PARSE_CORPUS_MODULE = _load_module(path, "scribe_parse_corpus")
        _PARSE_CORPUS_PATH = path
    return _PARSE_CORPUS_MODULE


def load_chunking(config: Config):
    global _CHUNKING_MODULE, _CHUNKING_PATH
    path = config.brain_skills / "knowledge-index" / "chunking.py"
    if not path.is_file():
        raise ScribeError(f"chunking.py not found under {path}")
    if _CHUNKING_MODULE is None or _CHUNKING_PATH != path:
        _CHUNKING_MODULE = _load_module(path, "scribe_knowledge_index_chunking")
        _CHUNKING_PATH = path
    return _CHUNKING_MODULE


def extract(config: Config, path: Path) -> tuple[str | None, str]:
    """(markdown_or_None, method) via `parse_corpus.parse_one`, cached per file.

    Never raises for a per-file parse failure — mirrors `parse_one`'s own
    "skip with a reason" contract; callers that need to distinguish a hard
    error from "not this format" should call `parse_one` directly (as
    `gather_raw_task` does for its manifest's `status: "error"` entries).
    Returns `(None, "error: <exc>")` on an exception so a match-check caller
    (which only cares whether text came back) treats it as no-text-available
    without crashing raw-file selection over one bad file.
    """
    try:
        stat = path.stat()
    except OSError:
        return None, "unreadable"
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _TEXT_CACHE:
        return _TEXT_CACHE[key]
    parse_corpus = load_parse_corpus(config)
    try:
        md, method = parse_corpus.parse_one(str(path), XLSX_MAX_MB, SAMPLE_ROWS, merge_cues=MERGE_CUES)
    except Exception as exc:  # noqa: BLE001 - one bad file must not abort selection/parsing
        md, method = None, f"error: {exc}"
    _TEXT_CACHE[key] = (md, method)
    return _TEXT_CACHE[key]


def reset_cache() -> None:
    """Test helper: clear the module + per-file text caches between fixtures."""
    global _PARSE_CORPUS_MODULE, _PARSE_CORPUS_PATH, _CHUNKING_MODULE, _CHUNKING_PATH
    _PARSE_CORPUS_MODULE = None
    _PARSE_CORPUS_PATH = None
    _CHUNKING_MODULE = None
    _CHUNKING_PATH = None
    _TEXT_CACHE.clear()
