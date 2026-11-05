"""Store snapshot: copy the store up to (exclusive) a given evidence item for a re-extraction run.

`snapshot(store, before, out)` writes:
  out/taska.toml      copy of the source project config, with store rewritten to ".taska" if custom
  out/.taska/evidence/ every .json and .md evidence file (duplicates included)
  out/.taska/events.jsonl  filtered events (pre-cut only)

Events kept in the copy:
  - plan events          (evidence is None, type == "create", by == "plan")
  - events before cut    evidence is in S.order(ev) at a position before `before`
  - human events         by == "human" and date < ev[before]["date"]
  - verdict events       only when their mid belongs to a kept event

Events of items marked duplicate_of are excluded (they are not in S.order).
The source store is never written to.
"""
import json
import re
import shutil
import tomllib
from pathlib import Path

from . import store as S


def snapshot(store: Path, before: str, out: Path) -> dict:
    """Copy the store into *out*, keeping only events that precede *before*.

    Returns {"events": int, "evidence": int, "pending": list[str]}.
    Raises ValueError when *out* is not empty or *before* is not in S.order(ev).
    """
    store = Path(store)
    out = Path(out)

    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out}: directory exists and is not empty")

    ev = S.evidence(store)
    order = S.order(ev)

    if before not in order:
        raise ValueError(f"{before!r} is not in the evidence order")

    before_idx = order.index(before)
    before_date = ev[before]["date"]
    before_set = set(order[:before_idx])

    evs = S.events(store)

    # Two-pass: first collect non-verdict kept events (and record their mids),
    # then include verdict events whose mid appears among the kept mids.
    keep_idx: set[int] = set()
    kept_mids: set[str] = set()

    for i, e in enumerate(evs):
        if e["type"] == "verdict":
            continue
        keep = False
        # Condition 1: plan events have no evidence reference
        if e.get("evidence") is None and e.get("type") == "create" and e.get("by") == "plan":
            keep = True
        # Condition 2: evidence item is before the cut in S.order (a re-synced copy counts as its original)
        elif (ev.get(e.get("evidence"), {}).get("duplicate_of") or e.get("evidence")) in before_set:
            keep = True
        # Condition 3: human events dated strictly before the cut date
        elif e.get("by") == "human" and (e.get("date") or "") < before_date:
            keep = True
        if keep:
            keep_idx.add(i)
            if e.get("mid"):
                kept_mids.add(e["mid"])

    # Condition 4: verdict events for any kept event mid
    for i, e in enumerate(evs):
        if e["type"] == "verdict" and e.get("mid") in kept_mids:
            keep_idx.add(i)

    # Preserve original jsonl order
    final_events = [evs[i] for i in sorted(keep_idx)]

    # Pending: items at or after the cut, kind != "message", not processed in the copy
    copy_processed = S.processed(final_events)
    pending = [
        c for c in order[before_idx:]
        if ev[c]["kind"] != "message" and c not in copy_processed
    ]

    # Build output directories
    out.mkdir(parents=True, exist_ok=True)
    copy_store = out / ".taska"
    copy_ev_dir = copy_store / "evidence"
    copy_ev_dir.mkdir(parents=True, exist_ok=True)

    # Copy all evidence files (duplicates included)
    src_ev_dir = S.P(store, "evidence")
    evidence_count = 0
    if src_ev_dir.exists():
        for f in sorted(src_ev_dir.iterdir()):
            if f.suffix in (".json", ".md"):
                shutil.copy2(f, copy_ev_dir / f.name)
                if f.suffix == ".json":
                    evidence_count += 1

    # Write filtered events.jsonl (empty file when no events kept)
    (copy_store / "events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in final_events)
    )

    # Copy and (when needed) adjust taska.toml so the copy's store is out/.taska
    toml_path = store.parent / "taska.toml"
    toml_text = toml_path.read_text()
    raw = tomllib.loads(toml_text)
    explicit_store = (raw.get("project") or {}).get("store")
    if explicit_store not in (None, ".taska"):
        # Rewrite the first `store = …` line under [project] to use the default path
        toml_text = re.sub(
            r"(?m)(^\s*store\s*=\s*).*$",
            lambda m: m.group(1) + '".taska"',
            toml_text,
            count=1,
        )
    (out / "taska.toml").write_text(toml_text)

    return {"events": len(final_events), "evidence": evidence_count, "pending": pending}
