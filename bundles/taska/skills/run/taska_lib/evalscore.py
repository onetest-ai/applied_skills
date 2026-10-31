"""Extraction confidence scorer: compare a store against a gold annotation file.

score(store, gold) -> result dict with B7–B16 metrics.
match_item(g, N, events, ev) -> matched node id or None.

Gold offset convention: `offset` is the byte index of norm(quote) inside
norm(full_text(item)) — the same coordinate space that store.quote_offset returns.
"""
import json
import math
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


def _gap(ok, n, fails):
    """Build a gap dict. rate and lb95 are None when n == 0."""
    if n:
        rate = round(ok / n, 3)
        lb95 = _wilson(ok, n)
    else:
        rate = None
        lb95 = None
    return {"n": n, "ok": ok, "rate": rate, "lb95": lb95, "fails": fails[:20]}


def _fail(g, why):
    return {"evidence": g.get("evidence", ""), "quote": (g.get("quote") or "")[:80],
            "title": g.get("title") or "", "why": why}


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------

def _node_owner(n):
    return n.get("internal_owner") or n.get("client_owner")


def _map_node_state(state):
    if state == "in_progress":
        return "progress"
    if state == "done":
        return "done"
    # open, cancelled, blocked, merged, deleted → open
    return "open"


def _map_gold_status(status):
    if status in ("done", "held"):
        return "done"
    if status == "progress":
        return "progress"
    return "open"


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


def _gold_params(g, ev):
    """Return (gold_shingles, gold_offset, item) for a gold dict."""
    evid = g["evidence"]
    item = ev.get(evid)
    gold_q = norm(g.get("quote") or "")
    gold_shingles = _quote_shingles(gold_q)
    gold_offset = g.get("offset")
    if gold_offset is None and item:
        gold_offset = S.quote_offset(item, g.get("quote") or "")
        if gold_offset < 0:
            gold_offset = None
    return gold_shingles, gold_offset, item


