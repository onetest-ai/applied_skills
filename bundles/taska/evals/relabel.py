#!/usr/bin/env python3
"""Re-label the answer key from one eval record: a page asks about every key item the run missed (showing the run's
most similar node in that meeting) and every node the run created that the key does not have; the answers become a new
key version, never an edit of the old one.

  relabel.py page  --record REC --key KEY [--yours FILE]... [--hints FILE]... [--pool pool_full.json] [--out DIR]
  relabel.py apply --record REC --key KEY --answers FILE --version vN [--note TEXT] [--dry-run] [--out DIR]

`page` writes DIR/index.html and DIR/items.json (default DIR: REC/relabel); open the page from a local server (the
dashboard serves reports/), answer, Save. `apply` reads that download: a missed item answered "Not work" leaves the key;
one answered "Already tracked" gains the similar node's quote in `quotes`; a node answered "Committed" or "Proposed"
joins as a new item; "Can't tell" or no answer changes nothing. `apply` also reads the quality page's eval-labels/1
file (its stamp, record and key sha must match this key): drop and modify key items, add a node as a new item, a node
duplicate of a key item adds its quote to that item. --dry-run prints the changes and writes nothing; otherwise the
CHANGELOG line carries the counts.

Earlier labels are re-used: --yours takes the person's own label files (a saved page {"answers": …}, the labels file
{"items": [{evidence, quote, user}]}, chat rulings {cid: {kind}}), --hints the judges' answers ([{cid, commitment}]) and
rechecks ([{cid, verdict, rule}]). A question someone already answered shows that answer pre-selected; questions nobody
answered come first. A second mention you answered as work but the key dropped (the judge marked it duplicate_of a key
item) is added to that item's quotes by apply."""
import argparse
import datetime as dt
import json
import shlex
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
for p in (RUN_DIR, EVALS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from key_tools import label_page, write_version  # noqa: E402
from taska_lib import config as C, evalscore as ES, ledger as L, store as S  # noqa: E402
from taska_lib.text import norm  # noqa: E402

FINDING = ("B7", "B8", "B13")
SIMILAR = 0.4   # share of the key title's words a node title must carry to be shown as "taska already has"
WORK = ("committed", "proposed")
write_key = write_version


def _words(s: str | None) -> set[str]:
    return {w for w in norm(s or "").split() if len(w) > 3}


def _similar(g: dict, nodes: dict[str, dict]) -> dict[str, str] | None:
    want = _words(g.get("title"))
    scored = sorted(((len(want & _words(n.get("title"))) / max(1, len(want)), nid) for nid, n in nodes.items()
                     if n.get("created_in") == g["evidence"] and n.get("by") == "agent"), reverse=True)
    if not scored or scored[0][0] < SIMILAR:
        return None
    n = nodes[scored[0][1]]
    return {"id": scored[0][1], "title": n.get("title") or "", "quote": n.get("quote") or ""}


def _pool_index(pool: dict[str, list[dict]]) -> dict[str, dict]:
    return {c["cid"]: c for cs in pool.values() for c in cs}


def _candidate(evidence: str, quote: str, pool: dict[str, list[dict]], ev: dict[str, dict]) -> str | None:
    """The pool candidate a quote is, by the scorer's own rule (exact text first)."""
    best, best_key = None, None
    for c in pool.get(evidence, []):
        if norm(c["quote"]) == norm(quote):
            return c["cid"]
        hit = ES._hit_strength({"quote": quote}, *ES._gold_params({"evidence": evidence, "quote": c["quote"]}, ev))
        if hit and (best_key is None or (hit[0] >= 0.5, hit[0], -hit[1]) > best_key):
            best, best_key = c["cid"], (hit[0] >= 0.5, hit[0], -hit[1])
    return best


def read_labels(yours: list[Path], hints: list[Path], pool: dict[str, list[dict]],
                ev: dict[str, dict] | None = None) -> tuple[dict[str, dict], dict[str, dict]]:
    """({cid: {"kind", "by"}} from the person's own files, {cid: {"judge", "recheck", "duplicate_of"}} from readers)."""
    ev = ev or {}
    mine: dict[str, dict] = {}
    for path in yours:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "answers" in data:
            by = f"you, {(data.get('saved_at') or '')[:10]} ({path.stem})"
            mine.update({cid: {"kind": a["kind"], "by": by} for cid, a in data["answers"].items() if a.get("kind")})
        elif isinstance(data, dict) and "items" in data:
            by = f"you, {data.get('date') or ''} ({path.stem})"
            for it in data["items"]:
                cid = _candidate(it["evidence"], it["quote"], pool, ev)
                if cid and (it.get("user") or {}).get("kind"):
                    mine[cid] = {"kind": it["user"]["kind"], "by": by}
        elif isinstance(data, dict):
            mine.update({cid: {"kind": a["kind"], "by": f"you (chat ruling, {path.stem})"}
                         for cid, a in data.items() if isinstance(a, dict) and a.get("kind")})
    readers: dict[str, dict] = {}
    for path in hints:
        for r in json.loads(Path(path).read_text(encoding="utf-8")):
            h = readers.setdefault(r["cid"], {})
            if "commitment" in r:
                h["judge"] = r["commitment"]
                if r.get("duplicate_of"):
                    h["duplicate_of"] = r["duplicate_of"]
            elif "verdict" in r:
                h["recheck"] = r["verdict"] + (f" ({r['rule']})" if r.get("rule") else "")
    return mine, readers


def with_labels(items: list[dict], mine: dict[str, dict], readers: dict[str, dict], pool: dict[str, list[dict]],
                ev: dict[str, dict]) -> list[dict]:
    """Each question with the earlier answer to it; questions nobody answered first, then readers only, then yours."""
    out = []
    for i, it in enumerate(items):
        it = dict(it)
        cid = _candidate(it["evidence"], it["quote"], pool, ev) if it["cid"].startswith("node:") else it["cid"]
        if cid in mine:
            it["prior"] = mine[cid]
        h = readers.get(cid or "", {})
        if h.get("judge") or h.get("recheck"):
            it["hint"] = "; ".join(f"{k}: {h[k]}" for k in ("judge", "recheck") if h.get(k))
        out.append((2 if "prior" in it else 1 if "hint" in it else 0, i, it))
    return [it for _, _, it in sorted(out, key=lambda x: (x[0], x[1]))]


def dropped_mentions(key: list[dict], mine: dict[str, dict], readers: dict[str, dict],
                     pool: dict[str, list[dict]]) -> dict[str, list[str]]:
    """{key cid: [quotes]}: your work answers on second mentions the key dropped as duplicate_of one of its items."""
    owner = {g["cid"]: g["cid"] for g in key} | {m: g["cid"] for g in key for m in g.get("merged_cids") or []}
    cands = _pool_index(pool)
    out: dict[str, list[str]] = {}
    for cid, a in mine.items():
        target = owner.get(readers.get(cid, {}).get("duplicate_of") or "")
        if a["kind"] in WORK and cid not in owner and target and cid in cands:
            out.setdefault(target, []).append(cands[cid]["quote"])
    return out


def page_items(rows: list[dict], key: list[dict], nodes: dict[str, dict]) -> list[dict]:
    """The page's questions: each missed key item (in key order), then each node the key does not have."""
    missed = {r["key_id"]: r.get("stage") or "stage not recorded" for r in rows if r["bucket"] in FINDING and r["verdict"] == "miss" and r.get("key_id")}
    items = []
    for g in key:
        if g["cid"] not in missed:
            continue
        it = {"cid": g["cid"], "date": g["evidence"][:10], "ts": g.get("ts") or "", "speaker": g.get("speaker") or "",
              "quote": g.get("quote") or "",
              "meeting": f"{g['evidence']} · missed key item {g['cid']} ({missed[g['cid']]}): {g.get('title') or ''}"}
        similar = _similar(g, nodes)
        if similar:
            it["existing"] = similar
        it["evidence"] = g["evidence"]
        items.append(it)
    for r in rows:
        if r["bucket"] == "B15" and r["verdict"] == "invented":
            items.append({"cid": f"node:{r['run_node']}", "date": r["meeting"][:10], "ts": "", "speaker": "",
                          "quote": r.get("quote") or "", "node": {k: r.get(k) or "" for k in ("run_node", "run_kind", "run_title", "run_owner")},
                          "evidence": r["meeting"],
                          "meeting": f"{r['meeting']} · node the key does not have: {r['run_node']} {r['run_kind']} · {r['run_title']}"})
    return items


LABELS = "eval-labels/1"
REASONS = ("vague", "client_own", "status", "brainstorm", "people", "other")
KINDS = ("task", "story", "decision", "risk")
MODIFIABLE = ("title", "kind", "commitment", "owner", "due")
ADD_FIELDS = ("title", "owner", "due")
RECUR = 3   # a drop reason given this often is a signal for the tracking rule
ACTIONS = {"key": {"keep": {"note"}, "drop": {"reason", "note"}, "modify": {"fields", "note"}},
           "node": {"add": {"kind", "commitment", "fields", "quote", "note"}, "not_work": {"reason", "quote", "note"},
                    "duplicate": {"dup_of", "quote", "dup_of_quote", "note"}},
           "judge": {"agree": {"expected", "quote"}, "disagree": {"expected", "quote"}}}
EVERY = {"action", "seen_judge", "carried_from"}   # carried_from: the earlier answer a page pre-selected and you kept


def _qid_type(qid: str) -> str:
    return "node" if qid.startswith("node:") else "judge" if qid.startswith("judge:") else "key"


def _node_ref(qid: str) -> tuple[str, str]:
    """(evidence id, node id) of a quality page node qid "node:<evidence id>:<node id>"."""
    evid, _, nid = qid[len("node:"):].rpartition(":")
    return evid, nid


def _is_date(v: str) -> bool:
    try:
        return len(v) == 10 and dt.date.fromisoformat(v).isoformat() == v
    except ValueError:
        return False


def _check_fields(qid: str, fields: object, allowed: tuple[str, ...], act: str) -> None:
    if not isinstance(fields, dict) or not fields:
        raise ValueError(f"{qid}: {act} needs fields ({', '.join(allowed)})")
    bad = sorted(set(fields) - set(allowed))
    if bad:
        raise ValueError(f"{qid}: {act} cannot change {', '.join(bad)} (only {', '.join(allowed)})")
    v = fields.get("title", "-")
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{qid}: title must be a non-empty text, not {v!r}")
    v = fields.get("owner")
    if v is not None and not isinstance(v, str):
        raise ValueError(f"{qid}: owner must be a name or null, not {v!r}")
    v = fields.get("due")
    if v not in (None, "") and not (isinstance(v, str) and _is_date(v)):
        raise ValueError(f"{qid}: due must be YYYY-MM-DD or null, not {v!r}")
    if "kind" in fields and fields["kind"] not in KINDS:
        raise ValueError(f"{qid}: unknown kind {fields['kind']!r} (allowed: {', '.join(KINDS)})")
    if "commitment" in fields and fields["commitment"] not in WORK:
        raise ValueError(f"{qid}: unknown commitment {fields['commitment']!r} (allowed: {', '.join(WORK)})")


def _check_label(qid: str, a: object) -> None:
    kind = _qid_type(qid)
    if not isinstance(a, dict):
        raise ValueError(f"{qid}: an answer is an object with an action, not {a!r}")
    act = a.get("action")
    allowed = ACTIONS[kind].get(act if isinstance(act, str) else "")
    if allowed is None:
        raise ValueError(f"{qid}: unknown action {act!r} for a {kind} entry (allowed: {', '.join(ACTIONS[kind])})")
    extra = sorted(set(a) - allowed - EVERY)
    if extra:
        raise ValueError(f"{qid}: unknown field(s) {', '.join(extra)} for action {act!r}")
    if kind == "node" and not all(_node_ref(qid)):
        raise ValueError(f"{qid}: a node entry is node:<evidence id>:<node id>")
    if "seen_judge" in a and not isinstance(a["seen_judge"], bool):
        raise ValueError(f"{qid}: seen_judge is true or false, not {a['seen_judge']!r}")
    for f in ("quote", "dup_of_quote", "dup_of", "expected", "carried_from"):
        if f in a and not (isinstance(a[f], str) and a[f].strip()):
            raise ValueError(f"{qid}: {f} must be a non-empty text, not {a[f]!r}")
    if "reason" in a and a["reason"] not in REASONS:
        raise ValueError(f"{qid}: unknown reason {a['reason']!r} (allowed: {', '.join(REASONS)})")
    if "kind" in a and a["kind"] not in KINDS:
        raise ValueError(f"{qid}: unknown kind {a['kind']!r} (allowed: {', '.join(KINDS)})")
    if "commitment" in a and a["commitment"] not in WORK:
        raise ValueError(f"{qid}: unknown commitment {a['commitment']!r} (allowed: {', '.join(WORK)})")
    if act == "add" and "commitment" not in a:
        raise ValueError(f"{qid}: add needs a commitment ({', '.join(WORK)})")
    if act == "duplicate" and "dup_of" not in a:
        raise ValueError(f"{qid}: duplicate needs dup_of (a key item id or a node id)")
    if act == "modify":
        _check_fields(qid, a.get("fields"), MODIFIABLE, act)
    if act == "add" and "fields" in a:
        _check_fields(qid, a["fields"], ADD_FIELDS, act)


def _new_cid(evidence: str, taken: set[str]) -> str:
    base = evidence.split("_")[-1]
    n = 1
    while f"{base}-x{n:03d}" in taken:
        n += 1
    taken.add(f"{base}-x{n:03d}")
    return f"{base}-x{n:03d}"


def _resolve(qid: str, evid: str, nid: str, quote: str | None, nodes: dict[str, dict], record: str) -> str:
    """The store node a label names: by id when that node is in this record's store (and has the label's quote, when
    the label carries one), else by its creating quote among the nodes created in the same meeting."""
    n = nodes.get(nid)
    if n and (not quote or norm(n.get("quote") or "") == norm(quote)):
        return nid
    if quote:
        hits = [k for k, m in nodes.items() if m.get("created_in") == evid and norm(m.get("quote") or "") == norm(quote)]
        if len(hits) > 1:
            raise ValueError(f"{qid}: the quote is ambiguous: nodes {', '.join(sorted(hits))} of {evid} all have it")
        if hits:
            return hits[0]
        raise ValueError(f"{qid}: node {nid} is not in the store of record {record} and no node of {evid} has its quote")
    raise ValueError(f"{qid}: node {nid} is not in the store of record {record}")


def apply_answers(key: list[dict], items: list[dict], answers: dict[str, dict], record: str,
                  extra_quotes: dict[str, list[str]] | None = None, nodes: dict[str, dict] | None = None) -> list[dict]:
    """The next key from the person's answers; an item without an answer, or answered "Can't tell", is unchanged.
    extra_quotes (dropped_mentions) are earlier answers of the person's, added to their items' quotes.
    Answers with an "action" are eval-labels/1 entries (the quality page); `nodes` is the record's store
    (_nodes), the source of every added or duplicate node. Every added item carries source_record."""
    by_cid = {i["cid"]: i for i in items}
    extra_quotes = extra_quotes or {}
    nodes = nodes or {}
    keyed = {g["cid"] for g in key}
    labels = {q: a for q, a in answers.items() if isinstance(a, dict) and "action" in a}
    for q, a in labels.items():
        _check_label(q, a)
        if _qid_type(q) == "key" and q not in keyed:
            raise ValueError(f"{q}: not an item of this key")
    dropped = {q for q, a in answers.items() if a.get("action") == "drop" or ("action" not in a and a.get("kind") == "not_work")}
    added: dict[str, str] = {}
    for q, a in labels.items():
        if _qid_type(q) == "node" and a["action"] == "add":
            evid, nid = _node_ref(q)
            nid = _resolve(q, evid, nid, a.get("quote"), nodes, record)
            n = nodes[nid]
            if n.get("created_in") != evid:
                raise ValueError(f"{q}: node {nid} was created in {n.get('created_in')}, not in {evid}")
            if not n.get("quote"):
                raise ValueError(f"{q}: node {nid} has no creating quote in the store of record {record}")
            if not a.get("kind") and n.get("kind") not in KINDS:
                raise ValueError(f"{q}: node {nid} is a {n.get('kind')}, not a key kind ({', '.join(KINDS)}): give a kind")
            added[q] = nid
    added_by_nid = {nid: q for q, nid in added.items()}
    dup_quotes: dict[str, list[str]] = {}
    for q, a in labels.items():
        if _qid_type(q) != "node" or a["action"] != "duplicate":
            continue
        evid, nid = _node_ref(q)
        nid = _resolve(q, evid, nid, a.get("quote"), nodes, record)
        quote = nodes[nid].get("quote") or ""
        target = a["dup_of"]
        if target in keyed:
            if target in dropped:
                raise ValueError(f"{q}: duplicate of {target}, which this file drops: the labels contradict each other")
            dup_quotes.setdefault(target, []).append(quote)
            continue
        t_evid, t_nid = _node_ref(target) if target.startswith("node:") else (evid, target)
        if target in added:
            dup_quotes.setdefault(target, []).append(quote)
            continue
        try:
            t_nid = _resolve(q, t_evid, t_nid, a.get("dup_of_quote"), nodes, record)
        except ValueError as e:
            if t_nid in added_by_nid:
                dup_quotes.setdefault(added_by_nid[t_nid], []).append(quote)
                continue
            if "ambiguous" in str(e):
                raise
            raise ValueError(f"{q}: duplicate of {target}, which is neither a key item, a node of record {record} "
                             f"nor a node added by this file") from None
        if t_nid in added_by_nid:
            dup_quotes.setdefault(added_by_nid[t_nid], []).append(quote)
    out = []
    for g in key:
        a = answers.get(g["cid"]) or {}
        if g["cid"] in dropped:
            continue
        g = dict(g)
        if "action" not in a and a.get("kind") in WORK and a["kind"] != g.get("commitment"):
            g["commitment"] = a["kind"]
        if a.get("action") == "modify":
            g.update({f: (None if v == "" and f in ("owner", "due") else v) for f, v in a["fields"].items()})
        existing = (by_cid.get(g["cid"]) or {}).get("existing")
        more = extra_quotes.get(g["cid"], []) + ([existing["quote"]] if a.get("new") == "tracked" and existing and existing.get("quote") else [])
        more += dup_quotes.get(g["cid"], [])
        if more:
            g["quotes"] = list(dict.fromkeys(ES._gold_quotes(g) + more))
        out.append(g)
    taken = set(keyed)
    for it in items:
        a = answers.get(it["cid"]) or {}
        if not it["cid"].startswith("node:") or "action" in a or a.get("kind") not in WORK:
            continue
        cid = _new_cid(it["evidence"], taken)
        node = it["node"]
        out.append({"cid": cid, "evidence": it["evidence"], "quote": it["quote"], "ts": None, "speaker": None,
                    "title": node["run_title"], "kind": node["run_kind"], "commitment": a["kind"],
                    "owner": node["run_owner"] or None, "due": None, "status": None, "client_owed": False,
                    "covered_by": None, "note": a.get("note") or f"added from eval record {record}", "source": "user",
                    "source_record": record})
    for q, nid in added.items():
        a, n, evid = labels[q], nodes[nid], _node_ref(q)[0]
        f = a.get("fields") or {}
        item = {"cid": _new_cid(evid, taken), "evidence": evid, "quote": n["quote"], "ts": None, "speaker": None,
                "title": f.get("title") or n.get("title") or "", "kind": a.get("kind") or n.get("kind"),
                "commitment": a["commitment"],
                "owner": (f["owner"] or None) if "owner" in f else (n.get("internal_owner") or n.get("client_owner") or None),
                "due": f.get("due") or None, "status": None, "client_owed": False, "covered_by": None,
                "note": f"added from {record}", "source": "user", "source_record": record}
        if dup_quotes.get(q):
            item["quotes"] = list(dict.fromkeys([n["quote"]] + dup_quotes[q]))
        out.append(item)
    return out


def quote_matches(answers: dict[str, dict], nodes: dict[str, dict], record: str, keyed: set[str]) -> list[str]:
    """Each node label whose node (or dup_of node) is found by its quote alone, not by its id."""
    out = []
    for q, a in answers.items():
        if _qid_type(q) != "node" or not isinstance(a, dict) or a.get("action") not in ("add", "duplicate"):
            continue
        evid, nid = _node_ref(q)
        got = _resolve(q, evid, nid, a.get("quote"), nodes, record)
        if got != nid:
            out.append(f"{q} → {got} matched by quote")
        target = a.get("dup_of") or ""
        if a["action"] == "duplicate" and a.get("dup_of_quote") and target not in keyed:
            t_evid, t_nid = _node_ref(target) if target.startswith("node:") else (evid, target)
            try:
                got = _resolve(q, t_evid, t_nid, a.get("dup_of_quote"), nodes, record)
            except ValueError:
                continue
            if got != t_nid:
                out.append(f"{q} dup_of {target} → {got} matched by quote")
    return out


def _carried(a: object) -> str:
    return a["carried_from"] if isinstance(a, dict) and isinstance(a.get("carried_from"), str) else ""


def changes(old: list[dict], new: list[dict], answers: dict[str, dict],
            nodes: dict[str, dict] | None = None) -> dict[str, list[dict]]:
    """What a new key changes against the old one, item by item: dropped (with the reason given), added, modified
    fields (quotes apart) and quotes added."""
    was = {g["cid"]: g for g in old}
    now = {g["cid"]: g for g in new}
    ch: dict[str, list[dict]] = {"dropped": [], "added": [], "modified": [], "quotes": [], "carried": []}
    nodes = nodes or {}
    seen: set[str] = set()

    def mark(d: dict, qid: str) -> None:
        src = _carried(answers.get(qid))
        if src:
            d["carried_from"] = src
            if qid not in seen:
                seen.add(qid)
                ch["carried"].append({"qid": qid, "carried_from": src})

    for cid, g in was.items():
        if cid not in now:
            a = answers.get(cid) or {}
            d = {"cid": cid, "title": g.get("title") or "", "reason": a.get("reason") or a.get("kind") or "dropped"}
            if d["reason"] == "other" and a.get("note"):
                d["note"] = a["note"]
            mark(d, cid)
            ch["dropped"].append(d)
            continue
        h = now[cid]
        diff = {f: [g.get(f), h.get(f)] for f in sorted((set(g) | set(h)) - {"quotes"}) if g.get(f) != h.get(f)}
        if diff:
            m = {"cid": cid, "fields": diff}
            mark(m, cid)
            ch["modified"].append(m)
        before = ES._gold_quotes(g)
        for q in ES._gold_quotes(h):
            if q not in before:
                d = {"cid": cid, "quote": q}
                for dq, da in answers.items():
                    if isinstance(da, dict) and da.get("action") == "duplicate" and da.get("dup_of") == cid and _carried(da):
                        mark(d, dq)
                ch["quotes"].append(d)
    for cid, h in now.items():
        if cid not in was:
            d = {"cid": cid, "title": h.get("title") or "", "kind": h.get("kind") or "",
                 "commitment": h.get("commitment") or "", "evidence": h.get("evidence") or ""}
            for qid, a in answers.items():
                if (isinstance(a, dict) and a.get("action") == "add" and qid.startswith("node:")
                        and _node_ref(qid)[0] == h.get("evidence") and (nodes.get(_node_ref(qid)[1]) or {}).get("quote") == h.get("quote")):
                    mark(d, qid)
                    break
            ch["added"].append(d)
            ch["quotes"] += [{"cid": cid, "quote": q} for q in (h.get("quotes") or [])[1:]]
    return ch


def _reasons(ch: dict[str, list[dict]]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for d in ch["dropped"]:
        reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
    return reasons


def counts_text(ch: dict[str, list[dict]]) -> str:
    notes = [d["note"] for d in ch["dropped"] if d.get("note")]
    parts = [f"{r} {n}" + (f" [{'; '.join(notes)}]" if r == "other" and notes else "") for r, n in _reasons(ch).items()]
    by = f" ({', '.join(parts)})" if parts else ""
    carried = f", carried {len(ch['carried'])}" if ch.get("carried") else ""
    return (f"dropped {len(ch['dropped'])}{by}, added {len(ch['added'])}, modified {len(ch['modified'])}, "
            f"quotes added {len(ch['quotes'])}{carried}")


def _mark(d: dict) -> str:
    return f" (carried from {d['carried_from']})" if d.get("carried_from") else ""


def diff_text(was: str, version: str, ch: dict[str, list[dict]], matched: list[str] | None = None) -> str:
    lines = [f"{was} → {version}: {counts_text(ch)}"]
    lines += [f"? {m}" for m in matched or []]
    lines += [f"{r} × {n}: the tracking rule may need to say this; READER-block changes are your call"
              for r, n in _reasons(ch).items() if n >= RECUR]
    lines += [f"- drop {d['cid']} ({d['reason']}{': ' + d['note'] if d.get('note') else ''}): {d['title']}{_mark(d)}" for d in ch["dropped"]]
    lines += [f"+ add {d['cid']} {d['kind']} · {d['commitment']} ({d['evidence']}): {d['title']}{_mark(d)}" for d in ch["added"]]
    lines += [f"~ modify {d['cid']}: " + "; ".join(f"{f} {v[0]!r} → {v[1]!r}" for f, v in d["fields"].items()) + _mark(d)
              for d in ch["modified"]]
    lines += [f"+ quote {d['cid']}: {d['quote']}{_mark(d)}" for d in ch["quotes"]]
    return "\n".join(lines)


def held_out_text(record: str, ch: dict[str, list[dict]]) -> str:
    return (f"held out: {len(ch['added'])} added item(s) come from record {record} (source_record): judge a code change "
            f"on this key version on runs other than {record}") if ch["added"] else ""


def rescore_commands(records: list[Path], key: Path, version: str) -> list[str]:
    """The free record.py commands that score each stored run again on the new key version (no model call)."""
    cmds = []
    for rec in records:
        meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or not meta.get("project"):
            raise ValueError(f"{rec}/meta.json names no project")
        cmd = ["python3", str(EVALS / "record.py"), "--project", str(meta["project"]), "--key", str(key),
               "--tier", str(meta.get("tier") or "0"), "--label", f"rescore {rec.name} on {version}", "--out-root", str(rec.parent)]
        cmd += ["--neutral"] if meta.get("covered_by") is False else []
        cmd += ["--model-tier", str(meta["model_tier"])] if meta.get("model_tier") else []
        cmds.append(" ".join(shlex.quote(c) for c in cmd))
    return cmds


def read_answers(path: Path, stamp: str, key_sha: str | None = None, record: str | None = None,
                 key_version: str | None = None) -> dict[str, dict]:
    """The answers of a saved page: the relabel page's {"stamp", "answers"}, or the quality page's eval-labels/1,
    whose record, key version and key sha must also match and whose every entry is checked."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a labels file: a JSON object with a stamp and answers")
    schema = data.get("schema")
    if schema is not None and schema != LABELS:
        raise ValueError(f"{path} is {schema!r}, not {LABELS} or a relabel page's answers")
    if data.get("stamp") != stamp:
        raise ValueError(f"{path} answers page {data.get('stamp')!r}, but the page on disk is {stamp!r}: answer the current page")
    answers = data.get("answers") or {}
    if not isinstance(answers, dict):
        raise ValueError(f"{path}: answers must be an object keyed by question id")
    bad = [q for q, x in answers.items() if not isinstance(x, dict)]
    if bad:
        raise ValueError(f"{path}: answer(s) {', '.join(bad)} must be objects, not {answers[bad[0]]!r}")
    if schema == LABELS:
        if record is not None and data.get("record") != record:
            raise ValueError(f"{path} labels record {data.get('record')!r}, not {record!r}")
        if key_version is not None and data.get("key_version") != key_version:
            raise ValueError(f"{path} labels key {data.get('key_version')!r}, not this key's {key_version!r}")
        if key_sha is not None and data.get("key_sha") != key_sha:
            raise ValueError(f"{path} labels the key with sha {data.get('key_sha')!r}, not this key's {key_sha!r}")
        for q, a in answers.items():
            _check_label(q, a)
    return answers


def _nodes(project: Path) -> dict[str, dict]:
    store = C.load(project).store
    evs = S.events(store)
    quotes = {e["id"]: e.get("quote") or "" for e in evs if e.get("type") == "create"}
    return {nid: dict(n, quote=quotes.get(nid, "")) for nid, n in L.fold(store, evs=evs)[0].items()}


def _stamp(record: Path, key: Path) -> str:
    return f"{record.name}-{(ES.load_gold(key)[0].get('sha256') or '')[:8]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("page", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--record", required=True)
        p.add_argument("--key", required=True)
        p.add_argument("--out", help="default: RECORD/relabel")
        if name == "page":
            p.add_argument("--yours", action="append", default=[], help="a file of the person's own earlier labels")
            p.add_argument("--hints", action="append", default=[], help="a file of judges' answers or a recheck")
            p.add_argument("--pool", help="pool_full.json: maps earlier labels and nodes to candidate ids")
        if name == "apply":
            p.add_argument("--answers", required=True, help="a relabel page's answers or a quality page's eval-labels/1 file")
            p.add_argument("--version", required=True)
            p.add_argument("--note", default="", help="the CHANGELOG line's text; the counts are added to it")
            p.add_argument("--dry-run", action="store_true", help="print the changes; write nothing")
            p.add_argument("--records", nargs="*", default=[], help="stored eval records to rescore on the new version")
    a = ap.parse_args(argv)
    record, key = Path(a.record), Path(a.key)
    out = Path(a.out) if a.out else record / "relabel"
    if a.cmd == "apply":
        try:
            return _apply(a, record, key, out)
        except (ValueError, OSError, KeyError) as e:
            print(json.dumps({"status": "refused", "why": f"{type(e).__name__}: {e}"}))
            return 1
    stamp = _stamp(record, key)
    _, gold = ES.load_gold(key)
    if a.cmd == "page":
        meta = json.loads((record / "meta.json").read_text(encoding="utf-8"))
        rows = json.loads((record / "score.json").read_text(encoding="utf-8"))["rows"]
        project = Path(meta["project"])
        items = page_items(rows, gold, _nodes(project))
        pool = json.loads(Path(a.pool).read_text(encoding="utf-8")) if a.pool else {}
        ev = S.evidence(C.load(project).store)
        mine, readers = read_labels([Path(x) for x in a.yours], [Path(x) for x in a.hints], pool, ev)
        items = with_labels(items, mine, readers, pool, ev)
        extra = dropped_mentions(gold, mine, readers, pool)
        out.mkdir(parents=True, exist_ok=True)
        (out / "items.json").write_text(json.dumps({"stamp": stamp, "items": items, "extra_quotes": extra},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "index.html").write_text(label_page(items, stamp), encoding="utf-8")
        print(json.dumps({"status": "ok", "page": str(out / "index.html"), "questions": len(items),
                          "answered_by_you": sum("prior" in i for i in items), "readers_only": sum("hint" in i and "prior" not in i for i in items),
                          "new": sum("hint" not in i and "prior" not in i for i in items), "dropped_mentions": extra}))
        return 0
    return 2


def _apply(a: argparse.Namespace, record: Path, key: Path, out: Path) -> int:
    stamp = _stamp(record, key)
    head, gold = ES.load_gold(key)
    data = json.loads(Path(a.answers).read_text(encoding="utf-8"))
    nodes: dict[str, dict] = {}
    if not isinstance(data, dict):
        raise ValueError(f"{a.answers} is not a labels file: a JSON object with a stamp and answers")
    if data.get("schema") == LABELS:
        answers = read_answers(Path(a.answers), stamp, key_sha=head.get("sha256") or "", record=record.name,
                               key_version=head.get("version"))
        needs = any(_qid_type(q) == "node" and x.get("action") in ("add", "duplicate") for q, x in answers.items())
        if needs:
            meta = json.loads((record / "meta.json").read_text(encoding="utf-8"))
            nodes = _nodes(Path(meta["project"]))
        new = apply_answers(gold, [], answers, record.name, nodes=nodes)
        matched = quote_matches(answers, nodes, record.name, {g["cid"] for g in gold})
    else:
        matched = []
        items = json.loads((out / "items.json").read_text(encoding="utf-8"))
        if items.get("stamp") != stamp:
            raise ValueError(f"{out}/items.json is for {items.get('stamp')}, not {stamp}: run page again")
        answers = read_answers(Path(a.answers), stamp)
        new = apply_answers(gold, items["items"], answers, record.name, items.get("extra_quotes"))
    ch = changes(gold, new, answers, nodes)
    held = held_out_text(record.name, ch)
    if a.dry_run:
        print(diff_text(head.get("version") or key.name, a.version, ch, matched) + (f"\n{held}" if held else ""))
        return 0
    new_key = key.parent / f"items.{a.version}.jsonl"
    cmds = rescore_commands([Path(r) for r in a.records], new_key, a.version)
    note = f"{a.note + ': ' if a.note else ''}labels from {record.name}: {counts_text(ch)}"
    path = write_key(key.parent, new, a.version, dt.date.today().isoformat(), note)
    print(json.dumps({"status": "ok", "key": str(path), "items": len(new), "was": len(gold), "changes": counts_text(ch)},
                     ensure_ascii=False))
    if held:
        print(held)
    print("\n".join([f"rescore the stored runs on {a.version} (free, no model call):"] + cmds) if cmds else
          f"rescore the stored runs on {a.version} (free, no model call): pass --records REC... to print the "
          f"record.py commands for them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
