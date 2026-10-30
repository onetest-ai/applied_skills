"""check_reader.py OUT.json EVIDENCE_DIR

Validate independent-reader output against a meeting evidence directory.

CLI:
    python3 check_reader.py OUT.json EVIDENCE_DIR

Exit 0 when all checks pass, exit 1 when any fail.
Output: one JSON line {"ok": bool, "problems": [...], "meetings": int, "items": int}.

Importable:
    from check_reader import check
    result = check(out_list, Path("evidence/"))
"""
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# ── allowed field values ──────────────────────────────────────────────────────

REQUIRED_FIELDS = (
    "quote", "ts", "speaker", "title", "kind", "commitment",
    "owner", "due", "status", "client_owed", "covered_by", "note",
)

KIND_VALUES = {"task", "story", "decision", "risk"}
COMMITMENT_VALUES = {"committed", "proposed", "declined"}
STATUS_VALUES = {"open", "progress", "done", "held", None}

# Quote: 8–40 words (normalised)
QUOTE_MIN_WORDS = 8
QUOTE_MAX_WORDS = 40

# Note: ≥ 8 words
NOTE_MIN_WORDS = 8

# Note reuse: at most 2 items may share the same normalised note
NOTE_MAX_REUSE = 2

# Density: 1 item per this many characters, minimum 1 item
DENSITY_CHARS_PER_ITEM = 6_000


# ── helpers ───────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Normalise for comparison: NFKC, lowercase, collapse whitespace, strip punct."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[''‘’“”]", "'", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w']+", " ", s)).strip()


def _words(s: str) -> int:
    return len((s or "").split())


# ── main check function ───────────────────────────────────────────────────────

def check(out: list, evidence_dir: Path) -> dict:
    """Validate reader output.

    Args:
        out: list of meeting dicts, each with "evidence", "items", optional "empty_reason".
        evidence_dir: directory that contains <evidence_id>.md files.

    Returns:
        {"ok": bool, "problems": [str, ...], "meetings": int, "items": int}
        At most 15 problems are returned.
    """
    problems: list[str] = []
    meetings = 0
    total_items = 0

    for m in out:
        meetings += 1
        ev_id = m.get("evidence", "")
        ev_path = evidence_dir / f"{ev_id}.md"

        try:
            ev_text = ev_path.read_text(encoding="utf-8")
        except OSError:
            problems.append(f"{ev_id}: evidence file not found at {ev_path}")
            continue

        ev_norm = _norm(ev_text)
        ev_chars = len(ev_text)
        items: list[dict] = m.get("items", [])
        total_items += len(items)
        empty_reason = m.get("empty_reason", "")

        # ── density ──────────────────────────────────────────────────────────
        if not empty_reason:
            required = max(1, math.floor(ev_chars / DENSITY_CHARS_PER_ITEM))
            if len(items) < required:
                problems.append(
                    f"{ev_id}: too sparse — {len(items)} items for {ev_chars} chars "
                    f"(need at least {required})"
                )

        # ── note reuse ───────────────────────────────────────────────────────
        note_counts = Counter(
            _norm(x.get("note", "")) for x in items if x.get("note")
        )
        for note_text, count in note_counts.items():
            if count > NOTE_MAX_REUSE:
                problems.append(
                    f"{ev_id}: note reused more than twice — "
                    f"{note_text[:50]!r} ({count}×)"
                )

        # ── per-item checks ───────────────────────────────────────────────────
        for i, x in enumerate(items):
            prefix = f"{ev_id} #{i}"

            # Required fields present
            miss = [k for k in REQUIRED_FIELDS if k not in x]
            if miss:
                problems.append(f"{prefix}: missing fields {miss}")
                continue  # skip further checks — too many cascading errors

            # quote: verbatim (normalised) and 8–40 words
            q: str = x["quote"] or ""
            q_words = _words(q)
            if q_words < QUOTE_MIN_WORDS:
                problems.append(
                    f"{prefix}: quote too short ({q_words} words): {q[:60]!r}"
                )
            elif q_words > QUOTE_MAX_WORDS:
                problems.append(
                    f"{prefix}: quote too long ({q_words} words): {q[:60]!r}"
                )
            q_norm = _norm(q)
            if q_norm and q_norm not in ev_norm:
                problems.append(
                    f"{prefix}: quote not verbatim in transcript: {q[:60]!r}"
                )

            # kind
            if x["kind"] not in KIND_VALUES:
                problems.append(
                    f"{prefix}: bad kind {x['kind']!r} "
                    f"(allowed: {sorted(KIND_VALUES)})"
                )

            # commitment
            if x["commitment"] not in COMMITMENT_VALUES:
                problems.append(
                    f"{prefix}: bad commitment {x['commitment']!r} "
                    f"(allowed: {sorted(COMMITMENT_VALUES)})"
                )

            # status
            if x["status"] not in STATUS_VALUES:
                problems.append(
                    f"{prefix}: bad status {x['status']!r} "
                    f"(allowed: {sorted(v for v in STATUS_VALUES if v is not None)}|null)"
                )

            # due: null or YYYY-MM-DD
            due = x["due"]
            if due is not None:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(due)):
                    problems.append(
                        f"{prefix}: bad due format {due!r} (expected YYYY-MM-DD or null)"
                    )

            # covered_by: null or string
            cv = x["covered_by"]
            if cv is not None and not isinstance(cv, str):
                problems.append(
                    f"{prefix}: covered_by must be null or a node-id string, got {cv!r}"
                )

            # note: ≥ 8 words
            note: str = x.get("note") or ""
            n_words = _words(note)
            if n_words < NOTE_MIN_WORDS:
                problems.append(
                    f"{prefix}: note too short ({n_words} words): {note[:60]!r}"
                )

        if len(problems) >= 15:
            break  # cap at 15; stop accumulating

    return {
        "ok": len(problems) == 0,
        "problems": problems[:15],
        "meetings": meetings,
        "items": total_items,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 3:
        print(
            "usage: check_reader.py OUT.json EVIDENCE_DIR",
            file=sys.stderr,
        )
        sys.exit(2)
    out_path = Path(sys.argv[1])
    evidence_dir = Path(sys.argv[2])
    out = json.loads(out_path.read_text(encoding="utf-8"))
    result = check(out, evidence_dir)
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