def match_item(g, N, events, ev):
    """Return the node id matching gold item g, or None.

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
        return _resolve_node(cb, N)

    # 2. event scan: of every hitting event, the best, never the first (an epic, a story and a task made from one
    # sentence all hit; a quote hit outranks a nearby-position hit; the most specific node wins a tie)
    evid = g["evidence"]
    gold_shingles, gold_offset, item = _gold_params(g, ev)

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
        hit = _hit_strength(e, gold_shingles, gold_offset, item)
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
    return best


def _decided(events):
    """Card mids the user confirmed or rejected (an undone decision does not count)."""
    undone = {e.get("target") for e in events if e.get("type") == "human" and e.get("op") == "undo"}
    return {e.get("target") for e in events if e.get("type") == "human" and e.get("op") in ("accept", "reject")
            and e.get("mid") not in undone}


def _card_match(g, events, ev):
    """True when a still-pending propose card matches g by the same quote rule. A confirmed card is a node (match_item
    finds it); a rejected one is nothing."""
    evid = g["evidence"]
    gold_shingles, gold_offset, item = _gold_params(g, ev)
    decided = _decided(events)
    for e in events:
        if e.get("evidence") != evid:
            continue
        if e.get("type") != "agent" or e.get("op") != "propose":
            continue
        if e.get("by") != "agent" or e.get("mid") in decided:
            continue
        if _event_hit(e, gold_shingles, gold_offset, item):
            return True
    return False


# ---------------------------------------------------------------------------
# Offset fraction (for thirds)
# ---------------------------------------------------------------------------

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
    """Compare store against gold items. Returns B7–B16 gaps plus metadata.

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
    carded_flags = {} # idx → bool (propose card matched)
    for idx, g in enumerate(gold):
        nid = match_item(g, N, evs, ev)
        matches[idx] = nid
        carded_flags[idx] = False if nid is not None else _card_match(g, evs, ev)

    # ── B7: committed task/story recall ─────────────────────────────────────
    b7_items = [(idx, g) for idx, g in enumerate(gold)
                if g.get("commitment") == "committed" and g.get("kind") in ("task", "story")]
    b7_ok, b7_fails = 0, []
    for idx, g in b7_items:
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
        ok = False
        if nown:
            can_nown = cfg.canonical(nown)
            can_gown = cfg.canonical(gown)
            if can_nown == can_gown:
                ok = True
            else:
                # gold owner is a team and node owner is that team or a member
                team = cfg.team(gown)
                if team:
                    member_names = {cfg.canonical(m) for m in team.members}
                    if can_nown == team.name or can_nown in member_names:
                        ok = True
        owner_ok[idx] = ok
        if ok:
            b10_ok += 1
        else:
            b10_fails.append(_fail(g, f"owner {nown!r} != {gown!r}"))

    # ── B11: date accuracy for B7 hits with non-null gold due ───────────────
    b11_items = [(idx, g) for idx, g in b7_items if matches[idx] is not None and g.get("due") is not None]
    b11_ok, b11_fails = 0, []
    for idx, g in b11_items:
        node = N[matches[idx]]
        if node.get("due") == g["due"]:
            b11_ok += 1
        else:
            b11_fails.append(_fail(g, f"due {node.get('due')!r} != {g['due']!r}"))

    # ── B12: status accuracy for B7 hits with non-null gold status ──────────
    b12_items = [(idx, g) for idx, g in b7_items if matches[idx] is not None and g.get("status") is not None]
    b12_ok, b12_fails = 0, []
    for idx, g in b12_items:
        node = N[matches[idx]]
        if _map_node_state(node["state"]) == _map_gold_status(g["status"]):
            b12_ok += 1
        else:
            b12_fails.append(_fail(g, f"status {node['state']!r} != {g['status']!r}"))

    # ── B13: decision/risk recall (not declined) ─────────────────────────────
    b13_items = [(idx, g) for idx, g in enumerate(gold)
                 if g.get("kind") in ("decision", "risk") and g.get("commitment") != "declined"]
    b13_ok, b13_fails = 0, []
    for idx, g in b13_items:
        if matches[idx] is not None:
            b13_ok += 1
        else:
            b13_fails.append(_fail(g, "no matching decision/risk node"))

    # ── B14: client_owed accuracy for B13 hits ───────────────────────────────
    b14_items = [(idx, g) for idx, g in b13_items if matches[idx] is not None]
    b14_ok, b14_fails = 0, []
    for idx, g in b14_items:
        node = N[matches[idx]]
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
        if nid in matched_nids:
            b15_ok += 1
        else:
            q = create_quote.get(nid, "")
            b15_fails.append({"evidence": n.get("created_in") or "", "quote": q[:80],
                               "title": n.get("title") or "", "why": "false positive node"})

    # ── B16: declined recall (ok when no matching node) ──────────────────────
    b16_items = [(idx, g) for idx, g in enumerate(gold) if g.get("commitment") == "declined"]
    b16_ok, b16_fails = 0, []
    for idx, g in b16_items:
        if matches[idx] is None or N[matches[idx]].get("kind") in ("decision", "risk"):   # a decision recording the "no"
            b16_ok += 1
        else:
            b16_fails.append(_fail(g, f"declined item matched node {matches[idx]!r}"))

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
        "incomplete": incomplete,
        "carded": carded,
        # one line per gold item, never truncated: per-item comparisons read this, not the capped `fails`
        "items": [{"cid": g.get("cid"), "evidence": g.get("evidence"),
                   "match": f"node:{matches[idx]}" if matches[idx] else ("card" if carded_flags[idx] else None)}
                  | ({"owner_ok": owner_ok[idx]} if idx in owner_ok else {})
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


def load_gold(path):
    """(header, items). A header's n and sha256, when present, must match the body; a file without a header is
    accepted with header {}."""
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
    return head, items


def freeze_gold(path):
    """Write the body's sha256 into the header, so any later in-place edit is refused by load_gold."""
    p = Path(path)
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    head = lines.pop(0) if lines and lines[0].startswith("#") else f"# gold v1 unknown n={len(lines)}"
    head = " ".join(w for w in head.split() if not w.startswith("sha256=")) + f" sha256={_body_sha(lines)}"
    p.write_text("\n".join([head] + lines) + "\n", encoding="utf-8")
    return head
