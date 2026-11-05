"""Extraction confidence scorer: compare a store against a gold annotation file.

score(store, gold) -> result dict with B7–B18 metrics.
match_item(g, N, events, ev) -> matched node id or None.

Gold offset convention: `offset` is the byte index of norm(quote) inside
norm(full_text(item)) — the same coordinate space that store.quote_offset returns.
"""
import json
import math
import re
from collections import Counter
from pathlib import Path

from . import config as C, ledger as L, store as S
from .text import norm


# ---------------------------------------------------------------------------
# Shingle helpers
# ---------------------------------------------------------------------------

def quote_shingles(s, k=5):
    """k-word shingles of norm(s). Empty set when fewer than k words."""
    words = norm(s).split()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)} if len(words) >= k else set()


def containment(a, b):
    """|A∩B| / min(|A|,|B|). 0.0 when either set is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# Private aliases for internal use (backward compatibility)
_quote_shingles = quote_shingles
_containment = containment


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _wilson(ok, n, z=1.96):
    """Wilson lower bound for a proportion. n must be > 0."""
    p = ok / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = p + z2 / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return round((centre - spread) / denom, 3)


BUCKET_NAMES = {
    "B7": "Committed work found", "B7c": "Committed work found, proposal cards counted", "B8": "Proposed work found",
    "B9": "Committed work found in the last third", "B10": "Owner right", "B11": "Due date right", "B12": "Status right",
    "B13": "Decisions and risks found", "B14": "Client-owed flag right", "B15": "Key-match precision (lower bound)",
    "B16": "Declined work not created", "B18": "Proposal cards matching the key (lower bound)"}
UNMATCHED = "not matched to the key (unjudged)"


def _gap(ok, n, fails):
    """Build a gap dict. rate and lb95 are None when n == 0. `fails` is never cut: a capped list hides misses."""
    if n:
        rate = round(ok / n, 3)
        lb95 = _wilson(ok, n)
    else:
        rate = None
        lb95 = None
    return {"n": n, "ok": ok, "rate": rate, "lb95": lb95, "fails": fails}


def _fail(g, why):
    return {"evidence": g.get("evidence", ""), "quote": (g.get("quote") or "")[:80],
            "title": g.get("title") or "", "why": why}


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------

def _node_owner(n):
    return n.get("internal_owner") or n.get("client_owner")


def owner_matches(cfg, node_owner, gold_owner):
    """The node's owner is the key's: the same person by canonical name, or the key names a team and the node names
    that team or one of its members."""
    if not node_owner or not gold_owner:
        return False
    nown, gown = cfg.canonical(node_owner), cfg.canonical(gold_owner)
    if nown == gown:
        return True
    team = cfg.team(gold_owner)
    return bool(team) and (nown == team.name or nown in {cfg.canonical(m) for m in team.members})


def _map_node_state(state):
    return {"in_progress": "progress", "done": "done", "blocked": "held"}.get(state, "open")


def _map_gold_status(status):
    """The key's `held` is "paused or blocked" (READER.md), not the ledger's `held` effect (a meeting took place)."""
    return status if status in ("progress", "done", "held") else "open"


def _resolve_node(node_id, N):
    """Follow merged_into chain from node_id; return None when final node is deleted or absent."""
    if not node_id or node_id not in N:
        return None
    cur = node_id
    seen = set()
    while cur in N and N[cur].get("merged_into"):
        if cur in seen:
            break  # cycle guard
        seen.add(cur)
        cur = N[cur]["merged_into"]
    n = N.get(cur)
    if n is None or n["state"] == "deleted":
        return None
    return cur


# ---------------------------------------------------------------------------
# Core match logic
# ---------------------------------------------------------------------------

def _hit_strength(e, gold_shingles, gold_offset, ev_item):
    """(containment, distance) when event e hits gold: its quote has 5-word containment ≥ 0.5 with the gold quote, or
    sits within 400 characters of it in the transcript. None when it does not hit."""
    eq = norm(e.get("quote") or "")
    cont = 0.0
    if gold_shingles and eq:
        esh = _quote_shingles(eq)
        if esh:
            cont = _containment(gold_shingles, esh)
    dist = float("inf")
    if gold_offset is not None and ev_item:
        eq_off = S.quote_offset(ev_item, e.get("quote") or "")
        if eq_off >= 0:
            dist = abs(eq_off - gold_offset)
    return (cont, dist) if cont >= 0.5 or dist <= 400 else None


def _event_hit(e, gold_shingles, gold_offset, ev_item):
    return _hit_strength(e, gold_shingles, gold_offset, ev_item) is not None


SPECIFICITY = {"task": 3, "story": 2, "decision": 2, "risk": 2, "epic": 0}
STAGES = ("matched", "card", "applied_not_live", "proposed_not_applied", "not_proposed", "meeting_not_processed")


def _gold_quotes(g):
    """Every quote of a key item: a merged item (key_tools.collapse_near_duplicates) carries them all in `quotes`."""
    return g.get("quotes") or [g.get("quote") or ""]


def _gold_params(g, ev, quote=None):
    """Return (gold_shingles, gold_offset, item) for one quote of a gold dict (its `quote` when none is given)."""
    evid = g["evidence"]
    item = ev.get(evid)
    q = g.get("quote") if quote is None else quote
    gold_shingles = _quote_shingles(norm(q or ""))
    gold_offset = g.get("offset") if q == g.get("quote") else None
    if gold_offset is None and item:
        gold_offset = S.quote_offset(item, q or "")
        if gold_offset < 0:
            gold_offset = None
    return gold_shingles, gold_offset, item


def _best_hit(g, e, ev):
    """(containment, distance) of the strongest hit of event or op `e` on any quote of g; None when none hits."""
    best = None
    for q in _gold_quotes(g):
        hit = _hit_strength(e, *_gold_params(g, ev, q))
        if hit and (best is None or (hit[0] >= 0.5, hit[0], -hit[1]) > (best[0] >= 0.5, best[0], -best[1])):
            best = hit
    return best


def match_item(g, N, events, ev):
    """The node id matching gold item g, or None (match_with_strength without the strength)."""
    return match_with_strength(g, N, events, ev)[0]


def match_with_strength(g, N, events, ev):
    """(node id or None, quote containment of the winning hit: 1.0 for covered_by, 0.0 for a position-only hit).

    Hit order:
    1. covered_by resolved through merged_into (deleted → None).
    2. An agent event (type create/agent/note) for the same evidence whose quote has
       5-word shingle containment ≥ 0.5 with the gold quote, or whose quote offset is
       within 400 chars of the gold offset. The node id returned by the event is then
       routed through _resolve_node (merged → surviving node; deleted → None).
       Propose events are skipped here — cards are handled only by _card_match.
    """
    # 1. covered_by
    cb = g.get("covered_by")
    if cb:
        return _resolve_node(cb, N), 1.0

    # 2. event scan: of every hitting event, the best, never the first (an epic, a story and a task made from one
    # sentence all hit; a quote hit outranks a nearby-position hit; the most specific node wins a tie)
    evid = g["evidence"]
    best, best_key = None, None
    for e in events:
        if e.get("evidence") != evid:
            continue
        etype = e.get("type")
        if etype not in ("create", "agent", "note"):
            continue
        if e.get("by") != "agent" and not (etype == "create" and e.get("from_proposal")):
            continue   # a create made by the user's Confirm on a card is the card's node: it counts like the agent's
        # Skip propose events — they are pending cards, not nodes; _card_match handles them
        if etype == "agent" and e.get("op") == "propose":
            continue
        hit = _best_hit(g, e, ev)
        if hit is None:
            continue
        nid = e.get("id") if etype == "create" else e.get("node")
        resolved = _resolve_node(nid, N) if nid else None
        if not resolved:
            continue
        cont, dist = hit
        key = (cont >= 0.5, round(cont, 3), SPECIFICITY.get(N[resolved].get("kind"), 1), -dist)
        if best_key is None or key > best_key:
            best, best_key = resolved, key
    return best, (best_key[1] if best_key else 0.0)


def _decisions(events):
    """Card mid -> "accept" or "reject", the user's latest decision on it (an undone decision does not count)."""
    undone = {e.get("target") for e in events if e.get("type") == "human" and e.get("op") == "undo"}
    return {e.get("target"): e.get("op") for e in events if e.get("type") == "human"
            and e.get("op") in ("accept", "reject") and e.get("mid") not in undone}


