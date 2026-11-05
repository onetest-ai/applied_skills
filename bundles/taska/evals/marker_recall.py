"""$0 measures for leftovers.MARKERS: the share of an answer key's quotes the markers match, and the median number
of leftovers per 3,000 words over a store's meetings (no events applied)."""
import argparse
import json
import statistics
import sys
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parent.parent / "skills" / "run"
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from taska_lib import store as S  # noqa: E402
from taska_lib.leftovers import MARKERS, leftovers  # noqa: E402
from taska_lib.text import norm  # noqa: E402

WORDS_PER_UNIT = 3000


def key_quotes(path: Path) -> list[str]:
    quotes: list[str] = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        quote = json.loads(line).get("quote")
        if quote:
            quotes.append(quote)
    return quotes


def recall(path: Path) -> dict[str, object]:
    quotes = key_quotes(path)
    matched = sum(1 for q in quotes if MARKERS.search(norm(q)))
    return {"key": str(path), "quotes": len(quotes), "matched": matched,
            "recall": round(matched / len(quotes), 4) if quotes else 0.0}


def per_3000_words(project: Path) -> dict[str, object]:
    evidence = project / ".taska" / "evidence"
    rates: list[float] = []
    for f in sorted(evidence.glob("*.json")):
        item = json.loads(f.read_text())
        if item.get("kind") != "meeting":
            continue
        words = len(S.full_text(item).split())
        if words:
            rates.append(leftovers(item, [])["count"] * WORDS_PER_UNIT / words)
    return {"store": str(project), "meetings": len(rates),
            "median_per_3000_words": round(statistics.median(rates), 2) if rates else 0.0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", nargs="+", type=Path, default=[], help="answer key files (items.vN.jsonl)")
    ap.add_argument("--store", type=Path, help="project folder holding .taska/evidence")
    a = ap.parse_args(argv)
    result: dict[str, object] = {"keys": [recall(k) for k in a.key]}
    if a.store:
        result["store"] = per_3000_words(a.store)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
