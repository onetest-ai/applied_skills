"""Candidate pool builder for meeting extraction evals.

build_pool(store, sources, evidence_ids, external=None) -> dict[str, list[dict]]

Gathers every work-item candidate any source surfaced for a meeting,
de-duplicates within a meeting using greedy clustering (span overlap in the
normalised transcript OR 5-word-shingle containment ≥ 0.5; never merging
two candidates from the same source), picks the shortest verbatim-in-one-turn
representative (≥ 8 words), and assigns stable cids sorted by transcript offset.

Candidates whose quote is not verbatim in any single turn are unverifiable:
they appear in pool_full.json but never in blind judge batches.

CLI:
    python3 pool.py --project P --source NAME=FILE [--source …]
                    [--external FILE] --evidence-ids FILE --out-dir DIR
                    [--batch-size 40]

Writes:
    DIR/pool_full.json          — all candidates (with "sources", "verifiable" fields)
    DIR/pool_unverifiable.json  — unverifiable candidates only (when any exist)
    DIR/judge_NN.json           — blind batches from verifiable candidates only
                                  (cid, evidence, quote, ts, speaker; no sources)
"""
import json
import re
import sys
from pathlib import Path

# Fix: _HERE is bundles/taska/evals/ so parent gives bundles/taska/
_HERE = Path(__file__).resolve().parent
_RUN_DIR = _HERE.parent / "skills" / "run"
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from taska_lib.evalscore import quote_shingles, containment
from taska_lib.store import quote_offset, evidence as _read_evidence, events as _read_events, full_text
from taska_lib.text import norm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_from_secs(t):
    """Convert seconds (int/float) to 'HH:MM:SS' string, or None."""
    if t is None:
        return None
    t = int(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ev_short(ev_id):
    """Last '_'-separated segment of an evidence id (the hash part, e.g. 'ab12cd34')."""
    return ev_id.split("_")[-1] if "_" in ev_id else ev_id[-8:]


def _spans_overlap(off1, len1, off2, len2):
    """True when [off1, off1+len1) and [off2, off2+len2) intersect.
    Returns False when either offset is negative (not found in transcript).
    """
    if off1 < 0 or off2 < 0:
        return False
    return off1 < off2 + len2 and off2 < off1 + len1


def _find_verbatim_turn(turns, norm_query):
    """Return the first turn whose normalised text contains norm_query, or None."""
    for turn in turns:
        if norm_query in norm(turn.get("text", "")):
            return turn
    return None


def _find_original_in_turn(turn_text, norm_query):
    """Extract original-casing span from turn_text matching norm_query.

    Tokenises turn_text into word-character runs, normalises each, and finds
    the token sequence that matches norm_query.split().  Returns the
    original-casing substring, or None if not found.
    """
    norm_q_words = norm_query.split()
    if not norm_q_words:
        return None

    tokens = list(re.finditer(r"[\w']+", turn_text))
    if not tokens:
        return None

    normed = [norm(t.group()) for t in tokens]

    nq = len(norm_q_words)
    for i in range(len(normed) - nq + 1):
        if normed[i:i + nq] == norm_q_words:
            start = tokens[i].start()
            end = tokens[i + nq - 1].end()
            return turn_text[start:end]
    return None


def _pick_representative(raw_members, turns):
    """Pick the representative from a cluster of raw candidates.

    Criteria (ruling b):
    - Must be verbatim in ONE transcript turn.
    - Among eligible members, prefer the shortest that still has ≥ 8 words.

    Returns (raw_member, turn, original_text) or (None, None, None).
    """
    eligible = []
    for raw in raw_members:
        q_norm = norm(raw["quote"])
        q_words = len(q_norm.split())
        if q_words < 8:
            continue
        turn = _find_verbatim_turn(turns, q_norm)
        if turn is None:
            continue
        orig = _find_original_in_turn(turn["text"], q_norm)
        if orig:
            eligible.append((q_words, raw, turn, orig))

    if not eligible:
        return None, None, None

    # Shortest first (ruling b: prefer shortest with ≥ 8 words)
    eligible.sort(key=lambda x: x[0])
    _, best_raw, best_turn, best_orig = eligible[0]
    return best_raw, best_turn, best_orig


# ---------------------------------------------------------------------------
# Core build_pool function
# ---------------------------------------------------------------------------

def build_pool(store, sources, evidence_ids, external=None):
    """Build a candidate pool from multiple sources.

    Parameters
    ----------
    store : Path
        Path to the .taska directory.
    sources : dict[str, list]
        Maps source name → reader-format output:
        [{"evidence": ..., "items": [{"quote", "ts", "speaker", ...}]}]
    evidence_ids : list[str]
        Evidence ids to include.  Raises ValueError for an id not in store.
    external : dict | None
        compare_external output ({"meetings": [...]}). Source "echo".

    Returns
    -------
    dict[str, list[dict]]
        {evidence_id: [candidate_dict]}
        Each candidate has: cid, evidence, quote, ts, speaker, offset, sources,
        verifiable.  Sorted by offset (ascending; unresolved last).
    """
    store = Path(store)
    ev_ids_set = set(evidence_ids)

    ev_dict = _read_evidence(store)
    evs = _read_events(store)

    # Refuse missing evidence ids
    for ev_id in evidence_ids:
        if ev_id not in ev_dict:
            raise ValueError(f"Evidence id not found in store: {ev_id!r}")

    # ── Gather raw candidates per evidence_id ─────────────────────────────────
    # raw[ev_id] = [{quote, ts, speaker, source}]
    raw: dict = {ev_id: [] for ev_id in evidence_ids}

    # 1. Reader sources
    for source_name, meetings in (sources or {}).items():
        for mtg in meetings:
            ev_id = mtg.get("evidence") or mtg.get("evidence_id") or ""
            if ev_id not in ev_ids_set:
                continue
            for item in mtg.get("items") or []:
                q = item.get("quote") or ""
                if not q:
                    continue
                raw[ev_id].append({
                    "quote": q,
                    "ts": item.get("ts"),
                    "speaker": item.get("speaker"),
                    "source": source_name,
                })

    # 2. Taska source: agent events with quotes (create/agent/note + propose)
    for e in evs:
        ev_id = e.get("evidence") or ""
        if ev_id not in ev_ids_set:
            continue
        if e.get("by") != "agent":
            continue
        etype = e.get("type")
        if etype not in ("create", "agent", "note"):
            continue
        q = e.get("quote") or ""
        if not q:
            continue
        raw[ev_id].append({
            "quote": q,
            "ts": e.get("ts"),
            "speaker": e.get("speaker"),
            "source": "taska",
        })

    # 3. Echo source: aligned spans from compare_external output
    if external:
        for mtg in external.get("meetings") or []:
            ev_id = mtg.get("taska_id") or ""
            if ev_id not in ev_ids_set:
                continue
            for action in mtg.get("actions") or []:
                span = action.get("span")
                if span is None:
                    continue
                raw[ev_id].append({
                    "quote": span,
                    "ts": _ts_from_secs(action.get("t")),
                    "speaker": None,
                    "source": "echo",
                })

    # ── Greedy clustering per evidence id ────────────────────────────────────
    result = {}

    for ev_id in evidence_ids:
        ev_item = ev_dict.get(ev_id)

        # Skip duplicate evidence items
        if ev_item and ev_item.get("duplicate_of"):
            result[ev_id] = []
            continue

        raw_list = raw.get(ev_id, [])
        turns = (ev_item or {}).get("turns") or []

        # Compute offsets and shingles for each raw candidate
        infos = []
        for r in raw_list:
            q_norm = norm(r["quote"])
            off = quote_offset(ev_item, r["quote"]) if ev_item else -1
            infos.append({
                "raw": r,
                "norm": q_norm,
                "offset": off,
                "norm_len": len(q_norm),
                "shingles": quote_shingles(q_norm),
            })

        # Greedy clustering: each candidate is compared only to the cluster
        # representative (first member), never merged with same-source items.
        clusters = []  # list of [list of info], first element = seed/representative

        for ci in infos:
            merged = False
            src = ci["raw"]["source"]

            for cluster in clusters:
                rep = cluster[0]

                # Never merge if this source already appears in the cluster
                if any(m["raw"]["source"] == src for m in cluster):
                    continue

                # Merge if spans overlap OR shingle containment ≥ 0.5 with representative
                if _spans_overlap(ci["offset"], ci["norm_len"],
                                  rep["offset"], rep["norm_len"]):
                    cluster.append(ci)
                    merged = True
                    break
                if containment(ci["shingles"], rep["shingles"]) >= 0.5:
                    cluster.append(ci)
                    merged = True
                    break

            if not merged:
                clusters.append([ci])

        # Build one candidate per cluster
        all_candidates = []

        for cluster in clusters:
            raw_members = [ci["raw"] for ci in cluster]
            sources_list = list(dict.fromkeys(ci["raw"]["source"] for ci in cluster))

            rep_raw, rep_turn, rep_orig = _pick_representative(raw_members, turns)

            if rep_turn is None:
                # Unverifiable: no single-turn verbatim quote ≥ 8 words
                seed = cluster[0]
                all_candidates.append({
                    "evidence": ev_id,
                    "quote": seed["raw"]["quote"],
                    "ts": seed["raw"].get("ts"),
                    "speaker": seed["raw"].get("speaker"),
                    "offset": seed["offset"] if seed["offset"] >= 0 else None,
                    "sources": sources_list,
                    "verifiable": False,
                    "_sort_key": seed["offset"] if seed["offset"] >= 0 else float("inf"),
                })
            else:
                # Verifiable: use original-casing from the containing turn
                rep_off = quote_offset(ev_item, rep_raw["quote"]) if ev_item else -1
                all_candidates.append({
                    "evidence": ev_id,
                    "quote": rep_orig,
                    "ts": rep_turn.get("ts"),
                    "speaker": rep_turn.get("speaker"),
                    "offset": rep_off if rep_off >= 0 else None,
                    "sources": sources_list,
                    "verifiable": True,
                    "_sort_key": rep_off if rep_off >= 0 else float("inf"),
                })

        # Sort by offset (ascending; unresolved offsets last)
        all_candidates.sort(key=lambda c: c["_sort_key"])

        # Assign stable cids
        ev_short = _ev_short(ev_id)
        for i, c in enumerate(all_candidates):
            c["cid"] = f"{ev_short}-{i + 1:03d}"
            del c["_sort_key"]

        result[ev_id] = all_candidates

    return result


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_outputs(pool, out_dir, batch_size=40):
    """Write pool_full.json, pool_unverifiable.json (if any), and judge batches.

    pool_full.json: all candidates (verifiable + unverifiable) with sources.
    pool_unverifiable.json: only unverifiable candidates.
    judge_NN.json: blind batches from verifiable candidates (no sources/verifiable field).

    Clears any stale judge_*.json before writing new ones.
    Does not write any batch file when there are no verifiable candidates.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Clear stale judge batch files
    for f in out_dir.glob("judge_*.json"):
        f.unlink()

    # Separate verifiable and unverifiable
    verifiable_all = []
    unverifiable_all = []
    for ev_id, candidates in pool.items():
        for c in candidates:
            if c.get("verifiable", True):
                verifiable_all.append(c)
            else:
                unverifiable_all.append(c)

    # Write pool_full.json (all candidates)
    (out_dir / "pool_full.json").write_text(
        json.dumps(pool, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Write pool_unverifiable.json if any
    if unverifiable_all:
        unverifiable_by_ev: dict = {}
        for c in unverifiable_all:
            unverifiable_by_ev.setdefault(c["evidence"], []).append(c)
        (out_dir / "pool_unverifiable.json").write_text(
            json.dumps(unverifiable_by_ev, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # Write judge batches (verifiable only, blind: no sources/verifiable)
    if not verifiable_all:
        return

    batch_num = 1
    for start in range(0, len(verifiable_all), batch_size):
        batch = verifiable_all[start:start + batch_size]
        blind_batch = [
            {
                "cid": c["cid"],
                "evidence": c["evidence"],
                "quote": c["quote"],
                "ts": c["ts"],
                "speaker": c["speaker"],
            }
            for c in batch
        ]
        (out_dir / f"judge_{batch_num:02d}.json").write_text(
            json.dumps(blind_batch, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        batch_num += 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    ap = argparse.ArgumentParser(description="Build candidate pool for meeting extraction evals")
    ap.add_argument("--project", required=True, metavar="DIR",
                    help="Project directory (contains taska.toml)")
    ap.add_argument("--source", action="append", default=[], metavar="NAME=FILE",
                    help="Reader-format source: NAME=path/to/output.json (repeatable)")
    ap.add_argument("--external", metavar="FILE",
                    help="compare_external output JSON")
    ap.add_argument("--evidence-ids", required=True, metavar="FILE",
                    help="JSON file containing a list of evidence ids")
    ap.add_argument("--out-dir", required=True, metavar="DIR",
                    help="Output directory")
    ap.add_argument("--batch-size", type=int, default=40, metavar="N",
                    help="Candidates per judge batch file (default 40)")
    args = ap.parse_args()

    project = Path(args.project)
    from taska_lib import config as C
    cfg = C.load(project)
    store = cfg.store

    sources = {}
    for s in args.source:
        if "=" not in s:
            ap.error(f"--source must be NAME=FILE, got: {s!r}")
        name, _, path = s.partition("=")
        sources[name] = json.loads(Path(path).read_text(encoding="utf-8"))

    evidence_ids = json.loads(Path(args.evidence_ids).read_text(encoding="utf-8"))

    external = None
    if args.external:
        external = json.loads(Path(args.external).read_text(encoding="utf-8"))

    pool = build_pool(store, sources, evidence_ids, external)
    write_outputs(pool, Path(args.out_dir), batch_size=args.batch_size)

    total = sum(len(v) for v in pool.values())
    verifiable = sum(1 for v in pool.values() for c in v if c.get("verifiable", True))
    print(json.dumps({
        "ok": True,
        "evidence_ids": len(evidence_ids),
        "candidates": total,
        "verifiable": verifiable,
    }))


if __name__ == "__main__":
    main()
