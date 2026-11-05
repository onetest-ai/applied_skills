"""compare_external: compare an external note-taker's actions against the taska tree.

compare_external(store, external, gold, tz) -> result dict

External format (the bundle knows nothing about Echo):
  [{"meeting_at": iso, "title": str,
    "actions": [{"task": str, "owner": str|null, "due": str|null,
                 "quote": str, "t": int, "status": str}],
    "decisions": [str]}]

Result:
  {"meetings": [{"taska_id", "echo_actions", "taska_items", "gold_items",
                  "tracked", "gap_candidates", "decisions_untracked",
                  "actions": [{"task","owner","t","aligned_by","span","status","node",
                               "nearest_event"?: {"node","distance_chars"}}]}],
   "unmatched_meetings": [...]}

Every Echo action appears exactly once in `actions` with one of:
  status="tracked"  — span aligned and matched to a taska node (node = node id)
  status="gap"      — span aligned but no matching event quote overlaps the span
                      (node = null); also in gap_candidates; "nearest_event" shows the
                      closest extraction by character offset — a large distance means a
                      different part of the transcript fired, not that the action was
                      necessarily missed
  status="unaligned"— no span found in the transcript (node = null)

A "gap" means "no event quote overlaps this span", not "certainly missing" — the
extraction may have fired on a different speaker turn that covers the same work item.

Meeting-matching helpers (`_a1_matches`, `_parse_dt`) are imported from
`ingest_audit` (private names — noted here; they are stable internal helpers).
"""
import datetime as dt
from pathlib import Path

from . import ledger as L, store as S
from .evalscore import _card_match, match_item
from .ingest_audit import _a1_matches, _parse_dt  # private; stable internal helpers
from .text import norm


# ── shingle helpers ───────────────────────────────────────────────────────────

def _shingle_set(text, k=5):
    """k-word shingles of norm(text). Empty set when fewer than k words."""
    words = norm(text).split()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)} if len(words) >= k else set()