def _decided(events):
    """Card mids the user confirmed or rejected (an undone decision does not count)."""
    return set(_decisions(events))


def _card_match(g, events, ev):
    """True when a still-pending propose card matches g by the same quote rule. A confirmed card is a node (match_item
    finds it); a rejected one is nothing."""
    evid = g["evidence"]
    decided = _decided(events)
    for e in events:
        if e.get("evidence") != evid:
            continue
        if e.get("type") != "agent" or e.get("op") != "propose":
            continue
        if e.get("by") != "agent" or e.get("mid") in decided:
            continue
        if _best_hit(g, e, ev):
            return True
    return False


LOSS_BUCKETS = ("B7", "B7c", "B8", "B13")


def _proposals(store, evids):
    """Ops the agent wrote per key meeting (.taska/proposals/<evidence>*.json), and the files that could not be read."""
    out, unreadable = {evid: [] for evid in evids}, []
    for f in sorted((Path(store) / "proposals").glob("*.json")):
        evid = next((x for x in evids if f.name.startswith(x)), None)
        if evid is None:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            unreadable.append(f"{f.name}: {exc}")
            continue
        out[evid] += [o for o in (data if isinstance(data, list) else data.get("ops") or []) if isinstance(o, dict)]
    return out, unreadable


def loss_stage(g, matched, carded, events, proposals, processed, ev):
    """Where a key item was lost, by the scorer's own quote rule: matched, card, applied_not_live (its node was undone,
    merged, deleted or its card rejected), proposed_not_applied, not_proposed, meeting_not_processed."""
    if matched:
        return "matched"
    if carded:
        return "card"
    if g["evidence"] not in processed:
        return "meeting_not_processed"
    if any(_best_hit(g, e, ev) for e in events if e.get("evidence") == g["evidence"] and e.get("by") == "agent"
           and e.get("type") in ("create", "agent", "note")):
        return "applied_not_live"
    if any(_best_hit(g, o, ev) for o in proposals.get(g["evidence"], [])):
        return "proposed_not_applied"
    return "not_proposed"


