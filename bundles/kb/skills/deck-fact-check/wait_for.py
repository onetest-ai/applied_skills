"""Wait until stage-E or stage-V workers have written their files, in one plain command.

    python wait_for.py <work dir> --glob "findings_chunk_*.json" --count 10 [--glob "coverage_batch_*.json" ...] [--timeout 120]

Counts the complete files (non-empty, and valid JSON when the name ends in .json, so a half-written file is not counted) matching each
--glob; every glob must reach --count. Prints `ready: ...` when they do, or `waiting: ... run this command again` after --timeout
seconds (capped below a tool call's time limit). It always exits 0 on either answer: waiting is not a failure. This is the documented
way to wait: the shell blocks `sleep`, and any other improvised shape (`ls`, `until` loops, `timeout`, `&&` chains) asks the user to
approve it. Standard library only; shared byte-identical by doc-fact-check and deck-fact-check.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

MAX_TIMEOUT = 170          # a tool call is cut off at about 180 seconds


def clamp_timeout(v: float) -> float:
    return max(1, min(float(v), MAX_TIMEOUT))


def _complete(p: Path) -> bool:
    try:
        if not p.is_file() or p.stat().st_size == 0:
            return False
        if p.suffix == ".json":
            json.loads(p.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def count(d: Path, pattern: str) -> int:
    return sum(_complete(p) for p in Path(d).glob(pattern))


def _line(word: str, d: Path, globs: list[str], n: int, extra: str = "") -> str:
    return f"{word}: " + ", ".join(f"{g} {count(d, g)} of {n}" for g in globs) + extra


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", type=Path, help="the work dir the workers write into")
    ap.add_argument("--glob", action="append", required=True, help="file pattern; repeat it for several (each must reach --count)")
    ap.add_argument("--count", type=int, required=True, help="how many complete files each pattern needs")
    ap.add_argument("--timeout", type=float, default=120, help=f"seconds to wait before answering `waiting` (1 to {MAX_TIMEOUT})")
    ap.add_argument("--interval", type=float, default=2, help="seconds between looks")
    a = ap.parse_args(argv)
    end = time.monotonic() + clamp_timeout(a.timeout)
    while True:
        if all(count(a.dir, g) >= a.count for g in a.glob):
            print(_line("ready", a.dir, a.glob, a.count))
            return 0
        if time.monotonic() >= end:
            print(_line("waiting", a.dir, a.glob, a.count, "; run this command again"))
            return 0
        time.sleep(max(0.05, min(a.interval, end - time.monotonic())))


if __name__ == "__main__":
    sys.exit(main())
