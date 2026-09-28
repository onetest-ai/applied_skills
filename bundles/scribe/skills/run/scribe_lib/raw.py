"""gather-raw: select raw-replay files for a task, parse them via the Brain's
`parse_corpus.py`, and build a per-task FTS5 index over the parsed text.

Selection reuses `select_raw_files` (glob + exclude + case-insensitive
`match` on path or PARSED text — see `scribe_lib.parsing`) unchanged; this
module does not duplicate that logic, it only writes out what gets selected.
Parsing itself goes through `scribe_lib.parsing.extract`, the SAME call
`select_raw_files` uses to check a `match` term against a file's text, so a
file's selection decision and its parsed output can never disagree, and
(within one process) a file already parsed once during selection is not
parsed again here.

Writes, under `work/<task>/raw/`:
  <rel path>.md   -- parsed Markdown for each selected file that parsed OK, at
                     the SAME relative path it has under raw_root (nested dirs
                     created as needed) plus a trailing `.md` — unlike
                     parse_corpus.py's own CLI, which flattens output into one
                     dir with `__`-joined names, gather-raw keeps raw_root's
                     directory shape so a `[FILE:<path>#<locator>]` tag can
                     cite the original relative path directly.
  manifest.json   -- list[{path, sha256, md?, status, reason}], sorted by
                     path. `status` is "ok" | "skipped" | "error"; `md` (the
                     parsed file's path relative to `raw/`) is present only
                     when status == "ok". `reason` carries the parse method
                     for "ok" (e.g. "transcript-etl") and the skip/error
                     explanation otherwise.
  raw.sqlite      -- one FTS5 table `raw_fts(path, locator, text)`, one row
                     per section from `chunking.section_records()` — the
                     KNOWLEDGE-INDEX copy of chunking.py (see the "chunking.py
                     exists twice" invariant in CLAUDE.md; corpus-taxonomy-
                     extraction's copy is not used here) — for every "ok"
                     file. `locator` is that section's `breadcrumb_path` when
                     non-empty, else `§<ordinal>` (1-based, per file).

Re-running is idempotent: `work/<task>/raw/` is deleted and rebuilt from
scratch on every call — nothing here is incremental.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from typing import Any

from scribe_lib import parsing
from scribe_lib.config import Config, select_raw_files, sha256_file


def gather_raw_task(config: Config, task_id: str, instance: dict[str, Any], raw_inputs: dict[str, Any]) -> dict[str, Any]:
    """Select + parse this task's raw files; build raw/manifest.json + raw/raw.sqlite.

    `raw_inputs` is the task's resolved (template deep-merged with instance,
    `{{param}}`-substituted) `inputs.raw` dict, e.g. from
    `resolve_instance_inputs(instance, template).get("raw") or {}`.
    """
    chunking = parsing.load_chunking(config)

    files = select_raw_files(config, raw_inputs) or []

    raw_dir = config.work_dir / task_id / "raw"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)

    manifest: list[dict[str, Any]] = []
    parsed_texts: dict[str, str] = {}
    for path in files:
        rel = path.relative_to(config.raw_root).as_posix()
        sha = sha256_file(path)
        md, method = parsing.extract(config, path)
        if method.startswith("error:"):
            manifest.append({"path": rel, "sha256": sha, "status": "error", "reason": method[len("error: "):]})
            continue
        if not md:
            manifest.append({"path": rel, "sha256": sha, "status": "skipped", "reason": method})
            continue
        out_path = raw_dir / f"{rel}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        manifest.append({"path": rel, "sha256": sha, "md": f"{rel}.md", "status": "ok", "reason": method})
        parsed_texts[rel] = md

    manifest.sort(key=lambda e: e["path"])
    (raw_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    db_path = raw_dir / "raw.sqlite"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.execute("CREATE VIRTUAL TABLE raw_fts USING fts5(path, locator, text)")
        for rel, md in sorted(parsed_texts.items()):
            for i, rec in enumerate(chunking.section_records(md), start=1):
                locator = rec.get("breadcrumb_path") or f"§{i}"
                con.execute(
                    "INSERT INTO raw_fts(path, locator, text) VALUES (?, ?, ?)",
                    (rel, locator, rec.get("body", "")),
                )
        con.commit()
    finally:
        con.close()

    ok = sum(1 for m in manifest if m["status"] == "ok")
    return {
        "task": task_id,
        "raw_dir": str(raw_dir),
        "files": manifest,
        "counts": {
            "ok": ok,
            "skipped": sum(1 for m in manifest if m["status"] == "skipped"),
            "error": sum(1 for m in manifest if m["status"] == "error"),
        },
    }