# ---------------------------------------------------------------------------
# Offset fraction (for thirds)
# ---------------------------------------------------------------------------

LEGACY = {"B7": "detected (quote or within 400 characters): a candidate, not acceptance",
          "B13": "detected (quote or within 400 characters): a candidate, not acceptance",
          "strict.quote_candidate": "a quote hit (containment ≥ 0.5) on a node of the key item's kind, not broader, that no "
                                    "stronger hit holds: a candidate only, never acceptance; a vague node can quote the "
                                    "sentence, so only the judged outcome (quality.strict_outcomes) accepts",
          "strict.unresolved": "a position-only hit, a node a stronger hit already holds, or a broader node (a story "
                               "for a task: is the deliverable explicit there? DELIVERABLE-IN-PARENT): needs a judge or a person",
          "strict.wrong_kind": "the node is another kind than the key item (a task for a risk): not found; a task for a "
                               "story is the right kind at another level (level_ok false)",
          "strict.missed": "no node (a proposal card is a miss for committed work; B7c counts it)",
          "strict.complete": "a quote candidate with every annotated field right (exact equal, must_be_unknown empty) at the key's "
                             "level: a task for a story is a candidate but incomplete (field 'level')"}


KIND_FAMILY = {"task": "work", "story": "work", "decision": "decision", "risk": "risk", "epic": "epic"}


def same_kind(node_kind, key_kind):
    """A task for a story (or a story for a task) is the work found at another level, a field error (`level_ok`);
    a task for a risk, or a risk for a decision, is not the key item."""
    return KIND_FAMILY.get(node_kind or "", node_kind) == KIND_FAMILY.get(key_kind or "", key_kind)


def field_errors(cfg, g, node):
    """The fields of a found node that fail the key item's field modes: exact must match, must_be_unknown must stay
    empty, not_annotated is not checked; `level` when a task stands for a story or the reverse."""
    modes = g.get("field_modes") or _annotate(g, None)["field_modes"]
    errs = [] if node.get("kind") == g.get("kind") else ["level"]
    got = {"owner": _node_owner(node), "due": node.get("due"), "status": node.get("state"), "client_owed": node.get("client_owed")}
    for f in FIELDS:
        m = modes.get(f, "not_annotated")
        if m == "must_be_unknown" and f in ("owner", "due") and got[f]:
            errs.append(f)
        elif m == "exact" and not (owner_matches(cfg, got["owner"], g["owner"]) if f == "owner" else
                                   _map_node_state(got["status"]) == _map_gold_status(g["status"]) if f == "status" else
                                   bool(got[f]) == bool(g[f]) if f == "client_owed" else got[f] == g[f]):
            errs.append(f)
    return errs


def _complete(members, outcome, items):
    """Complete-item correctness over the same denominator: a covered item with no field error; a covered item with
    one is incomplete; unresolved coverage stays unresolved; wrong kind and missed are not found."""
    res = {"ok": 0, "incomplete": 0, "unresolved": 0, "not_found": 0}
    errs = Counter()
    for idx, _ in members:
        o = outcome[idx]
        if o == "quote_candidate":
            e = items[idx]["field_errors"]
            res["incomplete" if e else "ok"] += 1
            errs.update(e)
        else:
            res["unresolved" if o == "unresolved" else "not_found"] += 1
    n = len(members)
    return {"n": n, **res, "rate": round(res["ok"] / n, 3) if n else None, "lb95": _wilson(res["ok"], n) if n else None,
            "field_errors": dict(errs)}


LEVEL = {"task": 1, "story": 2, "epic": 3}


def broader(node_kind, key_kind):
    """The node is a broader work item than the key item (a story for a task): a parent title alone is no coverage
    (DELIVERABLE-IN-PARENT), so code leaves it to the judge whether the deliverable is explicit there."""
    return LEVEL.get(node_kind or "", 0) > LEVEL.get(key_kind or "", 0) > 0


