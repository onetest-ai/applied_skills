"""Pack a fact-check run's review data into one zip that travels with the deliverables (step 10).

    python review_bundle.py <work dir> <run dir> --out "<draft dir>/<name> — review data.zip"

The fact-checked copy and the findings page say what was found; this zip holds what the checker saw and decided,
so a reviewer can trace any comment back to it, even when the run happened somewhere they cannot open (a sandbox):
- from <work dir>: the extraction (sections.json, stats.json, batches.json, batch_*.json), the claims
  (claims_batch_*.json, coverage_batch_*.json, claims_figures.json), the verify chunks and their findings
  (chunks.json, chunk_*.json, findings_chunk_*.json), brain_context.json, timings.json, and media/ (the images
  the vision step read);
- from <run dir>: findings.json, run.json, coverage.json, baseline.md, baseline.sources.json.
The full page render (render/) is left out for size. A README.txt in the zip says what each file is.
Deterministic: the same files give a byte-identical zip. Refuses (exit 2, nothing written) an --out that is not a
.zip or a work dir that does not exist. Standard library only.

This file ships byte-identical in doc-fact-check/ and deck-fact-check/ (a drift test pins the copies).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import zipfile
from pathlib import Path

WORK_PATTERNS = ("sections.json", "stats.json", "batches.json", "batch_*.json", "claims_batch_*.json",
                 "coverage_batch_*.json", "claims_figures.json", "chunks.json", "chunk_*.json",
                 "findings_chunk_*.json", "slide_review_*.json", "brain_context.json", "timings.json", "media/*")
RUN_FILES = ("findings.json", "run.json", "coverage.json", "baseline.md", "baseline.sources.json")
README = """What this zip holds (fact-check review data)

findings.json        every claim with its verdict, evidence, fix and source (what the comments and the page show)
run.json             mode, coverage, counts, timings; coverage.json: sections with no checkable statement
baseline.md          the run's baseline table (claims, verdicts, evidence, trail)
work/sections.json   the extraction: every section, paragraph, table row and figure the checker read, with ids
work/stats.json      counts, risk tags, what was left out (hidden slides, text not on the slide)
work/batch_*.json    the text handed to each extract worker
work/claims_*.json   the claims extracted (quote, claim, questions), per batch and for figures
work/chunk_*.json    the claims handed to each verify worker; findings_chunk_*.json: what each returned
work/file_times.json when each worker's file was written (UTC); the zip itself stamps every entry 1980, so a trace of the run needs this
work/media/          the images the vision step read
"""
STAMP = (1980, 1, 1, 0, 0, 0)


def members(work: Path, run: Path) -> list[tuple[str, Path]]:
    out: dict[str, Path] = {}
    for pattern in WORK_PATTERNS:
        for p in sorted(work.glob(pattern)):
            if p.is_file():
                out[f"work/{p.relative_to(work).as_posix()}"] = p
    for name in RUN_FILES:
        if (run / name).is_file():
            out[name] = run / name
    return sorted(out.items())


def file_times(files: list[tuple[str, Path]]) -> dict[str, str]:
    """When each work file was last written (UTC, to the second); the images are not workers."""
    return {a: dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
            for a, p in files if a.startswith("work/") and not a.startswith("work/media/")}


def write_bundle(work, run, out) -> int:
    work, run, out = Path(work), Path(run), Path(out)
    files = members(work, run)
    extra = [("work/file_times.json", json.dumps(file_times(files), indent=2, sort_keys=True).encode("utf-8"))]
    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in [("README.txt", README.encode("utf-8"))] + [(a, p.read_bytes()) for a, p in files] + extra:
            info = zipfile.ZipInfo(arc, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    tmp.replace(out)
    return len(files)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("work", help="<work dir>: the reader's output folder")
    ap.add_argument("run", help="<run dir>: the folder holding findings.json and run.json")
    ap.add_argument("--out", required=True, help='"<draft dir>/<name> — review data.zip"')
    a = ap.parse_args(argv)
    if Path(a.out).suffix.lower() != ".zip":
        print(f"review_bundle.py: --out must end in .zip, got {a.out}", file=sys.stderr)
        return 2
    if not Path(a.work).is_dir():
        print(f"review_bundle.py: no such work dir: {a.work}", file=sys.stderr)
        return 2
    n = write_bundle(a.work, a.run, a.out)
    print(f"{a.out}: {n} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
