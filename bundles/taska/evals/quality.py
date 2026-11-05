#!/usr/bin/env python3
"""The quality eval: each key item next to what taska produced and what an independent note-taker recorded, with
quality checks that say who decides them (code, the judge, or you); the nodes the key lacks, split by whether the
independent reader agrees; and what only the independent reader has. Writes eval-quality/1 (evalview.QUALITY_PAGE).

  quality.py build --record REC --echo ROOT [--key KEY] [--rule FILE] [--validation FILE ...] [--labels FILE ...] [--yours FILE ...] [--out DIR]
                                                     (no model call; --labels: the page's eval-labels/1 saves, source you;
                                                      --yours: earlier answers, pre-selected on the page)
  quality.py judge-prep --record REC [--out DIR]       one question batch per meeting, for QUALITY_JUDGE.md
  quality.py judge-run --record REC [--model high] [--same-tier-ok] [--ai CMD]   pays for one model call per unanswered batch
  quality.py judge-run --record REC --cache-only     reuses answers judged in other runs on the same key and tier (no model call)
  quality.py agreement --record REC --validation FILE [--validation FILE ...]   the judge against each source (no model call)
  quality.py stability --records R1 R2 [R3 ...] [--out DIR]   the same found-correctly verdict in most runs (no model call)

Matching reuses the scorer (evalscore) and compare_external's alignment of a note-taker's quote to the transcript; a
decision (a paraphrase, no quote) is proposed by shared words and left to the judge. Reads only."""
import argparse
import copy
import hashlib
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
for p in (RUN_DIR, EVALS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import echo as ECHO  # noqa: E402
import evalview  # noqa: E402
import record as REC  # noqa: E402
import relabel  # noqa: E402
from taska_lib import compare_external as CE, config as C, evalscore as ES, ledger as L, store as S  # noqa: E402
from taska_lib.text import norm  # noqa: E402

STAGE = {"not_proposed": "read, never proposed", "applied_not_live": "applied, then undone or merged away",
         "proposed_not_applied": "proposed, refused by apply", "meeting_not_processed": "meeting not processed yet"}
WORDS = 0.4   # share of an independent item's words a key item must carry to be proposed as the same (judge confirms)
WORK_KINDS = ("task", "story", "decision", "risk")


def _check(name: str, verdict: str, by: str, why: str = "") -> dict:
    return {"name": name, "verdict": verdict, "by": by, "why": why}


def _pending(name: str) -> dict:
    return _check(name, "check", "judge", "not judged yet")


def _independent(x: dict, kind: str, matched_by: str) -> dict:
    return {"what": x.get("task") or x.get("what") or "", "owner": x.get("owner") if x.get("owner") not in ("None", "") else None,
            "due": x.get("due") if x.get("due") not in ("None", "") else None, "at": x.get("at") or "", "kind": kind, "matched_by": matched_by}


def _words(*texts: str) -> set[str]:
    return set().union(*(relabel._words(t) for t in texts))


def build(project: Path, key: list[dict], calls: dict[str, "ECHO.EchoCall | None"], run: dict) -> dict:
    """The eval-quality/1 document for one store (a run's copy), its key, and each key meeting's independent call."""
    cfg = C.load(project)
    store = cfg.store
    ev, evs = S.evidence(store), S.events(store)
    N, notes = L.fold(store, evs=evs, ev=ev)[:2]

    def checklist(nid: str) -> list[str]:
        """What a node says it holds beside its title: its acceptance criteria and notes (DELIVERABLE-IN-PARENT)."""
        return [a["text"] for a in N[nid].get("ac") or []] + [x.get("text") or "" for x in notes.get(nid, [])]
    quotes = {e["id"]: e.get("quote") or "" for e in evs if e.get("type") == "create"}
    score = ES.score(store, REC.neutral(key))
    match = {i["cid"]: i["match"] for i in score["items"]}
    stage = {r["key_id"]: r.get("stage") or "" for r in score["rows"] if r.get("key_id")}
    by_node: dict[str, list[str]] = {}
    for cid, m in match.items():
        if m and m.startswith("node:"):
            by_node.setdefault(m[5:], []).append(cid)

    def produced(nid: str | None, g: dict | None) -> dict:
        if nid is None:
            return {"node": None, "stage": STAGE.get(stage.get(g["cid"], ""), stage.get(g["cid"], "")) if g else ""}
        n = N[nid]
        parent = N.get(n.get("parent") or "", {})
        hit = max((ES._best_hit(g, e, ev) or (0.0, 0) for e in evs if g and e.get("by") == "agent"
                   and (e.get("id") == nid or e.get("node") == nid) and e.get("quote")), default=(0.0, 0)) if g else (0.0, 0)
        return {"node": nid, "kind": n.get("kind"), "checklist": checklist(nid),
                "title": n.get("title") or "", "owner": ES._node_owner(n), "due": n.get("due"), "status": n.get("state"),
                "parent": f"{n.get('parent')} · {parent.get('title', '')}" if n.get("parent") else None, "quote": quotes.get(nid, ""),
                "matched_by": "quote" if hit[0] >= 0.5 else "position"}

    def field_checks(g: dict, prod: dict, indep: dict | None) -> list[dict]:
        """Owner, due date and status of a node against the key item, by code (a due the key lacks: the judge)."""
        out = []
        if g.get("owner"):
            ok = ES.owner_matches(cfg, prod["owner"], g["owner"])
            third = indep and indep.get("owner")
            out.append(_check("Owner", "ok" if ok else "no", "code", "" if ok else
                              f"expected {g['owner']}, taska {prod['owner'] or 'none'}" + (f", Echo {third}" if third else "")))
        if g.get("due") or prod.get("due"):
            same = g.get("due") == prod.get("due")
            out.append(_check("Due date", "ok" if same else "check" if not g.get("due") else "no", "code" if g.get("due") else "judge",
                              "" if same else f"expected {g.get('due') or 'none'}, taska {prod.get('due') or 'none'}"))
        if g.get("status"):
            ok = ES._map_node_state(prod["status"]) == ES._map_gold_status(g["status"])
            out.append(_check("Status", "ok" if ok else "no", "code", "" if ok else f"expected {g['status']}, taska {prod['status']}"))
        return out

    units, totals = [], {"found": 0, "expected": 0, "agreed_missed": 0, "owner": [0, 0], "status": [0, 0], "unjudged": 0,
                         "agreed_missed_pending": 0}
    for evid in sorted({g["evidence"] for g in key}):
        item_ev, call = ev.get(evid), calls.get(evid)
        mine = [g for g in key if g["evidence"] == evid]
        matched_nodes = {m[5:] for g in mine for m in [match.get(g["cid"]) or ""] if m.startswith("node:")}
        found_nodes = matched_nodes | {N[n].get("parent") for n in matched_nodes if N[n].get("kind") == "task"}
        live = [nid for nid, n in N.items() if n.get("created_in") == evid and n.get("by") == "agent"
                and n.get("kind") in WORK_KINDS and n.get("state") not in ("merged", "deleted")]
        extras_nodes = [nid for nid in live if nid not in found_nodes]
        # the independent reader's items: aligned to the transcript by quote, else proposed by shared words
        to_key: dict[str, dict] = {}
        to_node: dict[str, dict] = {}
        only = []
        for x, kind in [(a, "action") for a in (call.actions if call else [])] + [(d, "decision") for d in (call.decisions if call else [])]:
            span = CE._align_action({"quote": x.get("quote") or "", "task": x.get("task") or x.get("what") or ""}, item_ev)[0] if item_ev else None
            target_key = next((g for g in mine if span and ES._best_hit(g, {"quote": span}, ev)), None)
            how = "quote" if target_key else ""
            if not target_key and not span:
                said = _words(x.get("task") or x.get("what") or "")
                target_key = next((g for g in mine if said and len(said & _words(g.get("title") or "", g.get("quote") or "")) / len(said) >= WORDS), None)
                how = "words (needs the judge)" if target_key else ""
            if target_key:
                to_key.setdefault(target_key["cid"], _independent(x, kind, how))
                continue
            node = CE._tracked_node(span, evid, N, evs, ev) if span else None
            if node in extras_nodes:
                to_node.setdefault(node, _independent(x, kind, "quote"))
            elif node is None:
                only.append(_independent(x, kind, "quote" if span else "unaligned") | {"checks": [_pending("Is it work, by the tracking rule")]})
        items = []
        for g in mine:
            m = match.get(g["cid"]) or ""
            nid = m[5:] if m.startswith("node:") else None
            item_fields = {}
            prod = produced(nid, g) if nid else ({"node": "proposal card", "title": "(a pending proposal card)", "stage": "card"} if m == "card" else produced(None, g))
            indep = to_key.get(g["cid"])
            has = bool(m)
            agreement = ("all three" if has and indep else "key + Echo, taska missed" if indep else "key + taska" if has else "key only")
            agreement += " (taska only by position: the judge decides)" if prod.get("matched_by") == "position" else ""
            placing = prod.get("node") is None and bool(live)   # the judge is asked which node holds it
            checks = [_check("Found", "check", "judge", "the judge decides which node") if placing else
                      _check("Found", "ok" if has else "no", "code",
                             "a proposal card" if m == "card" else (f"matched by {prod['matched_by']}" if nid else prod.get("stage", "")))]
            if nid:
                checks.append(_pending("Same work as the key item"))
                checks.append(_pending("Title says what was said"))
                fields = field_checks(g, prod, indep)
                quote_match = prod["matched_by"] == "quote"
                checks += fields if quote_match else [_check(c["name"], "check", "judge", "waits for the judge") for c in fields]
                for k, v in _tally(fields if quote_match else []).items():
                    totals[k] = [totals[k][0] + v[0], totals[k][1] + v[1]]
                checks.append(_pending("Right parent"))
                item_fields = {"fields": fields}
                shared = by_node.get(nid, [])
                checks.append(_check("One node for one key item", "ok" if len(shared) < 2 else "check", "code",
                                     "" if len(shared) < 2 else f"{nid} is matched to {', '.join(shared)}"))
            if nid:
                item_fields["field_errors"] = ES.field_errors(cfg, g, N[nid])
            if placing:
                item_fields = {"fields_if_found": {n: field_checks(g, produced(n, None), indep) for n in live},
                               "field_errors_if_found": {n: ES.field_errors(cfg, g, N[n]) for n in live}}
            totals["found"] += has
            totals["expected"] += 1
            totals["agreed_missed"] += agreement == "key + Echo, taska missed"
            totals["agreed_missed_pending"] += agreement == "key + Echo, taska missed" and placing
            items.append({"id": g["cid"], "qid": g["cid"], "kind": f"{g.get('kind')} · {g.get('commitment')}", "quote": g.get("quote") or "", "ts": g.get("ts") or "",
                          "speaker": g.get("speaker") or "", "expected": {k: g.get(k) for k in ("title", "owner", "due", "status")},
                          "produced": prod, "independent": indep, "agreement": agreement, "checks": checks,
                          "edit": {k: g.get(k) for k in relabel.MODIFIABLE}} | item_fields)
        extras = []
        for nid in extras_nodes:
            indep = to_node.get(nid)
            group = "taska + Echo, not in the key" if indep else "taska only"
            totals["unjudged"] += 1
            extras.append({"qid": f"node:{evid}:{nid}", "group": group, "produced": produced(nid, None) | {"kind": N[nid].get("kind")}, "independent": indep,
                           "checks": [_pending(ASK["is_work"]), _pending(ASK["duplicate_of"])]})
        # the story holding a found task: not "not in the key", but judged like any node (no credit from its task)
        containers = [{"qid": f"node:{evid}:{nid}", "produced": produced(nid, None) | {"kind": N[nid].get("kind")}, "independent": None,
                       "checks": [_pending(ASK["is_work"]), _pending(ASK["duplicate_of"])]}
                      for nid in live if nid in found_nodes and nid not in matched_nodes]
        nodes = [{"id": nid, "kind": N[nid].get("kind"), "title": N[nid].get("title") or "", "quote": quotes.get(nid, ""),
                  "checklist": checklist(nid),
                  "parent": N[nid].get("parent")} for nid in live]
        units.append({"id": evid, "title": (item_ev or {}).get("title") or "", "independent": call.id if call else None, "nodes": nodes,
                      "independent_processed": bool(call and call.processed), "items": items, "extras": extras, "containers": containers, "independent_only": only})
    totals_found = [totals["found"], totals["expected"]]
    return {"schema": "eval-quality/1", "title": "taska quality eval", "unit_label": "meeting", "run": run,
            "independent": {"name": "Echo", "units": len(units), "with_notes": sum(u["independent_processed"] for u in units),
                            "recording_only": sum(bool(u["independent"]) and not u["independent_processed"] for u in units)},
            "summary": {"found": totals_found, "found_correct": None, "agreed_missed": totals["agreed_missed"],
                        "agreed_missed_pending": totals["agreed_missed_pending"],
                        "owner": totals["owner"], "status": totals["status"], "fields_scope": "quote matches",
                        "independent_coverage": [sum(u["independent_processed"] for u in units), len(units)], "not_in_key_unjudged": totals["unjudged"],
                        "inventions": None, "key_gap_candidates": None, "kind_errors": None,
                        "judged_items": [0, sum(bool(_node_ask(i, u)) for u in units for i in u["items"])]}
            | ({"proposal_cards": [score["B18"]["ok"], score["B18"]["n"]],
                "proposal_cards_burden": round((score["B18"]["n"] - score["B18"]["ok"]) / len(units), 2) if units else None}
               if "B18" in score else {}),
            "units": units}


# ── the judge: what code cannot decide ────────────────────────────────────────────────────────────────────────────

ASK = {"same_work": "Same work as the key item", "title_faithful": "Title says what was said", "right_parent": "Right parent",
       "is_work": "Is it work, by the tracking rule", "independent_same": "Echo describes the same work",
       "duplicate_of": "Not a duplicate of another node",
       "deliverable_explicit": "The deliverable is named on the broader node"}
VERDICT = {"yes": "ok", "no": "no", "unsure": "check"}
CANDIDATE = ("id", "kind", "title", "quote", "checklist")
HOW = {"same": "the same work", "inside": "part of a broader node", "other_kind": "the same content as another kind"}
NODE_ASKS = ("which_node", "duplicate_of")   # answered with a candidate's id or "none"


def _node_ask(item: dict, unit: dict) -> str | None:
    """The question that decides whether a key item was found: same_work for a matched node, which_node for an item
    with no match in a meeting that has nodes; none for a proposal card or a meeting without nodes (code decides)."""
    node = item["produced"].get("node")
    if node == "proposal card":
        return None
    return "same_work" if node else "which_node" if unit.get("nodes") else None


def judge_questions(doc: dict) -> dict[str, list[dict]]:
    """Per meeting, the questions only a reader can answer: is the matched node the same work, is its title faithful, is
    its parent right, which node (if any) holds an unmatched key item, does a words-only independent match describe the
    same work, is a node or an independent item work, and is a node the key lacks a duplicate of another."""
    out: dict[str, list[dict]] = {}
    for u in doc["units"]:
        qs = []
        nodes = [{k: n.get(k) for k in CANDIDATE} for n in u.get("nodes") or []]
        for i in u["items"]:
            prod, indep = i["produced"], i["independent"]
            node_ask = _node_ask(i, u)
            ask = ["same_work", "title_faithful", "right_parent"] if node_ask == "same_work" else [node_ask] if node_ask else []
            if node_ask in ("which_node", "same_work"):
                ask.append("deliverable_explicit")
            ask += ["independent_same"] if indep and indep["matched_by"].startswith("words") else []
            if ask:
                qs.append({"qid": i["id"], "type": "key item", "evidence": u["id"], "quote": i["quote"], "ts": i["ts"], "speaker": i["speaker"],
                           "expected": i["expected"], "produced": {k: prod.get(k) for k in ("node", "kind", "title", "parent", "quote", "checklist")},
                           "independent": indep, "ask": ask} | ({"candidates": nodes} if "which_node" in ask else {}))
        for x in u["extras"] + (u.get("containers") or []):
            nid = x["produced"]["node"]
            qs.append({"qid": f"node:{u['id']}:{nid}", "type": "node the key lacks", "evidence": u["id"],
                       "produced": {k: x["produced"].get(k) for k in ("node", "kind", "title", "parent", "quote")}, "independent": x["independent"],
                       "ask": ["is_work", "duplicate_of"], "candidates": [n for n in nodes if n["id"] != nid]})
        for n, x in enumerate(u["independent_only"]):
            qs.append({"qid": f"echo:{u['id']}:{n}", "type": "only the independent reader has it", "evidence": u["id"],
                       "independent": {k: v for k, v in x.items() if k != "checks"}, "ask": ["is_work"]})
        out[u["id"]] = qs
    return out


def _place_problems(v: object, q: dict, chosen: object, text: object = None) -> list[str]:
    """deliverable_explicit names where on the node the deliverable is: "title", "checklist:<n>" (1-based, an entry
    that exists on that node) or "none"; `explicit_text`, when given, must be words found in that place."""
    if v == "none":
        return []
    node = q.get("produced") or {}
    if "which_node" in q["ask"]:
        node = next((c for c in q.get("candidates") or [] if c.get("id") == chosen), {})
    if v == "title":
        where = node.get("title") or ""
    else:
        m = re.fullmatch(r"checklist:(\d+)", str(v))
        if not m:
            return [f"deliverable_explicit {v!r} is not title, checklist:<n> or none"]
        entries = node.get("checklist") or []
        if not 1 <= int(m.group(1)) <= len(entries):
            return [f"deliverable_explicit {v!r} names no checklist entry of that node"]
        where = entries[int(m.group(1)) - 1]
    if text is not None and norm(str(text)) not in norm(where):
        return [f"explicit_text {text!r} is not in {v}"]
    return []


def check_verdicts(answers: list[dict], batch: list[dict]) -> list[str]:
    """What is wrong with a judge's answers to one batch: every question answered once, every asked verdict given as
    yes/no/unsure (which_node and duplicate_of: a candidate's id or none; how with a which_node id only), a reason of
    8+ words. Nothing is merged while this list is not empty."""
    problems, seen, asked = [], {}, {q["qid"]: q for q in batch}
    for a in answers:
        qid = a.get("qid")
        if qid not in asked:
            problems.append(f"qid {qid!r}: not in the batch")
            continue
        if qid in seen:
            problems.append(f"qid {qid!r}: answered twice")
        seen[qid] = a
        verdicts = a.get("verdicts") or {}
        ids = {c["id"] for c in asked[qid].get("candidates") or []}
        for name in verdicts:
            if name not in asked[qid]["ask"] and not (name == "how" and "which_node" in asked[qid]["ask"]) \
                    and not (name == "explicit_text" and "deliverable_explicit" in asked[qid]["ask"]):
                problems.append(f"qid {qid!r}: {name} was not asked")
        for name in asked[qid]["ask"]:
            v = verdicts.get(name)
            if v is None:
                problems.append(f"qid {qid!r}: {name} not answered")
            elif name in NODE_ASKS and v != "none" and v not in ids:
                problems.append(f"qid {qid!r}: {name} {v!r} is not a candidate id or none")
            elif name == "deliverable_explicit":
                problems += [f"qid {qid!r}: {p}" for p in _place_problems(v, asked[qid], verdicts.get("which_node"),
                                                                          verdicts.get("explicit_text"))]
            elif name not in NODE_ASKS and name != "deliverable_explicit" and v not in VERDICT:
                problems.append(f"qid {qid!r}: {name} {v!r} is not yes/no/unsure")
        node, how = verdicts.get("which_node"), verdicts.get("how")
        if "which_node" in asked[qid]["ask"] and node in ids and how not in HOW:
            problems.append(f"qid {qid!r}: how {how!r} is not one of {'/'.join(HOW)} for node {node}")
        elif how is not None and "which_node" in asked[qid]["ask"] and node not in ids:
            problems.append(f"qid {qid!r}: how given without a node")
        if len(str(a.get("why") or "").split()) < 8:
            problems.append(f"qid {qid!r}: why has fewer than 8 words")
    problems += [f"qid {qid!r}: not answered" for qid in asked if qid not in seen]
    return problems


FIELDS = ("Owner", "Due date", "Status")


def _tally(checks: list[dict]) -> dict[str, list[int]]:
    """Owner and status totals [right, checked] from code-decided field checks."""
    return {k: [sum(c["verdict"] == "ok" for c in checks if c["name"] == name and c["by"] == "code"),
                sum(c["name"] == name and c["by"] == "code" for c in checks)] for k, name in (("owner", "Owner"), ("status", "Status"))}


def _place_fields(checks: list[dict], fields: list[dict]) -> list[dict]:
    """The checks without their field checks, then these field checks before "Right parent" (or at the end)."""
    rest = [c for c in checks if c["name"] not in FIELDS]
    at = next((k for k, c in enumerate(rest) if c["name"] == ASK["right_parent"]), len(rest))
    return rest[:at] + fields + rest[at:]


def _set(checks: list[dict], c: dict) -> None:
    idx = next((k for k, x in enumerate(checks) if x["name"] == c["name"]), None)
    if idx is None:
        checks.append(c)
    else:
        checks[idx] = c


def _dup_cycle(nid: str, dups: dict[str, str]) -> list[str]:
    """The nodes of the duplicate_of cycle through nid (A -> B -> A), or [] when following its answers never returns."""
    path, cur = [nid], dups.get(nid)
    while cur in dups and cur not in path:
        path.append(cur)
        cur = dups[cur]
    return path if cur == nid else []


def _unit_nodes(u: dict) -> list[str]:
    """Every node of a meeting once: its node list, then extras, extras the judge took and containers."""
    more = (u.get("extras") or []) + (u.get("extras_taken") or []) + (u.get("containers") or [])
    return list(dict.fromkeys([*(n["id"] for n in u.get("nodes") or []), *(x["produced"]["node"] for x in more)]))


def _node_dups(u: dict, verdicts: dict[str, dict]) -> dict[str, str | None]:
    """Each node's duplicate_of answer over all of the meeting's nodes, with one cycle rule: of mutual duplicates
    (A -> B -> A) the earliest node is not the duplicate ("none"), the later ones are."""
    order = {n["id"]: k for k, n in enumerate(u.get("nodes") or [])}
    raw = {nid: verdicts.get(f"node:{u['id']}:{nid}", {}).get("duplicate_of") for nid in _unit_nodes(u)}
    dups = {nid: d for nid, d in raw.items() if d not in (None, "none")}
    out = {}
    for nid, d in raw.items():
        cycle = _dup_cycle(nid, dups)
        out[nid] = "none" if cycle and nid == min(cycle, key=lambda n: order.get(n, 0)) else d
    return out


def apply_verdicts(doc: dict, answers: list[dict]) -> dict:
    """The document with the judge's verdicts in place of the pending checks. Found correctly counts judged items only:
    a matched node judged the same work, or a key item with no match that the judge finds via which_node (that node, and
    a found task's parent, leave the meeting's nodes not in the key). Nodes not in the key are then counted: inventions
    (not work, or a duplicate), key gap candidates (work, not a duplicate); the rest stay not yet judged."""
    out = copy.deepcopy(doc)
    by = {a["qid"]: a for a in answers}

    def put(checks: list[dict], answer: dict | None, asks: tuple[str, ...]) -> None:
        why = (answer or {}).get("why") or ""
        for name, v in ((answer or {}).get("verdicts") or {}).items():
            if name not in asks:
                continue
            if name == "duplicate_of":
                _set(checks, _check(ASK[name], "ok" if v == "none" else "no", "judge", why if v == "none" else f"duplicate of {v}: {why}"))
            elif v in VERDICT:
                _set(checks, _check(ASK[name], VERDICT[v], "judge", why))
    correct, answered, asked, kind_errors, counted, waiting = 0, 0, 0, [], [], []
    unjudged, inventions, gaps, missed, missed_pending = 0, 0, 0, 0, 0
    two_readers = "key + Echo, taska missed"
    for u in out["units"]:
        taken: set[str] = set()
        parents = {n["id"]: n.get("parent") for n in u.get("nodes") or [] if n.get("kind") == "task"}
        for i in u["items"]:
            answer, node_ask = by.get(i["id"]), _node_ask(i, u)
            verdicts = (answer or {}).get("verdicts") or {}
            put(i["checks"], answer, ("same_work", "title_faithful", "right_parent", "independent_same"))
            if "deliverable_explicit" in verdicts:
                place = verdicts["deliverable_explicit"]
                i["explicit_in"] = place
                i["explicit_text"] = verdicts.get("explicit_text")
                _set(i["checks"], _check(ASK["deliverable_explicit"], "no" if place == "none" else "ok", "judge",
                                         f"{place}: {(answer or {}).get('why') or ''}"))
            asked += bool(node_ask)
            answered += bool(node_ask) and node_ask in verdicts
            if i["agreement"] == two_readers:
                found = verdicts.get("which_node") not in (None, "none") and node_ask == "which_node"
                missed += not found
                missed_pending += node_ask == "which_node" and "which_node" not in verdicts
                i["agreement"] = "all three (taska's node found by the judge)" if found else i["agreement"]
            if node_ask == "same_work" and "same_work" not in verdicts and i.get("fields"):
                waiting.append(i)
            if node_ask == "same_work" and "same_work" in verdicts:
                same = verdicts["same_work"] == "yes"
                correct += same
                fields = i.get("fields") or []
                counted += fields if same else []
                i["checks"] = _place_fields(i["checks"], fields if same else [] if verdicts["same_work"] == "no" else
                                            [_check(c["name"], "check", "judge", "same work unsure") for c in fields])
            if node_ask != "which_node" or "which_node" not in verdicts:
                continue
            node, how = verdicts["which_node"], verdicts.get("how") or ""
            if node == "none":
                _set(i["checks"], _check("Found", "no", "judge", answer.get("why") or ""))
                continue
            _set(i["checks"], _check("Found", "ok", "judge", f"{node}, {HOW.get(how, how)} ({how}): {answer.get('why') or ''}"))
            i["found_by_judge"] = {"node": node, "how": how}
            fields = (i.get("fields_if_found") or {}).get(node) or []
            if how == "same":
                counted += fields
            else:   # part of a broader node, or another kind: shown, never counted
                fields = [_check(c["name"], "check", "judge", f"{HOW.get(how, how)}: {c['why'] or c['verdict']}") for c in fields]
            i["checks"] = _place_fields(i["checks"], fields)
            taken |= {node} | ({parents[node]} if parents.get(node) else set())
            correct += 1
            if how == "other_kind":
                kind_errors.append({"item": i["id"], "node": node})
        credits: dict[str, list[str]] = {}
        for i in u["items"]:
            node = (i.get("found_by_judge") or {}).get("node") or (i["produced"].get("node") if i["produced"].get("node") != "proposal card" else None)
            if node:
                credits.setdefault(node, []).append(i["id"])
        for i in u["items"]:
            node = (i.get("found_by_judge") or {}).get("node")
            if node and len(credits[node]) > 1:
                _set(i["checks"], _check("One node for one key item", "check", "code", f"{node} is credited to {', '.join(credits[node])}"))
        # a matched node the judge says is not the key item's work is asked like a container: is it work, a duplicate?
        kinds = {n["id"]: n.get("kind") for n in u.get("nodes") or []}
        judged = [(i, (by.get(i["id"]) or {}).get("verdicts", {}).get("same_work")) for i in u["items"] if _node_ask(i, u) == "same_work"]
        keep = {i["produced"]["node"] for i, v in judged if v == "yes"} | taken | {x["produced"]["node"] for x in u.get("containers") or []}
        rejected = {i["produced"]["node"]: i for i, v in judged if v == "no" and i["produced"]["node"] not in keep}
        u["containers"] = (u.get("containers") or []) + [
            {"qid": f"node:{u['id']}:{nid}", "produced": i["produced"] | {"kind": kinds.get(nid)}, "independent": None,
             "checks": [_pending(ASK["is_work"]), _pending(ASK["duplicate_of"])], "not_same_work_as": i["id"]} for nid, i in rejected.items()]
        u["extras"] = [x for x in u["extras"] if x["produced"]["node"] not in taken]
        resolved = _node_dups(u, {q: a.get("verdicts") or {} for q, a in by.items()})
        answers = {x["produced"]["node"]: (by.get(f"node:{u['id']}:{x['produced']['node']}") or {}) for x in u["extras"]}
        for x in u["extras"]:
            nid = x["produced"]["node"]
            answer = answers[nid] or None
            put(x["checks"], answer, ("is_work", "duplicate_of"))
            work, dup = (((answer or {}).get("verdicts") or {}).get(k) for k in ("is_work", "duplicate_of"))
            if dup not in (None, "none") and resolved[nid] == "none":   # mutual duplicates: only the later nodes count
                dup = "none"
                _set(x["checks"], _check(ASK["duplicate_of"], "ok", "judge", "a mutual duplicate: the later node counts as the duplicate"))
            if work == "no" or dup not in (None, "none"):
                inventions += 1
            elif work == "yes" and dup == "none":
                gaps += 1
            else:
                unjudged += 1
        for x in u.get("containers") or []:   # judged for precision, never counted as not in the key
            put(x["checks"], by.get(f"node:{u['id']}:{x['produced']['node']}"), ("is_work", "duplicate_of"))
        for n, x in enumerate(u["independent_only"]):
            put(x["checks"], by.get(f"echo:{u['id']}:{n}"), ("is_work",))
    expected = out["summary"]["found"][1]
    if answered:
        out["summary"] |= _tally(counted) | {"fields_scope": "judged same work"}
        for i in waiting:   # in the judged scope, a quote match the judge has not answered waits too
            i["checks"] = _place_fields(i["checks"], [_check(c["name"], "check", "judge", "waits for the judge") for c in i["fields"]])
    out["summary"] |= {"agreed_missed": missed, "agreed_missed_pending": missed_pending,
                       "found_correct": [correct, expected] if answered else None, "judged_items": [answered, asked],
                       "kind_errors": kind_errors, "not_in_key_unjudged": unjudged, "inventions": inventions, "key_gap_candidates": gaps}
    out["summary"]["strict"] = strict_outcomes(out)
    return out


STRICT_CATEGORIES = {"committed": (("task", "story"), ("committed",)), "decisions": (("decision", "risk"), None)}


def _strict_outcome(i: dict, kinds: dict[str, str | None]) -> tuple[str, tuple[str, str] | None]:
    """(outcome, the content it rests on). accepted: the judge called the node (matched, or named in which_node) this
    work, of the key item's kind, and named where on it the deliverable is stated (DELIVERABLE-IN-PARENT); rejected:
    another work, or a node that never states it ("none"); wrong_kind; missed; else unresolved (not judged yet). The
    content is the node with the judge's `explicit_text` (the words that state it) or, without them, the place:
    strict_outcomes makes items resting on the same content unresolved, whatever their order."""
    want = i["kind"].split(" · ")[0]
    node = i["produced"].get("node")
    checks = {c["name"]: c for c in i["checks"]}
    place = i.get("explicit_in")

    def by_place(nid: str) -> tuple[str, tuple[str, str] | None]:
        if place is None:
            return "unresolved", None
        if place == "none":
            return "rejected", None
        text = i.get("explicit_text")
        return "accepted", (nid, "text:" + norm(text)) if text else (nid, "place:" + place)
    if node and node != "proposal card":
        same = checks.get(ASK["same_work"]) or {}
        if not ES.same_kind(kinds.get(node), want):
            return "wrong_kind", None
        if same.get("by") == "judge" and same.get("verdict") == "no":
            return "rejected", None
        if not (same.get("by") == "judge" and same.get("verdict") == "ok"):
            return "unresolved", None
        return by_place(node)
    found = checks.get("Found") or {}
    if found.get("by") == "judge" and found.get("verdict") == "no":
        return "missed", None
    fbj = i.get("found_by_judge")
    if fbj:
        if fbj.get("how") == "other_kind" or not ES.same_kind(kinds.get(fbj["node"]), want):
            return "wrong_kind", None
        return by_place(fbj["node"])
    return ("missed" if node == "proposal card" or not kinds else "unresolved"), None


def _item_errors(i: dict) -> list[str]:
    """Field errors of the node that holds a covered key item (code), plus `title` when the judge said the title
    does not say what was said."""
    fbj = i.get("found_by_judge")
    errs = list((i.get("field_errors_if_found") or {}).get(fbj["node"], []) if fbj else i.get("field_errors") or [])
    title = next((c for c in i["checks"] if c["name"] == ASK["title_faithful"] and c["by"] == "judge"), None)
    return errs + (["title"] if title and title["verdict"] == "no" else [])


def _judged_complete(doc: dict, per: dict[str, str]) -> dict:
    """Complete-item correctness over the coverage denominator: accepted with no field error (exact fields equal,
    must_be_unknown empty, the key's level, a faithful title); misses stay in the denominator."""
    items = {i["id"]: i for u in doc["units"] for i in u["items"]}
    res = {"ok": 0, "incomplete": 0, "unresolved": 0, "not_found": 0}
    errs: dict[str, int] = {}
    for cid, o in per.items():
        if o == "accepted":
            e = _item_errors(items[cid])
            res["incomplete" if e else "ok"] += 1
            for f in e:
                errs[f] = errs.get(f, 0) + 1
        else:
            res["unresolved" if o == "unresolved" else "not_found"] += 1
    n = len(per)
    return {"n": n, **res, "rate": round(res["ok"] / n, 3) if n else None, "lb95": ES._wilson(res["ok"], n) if n else None,
            "field_errors": errs}


def strict_outcomes(doc: dict) -> dict:
    """Per category (committed work; decisions and risks, declined excluded) the strict outcome of each key item and
    the counts: certified only when nothing is unresolved."""
    out: dict[str, dict] = {}
    judged: dict[str, tuple[str, str, tuple[str, str] | None]] = {}
    for cat, (kinds_in, commitments) in STRICT_CATEGORIES.items():
        for u in doc["units"]:
            kinds = {n["id"]: n.get("kind") for n in u.get("nodes") or []}
            for i in u["items"]:
                kind, _, commitment = i["kind"].partition(" · ")
                if kind in kinds_in and (commitment in commitments if commitments else commitment != "declined"):
                    judged[i["id"]] = (cat, *_strict_outcome(i, kinds))
    holders: dict[tuple[str, str], int] = {}
    for _, o, content in judged.values():
        if o == "accepted" and content:
            holders[content] = holders.get(content, 0) + 1
    for cat in STRICT_CATEGORIES:
        per = {cid: ("unresolved" if o == "accepted" and content and holders[content] > 1 else o)
               for cid, (c, o, content) in judged.items() if c == cat}
        c = {k: sum(v == k for v in per.values()) for k in ("accepted", "rejected", "wrong_kind", "unresolved", "missed")}
        n = len(per)
        out[cat] = {"n": n, **c, "rate": round(c["accepted"] / n, 3) if n else None,
                    "lb95": ES._wilson(c["accepted"], n) if n else None, "certified": n > 0 and c["unresolved"] == 0, "items": per,
                    "complete": _judged_complete(doc, per)}
    return out


NODE_LABELS = ("add", "not_work", "duplicate")


def node_labels(pages: list[dict], doc: dict) -> dict[str, str]:
    """{node qid of this doc: add / not_work / duplicate} from the quality page's labels files (a later file wins); a
    node from another run is found by its quote, as page_label_rows does."""
    units = {u["id"]: u for u in doc.get("units") or []}
    out: dict[str, str] = {}
    for page in pages:
        same_run = page.get("record") == (doc.get("run") or {}).get("record")
        for label, a in (page.get("answers") or {}).items():
            if not label.startswith("node:") or a.get("action") not in NODE_LABELS:
                continue
            evid = label[len("node:"):].rpartition(":")[0]
            nid = _label_node(label, a.get("quote"), units[evid], same_run)[0] if evid in units else None
            if nid:
                out[f"node:{evid}:{nid}"] = a["action"]
    return out


def precision(doc: dict, answers: list[dict], labels: dict[str, str], overrides: dict[tuple[str, str], str] | None = None) -> dict:
    """Judged precision over every agent node of the key meetings (node lists, extras, extras the judge took and
    containers), each counted once. Correct: a node a key item credits (judged same work, or found via which_node; a
    node credited by several items counts once), or work the key lacks (is_work yes, not a duplicate). Wrong: not_work
    (is_work no) or duplicate (duplicate_of a node; of mutual duplicates only the later). Your labels override the
    judge: node labels (add, not_work, duplicate) and judge labels ({(qid, ask): expected}). A story gets no credit from
    its task. Everything else is unjudged and never wrong; the rate is over the judged nodes only. validated and flag
    are set from the pass rule."""
    verdicts = {a["qid"]: dict(a.get("verdicts") or {}) for a in answers}
    for (qid, ask), expected in (overrides or {}).items():
        verdicts.setdefault(qid, {})[ask] = expected
    correct, not_work, dup_n, unjudged = 0, 0, 0, 0
    for u in doc.get("units") or []:
        credited: set[str] = set()
        for i in u.get("items") or []:
            node = i["produced"].get("node")
            found = (verdicts.get(i["id"], {}).get("which_node") if (i["id"], "which_node") in (overrides or {})
                     else (i.get("found_by_judge") or {}).get("node"))
            if found and found != "none":
                credited.add(found)
            elif node and node != "proposal card" and verdicts.get(i["id"], {}).get("same_work") == "yes":
                credited.add(node)
        dups = _node_dups(u, verdicts)
        for nid in _unit_nodes(u):
            label = labels.get(f"node:{u['id']}:{nid}")
            work, dup = verdicts.get(f"node:{u['id']}:{nid}", {}).get("is_work"), dups[nid]
            if label == "add" or (label is None and nid in credited):
                correct += 1
            elif label == "not_work" or (label is None and work == "no"):
                not_work += 1
            elif label == "duplicate" or (label is None and dup not in (None, "none")):
                dup_n += 1
            elif work == "yes" and dup == "none":
                correct += 1
            else:
                unjudged += 1
    n, meetings = correct + not_work + dup_n, len(doc.get("units") or [])
    return {"correct": correct, "wrong": {"not_work": not_work, "duplicate": dup_n}, "unjudged": unjudged, "n": n,
            "rate": round(correct / n, 3) if n else None, "lb95": ES._wilson(correct, n) if n else None, "meetings": meetings,
            "per_meeting_to_delete": round((not_work + dup_n) / meetings, 2) if n and meetings else None,
            "validated": False, "flag": None if n else "not judged yet"}


COMPARED = ("same_work", "is_work", "which_node", "duplicate_of")   # how is not compared


def _rate(agree: int, n: int) -> float | None:
    return round(agree / n, 3) if n else None


def _check_rows(rows: list[dict], where: str = "validation") -> None:
    """Refuses a validation row without qid, ask or expected."""
    for k, row in enumerate(rows, 1):
        missing = [f for f in ("qid", "ask", "expected") if not isinstance(row, dict) or row.get(f) in (None, "")]
        if missing:
            raise ValueError(f"{where}: row {k} has no {', '.join(missing)} (each row needs qid, ask, expected)")


def agreement(answers: list[dict], validation: list[dict]) -> dict[str, dict]:
    """The judge's verdicts against each validation source ([{qid, ask, expected, source}], expected yes/no, a node id
    or none): per source n, agree, rate, the disagreements and the same per ask. A row the judge has not answered is
    not counted."""
    _check_rows(validation)
    by = {a["qid"]: (a.get("verdicts") or {}) for a in answers}
    out: dict[str, dict] = {}
    for row in validation:
        r = out.setdefault(row.get("source") or "?", {"n": 0, "agree": 0, "rate": None, "disagreements": [], "by_ask": {}})
        judge = by.get(row["qid"], {}).get(row["ask"])
        if row["ask"] not in COMPARED or judge is None:
            continue
        ask = r["by_ask"].setdefault(row["ask"], {"n": 0, "agree": 0, "rate": None})
        same = judge == row["expected"]
        for x in (r, ask):
            x["n"] += 1
            x["agree"] += same
            x["rate"] = _rate(x["agree"], x["n"])
        if not same:
            r["disagreements"].append({"qid": row["qid"], "ask": row["ask"], "judge": judge, "expected": row["expected"]})
    return out


def overall(answers: list[dict], validation: list[dict]) -> dict:
    """Agreement over every (qid, ask) once; where several sources label it, your label ("you") decides."""
    rows: dict[tuple[str, str], dict] = {}
    for row in validation:
        k = (row["qid"], row["ask"])
        if k not in rows or row.get("source") == "you":
            rows[k] = row
    r = agreement(answers, [row | {"source": "all"} for row in rows.values()]).get("all") or {"n": 0, "agree": 0}
    return {"n": r["n"], "agree": r["agree"], "rate": _rate(r["agree"], r["n"])}


LABELS = "eval-labels/1"
SEEN_ONLY = "validated only on labels made after seeing the judge"
STATE_UNKNOWN = "validated only on labels not marked as made before seeing the judge (label state unknown)"
SEEN_AND_UNKNOWN = SEEN_ONLY + "; label state unknown for some"
NO_ROWS = {"n": 0, "agree": 0, "rate": None, "by_ask": {}}


def _label_node(label: str, quote: str | None, unit: dict, same_run: bool) -> tuple[str | None, str]:
    """(this doc's node id, why not) for a node a page label names: by id on the run it was made on (when its quote, if
    any, agrees), else by its creating quote among the meeting's nodes; two nodes with that quote are ambiguous."""
    nid = label.rpartition(":")[2] if label.startswith("node:") else label
    nodes = unit.get("nodes") or []
    want = norm(quote or "")
    if same_run and any(n["id"] == nid and (not want or norm(n.get("quote") or "") == want) for n in nodes):
        return nid, ""
    if not want:
        return None, f"{nid} is not a node of this run and the label carries no quote to find it by"
    hits = [n["id"] for n in nodes if norm(n.get("quote") or "") == want]
    if len(hits) > 1:
        return None, f"the quote is ambiguous: nodes {', '.join(hits)} of {unit['id']} all have it"
    return (hits[0], "") if hits else (None, f"no node of {unit['id']} has the quote of {nid}")


def _one_per_question(ranked: list[tuple[int, dict]]) -> list[dict]:
    """One row per (qid, ask): the higher rank wins (an explicit judge label over a row derived from a node label), a
    later row of the same rank over an earlier one; rows keep the order their question first appeared in."""
    best: dict[tuple[str, str], tuple[int, dict]] = {}
    for rank, row in ranked:
        k = (row["qid"], row["ask"])
        if k not in best or rank >= best[k][0]:
            best[k] = (rank, row)
    order = list(dict.fromkeys((row["qid"], row["ask"]) for _, row in ranked))
    return [best[k][1] for k in order]


def _ranked_label_rows(labels: dict, doc: dict, answers: list[dict] | None = None) -> tuple[list[tuple[int, dict]], list[dict]]:
    same_run = labels.get("record") == (doc.get("run") or {}).get("record")
    units = {u["id"]: u for u in doc.get("units") or []}
    items = {i["id"]: (i, u) for u in units.values() for i in u.get("items") or []}
    keyed = set(items)
    verdicts = {a["qid"]: a.get("verdicts") or {} for a in answers or []}
    rows: list[tuple[int, dict]] = []
    skipped: list[dict] = []

    def row(qid: str, ask: str, expected: str, a: dict, rank: int) -> None:   # an answer carried from an earlier file: not blind
        seen = None if a.get("carried_from") else a.get("seen_judge")
        rows.append((rank, {"qid": qid, "ask": ask, "expected": expected, "source": "you", "seen_judge": seen}))
    for label, a in (labels.get("answers") or {}).items():
        act = a.get("action")
        if label.startswith("judge:"):
            qid, _, ask = label[len("judge:"):].rpartition(":")
            if qid.startswith("node:") and not same_run:
                skipped.append({"label": label, "why": "node ids differ between runs and a judge label carries no quote"})
                continue
            expected = a.get("expected") or (verdicts.get(qid, {}).get(ask) if act == "agree" and same_run else None)
            if act not in ("agree", "disagree") or not expected:
                skipped.append({"label": label, "why": "no expected value: an agree names the judge answer of the run it was made on"
                                if act == "agree" else f"{act!r} without an expected value"})
                continue
            if qid in items and not same_run:
                item, unit = items[qid]
                if ask == "which_node":
                    if expected == "none":
                        skipped.append({"label": label, "why": "none names the nodes of the run it was made on, not this one's"})
                        continue
                    expected, why = _label_node(expected, a.get("quote"), unit, False)
                    if expected is None:
                        skipped.append({"label": label, "why": f"which_node: {why}"})
                        continue
                else:
                    paired = (item.get("produced") or {}).get("quote") or ""
                    if not a.get("quote") or not paired or norm(a["quote"]) != norm(paired):
                        skipped.append({"label": label, "why": "made on another run, and this run pairs the item with another node"
                                        if a.get("quote") else "made on another run without the node's quote"})
                        continue
            row(qid, ask, expected, a, 1)
            continue
        if not label.startswith("node:") or act not in ("add", "not_work", "duplicate"):
            continue
        evid = label[len("node:"):].rpartition(":")[0]
        unit = units.get(evid)
        if unit is None:
            skipped.append({"label": label, "why": f"{evid} is not a meeting of this run"})
            continue
        nid, why = _label_node(label, a.get("quote"), unit, same_run)
        if nid is None:
            skipped.append({"label": label, "why": why})
            continue
        if act != "duplicate":
            row(f"node:{evid}:{nid}", "is_work", "yes" if act == "add" else "no", a, 0)
            continue
        target = a.get("dup_of") or ""
        if target in keyed:
            continue
        t_unit = units.get(target[len("node:"):].rpartition(":")[0]) if target.startswith("node:") else unit
        tid, why = _label_node(target, a.get("dup_of_quote"), t_unit, same_run) if t_unit else (None, f"{target} is not of this run")
        if tid is None:
            skipped.append({"label": label, "why": f"dup_of: {why}"})
            continue
        row(f"node:{evid}:{nid}", "duplicate_of", tid, a, 0)
    return rows, skipped


def page_label_rows(labels: dict, doc: dict, answers: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """(validation rows {qid, ask, expected, source: "you", seen_judge}, skipped [{label, why}]) from a quality page's
    eval-labels/1 file, in this doc's qid space, one row per (qid, ask): node Add/Not work -> is_work yes/no; node
    Duplicate of a node -> duplicate_of that node (a key item target asks nothing of the judge); judge Agree/Disagree ->
    that ask's expected value (an Agree without one takes this run's judge answer, so only on the run it was made on; a
    key item's label from another run counts only when its node quote is the node this run pairs with the item, or,
    for which_node, names a node of this run); key items -> none. An explicit judge label wins over a node label."""
    ranked, skipped = _ranked_label_rows(labels, doc, answers)
    return _one_per_question(ranked), skipped


def validation_from_page_labels(labels: dict, doc: dict, answers: list[dict] | None = None) -> list[dict]:
    """The validation rows of a quality page's labels file (page_label_rows, without the skipped list)."""
    return page_label_rows(labels, doc, answers)[0]


def load_page_labels(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != LABELS or not isinstance(data.get("answers"), dict) \
                or not all(isinstance(a, dict) for a in data["answers"].values()):
            raise ValueError(f"{Path(p).name} is not a {LABELS} labels file (the quality page's Save)")
        out.append(data)
    return out


def with_blind(answers: list[dict], rows: list[dict], agreed: dict[str, dict]) -> dict[str, dict]:
    """Your source, and any whose rows say whether the judge was seen, gains `blind` (labels made before seeing the
    judge), `seen` (after) and `unknown` (rows that do not say)."""
    empty = {"n": 0, "agree": 0, "rate": None, "disagreements": [], "by_ask": {}}
    for src in {r.get("source") or "?" for r in rows if "seen_judge" in r or r.get("source") == "you"}:
        if src not in agreed:
            continue
        mine = [r for r in rows if (r.get("source") or "?") == src]
        for name, keep in (("blind", lambda v: v is False), ("seen", lambda v: v is True), ("unknown", lambda v: not isinstance(v, bool))):
            agreed[src][name] = agreement(answers, [r for r in mine if keep(r.get("seen_judge"))]).get(src) or dict(empty)
    return agreed


def load_validation(paths: list[Path]) -> list[dict]:
    """Validation rows from files; a row without a source takes its file's name."""
    rows = []
    for p in paths:
        mine = json.loads(Path(p).read_text(encoding="utf-8"))
        _check_rows(mine, Path(p).name)
        rows += [{"source": Path(p).stem} | row for row in mine]
    return rows


# ── the page's labels: each card's qid, the judge's answers to it, your earlier answers ─────────────────────────

ASK_NAME = ASK | {"which_node": "Which node holds it"}


def label_cards(doc: dict) -> dict[str, tuple[str, dict, dict]]:
    """{qid: (type, card, unit)} for every card the page can label (item, node, only), each card given its qid."""
    out: dict[str, tuple[str, dict, dict]] = {}
    for u in doc.get("units") or []:
        for i in u.get("items") or []:
            i["qid"] = i["id"]
            out[i["qid"]] = ("item", i, u)
        for x in (u.get("extras") or []) + (u.get("extras_taken") or []) + (u.get("containers") or []):
            x["qid"] = f"node:{u['id']}:{x['produced']['node']}"
            out[x["qid"]] = ("node", x, u)
        for n, x in enumerate(u.get("independent_only") or []):
            x["qid"] = f"echo:{u['id']}:{n}"
            out[x["qid"]] = ("only", x, u)
    return out


def attach_judge(doc: dict, answers: list[dict]) -> None:
    """Each card's judge answers [{qid, ask, name, value, why, options}] (the page hides them until you answer):
    options are yes/no, or none and the meeting's other nodes for a node ask."""
    by = {a["qid"]: a for a in answers}
    for qid, (kind, card, unit) in label_cards(doc).items():
        a = by.get(qid)
        if not a:
            continue
        own = card["produced"]["node"] if kind == "node" else None
        nodes = [n["id"] for n in unit.get("nodes") or [] if n["id"] != own]
        shown = [{"qid": qid, "ask": ask, "name": ASK_NAME.get(ask, ask), "value": v, "why": a.get("why") or "",
                  "options": ["none", *nodes] if ask in NODE_ASKS else ["yes", "no"]}
                 for ask, v in (a.get("verdicts") or {}).items() if ask != "how" and isinstance(v, str)]
        if shown:
            card["judge"] = shown


def keep_pre_judge(doc: dict, before: dict) -> None:
    """What the page shows until you reveal the judge: each card the judge answered keeps its checks (and agreement) as
    they were before the judge (`before_judge`), and each node the judge took out of a meeting's nodes not in the key
    stays in `extras_taken` with `taken_by_judge`: the key item whose found node (or that node's parent) it is."""
    old = label_cards(before)
    for qid, (_, card, _) in label_cards(doc).items():
        if card.get("judge") and qid in old:
            card["before_judge"] = {k: old[qid][1][k] for k in ("checks", "agreement") if k in old[qid][1]}
    was = {u["id"]: u for u in before.get("units") or []}
    for u in doc.get("units") or []:
        left = {x["produced"]["node"] for x in u.get("extras") or []}
        parent = {n["id"]: n.get("parent") for n in u.get("nodes") or []}
        took = {}
        for i in u.get("items") or []:
            node = (i.get("found_by_judge") or {}).get("node")
            for n in (node, parent.get(node or "")) if node else ():
                if n:
                    took.setdefault(n, i["id"])
        u["extras_taken"] = [x | {"taken_by_judge": took.get(x["produced"]["node"], "")}
                             for x in was.get(u["id"], {}).get("extras") or [] if x["produced"]["node"] not in left]


def _entry_from_kind(kind: str, card_type: str, card: dict) -> dict | None:
    """The page answer an earlier work answer (committed / proposed / not_work) stands for; none for "can't tell"."""
    if card_type == "item":
        if kind == "not_work":
            return {"action": "drop"}
        if kind in relabel.WORK:
            same = kind == (card.get("edit") or {}).get("commitment")
            return {"action": "keep"} if same else {"action": "modify", "fields": {"commitment": kind}}
    if card_type == "node":
        if kind == "not_work":
            return {"action": "not_work"}
        if kind in relabel.WORK:
            return {"action": "add", "commitment": kind}
    return None


def attach_yours(doc: dict, paths: list[Path], ev: dict[str, dict]) -> int:
    """Marks each card with your earlier answer {answer, source, entry} (entry: what the page pre-selects) from your
    files: the page's own eval-labels/1 saves (by qid; a node from another run by its quote; judge answers only on their
    own run) and every format relabel.read_labels reads (a relabel page's save, the labels file, chat rulings). A later
    file wins. Returns how many earlier answers match no card."""
    cards = label_cards(doc)
    record = (doc.get("run") or {}).get("record")
    units = {u["id"]: u for u in doc.get("units") or []}
    pool = {u["id"]: [{"cid": i["id"], "quote": i.get("quote") or ""} for i in u.get("items") or []]
            + [{"cid": x["qid"], "quote": x["produced"].get("quote") or ""} for x in (u.get("extras") or []) + (u.get("extras_taken") or []) + (u.get("containers") or [])] for u in units.values()}
    unmatched = 0
    for path in paths:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema") == LABELS:
            same = data.get("record") == record
            source = f"you, {str(data.get('saved_at') or '')[:10]} ({path.stem})"
            for q, a in (data.get("answers") or {}).items():
                target: dict | None = None
                if not isinstance(a, dict):
                    pass
                elif q.startswith("judge:"):
                    qid, _, ask = q[len("judge:"):].rpartition(":")
                    hit = cards.get(qid) if same else None
                    target = next((j for j in (hit[1].get("judge") or []) if j["ask"] == ask), None) if hit else None
                elif q.startswith("node:"):
                    unit = units.get(q[len("node:"):].rpartition(":")[0])
                    nid = _label_node(q, a.get("quote"), unit, same)[0] if unit else None
                    hit = cards.get(f"node:{unit['id']}:{nid}") if unit and nid else None
                    target = hit[1] if hit and hit[0] == "node" else None
                else:
                    hit = cards.get(q)
                    target = hit[1] if hit and hit[0] == "item" else None
                if target is None:
                    unmatched += 1
                    continue
                target["yours"] = {"answer": str(a.get("action") or ""), "source": source, "entry": a}
            continue
        stamp = str(data.get("stamp") or "") if isinstance(data, dict) else ""
        mine, _ = relabel.read_labels([path], [], pool, ev)
        for cid, a in mine.items():
            if cid.startswith("node:") and cid.count(":") == 1:   # a relabel page's node: its id holds on its own run only
                cid = next((q for q, (t, c, _) in cards.items() if t == "node" and c["produced"]["node"] == cid[len("node:"):]
                            and stamp.rpartition("-")[0] == record), "")
            hit = cards.get(cid)
            if not hit or hit[0] not in ("item", "node"):
                unmatched += 1
                continue
            entry = _entry_from_kind(a["kind"], hit[0], hit[1])
            hit[1]["yours"] = {"answer": a["kind"], "source": a["by"]} | ({"entry": entry} if entry else {})
    return unmatched


# ── the pass rule: your bars, never a default ────────────────────────────────────────────────────────────────────

CARDS = ES.BUCKET_NAMES["B18"]
GROUNDS = {"found": ("same_work", "which_node"), "work": ("is_work", "duplicate_of"),   # the asks a judge measure rests on
           "precision": ("same_work", "which_node", "is_work", "duplicate_of")}


def pass_rule(summary: dict, rule: dict) -> list[dict]:
    """Each measure with its value, your bar (None when not set) and whether it passes (None without a bar). A measure
    the judge decides passes only when, on the asks it rests on (GROUNDS), the judge agrees with the chosen validation
    source (rule judge_agreement_source, default "you") at a rate >= judge_agreement_min over >= judge_agreement_min_n
    answers, and no merged batch was judged on the reader's tier; otherwise its flag says why."""
    ratio = lambda pair: round(pair[0] / pair[1], 3) if pair and pair[1] else None
    name = rule.get("judge_agreement_source", "you")
    full = ((summary.get("judge_agreement") or {}).get(name) or {})
    least, least_n = rule.get("judge_agreement_min"), rule.get("judge_agreement_min_n")
    strict = name == "you"   # your labels validate the judge only when made before seeing it (automation bias)
    blind = (full.get("blind") or NO_ROWS) if strict else full
    unknown = (full.get("unknown") or NO_ROWS) if "blind" in full else full

    def counts(entry: dict, asks: tuple[str, ...] | None) -> tuple[int, int]:
        if asks is None:
            return entry.get("n", 0), entry.get("agree", 0)
        got = [(entry.get("by_ask") or {}).get(a) or {"n": 0, "agree": 0} for a in asks]
        return sum(a["n"] for a in got), sum(a["agree"] for a in got)

    def after_seeing(asks: tuple[str, ...] | None) -> str | None:
        """Why your labels on these asks cannot validate the judge: none made blind, only after seeing it or unmarked."""
        rest = counts(full, asks)[0] - counts(blind, asks)[0]
        if not strict or rest <= 0:
            return None
        u = counts(unknown, asks)[0]
        return SEEN_ONLY if u == 0 else STATE_UNKNOWN if u >= rest else SEEN_AND_UNKNOWN

    def gate(group: str) -> str | None:
        if least is None or least_n is None:
            return "not validated"
        n, agree = counts(blind, GROUNDS[group])
        if n >= least_n and n > 0:
            return None if agree / n >= least else "not validated"
        return after_seeing(GROUNDS[group]) or "not validated"
    blind_enough = least_n is not None and blind.get("n", 0) >= least_n
    source = blind if blind_enough else full
    judged_fields = summary.get("fields_scope") == "judged same work"
    judged = summary.get("judged_items") or [0, 0]
    notes = {"Owner right": f"on {summary.get('fields_scope') or 'quote matches'}", "Status right": f"on {summary.get('fields_scope') or 'quote matches'}"}
    rows = [("Work found, correctly", ratio(summary.get("found_correct")), rule.get("found_correct_min"), ">=", "found"),
            ("Nothing two readers agree on is missed", summary.get("agreed_missed"), rule.get("agreed_missed_max"), "<=",
             "found" if judged[0] else None),
            ("Owner right", ratio(summary.get("owner")), rule.get("owner_min"), ">=", "found" if judged_fields else None),
            ("Status right", ratio(summary.get("status")), rule.get("status_min"), ">=", "found" if judged_fields else None),
            ("Inventions", summary.get("inventions"), rule.get("inventions_max"), "<=", "work"),
            ("Key gap candidates", summary.get("key_gap_candidates"), rule.get("key_gap_candidates_max"), "<=", "work"),
            ("Precision (judged)", (summary.get("precision") or {}).get("rate"), rule.get("precision_min"), ">=", "precision"),
            ("Judge agrees with you", source.get("rate"), least, ">=", None)]
    if (summary.get("proposal_cards") or [0, 0])[1]:   # a lower bound like B15: shown, never passed
        rows.insert(-1, (CARDS, ratio(summary["proposal_cards"]), None, ">=", None))
        notes[CARDS] = (f"matched to the key by code; {summary.get('proposal_cards_burden')} not matched per meeting, "
                        "unjudged")
    if summary.get("stability") is not None:
        rows.append(("Stable", ratio(summary["stability"]), rule.get("stability_min"), ">=", "stability"))
    out = []
    for measure, value, bar, op, group in rows:
        few = measure == "Judge agrees with you" and value is not None and (least_n is None or source.get("n", 0) < least_n)
        waits = measure == "Nothing two readers agree on is missed" and summary.get("agreed_missed_pending")
        flag = ("too few answers" if few else "not judged yet" if waits else None if group is None else
                ("not validated" if not summary.get("stability_validated") else None) if group == "stability" else
                "not judged yet" if value is None else
                "judge tier not independent" if summary.get("judge_same_tier") else
                gate(group) or
                ("partly judged" if measure == "Work found, correctly" and judged[0] < judged[1] else None))
        flag = "lower bound" if measure == CARDS else flag
        if measure == "Judge agrees with you" and not flag and value is not None and not blind_enough:
            flag = after_seeing(None)
        passed = None if flag or bar is None or value is None else (value >= bar if op == ">=" else value <= bar)
        out.append({"measure": measure, "value": value, "bar": bar, "pass": passed, "flag": flag} | ({"note": notes[measure]} if measure in notes else {}))
    return out


# ── stability: the same verdict across runs on one key ────────────────────────────────────────────────────────────

UNVALIDATED = ("not validated", "judge tier not independent", "not judged yet", SEEN_ONLY, STATE_UNKNOWN, SEEN_AND_UNKNOWN)


def _found_correctly(item: dict, unit: dict) -> tuple[str, str]:
    """(yes / no / not judged, who decided): a matched node judged the same work, or an unmatched item the judge found;
    a proposal card or an item in a meeting without nodes is not found correctly (code)."""
    node_ask = _node_ask(item, unit)
    if node_ask is None:
        return "no", "code"
    name = ASK["same_work"] if node_ask == "same_work" else "Found"
    c = next((c for c in item["checks"] if c["name"] == name and c["by"] == "judge"), None)
    return ({"ok": "yes", "no": "no"}.get(c["verdict"], "not judged") if c else "not judged"), "judge"


def stability(records: list[Path], rule: dict | None = None, rule_sha: str | None = None) -> dict:
    """Per key item, found correctly in each record's built quality document (quality/quality.json) and n of k judged
    records; stable = every one of the k records judged it and all agree (a majority is flaky, a record with no verdict
    is a missing trial). Each record's judge is validated
    again under this rule (pass_rule on its summary). Refuses records on different keys, a record judged after its
    quality document was built, and a quality document built before the node lists."""
    rule = rule or {}
    metas = [json.loads((Path(r) / "meta.json").read_text(encoding="utf-8")) for r in records]
    keys = {(m.get("key_version"), m.get("key_sha")) for m in metas}
    if len(keys) != 1 or not next(iter(keys))[1]:
        raise ValueError(f"different answer keys ({', '.join(sorted(f'{v} {s}' for v, s in keys))}): no stability can be reported")
    code = {m.get("execution_manifest_sha256") for m in metas}
    if len(code) > 1:
        raise ValueError("different code (execution manifests differ): these records are not one version's repeated runs")
    cohort = "one version" if code != {None} else "unverified: the records carry no execution manifest"
    per_item: dict[str, dict[str, tuple[str, str]]] = {}
    runs = []
    for r in records:
        name = Path(r).name
        f = Path(r) / "quality" / "quality.json"
        if not f.exists():
            raise ValueError(f"{name}: no quality/quality.json (run quality.py build first)")
        judge = Path(r) / "quality" / "judge"
        later = [x.name for x in [*judge.glob("answers_*.json"), judge / "models.json"] if x.exists() and x.stat().st_mtime > f.stat().st_mtime]
        if later:
            raise ValueError(f"{', '.join(later)} is newer than its quality.json: rebuild {name} first (quality.py build)")
        doc = json.loads(f.read_text(encoding="utf-8"))
        if any("nodes" not in u for u in doc["units"]):
            raise ValueError(f"{name}: its quality.json was built before the node lists: rebuild {name} first (quality.py build)")
        found = next(x for x in pass_rule(doc.get("summary") or {}, rule) if x["measure"] == "Work found, correctly")
        runs.append({"record": name, "judge": found["flag"] if found["flag"] in UNVALIDATED else "validated"})
        for u in doc["units"]:
            for i in u["items"]:
                per_item.setdefault(i["id"], {})[name] = _found_correctly(i, u)
    items = []
    k = len(runs)
    for cid, verdicts in per_item.items():
        judged = [v for v, _ in verdicts.values() if v != "not judged"]
        yes, no = judged.count("yes"), judged.count("no")
        items.append({"id": cid, "verdicts": {r: v for r, (v, _) in verdicts.items()}, "found": yes, "of": len(judged),
                      "by_judge": any(by == "judge" and v != "not judged" for v, by in verdicts.values()),
                      "stable": k >= 2 and (yes == k or no == k), "all_found": yes == k, "consistently_failed": no == k,
                      "flaky": yes > 0 and no > 0, "missing": k - len(judged), "success_fraction": round(yes / k, 3)})
    by_judge = [i for i in items if i["by_judge"]]
    return {"schema": "eval-stability/1", "key": metas[0].get("key_version"), "key_sha": metas[0].get("key_sha"), "rule_sha256": rule_sha,
            "records": runs, "items": items,
            "summary": {"stable": [sum(i["stable"] for i in items), len(items)],
                        "stable_judged": [sum(i["stable"] for i in by_judge), len(by_judge)],
                        "flaky": sum(i["flaky"] for i in items), "consistently_failed": sum(i["consistently_failed"] for i in items),
                        "all_found": sum(i["all_found"] for i in items), "missing": sum(i["missing"] > 0 for i in items),
                        "rule": "every one of the k records judged it and all agree (k >= 2); a missing verdict is not stable",
                        "validated": all(x["judge"] == "validated" for x in runs), "cohort": cohort}}


# ── the CLI ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _answers(judge: Path) -> list[dict]:
    """Every batch's answers that passed check_verdicts (judge-run writes only those)."""
    return [a for f in sorted(judge.glob("answers_*.json")) for a in json.loads(f.read_text(encoding="utf-8"))]


def _judge_models(out: Path) -> dict[str, dict]:
    """{evid: {model, same_tier_ok}} for each batch whose answers passed check_verdicts."""
    f = out / "judge" / "models.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _judge_tiers(out: Path, reader: set[str]) -> tuple[list[str], list[str]]:
    """The judge tiers behind the merged answers ("unknown" where no judge-run recorded one) and the batches whose tier
    is the reader's, or not known to differ from it, without --same-tier-ok."""
    models, tiers, same = _judge_models(out), set(), []
    for f in sorted((out / "judge").glob("answers_*.json")):
        evid = f.stem[len("answers_"):]
        m = models.get(evid) or {}
        tier = m.get("model") or "unknown"
        tiers.add(tier)
        if (tier == "unknown" or not reader or tier in reader) and not m.get("same_tier_ok"):
            same.append(evid)
    return sorted(tiers), same


def document(record: Path, echo_root: Path, key_path: Path | None, out: Path, rule: dict, validation: list[Path] | None = None,
             stable: Path | None = None, labels: list[Path] | None = None, yours: list[Path] | None = None) -> dict:
    """The run's quality document: built from its copy, with the judge's checked answers in place, the judge's agreement
    with each validation source, and your bars."""
    meta = json.loads((record / "meta.json").read_text(encoding="utf-8"))
    key_path = key_path or Path(meta["key_path"])
    head, key = ES.load_gold(key_path)
    project = Path(meta["project"])
    ev = S.evidence(C.load(project).store)
    meetings = {e: ev[e] for e in dict.fromkeys(g["evidence"] for g in key) if e in ev}
    calls = ECHO.match_calls(meetings, ECHO.load_calls(echo_root))
    reader = _reader_tiers(meta)
    judge_tiers, same_tier = _judge_tiers(out, reader)
    run = {"record": record.name, "code": (meta.get("git_commit") or "")[:7], "tier": ", ".join(sorted(reader)) or "?", "judge_tiers": judge_tiers}
    before = build(project, key, calls, run)
    answers = _answers(out / "judge")
    doc = apply_verdicts(before, answers) if answers else copy.deepcopy(before)
    doc["key"] = meta.get("key_version") if key_path == Path(meta["key_path"]) else key_path.name
    doc |= {"stamp": relabel._stamp(record, key_path), "record": record.name, "key_version": head.get("version"),
            "key_sha": head.get("sha256")}
    attach_judge(doc, answers)
    keep_pre_judge(doc, before)
    if yours:
        doc["summary"]["yours_unmatched"] = attach_yours(doc, yours, ev)
    doc["judged"] = len(answers)
    doc["summary"]["judged_from_cache"] = sum(a.get("source") == "cache" for a in answers)
    rows = load_validation(validation or [])
    ranked: list[tuple[int, dict]] = [(1, r) for r in rows if r.get("source") == "you"]
    skipped: list[dict] = []
    pages = load_page_labels(labels or [])
    for page in pages:
        if (page.get("key_version"), page.get("key_sha")) != (head.get("version"), head.get("sha256")):
            raise ValueError(f"a labels file is on key {page.get('key_version')} {page.get('key_sha')}, this build on "
                             f"{head.get('version')} {head.get('sha256')}: label this key's page")
        mine, why = _ranked_label_rows(page, doc, answers)
        ranked += mine
        skipped += why
    rows = [r for r in rows if r.get("source") != "you"] + _one_per_question(ranked)
    blind_rows = [r for r in rows if r.get("source") != "you" or r.get("seen_judge") is False]
    doc["summary"] |= {"judge_agreement": with_blind(answers, rows, agreement(answers, rows)),
                       "judge_agreement_overall": overall(answers, rows) | {"blind": overall(answers, blind_rows)},
                       "judge_same_tier": same_tier}
    if labels:
        doc["summary"]["labels_skipped"] = skipped
    if stable:
        st = json.loads(Path(stable).read_text(encoding="utf-8"))
        if (st.get("key"), st.get("key_sha")) != (meta.get("key_version"), meta.get("key_sha")) or not st.get("key_sha"):
            raise ValueError(f"different answer keys: {Path(stable).name} is on {st.get('key')} {st.get('key_sha')}, "
                             f"{record.name} on {meta.get('key_version')} {meta.get('key_sha')}")
        if record.name not in {x["record"] for x in st.get("records") or []}:
            raise ValueError(f"{Path(stable).name} does not include {record.name}: run quality.py stability with it")
        doc["summary"] |= {"stability": st["summary"]["stable"], "stability_judged": st["summary"].get("stable_judged"),
                           "stability_validated": bool(st["summary"].get("validated"))}
    yours_rows = {(r["qid"], r["ask"]): r["expected"] for r in rows if r.get("source") == "you"}
    doc["summary"]["precision"] = precision(doc, answers, node_labels(pages, doc), yours_rows)
    doc["rule"] = pass_rule(doc["summary"], rule)
    row = next(r for r in doc["rule"] if r["measure"] == "Precision (judged)")
    doc["summary"]["precision"] |= {"validated": row["flag"] is None and row["value"] is not None, "flag": row["flag"]}
    unverified = {e: why for e, why in answer_check(record, out).items() if why != "ok"}
    doc["summary"]["unverified_answers"] = unverified
    if unverified:
        doc["summary"]["precision"] |= {"validated": False,
                                        "flag": doc["summary"]["precision"].get("flag") or "answers not verified against their questions"}
    mark_strict_validation(doc["summary"], doc["rule"], bool(unverified))
    return doc


def mark_strict_validation(summary: dict, rule_rows: list[dict], unverified: bool = False) -> None:
    """Strict figures rest on the same_work and which_node asks, validated as "Work found, correctly" is; a figure is
    eligible for release only when every item is judged (certified) and that judge is validated."""
    row = next((r for r in rule_rows if r["measure"] == "Work found, correctly"), {"flag": "not judged yet"})
    for s in (summary.get("strict") or {}).values():
        s |= {"validated": row["flag"] is None and not unverified,
              "flag": "answers not verified against their questions" if unverified else row["flag"]}
        s["eligible"] = bool(s.get("certified")) and s["validated"]


def judge_prep(record: Path, out: Path) -> list[Path]:
    doc = json.loads((out / "quality.json").read_text(encoding="utf-8"))
    judge = out / "judge"
    judge.mkdir(parents=True, exist_ok=True)
    paths = []
    for evid, qs in judge_questions(doc).items():
        if qs:
            paths.append(judge / f"judge_{evid}.json")
            paths[-1].write_text(json.dumps(qs, ensure_ascii=False, indent=1), encoding="utf-8")
    return paths


def _json_array(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    return json.loads(text[start:end + 1]) if 0 <= start < end else []


class SameTier(ValueError):
    pass


def _reader_tiers(meta: dict) -> set[str]:
    """The tiers the run's reader used: meta.json model_tier, model_pinned and every model it used, mapped back through
    tiers.json. Empty when unknown."""
    tier_of = {m: t for t, m in REC.TIERS.items()}
    ids = ([meta["model_pinned"]] if meta.get("model_pinned") else []) + list(meta.get("models") or [])
    return ({meta["model_tier"]} if meta.get("model_tier") else set()) | {tier_of[m] for m in ids if m in tier_of}


CACHE = "judge-cache"   # beside the records: <key sha>-<judge tier>.v<schema>.jsonl, one checked answer per line, later lines win
CACHE_SCHEMA = 2        # older cache files stay on disk and are never read
JUDGE_PROMPTS = ("QUALITY_JUDGE.md", "JUDGE.md")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def judge_binding(model: str, transcript: Path | None) -> dict[str, str]:
    """What an answer depends on beside the question: the judge prompts, the exact judge model and the transcript."""
    prompts = b"".join(((EVALS / n).read_bytes() if (EVALS / n).exists() else b"") + b"\0" for n in JUDGE_PROMPTS)
    return {"prompt": _sha(prompts), "model": REC.TIERS.get(model, model),
            "transcript": _sha(transcript.read_bytes()) if transcript and transcript.exists() else ""}


def _parent_title(parent: object) -> object:
    return parent.split(" · ", 1)[1] if isinstance(parent, str) and " · " in parent else parent


def _node_content(n: dict) -> str:
    return json.dumps({k: v for k, v in n.items() if k not in ("id", "node", "parent")}, sort_keys=True, ensure_ascii=False)


def _candidate_keys(q: dict) -> dict[str, str]:
    """Each candidate's id -> what the judge sees of it, ids replaced by content (a parent outside the question keeps its id)."""
    cands = q.get("candidates") or []
    content = {c["id"]: _node_content(c) for c in cands}
    prod = q.get("produced") or {}
    if prod.get("node"):
        content.setdefault(str(prod["node"]), _node_content(prod))
    return {c["id"]: json.dumps({"node": content[c["id"]], "parent": content.get(c.get("parent") or "", c.get("parent"))},
                                sort_keys=True, ensure_ascii=False) for c in cands}


def _canonical(q: dict) -> dict[str, object]:
    """The question as the judge sees it, with this run's node ids left out (a key item keeps its stable key id)."""
    out: dict[str, object] = {k: v for k, v in q.items() if k not in ("qid", "produced", "candidates", "ask")}
    out["ask"] = sorted(q["ask"])
    if q["type"] == "key item":
        out["item"] = q["qid"]
    if "produced" in q:
        out["produced"] = {k: (_parent_title(v) if k == "parent" else v) for k, v in (q["produced"] or {}).items() if k != "node"}
    if "candidates" in q:
        out["candidates"] = sorted(_candidate_keys(q).values())
    return out


def _fingerprint(q: dict, bind: dict[str, str]) -> str:
    """A question as the same question in any run of the same key: every field the judge is shown, ids excluded, bound
    to the cache schema, judge prompts, judge model and transcript."""
    base = {"schema": CACHE_SCHEMA, "bind": bind, "question": _canonical(q)}
    return _sha(json.dumps(base, sort_keys=True, ensure_ascii=False).encode())


def _cache_file(record: Path, meta: dict, model: str) -> Path | None:
    return record.parent / CACHE / f"{meta['key_sha']}-{model}.v{CACHE_SCHEMA}.jsonl" if meta.get("key_sha") else None


def _cache_put(path: Path, batch: list[dict], answers: list[dict], record: str, bind: dict[str, str]) -> None:
    """Each model answer once, its node verdicts stored as what the judge saw of that candidate."""
    qs = {q["qid"]: q for q in batch}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for a in answers:
            q = qs[a["qid"]]
            keys = _candidate_keys(q)
            verdicts = {k: ({"node": keys[v]} if k in NODE_ASKS and v != "none" else v) for k, v in a["verdicts"].items()}
            f.write(json.dumps({"fp": _fingerprint(q, bind), "verdicts": verdicts, "why": a["why"], "from": f"{record}:{a['qid']}"},
                               ensure_ascii=False) + "\n")


def _from_cache(path: Path | None, batch: list[dict], bind: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """(answers the cache holds for this batch, mapped to this run's qids and node ids; the questions it cannot answer).
    A node answer maps back only to the one candidate showing the same content; two alike candidates: the model is asked."""
    entries: dict[str, dict] = {}
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                entries[e["fp"]] = e
    found, missing = [], []
    for q in batch:
        e = entries.get(_fingerprint(q, bind))
        keys = list(_candidate_keys(q).items())
        ids = {k: i for i, k in keys if sum(k == other for _, other in keys) == 1}
        verdicts: dict[str, str | None] = {}
        for name, v in (e or {}).get("verdicts", {}).items():
            verdicts[name] = ids.get(v.get("node") or "") if isinstance(v, dict) else v
        if e is None or None in verdicts.values():
            missing.append(q)
        else:
            found.append({"qid": q["qid"], "verdicts": verdicts, "why": e["why"], "source": "cache", "cached_from": e["from"]})
    return found, missing


def _store_of(record: Path) -> Path:
    return C.load(Path(json.loads((record / "meta.json").read_text(encoding="utf-8"))["project"])).store


def answer_check(record: Path, out: Path) -> dict[str, str]:
    """Per saved answers file: "ok" when its fingerprints file matches the questions now in judge_<evid>.json under
    the same judge prompts, model and transcript; else why not."""
    judge, store, res = out / "judge", _store_of(record), {}
    for f in sorted(judge.glob("answers_*.json")):
        evid = f.stem[len("answers_"):]
        fp_file, batch = judge / f"fingerprints_{evid}.json", judge / f"judge_{evid}.json"
        if not fp_file.exists():
            res[evid] = "no fingerprints: saved before they were kept"
            continue
        saved = json.loads(fp_file.read_text(encoding="utf-8"))
        bind = judge_binding(saved["model"], S.P(store, "evidence", evid + ".md"))
        now = {q["qid"]: _fingerprint(q, bind) for q in json.loads(batch.read_text(encoding="utf-8"))} if batch.exists() else {}
        changed = sorted(q for q in set(now) | set(saved["fingerprints"]) if now.get(q) != saved["fingerprints"].get(q))
        res[evid] = "ok" if not changed else f"questions changed since they were answered: {', '.join(changed)}"
    return res


def _set_aside(judge: Path, evid: str) -> None:
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for name, aside in ((f"answers_{evid}.json", f"stale_answers_{evid}_{ts}.json"),
                        (f"fingerprints_{evid}.json", f"stale_fingerprints_{evid}_{ts}.json")):
        if (judge / name).exists():
            (judge / name).rename(judge / aside)


def judge_run(record: Path, out: Path, model: str, ai: list[str] | None, same_tier_ok: bool = False, cache_only: bool = False) -> dict:
    """One model call per batch with no checked answers yet, asking only what the cache of this key and judge tier cannot
    answer (cache_only: no call; a batch the cache does not fully answer stays unanswered); an answer that fails
    check_verdicts is kept beside the batch (rejected_<evid>.json) and never merged. Refuses the reader's own tier
    (correlated errors) unless told."""
    import run as RUN
    meta = json.loads((record / "meta.json").read_text(encoding="utf-8"))
    reader = _reader_tiers(meta)
    if not same_tier_ok and not reader:
        raise SameTier("the reader tier is unknown (meta.json has no model_tier, model_pinned or models in tiers.json); "
                       "pass --same-tier-ok to judge anyway")
    if not same_tier_ok and model in reader:
        raise SameTier(f"the judge would run on the same tier as the reader ({model}); pass --same-tier-ok to allow it")
    project = Path(meta["project"])
    store = C.load(project).store
    ai = ai or [sys.executable, str(RUN_DIR / "taska.py"), "--project", str(project), "ai", "--"]
    cache = _cache_file(record, meta, model)
    result = {}
    saved = answer_check(record, out)
    for batch in sorted((out / "judge").glob("judge_*.json")):
        evid = batch.stem[len("judge_"):]
        done = out / "judge" / f"answers_{evid}.json"
        if done.exists() and saved.get(evid) == "ok":
            result[evid] = "answered before"
            continue
        if done.exists():   # answers to other questions: kept aside, never merged, never deleted
            _set_aside(out / "judge", evid)
        qs = json.loads(batch.read_text(encoding="utf-8"))
        bind = judge_binding(model, S.P(store, "evidence", evid + ".md"))
        cached, missing = _from_cache(cache, qs, bind)
        if missing and cache_only:
            result[evid] = f"cache: {len(cached)} of {len(qs)}"
            continue
        stdout, fresh = "", []
        if missing:
            todo = out / "judge" / f"todo_{evid}.json"
            todo.write_text(json.dumps(missing, ensure_ascii=False, indent=1), encoding="utf-8")
            prompt = (f"Read {EVALS / 'QUALITY_JUDGE.md'} and {EVALS / 'JUDGE.md'}, then the questions in {todo} and the "
                      f"transcript {S.P(store, 'evidence', evid + '.md')}. Answer as QUALITY_JUDGE.md says.")
            r = subprocess.run([*ai, "-p", prompt, "--model", REC.TIERS[model]], capture_output=True, text=True,
                               env=RUN.model_env(model))
            stdout = r.stdout
            try:
                fresh = _json_array(stdout)
            except json.JSONDecodeError:
                fresh = []
        answers = cached + fresh
        problems = check_verdicts(answers, qs) if fresh or not missing else ["no JSON array in the output"]
        target = done if not problems else out / "judge" / f"rejected_{evid}.json"
        target.write_text(json.dumps(answers if not problems else {"problems": problems, "stdout": stdout[-4000:]},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
        if not problems:
            (out / "judge" / f"fingerprints_{evid}.json").write_text(json.dumps(
                {"model": model, "fingerprints": {q["qid"]: _fingerprint(q, bind) for q in qs}}, indent=1), encoding="utf-8")
            if cache and fresh:
                _cache_put(cache, qs, fresh, record.name, bind)
            models = _judge_models(out) | {evid: {"model": model, "same_tier_ok": same_tier_ok} | ({"cached": len(cached)} if cached else {})}
            (out / "judge" / "models.json").write_text(json.dumps(models, indent=1), encoding="utf-8")
        result[evid] = "ok" if not problems else f"rejected: {len(problems)} problems"
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("stability")
    st.add_argument("--records", nargs="+", required=True, help="two or more record folders scored on one key, each built")
    st.add_argument("--out", help="default: the first record's quality folder")
    st.add_argument("--rule", help="the bars each record's judge is validated against (as for build)")
    for name in ("build", "judge-prep", "judge-run", "agreement"):
        p = sub.add_parser(name)
        p.add_argument("--record", required=True, help="a record folder (meta.json names its copy and key)")
        p.add_argument("--out", help="default: RECORD/quality")
        if name == "build":
            p.add_argument("--echo", required=True, help="the independent note-taker's calls folder")
            p.add_argument("--key", help="default: the key the record was scored on")
            p.add_argument("--rule", help="a JSON file of your bars (found_correct_min, judge_agreement_min, judge_agreement_source, ...)")
            p.add_argument("--stability", help="a stability.json (quality.py stability) for the Stable row")
            p.add_argument("--labels", action="append", default=[], help="a labels file the quality page saved (eval-labels/1): source you")
            p.add_argument("--yours", action="append", default=[], help="a file of your earlier answers, pre-selected on the page")
        if name in ("build", "agreement"):
            p.add_argument("--validation", action="append", default=[], required=name == "agreement",
                           help='a JSON file [{"qid", "ask", "expected", "source"}]; repeatable, one per source')
        if name == "judge-run":
            p.add_argument("--model", default="high", choices=list(REC.TIERS))
            p.add_argument("--same-tier-ok", action="store_true", help="allow the judge on the reader's tier")
            p.add_argument("--ai", nargs="+", help="the command that reaches Claude (default: taska ai --)")
            p.add_argument("--cache-only", action="store_true", help="reuse answers from other runs on this key and tier; no model call")
    a = ap.parse_args(argv)
    if a.cmd == "stability":
        records = [Path(r) for r in a.records]
        try:
            if len(records) < 2:
                raise ValueError("stability needs two or more records")
            rule_bytes = Path(a.rule).read_bytes() if a.rule else b"{}"
            result = stability(records, json.loads(rule_bytes), hashlib.sha256(rule_bytes).hexdigest())
        except ValueError as e:
            print(json.dumps({"status": "refused", "error": str(e)}))
            return 2
        out = Path(a.out) if a.out else records[0] / "quality"
        out.mkdir(parents=True, exist_ok=True)
        (out / "stability.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"status": "ok", "file": str(out / "stability.json"), "summary": result["summary"], "records": result["records"]}))
        return 0
    record = Path(a.record)
    out = Path(a.out) if a.out else record / "quality"
    if a.cmd == "build":
        rule = json.loads(Path(a.rule).read_text(encoding="utf-8")) if a.rule else {}
        try:
            doc = document(record, Path(a.echo).expanduser(), Path(a.key) if a.key else None, out, rule, [Path(v) for v in a.validation],
                           Path(a.stability) if a.stability else None, [Path(x) for x in a.labels], [Path(x) for x in a.yours])
        except ValueError as e:
            print(json.dumps({"status": "refused", "error": str(e)}))
            return 2
        evalview.write_quality(out, doc)
        print(json.dumps({"status": "ok", "page": str(out / "index.html"), "summary": doc["summary"], "judged": doc["judged"]}))
    elif a.cmd == "judge-prep":
        paths = judge_prep(record, out)
        print(json.dumps({"status": "ok", "batches": [str(x) for x in paths],
                          "questions": sum(len(json.loads(x.read_text(encoding="utf-8"))) for x in paths)}))
    elif a.cmd == "agreement":
        try:
            rows = load_validation([Path(v) for v in a.validation])
        except ValueError as e:
            print(json.dumps({"status": "refused", "error": str(e)}))
            return 2
        answers = _answers(out / "judge")
        print(json.dumps({"status": "ok", "agreement": agreement(answers, rows), "overall": overall(answers, rows)}, ensure_ascii=False))
    else:
        try:
            print(json.dumps({"status": "ok", "batches": judge_run(record, out, a.model, a.ai, a.same_tier_ok, a.cache_only)}))
        except SameTier as e:
            print(json.dumps({"status": "refused", "error": str(e)}))
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