def strict_scores(gold, matches, strength, N, buckets, cfg=None):
    """Per item {basis, kind_ok, shared} and per bucket quote_candidate / unresolved / wrong_kind / missed (code finds
    candidates; it never accepts). One node holds one
    key item: of the items it matches, the strongest hit keeps it (then key order), the others are unresolved."""
    holders = {}
    for idx, nid in matches.items():
        if nid is not None:
            holders.setdefault(nid, []).append(idx)
    keeper = {nid: max(ix, key=lambda i: (strength[i], -i)) for nid, ix in holders.items()}
    items, outcome = {}, {}
    for idx, g in enumerate(gold):
        nid = matches[idx]
        if nid is None:
            items[idx], outcome[idx] = {"basis": None, "kind_ok": None, "level_ok": None, "shared": 0}, "missed"
            continue
        kind_ok = same_kind(N[nid].get("kind"), g.get("kind"))
        basis = "quote" if strength[idx] >= 0.5 else "position"
        wide = broader(N[nid].get("kind"), g.get("kind"))
        items[idx] = {"basis": basis, "kind_ok": kind_ok, "level_ok": N[nid].get("kind") == g.get("kind"), "broader": wide,
                      "shared": len(holders[nid]), "field_errors": field_errors(cfg, g, N[nid]) if kind_ok else []}
        outcome[idx] = ("wrong_kind" if not kind_ok else
                        "quote_candidate" if basis == "quote" and keeper[nid] == idx and not wide else "unresolved")
    out = {}
    for b, members in buckets.items():
        c = Counter(outcome[idx] for idx, _ in members)
        n, ok = len(members), c["quote_candidate"]
        out[b] = {"n": n, "quote_candidate": ok, "unresolved": c["unresolved"], "wrong_kind": c["wrong_kind"], "missed": c["missed"],
                  "rate": round(ok / n, 3) if n else None, "lb95": _wilson(ok, n) if n else None,
                  "upper_rate": round((ok + c["unresolved"]) / n, 3) if n else None,
                  "items": {gold[idx].get("cid") or str(idx): outcome[idx] for idx, _ in members},
                  "complete": _complete(members, outcome, items)}
    out["legacy"] = LEGACY
    return {"items": items, "buckets": out}