def _containment(a, b):
    """|A∩B| / min(|A|,|B|). 0.0 when either set is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# ── alignment ─────────────────────────────────────────────────────────────────

def _best_window_span(transcript_words, echo_text, min_w=12, max_w=40):
    """Slide a window of len(echo_text.split()) words (clamped to [min_w, max_w]) over
    transcript_words. Return the verbatim normalised window whose 5-word shingle
    containment with echo_text is best and ≥ 0.6, else None.
    """
    if not echo_text or not transcript_words:
        return None
    echo_words = norm(echo_text).split()
    if not echo_words:
        return None
    w = max(min_w, min(max_w, len(echo_words)))
    eq_shingles = _shingle_set(echo_text)
    if not eq_shingles:
        # Fewer than 5 words: cannot compute 5-word shingle containment
        return None
    best_score = 0.0
    best_span = None
    n = len(transcript_words)
    for i in range(max(1, n - w + 1)):
        window = " ".join(transcript_words[i:i + w])
        ws = _shingle_set(window)
        c = _containment(eq_shingles, ws)
        if c > best_score:
            best_score = c
            best_span = window
    return best_span if best_score >= 0.6 else None


def _align_action(action, ev_item):
    """Try to align an Echo action to the taska transcript.

    Tries the action's quote first, then the task text.  Returns
    (span, aligned_by) where span is the verbatim normalised transcript window
    (shingle containment ≥ 0.6) and aligned_by is "quote" or "task",
    or (None, None) when no window meets the threshold.
    """
    full = S.full_text(ev_item)
    words = norm(full).split()
    if not words:
        return None, None
    for text, source in [(action.get("quote") or "", "quote"),
                         (action.get("task") or "", "task")]:
        span = _best_window_span(words, text)
        if span is not None:
            return span, source
    return None, None


# ── tracking check ────────────────────────────────────────────────────────────

def _tracked_node(span, ev_id, N, evs, ev):
    """Return the node id that matches span by the Task 3 match rule, or None.

    Builds a gold-like dict and delegates to evalscore.match_item so that the
    same shingle-containment ≥ 0.5 / offset-within-400 rule is used throughout.
    """
    if not span:
        return None
    g = {"evidence": ev_id, "quote": span, "covered_by": None}
    return match_item(g, N, evs, ev)  # returns node id or None


# ── nearest-event for gap diagnostics ────────────────────────────────────────

def _nearest_event(span, ev_id, ev_item, evs):
    """Return {"node": id, "distance_chars": int} for the agent event of ev_id
    whose quote offset is closest to span's offset in the full text, or None.

    Used to give gap candidates context: a large distance means the extraction
    fired on a different speaker turn, not that the action was definitely missed.
    """
    if not span or not ev_item:
        return None
    full = norm(S.full_text(ev_item))
    span_off = full.find(span)
    if span_off < 0:
        return None
    best_dist = None
    best_node = None
    for e in evs:
        if e.get("evidence") != ev_id:
            continue
        if e.get("type") not in ("create", "agent", "note"):
            continue
        if e.get("by") != "agent":
            continue
        q = e.get("quote") or ""
        if not q:
            continue
        eq_off = S.quote_offset(ev_item, q)
        if eq_off < 0:
            continue
        dist = abs(span_off - eq_off)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            etype = e.get("type")
            best_node = e.get("id") if etype == "create" else e.get("node")
    return {"node": best_node, "distance_chars": best_dist} if best_node is not None else None


# ── decision tracking ─────────────────────────────────────────────────────────

def _decision_tracked(dec, ev_id, N, evs):
    """True when any decision node created in ev_id has a create-event quote with
    shingle containment ≥ 0.5 against dec.
    """
    dec_shingles = _shingle_set(dec)
    if not dec_shingles:
        return False
    # Build a lookup: node_id -> create-event quote for decisions created in ev_id
    create_quote = {
        e.get("id"): (e.get("quote") or "")
        for e in evs
        if e.get("type") == "create" and e.get("evidence") == ev_id
    }
    for n in N.values():
        if n.get("created_in") != ev_id:
            continue
        if n.get("kind") != "decision":
            continue
        q = create_quote.get(n["id"], "")
        if q and _containment(dec_shingles, _shingle_set(q)) >= 0.5:
            return True
    return False


# ── main entry point ──────────────────────────────────────────────────────────

def compare_external(store, external, gold, tz):
    """Compare external note-taker actions against the taska tree.

    Parameters
    ----------
    store    : Path to the .taska directory.
    external : list of {"meeting_at": iso, "title": str,
                         "actions": [...], "decisions": [...]}.
    gold     : list of gold annotation dicts (for gold_items counts) or None.
    tz       : project timezone name.

    Returns
    -------
    {"meetings": [{"taska_id", "echo_actions", "taska_items", "gold_items",
                    "tracked", "gap_candidates", "decisions_untracked",
                    "actions": per-action list with status/node}],
     "unmatched_meetings": [{"title", "meeting_at", "reason"}]}
    """
    store = Path(store)
    ev = S.evidence(store)
    evs = S.events(store)
    N, _notes, _order, _ = L.fold(store, evs=evs, ev=ev)
    # work read from a re-synced copy counts for the original meeting (as in the fold and the scorer)
    evs = [dict(e, evidence=ev[e["evidence"]]["duplicate_of"])
           if ev.get(e.get("evidence"), {}).get("duplicate_of") else e for e in evs]

    # Active (non-duplicate) meetings only
    meetings = [x for x in ev.values()
                if x.get("kind") == "meeting" and not x.get("duplicate_of")]
    m_dt = {m["id"]: dt.datetime.fromisoformat(m["at"][:19]) for m in meetings}

    # Gold items indexed by meeting evidence id
    gold_per_meeting: dict = {}
    if gold is not None:
        for g in gold:
            evid = g.get("evidence")
            if evid:
                gold_per_meeting.setdefault(evid, []).append(g)

    result_meetings = []
    unmatched = []

    for ext_mtg in external:
        ext_at_str = ext_mtg.get("meeting_at") or ""
        ext_title = ext_mtg.get("title") or ""
        try:
            ext_at = _parse_dt(ext_at_str, tz)
        except (ValueError, KeyError):
            unmatched.append({"title": ext_title, "meeting_at": ext_at_str,
                               "reason": "invalid meeting_at"})
            continue

        # Match to a taska meeting: _a1_matches enforces ±20 min + ≥ 2 shared title
        # tokens (or same-day when the taska meeting has a guessed time).
        matched_m = None
        for m in meetings:
            if _a1_matches(m, m_dt[m["id"]], ext_at, ext_title):
                matched_m = m
                break

        if matched_m is None:
            unmatched.append({"title": ext_title, "meeting_at": ext_at_str,
                               "reason": "no taska meeting within ±20 min with ≥ 2 shared title tokens"})
            continue

        ev_id = matched_m["id"]
        ev_item = ev.get(ev_id)

        # Count taska items (tasks, stories, decisions, risks) created by the agent
        # for this meeting and not deleted/merged.
        taska_items = sum(
            1 for n in N.values()
            if n.get("created_in") == ev_id
            and n.get("kind") in ("task", "story", "decision", "risk")
            and n.get("state") not in ("deleted", "merged")
            and n.get("by") == "agent"
        )

        gold_items = len(gold_per_meeting.get(ev_id, [])) if gold is not None else None

        # Process Echo actions — build per-action list and derive counts/gaps
        tracked = carded = unaligned = 0
        gap_candidates = []
        actions_list = []
        for action in ext_mtg.get("actions") or []:
            span, aligned_by = _align_action(action, ev_item) if ev_item else (None, None)
            ne = None  # nearest_event; set only for gap entries
            if span is not None:
                node_id = _tracked_node(span, ev_id, N, evs, ev)
                if node_id is not None:
                    status = "tracked"
                    tracked += 1
                elif _card_match({"evidence": ev_id, "quote": span}, evs, ev):
                    status = "card"            # held as a proposal card the user still has to confirm
                    tracked += 1
                    carded += 1
                else:
                    status = "gap"
                    ne = _nearest_event(span, ev_id, ev_item, evs)
                    gap_candidates.append({
                        "task": action.get("task") or "",
                        "owner": action.get("owner"),
                        "t": action.get("t"),
                        "span": span,
                        "nearest_event": ne,
                    })
            else:
                node_id = None
                aligned_by = None
                status = "unaligned"
                unaligned += 1
            entry = {
                "task": action.get("task") or "",
                "owner": action.get("owner"),
                "t": action.get("t"),
                "aligned_by": aligned_by,
                "span": span,
                "status": status,
                "node": node_id,
            }
            if status == "gap":
                entry["nearest_event"] = ne
            actions_list.append(entry)

        # Process Echo decisions
        decisions_untracked = [
            dec for dec in ext_mtg.get("decisions") or []
            if not _decision_tracked(dec, ev_id, N, evs)
        ]

        result_meetings.append({
            "taska_id": ev_id,
            "echo_actions": len(ext_mtg.get("actions") or []),
            "taska_items": taska_items,
            "gold_items": gold_items,
            "tracked": tracked,
            "carded": carded,
            "unaligned": unaligned,
            "unaligned_rate": round(unaligned / len(actions_list), 3) if actions_list else None,
            "actions": actions_list,
            "gap_candidates": gap_candidates,
            "decisions_untracked": decisions_untracked,
        })

    return {"meetings": result_meetings, "unmatched_meetings": unmatched}
