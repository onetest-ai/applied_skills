"""check_judge.py JUDGED.json BATCH.json

Validate blind-judge output against its batch file.

CLI:
    python3 check_judge.py JUDGED.json BATCH.json

Exit 0 when all checks pass, exit 1 when any fail.
Output: one JSON line {"ok": bool, "problems": [...]}.

Importable:
    from check_judge import check
    result = check(judged_list, batch_list)
"""
import datetime
import json
import sys
from collections import Counter
from pathlib import Path

# Ensure taska_lib is importable regardless of calling directory
_HERE = Path(__file__).resolve().parent
_RUN_DIR = _HERE.parent / "skills" / "run"
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from taska_lib.text import norm as _norm

# ── allowed field values ──────────────────────────────────────────────────────

COMMITMENT_VALUES = {"committed", "proposed", "declined", "not_work"}
KIND_VALUES = {"task", "story", "decision", "risk"}
STATUS_VALUES = {"open", "progress", "done", "held", None}

NOTE_MIN_WORDS = 8
NOTE_MAX_REUSE = 2


# ── helpers ───────────────────────────────────────────────────────────────────

def _words(s):
    return len((s or "").split())


def _valid_date(s):
    """True when s is a valid YYYY-MM-DD date string."""
    try:
        datetime.date.fromisoformat(str(s))
        return True
    except (ValueError, TypeError):
        return False


# ── main check function ───────────────────────────────────────────────────────

def check(judged, batch):
    """Validate judge output against batch.

    Parameters
    ----------
    judged : list of dicts
        The judge's output.
    batch : list of dicts
        The input batch with at least "cid" per item.

    Returns
    -------
    {"ok": bool, "problems": [str, ...]}
    """
    problems = []

    # Non-list input
    if not isinstance(judged, list):
        return {"ok": False, "problems": [
            f"judged output must be a list, got {type(judged).__name__!r}"
        ]}
    if not isinstance(batch, list):
        return {"ok": False, "problems": [
            f"batch must be a list, got {type(batch).__name__!r}"
        ]}

    batch_cids = [item["cid"] for item in batch]
    batch_cids_set = set(batch_cids)

    judged_cids = [item.get("cid") for item in judged]
    cid_counts = Counter(judged_cids)

    # ── Coverage: every batch cid answered exactly once ───────────────────────
    for cid in batch_cids:
        if cid_counts.get(cid, 0) == 0:
            problems.append(f"cid {cid!r}: not answered in judged output (missing)")
            if len(problems) >= 15:
                return {"ok": False, "problems": problems[:15]}

    for cid, count in cid_counts.items():
        if count > 1:
            problems.append(f"cid {cid!r}: answered {count} times (duplicate)")
            if len(problems) >= 15:
                return {"ok": False, "problems": problems[:15]}

    for cid in judged_cids:
        if cid is not None and cid not in batch_cids_set:
            problems.append(f"cid {cid!r}: in judged output but not in batch (extra)")
            if len(problems) >= 15:
                return {"ok": False, "problems": problems[:15]}

    # ── Note reuse ────────────────────────────────────────────────────────────
    note_counts = Counter(
        _norm(item.get("note", "")) for item in judged if item.get("note")
    )
    for note_text, count in note_counts.items():
        if count > NOTE_MAX_REUSE:
            problems.append(
                f"note reused {count} times (max {NOTE_MAX_REUSE}): {note_text[:50]!r}"
            )
            if len(problems) >= 15:
                return {"ok": False, "problems": problems[:15]}

    # ── Per-item checks ───────────────────────────────────────────────────────
    for item in judged:
        cid = item.get("cid") or "?"
        commitment = item.get("commitment")
        is_not_work = commitment == "not_work"

        # commitment enum
        if commitment not in COMMITMENT_VALUES:
            problems.append(
                f"cid {cid!r}: bad commitment {commitment!r} "
                f"(allowed: {sorted(COMMITMENT_VALUES)})"
            )

        if is_not_work:
            # For not_work: note required
            note = item.get("note") or ""
            if _words(note) < NOTE_MIN_WORDS:
                problems.append(
                    f"cid {cid!r}: note too short ({_words(note)} words) for not_work item"
                )
        else:
            # title required
            if not item.get("title"):
                problems.append(f"cid {cid!r}: title is required when commitment != not_work")

            # kind enum
            kind = item.get("kind")
            if kind not in KIND_VALUES:
                problems.append(
                    f"cid {cid!r}: bad kind {kind!r} "
                    f"(allowed: {sorted(KIND_VALUES)})"
                )

            # status enum
            status = item.get("status")
            if status not in STATUS_VALUES:
                problems.append(
                    f"cid {cid!r}: bad status {status!r} "
                    f"(allowed: {sorted(v for v in STATUS_VALUES if v is not None)}|null)"
                )

            # due: null or valid YYYY-MM-DD
            due = item.get("due")
            if due is not None:
                if not _valid_date(due):
                    problems.append(
                        f"cid {cid!r}: bad due {due!r} (expected valid YYYY-MM-DD or null)"
                    )

            # client_owed must be bool
            co = item.get("client_owed")
            if not isinstance(co, bool):
                problems.append(
                    f"cid {cid!r}: client_owed must be true or false (bool), got {co!r}"
                )

            # note: ≥ 8 words
            note = item.get("note") or ""
            if _words(note) < NOTE_MIN_WORDS:
                problems.append(
                    f"cid {cid!r}: note too short ({_words(note)} words)"
                )

        if len(problems) >= 15:
            break

    return {
        "ok": len(problems) == 0,
        "problems": problems[:15],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("usage: check_judge.py JUDGED.json BATCH.json", file=sys.stderr)
        sys.exit(2)
    judged = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    batch = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    result = check(judged, batch)
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