def _offset_frac(g, ev):
    """Fraction of evidence text where this gold item falls. None when not computable."""
    item = ev.get(g["evidence"])
    if not item:
        return None
    full = norm(S.full_text(item))
    if not full:
        return None
    offset = g.get("offset")
    if offset is None:
        offset = S.quote_offset(item, g.get("quote") or "")
        if offset < 0:
            offset = None
    if offset is None:
        return None
    return offset / len(full)


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score(store, gold):
    """Compare store against gold items. Returns B7–B18 gaps plus metadata.

    store: Path to the .taska directory.
    gold:  list of gold dicts, each with an 'evidence' key and fields matching
           the gold annotation format. Raises ValueError for any item missing 'evidence'.

    offset convention: gold['offset'] is the byte index of norm(quote) inside
    norm(full_text(evidence_item)), the same coordinate space as store.quote_offset.
    """
    store = Path(store)

    # Validate gold items before any store access
    for idx, g in enumerate(gold):
        if "evidence" not in g:
            raise ValueError(f"gold item {idx}: missing required field 'evidence'")

    cfg = C.load(store.parent)
    ev_dict = S.evidence(store)
    evs = S.events(store)
    N, _notes, _order, ev = L.fold(store, evs=evs, ev=ev_dict)
    proc = S.processed(evs)
    # work read from a re-synced copy of a meeting counts for the original meeting (as in the fold)
    evs = [dict(e, evidence=ev_dict[e["evidence"]]["duplicate_of"])
           if ev_dict.get(e.get("evidence"), {}).get("duplicate_of") else e for e in evs]

    # incomplete flag
    gold_evids = {g["evidence"] for g in gold}
    missing = sorted(i for i in gold_evids if i not in ev or i not in proc)
    incomplete = bool(missing)

    # pre-compute per-item match results
    matches = {}      # idx → node id or None
    strength = {}     # idx → quote containment of the winning hit
    carded_flags = {} # idx → bool (propose card matched)
    for idx, g in enumerate(gold):
        nid, strength[idx] = match_with_strength(g, N, evs, ev)
        matches[idx] = nid
        carded_flags[idx] = False if nid is not None else _card_match(g, evs, ev)
    props, unreadable = _proposals(store, gold_evids)
    stages = {idx: loss_stage(g, matches[idx] is not None, carded_flags[idx], evs, props, proc, ev)
              for idx, g in enumerate(gold)}

    rows = []

    def run_fields(nid):
        n = N.get(nid) or {}
        return {"run_node": nid or "", "run_kind": n.get("kind") or "", "run_title": n.get("title") or "",
                "run_owner": (_node_owner(n) or "") if n else "", "run_due": n.get("due") or "", "run_status": n.get("state") or ""}

    def verdict(idx, bucket, ok, why=""):
        g = gold[idx]
        rows.append({"key_id": g.get("cid") or "", "bucket": bucket, "bucket_name": BUCKET_NAMES[bucket],
                     "meeting": g.get("evidence") or "", "ts": g.get("ts") or "", "speaker": g.get("speaker") or "",
                     "quote": g.get("quote") or "", "expected_kind": g.get("kind") or "", "expected_title": g.get("title") or "",
                     "expected_owner": g.get("owner") or "", "expected_due": g.get("due") or "", "expected_status": g.get("status") or "",
                     "commitment": g.get("commitment") or "", "verdict": "hit" if ok else "miss", "why": "" if ok else why,
                     "stage": stages[idx], **run_fields(matches[idx])})

    # ── B7: committed task/story recall ─────────────────────────────────────
    b7_items = [(idx, g) for idx, g in enumerate(gold)
                if g.get("commitment") == "committed" and g.get("kind") in ("task", "story")]
    b7_ok, b7_fails = 0, []
    for idx, g in b7_items:
        verdict(idx, "B7", matches[idx] is not None, "no matching node")
        verdict(idx, "B7c", matches[idx] is not None or carded_flags[idx], "no matching node or proposal card")
        if matches[idx] is not None:
            b7_ok += 1
        else:
            b7_fails.append(_fail(g, "no matching node"))

    # carded: B7 items not matched to a node but matched by a propose card
    carded = sum(1 for idx, g in b7_items if matches[idx] is None and carded_flags[idx])

    # ── B8: proposed recall ──────────────────────────────────────────────────
    b8_items = [(idx, g) for idx, g in enumerate(gold) if g.get("commitment") == "proposed"]
    b8_ok, b8_fails = 0, []
    for idx, g in b8_items:
        verdict(idx, "B8", matches[idx] is not None or carded_flags[idx], "no matching node or propose card")
        if matches[idx] is not None or carded_flags[idx]:
            b8_ok += 1
        else:
            b8_fails.append(_fail(g, "no matching node or propose card"))

    # ── B9: last-third recall + by_part breakdown ───────────────────────────
    # Items with no computable offset go into "unknown" bucket.
    parts = {"1": [0, 0], "2": [0, 0], "3": [0, 0], "unknown": [0, 0]}  # [n, ok]
    b9_n, b9_ok, b9_fails = 0, 0, []
    for idx, g in b7_items:
        frac = _offset_frac(g, ev)
        if frac is None:
            part = "unknown"
        elif frac < 1 / 3:
            part = "1"
        elif frac < 2 / 3:
            part = "2"
        else:
            part = "3"
        hit = 1 if matches[idx] is not None else 0
        parts[part][0] += 1
        parts[part][1] += hit
        if part == "3":
            verdict(idx, "B9", bool(hit), "last-third miss")
            b9_n += 1
            b9_ok += hit
            if not hit:
                b9_fails.append(_fail(g, "last-third miss"))

    by_part = {p: _gap(parts[p][1], parts[p][0], []) for p in ("1", "2", "3", "unknown")}

    # ── B10: owner accuracy for B7 hits with non-null gold owner ────────────
    b10_items = [(idx, g) for idx, g in b7_items if matches[idx] is not None and g.get("owner") is not None]
    b10_ok, b10_fails, owner_ok = 0, [], {}
    for idx, g in b10_items:
        node = N[matches[idx]]
        nown = _node_owner(node)
        gown = g["owner"]
        ok = owner_matches(cfg, nown, gown)
        owner_ok[idx] = ok
        verdict(idx, "B10", ok, f"owner {nown!r} != {gown!r}")
        if ok:
            b10_ok += 1
        else:
            b10_fails.append(_fail(g, f"owner {nown!r} != {gown!r}"))

    # ── B11: date accuracy for B7 hits with non-null gold due ───────────────
    b11_items = [(idx, g) for idx, g in b7_items if matches[idx] is not None and g.get("due") is not None]
    b11_ok, b11_fails = 0, []
    for idx, g in b11_items:
        node = N[matches[idx]]
        verdict(idx, "B11", node.get("due") == g["due"], f"due {node.get('due')!r} != {g['due']!r}")
        if node.get("due") == g["due"]:
            b11_ok += 1
        else:
            b11_fails.append(_fail(g, f"due {node.get('due')!r} != {g['due']!r}"))

    # ── B12: status accuracy for B7 hits with non-null gold status ──────────
    b12_items = [(idx, g) for idx, g in b7_items if matches[idx] is not None and g.get("status") is not None]
    b12_ok, b12_fails = 0, []
    for idx, g in b12_items:
        node = N[matches[idx]]
        verdict(idx, "B12", _map_node_state(node["state"]) == _map_gold_status(g["status"]), f"status {node['state']!r} != {g['status']!r}")
        if _map_node_state(node["state"]) == _map_gold_status(g["status"]):
            b12_ok += 1
        else:
            b12_fails.append(_fail(g, f"status {node['state']!r} != {g['status']!r}"))

    # ── B13: decision/risk recall (not declined) ─────────────────────────────
    b13_items = [(idx, g) for idx, g in enumerate(gold)
                 if g.get("kind") in ("decision", "risk") and g.get("commitment") != "declined"]
    b13_ok, b13_fails = 0, []
    for idx, g in b13_items:
        verdict(idx, "B13", matches[idx] is not None, "no matching decision/risk node")
        if matches[idx] is not None:
            b13_ok += 1
        else:
            b13_fails.append(_fail(g, "no matching decision/risk node"))

    # ── B14: client_owed accuracy for every work item found (mostly tasks: the client owes a deliverable) ──
    b14_items = [(idx, g) for idx, g in enumerate(gold) if matches[idx] is not None and g.get("commitment") != "declined"]
    b14_ok, b14_fails = 0, []
    for idx, g in b14_items:
        node = N[matches[idx]]
        verdict(idx, "B14", bool(node.get("client_owed")) == bool(g.get("client_owed")),
                f"client_owed {node.get('client_owed')} != {g.get('client_owed')}")
        if bool(node.get("client_owed")) == bool(g.get("client_owed")):
            b14_ok += 1
        else:
            b14_fails.append(_fail(g, f"client_owed {node.get('client_owed')} != {g.get('client_owed')}"))

    # ── B15: precision – nodes from golden evidence, excluding merged ────────
    # Merged nodes are excluded: the claim lives on the surviving node.
    gold_evids_set = {g["evidence"] for g in gold}
    b15_nodes = [(nid, n) for nid, n in N.items()
                 if n.get("kind") in ("task", "story", "decision", "risk")
                 and n.get("by") == "agent"
                 and n.get("created_in") in gold_evids_set
                 and n.get("state") != "merged"]
    # Which node ids are matched by a gold item? A declined item matches only a decision or risk (the record of
    # the "no"); a task or story made from it is invented.
    matched_nids = {matches[idx] for idx, g in enumerate(gold) if matches[idx] is not None
                    and (g.get("commitment") != "declined" or N[matches[idx]].get("kind") in ("decision", "risk"))}
    # a story is the container a task must sit under: one that holds a matched task is matched work, not invented
    matched_nids |= {n.get("parent") for nid, n in N.items() if nid in matched_nids and n.get("kind") == "task"
                     and N.get(n.get("parent"), {}).get("kind") == "story"}
    b15_ok, b15_fails = 0, []
    create_quote = {e.get("id"): (e.get("quote") or "") for e in evs if e.get("type") == "create"}
    for nid, n in b15_nodes:
        rows.append({"key_id": "", "bucket": "B15", "bucket_name": BUCKET_NAMES["B15"], "meeting": n.get("created_in") or "",
                     "ts": "", "speaker": "", "quote": create_quote.get(nid, ""), "expected_kind": "", "expected_title": "",
                     "expected_owner": "", "expected_due": "", "expected_status": "", "commitment": "",
                     "verdict": "hit" if nid in matched_nids else "invented", "why": "" if nid in matched_nids else UNMATCHED,
                     "stage": "", **run_fields(nid)})
        if nid in matched_nids:
            b15_ok += 1
        else:
            q = create_quote.get(nid, "")
            b15_fails.append({"evidence": n.get("created_in") or "", "quote": q[:80],
                               "title": n.get("title") or "", "why": UNMATCHED})

    # ── B16: declined recall (ok when no matching node) ──────────────────────
    b16_items = [(idx, g) for idx, g in enumerate(gold) if g.get("commitment") == "declined"]
    b16_ok, b16_fails = 0, []
    for idx, g in b16_items:
        ok16 = matches[idx] is None or N[matches[idx]].get("kind") in ("decision", "risk")   # a decision recording the "no"
        verdict(idx, "B16", ok16, f"declined item matched node {matches[idx]!r}")
        if ok16:
            b16_ok += 1
        else:
            b16_fails.append(_fail(g, f"declined item matched node {matches[idx]!r}"))

    # ── B18: proposal cards matching the key (a lower bound) – every agent card from a key meeting, pending, confirmed or rejected ──
    # (a rejected card still cost a decision). A card matches when its change is a key item's work by the card
    # quote rule; a declined item credits only a decision or risk card, as in B15. Unmatched cards are unjudged here.
    # One-to-one: each key item credits its first card by time; a later card on an already-credited item is a duplicate.
    status = _decisions(evs)
    cards = list({e["mid"]: e for e in evs if e.get("type") == "agent" and e.get("op") == "propose"
                  and e.get("by") == "agent" and e.get("mid") and e.get("evidence") in gold_evids_set}.values())
    cards.sort(key=lambda e: e.get("date") or "")
    b18_ok, b18_fails, credited_cards = 0, [], set()
    for e in cards:
        pr = e.get("proposal") or {}
        hits = [idx for idx, g in enumerate(gold) if g["evidence"] == e["evidence"]
                and (g.get("commitment") != "declined" or pr.get("kind") in ("decision", "risk")) and _best_hit(g, e, ev)]
        free = next((idx for idx in hits if idx not in credited_cards), None)
        hit = gold[free] if free is not None else None
        why = "" if hit else f"duplicate card for {gold[hits[0]].get('cid') or ''}" if hits else UNMATCHED
        if free is not None:
            credited_cards.add(free)
        q = e.get("quote") or ""
        rows.append({"key_id": "", "bucket": "B18", "bucket_name": BUCKET_NAMES["B18"], "meeting": e["evidence"],
                     "ts": e.get("ts") or "", "speaker": e.get("speaker") or "", "quote": q,
                     "expected_kind": (hit or {}).get("kind") or "", "expected_title": (hit or {}).get("title") or "",
                     "expected_owner": (hit or {}).get("owner") or "", "expected_due": (hit or {}).get("due") or "",
                     "expected_status": (hit or {}).get("status") or "", "commitment": (hit or {}).get("commitment") or "",
                     "verdict": "hit" if hit else "invented", "why": why, "stage": "",
                     "run_node": e["mid"], "run_kind": pr.get("kind") or "", "run_title": pr.get("title") or "",
                     "run_owner": pr.get("internal_owner") or pr.get("client_owner") or "", "run_due": pr.get("due") or "",
                     "run_status": {"accept": "confirmed", "reject": "rejected"}.get(status.get(e["mid"]) or "", "pending")})
        if hit:
            b18_ok += 1
        else:
            b18_fails.append({"evidence": e["evidence"], "quote": q[:80], "title": pr.get("title") or "", "why": why})

    strict = strict_scores(gold, matches, strength, N, {"B7": b7_items, "B13": b13_items}, cfg)

    def distinct(items):
        return len({matches[idx] for idx, _ in items if matches[idx] is not None})

    # ── assemble result ───────────────────────────────────────────────────────
    result = {
        "B7":  dict(_gap(b7_ok,  len(b7_items),  b7_fails), distinct_nodes=distinct(b7_items)),
        "B7c": _gap(b7_ok + carded, len(b7_items), []),
        "B8":  dict(_gap(b8_ok,  len(b8_items),  b8_fails), distinct_nodes=distinct(b8_items)),
        "B9":  dict(_gap(b9_ok, b9_n, b9_fails), by_part=by_part),
        "B10": _gap(b10_ok, len(b10_items), b10_fails),
        "B11": _gap(b11_ok, len(b11_items), b11_fails),
        "B12": _gap(b12_ok, len(b12_items), b12_fails),
        "B13": dict(_gap(b13_ok, len(b13_items), b13_fails), distinct_nodes=distinct(b13_items)),
        "B14": _gap(b14_ok, len(b14_items), b14_fails),
        "B15": _gap(b15_ok, len(b15_nodes), b15_fails),
        "B16": _gap(b16_ok, len(b16_items), b16_fails),
        "B18": _gap(b18_ok, len(cards), b18_fails),
        "strict": strict["buckets"],
        "incomplete": incomplete,
        "carded": carded,
        "bucket_names": BUCKET_NAMES,
        # per finding bucket, how many key items ended at each stage (loss_stage): where the misses were lost
        "loss": {b: dict(sorted(Counter(r["stage"] for r in rows if r["bucket"] == b).items(),
                                key=lambda kv: STAGES.index(kv[0]))) for b in LOSS_BUCKETS},
        "proposals_unreadable": unreadable,
        "rows": rows,   # one per key item and bucket, plus one per created node (B15) and per proposal card (B18): items.csv
        # one line per gold item: per-item comparisons read this (`rows` carries the verdict per bucket)
        "items": [{"cid": g.get("cid"), "evidence": g.get("evidence"),
                   "match": f"node:{matches[idx]}" if matches[idx] else ("card" if carded_flags[idx] else None)}
                  | ({"owner_ok": owner_ok[idx]} if idx in owner_ok else {}) | strict["items"][idx]
                  for idx, g in enumerate(gold)],
    }
    if missing:
        result["missing"] = missing
    return result


# ---------------------------------------------------------------------------
# The key file: "# gold vN <date> n=<count> [sha256=<hex>]" then one JSON object per line
# ---------------------------------------------------------------------------

def _body_sha(lines):
    import hashlib
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


FIELDS = ("owner", "due", "status", "client_owed")
MODES = ("exact", "must_be_unknown", "not_annotated")
VALIDATION = ("draft", "deterministic_verified", "agent_adjudicated", "human_validated")
RULE_ID = re.compile(r"[A-Z][A-Z0-9]*(-[A-Z0-9]+)*")


def check_annotations(items, records):
    """Every problem with annotation records against the key's items (empty when valid)."""
    by = {g.get("cid"): g for g in items}
    out = []
    for r in records:
        cid = r.get("cid")
        g = by.get(cid)
        if g is None:
            out.append(f"unknown key item {cid}")
            continue
        for f, mode in (r.get("fields") or {}).items():
            if f not in FIELDS:
                out.append(f"{cid}: no field {f}")
            elif mode not in MODES:
                out.append(f"{cid}: {f} mode {mode}")
            elif mode == "must_be_unknown" and g.get(f) not in (None, "", False):
                out.append(f"{cid}: {f} has a value ({g.get(f)!r}), so it cannot be must_be_unknown")
            elif mode == "exact" and g.get(f) in (None, ""):
                out.append(f"{cid}: {f} is exact but empty in the key")
        if "validation_status" in r and r["validation_status"] not in VALIDATION:
            out.append(f"{cid}: validation status {r['validation_status']}")
        for rid in r.get("rule_ids") or []:
            if not isinstance(rid, str) or not RULE_ID.fullmatch(rid):
                out.append(f"{cid}: rule id {rid!r} (UPPER-CASE-WITH-DASHES)")
    return out


def _annotations_of(path, head, items):
    """The newest `<key stem>.ann-N.jsonl` beside the key: a whole version (header names the key's sha), never a
    patch on an older one. ({} or {"version", "sha256", "file"}, its records by cid)."""
    path = Path(path)
    found = sorted(path.parent.glob(f"{path.stem}.ann-*.jsonl"), key=lambda p: int(p.stem.rsplit("ann-", 1)[1] or 0)
                   if p.stem.rsplit("ann-", 1)[1].isdigit() else -1)
    if not found:
        return {}, {}
    f = found[-1]
    lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    parts = lines.pop(0).lstrip("#").split() if lines and lines[0].startswith("#") else []
    meta = dict(kv.partition("=")[::2] for kv in parts[3:])
    if meta.get("sha256") != _body_sha(lines):
        raise ValueError(f"{f}: body does not match its header sha256 (the annotations were edited in place)")
    key_sha = head.get("sha256") or _body_sha([json.dumps(i, ensure_ascii=False) for i in items])
    if meta.get("key_sha256") != key_sha:
        raise ValueError(f"{f}: annotates key {meta.get('key_sha256')}, not this key ({key_sha})")
    records = [json.loads(l) for l in lines]
    problems = check_annotations(items, records)
    if problems:
        raise ValueError(f"{f}: " + "; ".join(problems))
    return {"version": parts[1] if len(parts) > 1 else None, "sha256": meta["sha256"], "file": f.name}, \
        {r["cid"]: r for r in records}


def _annotate(g, rec):
    modes = {f: "exact" if g.get(f) not in (None, "") else "not_annotated" for f in FIELDS}
    if g.get("owner_unscored"):
        modes["owner"] = "not_annotated"
    modes |= (rec or {}).get("fields") or {}
    return dict(g, field_modes=modes, validation_status=(rec or {}).get("validation_status") or "unrecorded",
                rule_ids=(rec or {}).get("rule_ids") or [], **({"provenance": rec["provenance"]} if (rec or {}).get("provenance") else {}))


def load_gold(path):
    """(header, items). A header's n and sha256, when present, must match the body; a file without a header is
    accepted with header {}. Each item carries field_modes, validation_status and rule_ids: from the newest
    annotations file beside the key (`<stem>.ann-N.jsonl`, header["annotations"]), else implied (a value is exact,
    an empty field not annotated, nothing validated on record)."""
    lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    head = {}
    if lines and lines[0].startswith("#"):
        parts = lines.pop(0).lstrip("#").split()
        if parts[:1] == ["gold"]:
            head = {"version": parts[1] if len(parts) > 1 else None, "date": parts[2] if len(parts) > 2 else None}
            for kv in parts[3:]:
                k, _, v = kv.partition("=")
                head[k] = int(v) if k == "n" else v
    if "sha256" in head and head["sha256"] != _body_sha(lines):
        raise ValueError(f"{path}: body does not match its header sha256 (the key was edited in place)")
    items = [json.loads(l) for l in lines]
    if "n" in head and head["n"] != len(items):
        raise ValueError(f"{path}: header says n={head['n']}, file has {len(items)} items")
    ann, recs = _annotations_of(path, head, items)
    if ann:
        head["annotations"] = ann
    return head, [_annotate(g, recs.get(g.get("cid"))) for g in items]


def freeze_gold(path):
    """Write the body's sha256 into the header, so any later in-place edit is refused by load_gold."""
    p = Path(path)
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    head = lines.pop(0) if lines and lines[0].startswith("#") else f"# gold v1 unknown n={len(lines)}"
    head = " ".join(w for w in head.split() if not w.startswith("sha256=")) + f" sha256={_body_sha(lines)}"
    p.write_text("\n".join([head] + lines) + "\n", encoding="utf-8")
    return head
