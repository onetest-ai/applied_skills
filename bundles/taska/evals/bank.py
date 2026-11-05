"""The task bank: per meeting, what a correct run must create, must not create, must not duplicate and must leave
behind. An immutable, fingerprinted file (bank.vN.jsonl, header `# bank vN <date> n=<tasks> sha256=<body>`), written
through key_tools.write_version so a version is never overwritten."""
import argparse
import datetime
import hashlib
import html
import json
import os
import re
import signal
import concurrent.futures
import math
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
for _p in (RUN_DIR, EVALS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bankview  # noqa: E402
import evalview  # noqa: E402
import evaltrace  # noqa: E402
import key_tools  # noqa: E402
import relabel  # noqa: E402
import record  # noqa: E402
import run as RUN  # noqa: E402
import trajectory as TJ  # noqa: E402
from taska_lib import config as C, evalscore as ES, ledger as L, sensitive, store as S  # noqa: E402
from taska_lib.reads import READS  # noqa: E402
from taska_lib.text import norm  # noqa: E402
from taska_lib.workbench import code_version  # noqa: E402

CHECK_TYPES = ("must_create", "must_not_create", "no_duplicate", "outcome")
OUTCOMES = ("processed", "decisions_read", "no_people_matter", "pinned_untouched")
WHY = ("not_work", "brainstorm", "client_own", "people")
CLASSES = {"size": ("S", "M", "L", "XL"), "structure": ("conversation", "one block"),
           "owners": ("0–1", "2–3", "4+"), "audience": ("internal", "client")}
STATUSES = ("draft", "reviewed", "golden")
DIFFICULTIES = ("easy", "medium", "hard", None)
_HEAD = re.compile(r"^# bank (\S+) (\S+) n=(\d+) sha256=([0-9a-f]+)$")


def _check(task: dict) -> None:
    name = task.get("task", "?")
    for check in CHECK_TYPES[:3]:
        for entry in task.get(check, []):
            span = entry.get("span")
            if span is not None and not (len(span) == 2 and 0 <= span[0] < span[1]):
                raise ValueError(f"{name}: {check} has a bad span {span}")
            if check == "must_not_create" and entry.get("why") not in WHY:
                raise ValueError(f"{name}: unknown why {entry.get('why')!r}; one of {WHY}")
    for check in CHECK_TYPES[:3]:
        for entry in task.get(check, []):
            if entry.get("status", "draft") not in STATUSES:
                raise ValueError(f"{name}: {check} has an unknown status {entry.get('status')!r}; one of {STATUSES}")
            if entry.get("difficulty") not in DIFFICULTIES:
                raise ValueError(f"{name}: {check} has an unknown difficulty {entry.get('difficulty')!r}; one of {DIFFICULTIES}")
            if not isinstance(entry.get("pending_ruling", False), bool):
                raise ValueError(f"{name}: {check} pending_ruling must be true or false")
    for o in task.get("outcome", []):
        if o not in OUTCOMES:
            raise ValueError(f"{name}: unknown outcome {o!r}; one of {OUTCOMES}")
    klass = task.get("class")
    if klass is not None:
        for dim, val in klass.items():
            if dim not in CLASSES or val not in CLASSES[dim]:
                raise ValueError(f"{name}: unknown class {dim}={val!r}; one of {CLASSES}")
    after = task.get("after", [])
    if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
        raise ValueError(f"{name}: after must be a list of task ids")
    if len(after) > 1:
        raise ValueError(f"{name}: after names {len(after)} tasks; a task continues from exactly one task (linear chains)")


def _check_chains(tasks: list[dict]) -> None:
    ids = {t["task"] for t in tasks}
    graph = {t["task"]: t.get("after", []) for t in tasks}
    for tid, deps in graph.items():
        for d in deps:
            if d not in ids:
                raise ValueError(f"{tid}: after names {d!r}, which is not a task in this bank")
    seen: dict[str, int] = {}

    def visit(tid: str) -> None:
        if seen.get(tid) == 1:
            raise ValueError(f"{tid}: after forms a cycle")
        if seen.get(tid) == 2:
            return
        seen[tid] = 1
        for d in graph[tid]:
            visit(d)
        seen[tid] = 2
    for tid in graph:
        visit(tid)


def write_bank(dirpath: Path, tasks: list[dict], version: str, date: str, note: str) -> Path:
    """Write bank.<version>.jsonl after validating every task; an existing version is never overwritten."""
    for t in tasks:
        _check(t)
    _check_chains(tasks)
    Path(dirpath).mkdir(parents=True, exist_ok=True)
    return key_tools.write_version(dirpath, tasks, version, date, note, stem="bank", kind="bank")


def load_bank(path: Path) -> tuple[dict, list[dict]]:
    """Read a bank file: its header {version, date, n, sha256} and tasks; a body that no longer matches is refused."""
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    m = _HEAD.match(lines[0]) if lines else None
    if not m:
        raise ValueError(f"{path}: not a bank file (expected a '# bank vN <date> n=<tasks> sha256=<body>' header)")
    body = [ln for ln in lines[1:] if ln.strip()]
    head = {"version": m[1], "date": m[2], "n": int(m[3]), "sha256": m[4]}
    if key_tools._body_sha(body) != head["sha256"] or len(body) != head["n"]:
        raise ValueError(f"{path}: body does not match its header sha256; a bank version is never edited")
    return head, [json.loads(ln) for ln in body]


Span = tuple[str, int, int]


def transcript_sha(item: dict) -> str:
    """sha256 of the normalised text every span of this evidence item is an offset in."""
    return hashlib.sha256(norm(S.full_text(item)).encode("utf-8")).hexdigest()


def _creates(evs: list) -> dict[str, dict]:
    return {e["id"]: e for e in evs if e.get("type") == "create" and e.get("by", "agent") == "agent"}


def node_spans(store: Path, ev: dict, evs: list, nodes: dict) -> dict[str, Span | None]:
    """Agent-created node id -> (evidence, start, end) of its creating quote in the normalised text; None when the
    quote cannot be located (it matched only without backchannel turns, or is gone)."""
    creates, out = _creates(evs), {}
    for nid, n in nodes.items():
        c = creates.get(nid)
        if not c:
            continue
        evid = n.get("created_in") or c.get("evidence")
        item = ev.get(evid)
        start = S.quote_offset(item, c.get("quote")) if item else -1
        out[nid] = (evid, start, start + len(norm(c["quote"]))) if start >= 0 else None
    return out


_FIELD = {"revise": "title", "waiting": "waiting_for"}


def pinned_violations(nodes: dict) -> list[str]:
    """Ids of nodes where an agent write was applied to a field a human had already pinned."""
    bad = []
    for nid, n in nodes.items():
        pinned: set[str] = set()
        for h in n.get("history", []):
            if h.get("by") == "human":
                pinned |= set(h.get("set") or {})
                pinned |= {"title"} if h.get("op") == "create" else set()
                pinned |= {"state"} if h.get("op") == "delete" else set()
            elif h.get("applied") and h.get("by") == "agent":
                eff = h.get("effect")
                field = _FIELD.get(h.get("op")) or ("due" if eff in ("due", "delayed") else
                                                    "internal_owner" if eff == "reassign" else "state")
                if h.get("op") in ("revise", "waiting", "status") and field in pinned:
                    bad.append(nid)
                    break
    return bad


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _hits_create(check: dict, sp: Span) -> int:
    """Overlap of the node's span with the check's span."""
    return _overlap(tuple(check["span"]), (sp[1], sp[2]))


def _passes_create(check: dict, sp: Span) -> bool:
    """The node overlaps at least half of the check's span, or lies inside it."""
    cs, o = tuple(check["span"]), _hits_create(check, sp)
    return bool(o) and (2 * o >= cs[1] - cs[0] or (cs[0] <= sp[1] and sp[2] <= cs[1]))


def _violates_not(check: dict, sp: Span) -> bool:
    """The node overlaps at least half of its own span with the check's span."""
    o = _hits_create(check, sp)
    return bool(o) and 2 * o >= sp[2] - sp[1]


def transcript_refusal(task: dict, item: dict | None) -> str | None:
    """Why this task cannot be graded on a copy holding `item` (None when it can): its spans would not line up."""
    if item is None or transcript_sha(item) != task["transcript_sha"]:
        return (f"transcript of {task['evidence']} is missing or changed since the bank was made; "
                "spans would shift, so the task is refused")
    return None


def check_transcripts(project: Path, tasks: list[dict]) -> None:
    """Refuse (ValueError) a project whose evidence does not hold every task's transcript as the bank was made from:
    every span would shift, so no trial of it could be graded."""
    ev = S.evidence(C.load(project).store)
    bad = [why for t in tasks if (why := transcript_refusal(t, ev.get(t["evidence"])))]
    if bad:
        raise ValueError(f"{project} cannot be graded against this bank: " + "; ".join(bad))


def _refused(task: dict, why: str) -> dict:
    return {"task": task.get("task"), "pass": False, "checks": [], "unscored": [], "unlocated": [], "refused": why}


def _row(cid: str, typ: str, caps: list, result: str, node: str | None = None, **extra) -> dict:
    return {"id": cid, "type": typ, "caps": caps, "result": result, "node": node, "kind_ok": None, "owner_ok": None,
            "commitment_ok": None, "at": None} | extra


def _meta(entry: dict) -> dict:
    out = {"status": entry.get("status", "draft"), "difficulty": entry.get("difficulty")}
    return out | ({"pending_ruling": True} if entry.get("pending_ruling") else {})


def _pend(row: dict, entry: dict) -> dict:
    """A check awaiting the owner's ruling keeps its verdict as `ruled_result` and counts as n/a in the pass."""
    if entry.get("pending_ruling"):
        row |= {"ruled_result": row["result"], "result": "n/a", "pending_ruling": True}
    return row


def grade(task: dict, copy: Path, code_has_second_read: bool = True) -> dict:
    """Grade one trial's copy of the project against one bank task (code only; nothing calls a model)."""
    cfg = C.load(copy)
    ev, evs = S.evidence(cfg.store), S.events(cfg.store)
    item = ev.get(task["evidence"])
    why = transcript_refusal(task, item)
    if why:
        return _refused(task, why)
    N = L.fold(cfg.store, evs=evs, ev=ev)[0]
    creates = _creates(evs)
    live = {k: v for k, v in N.items() if v["by"] == "agent" and v["state"] not in ("merged", "deleted")
            and v.get("created_in") == task["evidence"]}
    decided = ES._decided(evs)   # evalscore's own reading of a pending card
    # a card on a re-synced copy (duplicate_of) is placed at the original meeting, as ledger.fold places its events
    original = lambda evid: (ev.get(evid) or {}).get("duplicate_of") or evid
    cards = {e["mid"]: e for e in evs if e.get("type") == "agent" and e.get("op") == "propose" and e.get("by") == "agent"
             and e.get("mid") and e.get("mid") not in decided and original(e.get("evidence")) == task["evidence"]}
    card_spans = {}
    for mid, e in cards.items():
        at = S.quote_offset(item, e.get("quote")) if e.get("quote") else -1
        if at >= 0:
            card_spans[mid] = (task["evidence"], at, at + len(norm(e["quote"])))
    spans = node_spans(cfg.store, ev, evs, {k: live[k] for k in live})
    located = {k: sp for k, sp in spans.items() if sp}
    unlocated = [{"node": k, "quote": creates[k]["quote"]} for k, sp in spans.items() if sp is None]
    checks, used = [], set()

    for mc in task.get("must_create", []):
        declined = mc.get("commitment") == "declined"   # only a decision or risk records the "no"
        hits = {k: _hits_create(mc, sp) for k, sp in located.items() if _passes_create(mc, sp)
                and (not declined or live[k]["kind"] in ("decision", "risk"))}
        used |= set(hits)
        row = _row(mc["id"], "must_create", mc.get("caps", []), "fail", quote=mc.get("quote"), by=None, **_meta(mc))
        card_hit = [m for m, sp in card_spans.items() if _passes_create(mc, sp)] if mc.get("commitment") == "proposed" else []
        if not hits and card_hit:
            m = card_hit[0]
            row |= {"result": "pass", "node": m, "by": "card",
                    "kind_ok": (cards[m].get("proposal") or {}).get("kind") == mc.get("kind")}
        if hits:
            best = max(hits, key=lambda k: (ES.SPECIFICITY.get(live[k]["kind"], 0), hits[k]))
            n, f = live[best], creates[best]["fields"]
            owner = n.get("internal_owner") or n.get("client_owner")
            row |= {"result": "pass", "node": best, "by": "node", "kind_ok": n["kind"] == mc.get("kind"),
                    "owner_ok": ES.owner_matches(cfg, owner, mc.get("owner")),
                    "commitment_ok": (f["commitment"] == mc.get("commitment")) if f.get("commitment") else None}
        same = [k for k in hits if live[k]["kind"] == mc.get("kind")]
        dup = "n/a" if row["result"] != "pass" else "fail" if len(same) > 1 else "pass"
        checks.append(_pend(row, mc))
        checks.append(_pend(_row(mc["id"] + ":dup", "no_duplicate", mc.get("caps", []), dup,
                                 same[0] if len(same) > 1 else None, quote=mc.get("quote"), **_meta(mc)), mc))
    for typ, mn in [("must_not_create", x) for x in task.get("must_not_create", [])] + \
                   [("no_duplicate", x) for x in task.get("no_duplicate", [])]:
        hit = [k for k, sp in located.items() if _violates_not(mn, sp)]
        used |= set(hit)
        checks.append(_pend(_row(mn["id"], typ, mn.get("caps", []), "fail" if hit else "pass",
                                 hit[0] if hit else None, quote=mn.get("quote"), **_meta(mn),
                                 **({"node_quote": creates[hit[0]]["quote"]} if hit else {})), mn))
    if task.get("outcome"):
        names = sensitive.names(cfg)
        pm = [k for k in live if sensitive.people_matter(live[k]["title"], creates[k]["quote"], names=names)]
        res = {"processed": task["evidence"] in S.processed(evs),
               "decisions_read": L.reads_of(evs).get(task["evidence"], {}).get("decisions", 0) >= READS["decisions"].version,
               "no_people_matter": not pm, "pinned_untouched": not pinned_violations(live)}
        for o in task["outcome"]:
            if o == "decisions_read" and not code_has_second_read:
                checks.append(_row(o, "outcome", [], "n/a"))
            else:
                checks.append(_row(o, "outcome", [], "pass" if res[o] else "fail"))
    unscored = [{"node": k, "quote": creates[k]["quote"]} for k in located if k not in used]
    ok = all(c["result"] != "fail" for c in checks)
    return {"task": task["task"], "pass": ok, "checks": checks, "unscored": unscored, "unlocated": unlocated,
            "refused": None}


_WHY_MAP = {"BRAINSTORM": "brainstorm", "CLIENT_SIDE": "client_own", "PEOPLE": "people"}


def _caps(row: dict) -> list[str]:
    caps = [row["commitment"]] if row.get("commitment") in ("committed", "proposed") else []
    if row.get("kind") in ("decision", "risk"):
        caps.append("decisions")
    if row.get("client_owed"):
        caps.append("client_owed")
    if row.get("owner"):
        caps.append("owners")
    return caps


class Conflict(str):
    """A problem line that also carries both sides of a key/not-work overlap for the review page."""

    def __new__(cls, text: str, detail: dict):
        self = super().__new__(cls, text)
        self.detail = detail
        return self


def _task_shell(evid: str, item: dict) -> dict:
    return {"task": evid.rsplit("_", 1)[-1], "evidence": evid, "transcript_sha": transcript_sha(item),
            "must_create": [], "must_not_create": [], "outcome": list(OUTCOMES),
            "budget": {"cost_usd": None, "minutes": None}}


def _locate(item: dict, quote: str | None) -> list[int] | None:
    """[start, end) of the quote in the item's normalised text, or None when it cannot be located."""
    start = S.quote_offset(item, quote) if quote else -1
    return None if start < 0 else [start, start + len(norm(quote))]


def _conflict(c: dict, m: dict, case_id: str) -> Conflict:
    return Conflict(
        f"{c['id']} vs {case_id}: key span {c['span'][0]}-{c['span'][1]} ({c['quote']!r}) overlaps not-work "
        f"span {m['span'][0]}-{m['span'][1]} ({m['quote']!r}); both left out, decide which is right",
        {"create": c, "not": m, "case": case_id})


def init(project: Path, key: list[dict], adjudications: list[tuple[list[dict], list[dict]]],
         key_version: str = "") -> tuple[list[dict], list[str]]:
    """Generate bank tasks: must_create from the key's quotes, must_not_create from NOT_WORK verdicts. Anything that
    cannot be located, or conflicts, is listed in problems and written as nothing."""
    cfg = C.load(Path(project))
    ev = S.evidence(cfg.store)
    problems: list[str] = []
    tasks: dict[str, dict] = {}
    located: dict[str, bool] = {}
    for row in key:
        evid = row.get("evidence")
        item = ev.get(evid)
        if item is None:
            problems.append(f"{row.get('cid')}: evidence {evid} is not in the project's store")
            continue
        t = tasks.setdefault(evid, _task_shell(evid, item) | {"_pending": []})
        quote = row.get("quote") or (row.get("quotes") or [None])[0]
        span = _locate(item, quote)
        if span is None:
            problems.append(f"{row.get('cid')}: quote not found in {evid}; no span written: {quote!r}")
            continue
        if row.get("commitment") == "not_work":
            problems.append(f"{row.get('cid')}: commitment not_work, not a must_create")
            continue
        located[evid] = True
        t["must_create"].append({"id": row["cid"], "span": span, "quote": quote,
                                 "kind": row.get("kind"), "commitment": row.get("commitment"),
                                 "owner": row.get("owner"), "caps": _caps(row), "source": f"key {key_version}".strip()})
    for evid, t in tasks.items():
        if evid not in located:
            problems.append(f"task {t['task']}: no locatable key quote")
    for cases, verdicts in adjudications:
        by_case = {v["case"]: v for v in verdicts}
        for c in cases:
            v = by_case.get(c["case"])
            if not v or v.get("verdict") != "NOT_WORK":
                continue
            evid = c.get("evidence_id")
            if evid not in tasks:
                problems.append(f"{c['case']}: {evid} is not a key meeting; its NOT_WORK span is not written")
                continue
            quote = (c.get("node") or {}).get("quote")
            span = _locate(ev[evid], quote)
            if span is None:
                problems.append(f"{c['case']}: quote not found in {evid}; no must-not span written: {quote!r}")
                continue
            t = tasks[evid]
            why = _WHY_MAP.get(v.get("why"), "not_work")
            same = [m for m in t["_pending"] if m["span"] == span]
            if same:
                if same[0]["why"] != why:
                    problems.append(f"{c['case']}: same span as {same[0]['source'][4:]} but why {why!r} against "
                                    f"{same[0]['why']!r}; the first is written")
                continue
            t["_pending"].append({"id": "", "span": span, "quote": quote, "why": why,
                                  "caps": ["people"] if why == "people" else ["not_work"],
                                  "source": "rca:" + c["case"]})
    out = []
    for t in tasks.values():
        original, dropped = list(t["must_create"]), set()
        for m in t.pop("_pending"):
            clash = [c for c in original if _overlap(tuple(c["span"]), tuple(m["span"]))]
            if not clash:
                t["must_not_create"].append(m)
            for c in clash:
                dropped.add(c["id"])
                problems.append(_conflict(c, m, m["source"][4:]))
        t["must_create"] = [c for c in original if c["id"] not in dropped]
        for i, m in enumerate(t["must_not_create"], 1):
            m["id"] = f"{t['task']}-n{i:02d}"
        out.append(t)
    return out, problems


LABELS = "eval-labels/1"
_LABEL_WHY = {"brainstorm": "brainstorm", "client_own": "client_own", "people": "people"}


def _next_n(task: dict, letter: str) -> int:
    ids = [e["id"] for e in task["must_create"] + task["must_not_create"] if e["id"].startswith(f"{task['task']}-{letter}")]
    return 1 + max([int(i[len(task["task"]) + 2:]) for i in ids if i[len(task["task"]) + 2:].isdigit()] or [0])


def grow(bank_path: Path, labels: list[Path], project: Path) -> tuple[list[dict], dict]:
    """The bank's tasks plus what the owner's eval-labels/1 files add (Add -> must_create, Not work -> must_not_create),
    drop (a key check removed) and modify (kind/commitment/owner), with a diff. A quote that cannot be located, or a
    span that overlaps its opposite, is a problem and written as nothing."""
    cfg = C.load(Path(project))
    ev = S.evidence(cfg.store)
    tasks = {t["evidence"]: json.loads(json.dumps(t)) for t in load_bank(bank_path)[1]}
    diff: dict = {"added_must_create": [], "added_must_not": [], "dropped": [], "modified": [], "problems": []}
    problems = diff["problems"]
    adds: list[tuple[dict, dict]] = []
    for path in labels:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != LABELS:
            raise ValueError(f"{path} is not an {LABELS} file")
        source = "labels:" + str(data.get("stamp") or Path(path).name)
        for qid, a in (data.get("answers") or {}).items():
            try:
                relabel._check_label(qid, a)
            except ValueError as e:
                problems.append(str(e))
                continue
            if qid.startswith("judge:") or a["action"] in ("duplicate", "keep"):
                continue
            if qid.startswith("node:"):
                adds.append((a | {"qid": qid}, {"source": source}))
                continue
            owners = [t for t in tasks.values() if any(m["id"] == qid for m in t["must_create"])]
            if not owners:
                problems.append(f"{qid}: not a must_create of this bank; {a['action']} not applied")
            elif a["action"] == "drop":
                owners[0]["must_create"] = [m for m in owners[0]["must_create"] if m["id"] != qid]
                diff["dropped"].append(qid)
            else:
                m = next(m for m in owners[0]["must_create"] if m["id"] == qid)
                f = a.get("fields") or {}
                new = {k: f[k] for k in ("kind", "commitment", "owner") if k in f}
                m.update(new)
                m["caps"] = _caps(m | {"client_owed": "client_owed" in m.get("caps", [])})
                diff["modified"].append(qid)
    fresh: dict[str, dict] = {}
    pending = []
    for a, meta in adds:
        evid, _ = relabel._node_ref(a["qid"])
        item = ev.get(evid)
        if item is None:
            problems.append(f"{a['qid']}: evidence {evid} is not in the project's store")
            continue
        span = _locate(item, a.get("quote"))
        if span is None:
            problems.append(f"{a['qid']}: quote not found in {evid}; no span written: {a.get('quote')!r}")
            continue
        t = tasks.get(evid) or fresh.setdefault(evid, _task_shell(evid, item))
        typ = "must_create" if a["action"] == "add" else "must_not_create"
        if any(m["span"] == span for m in t[typ]) or any(p[0] is t and p[1] == typ and p[2]["span"] == span for p in pending):
            continue
        f = a.get("fields") or {}
        if typ == "must_create":
            row = {"kind": a.get("kind") or f.get("kind"), "commitment": a.get("commitment") or f.get("commitment"),
                   "owner": f.get("owner") or a.get("owner")}
            entry = {"id": "", "span": span, "quote": a["quote"], **row, "caps": _caps(row), "source": meta["source"]}
        else:
            why = _LABEL_WHY.get(a.get("reason"), "not_work")
            entry = {"id": "", "span": span, "quote": a["quote"], "why": why,
                     "caps": ["people"] if why == "people" else ["not_work"], "source": meta["source"]}
        pending.append((t, typ, entry, a["qid"]))
    kept = []
    for t, typ, entry, qid in pending:
        creates = [e for tt, ty, e, _ in pending if tt is t and ty == "must_create"] + t["must_create"]
        nots = [e for tt, ty, e, _ in pending if tt is t and ty == "must_not_create"] + t["must_not_create"]
        mine = nots if typ == "must_create" else creates
        clash = [o for o in mine if o is not entry and _overlap(tuple(entry["span"]), tuple(o["span"]))]
        for o in clash:
            c, m = (entry, o) if typ == "must_create" else (o, entry)
            c = c | {"id": c["id"] or qid} if not c["id"] else c
            m = m | {"id": m["id"] or qid} if not m["id"] else m
            line = _conflict(c, m, m["source"][7:] if m["source"].startswith("labels:") else m["source"])
            if not any(isinstance(p, Conflict) and p.detail["create"]["span"] == c["span"] and p.detail["not"]["span"] == m["span"]
                       for p in problems):
                problems.append(line)
        if not clash:
            kept.append((t, typ, entry))
    for t, typ, entry in kept:
        letter = "x" if typ == "must_create" else "n"
        entry["id"] = f"{t['task']}-{letter}{_next_n(t, letter):02d}"
        t[typ].append(entry)
        diff["added_must_create" if typ == "must_create" else "added_must_not"].append(entry["id"])
    out = list(tasks.values()) + [t for t in fresh.values() if t["must_create"] or t["must_not_create"]]
    return out, diff


def review_page(tasks: list[dict], problems: list[str]) -> str:
    """A self-contained page: every span of every task with its quote, then every problem."""
    e = lambda x: html.escape(str(x if x is not None else ""))
    sp = lambda m: f"<code>{m['span'][0]}-{m['span'][1]}</code>"
    parts = ["<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             "<title>Task bank review</title><style>" + evalview.STYLE +
             "blockquote{margin:.2rem 0 0 1rem}li{margin:.4rem 0}</style><h1>Task bank review</h1>"]
    conflicts = [p for p in problems if isinstance(p, Conflict)]
    other = [p for p in problems if not isinstance(p, Conflict)]
    parts.append(f"<h2>Conflicts ({len(conflicts)})</h2>")
    for p in conflicts:
        c, m = p.detail["create"], p.detail["not"]
        parts.append(f"<h3>{e(c['id'])} vs {e(p.detail['case'])}</h3><ul>"
                     f"<li>Key {e(c['id'])} · {sp(c)}<blockquote>{e(c['quote'])}</blockquote></li>"
                     f"<li>Not work ({e(m['why'])}) {e(p.detail['case'])} · {sp(m)}<blockquote>{e(m['quote'])}"
                     "</blockquote></li></ul>")
    parts.append(f"<h2>Problems ({len(other)})</h2>")
    parts.append("<ul>" + "".join(f"<li>{e(p)}</li>" for p in other) + "</ul>" if other else "<p>None.</p>")
    for t in tasks:
        parts.append(f"<h2>Task {e(t['task'])} <small>{e(t['evidence'])}</small></h2>")
        parts.append(f"<h3>Must create ({len(t['must_create'])})</h3><ul>")
        for m in t["must_create"]:
            parts.append(f"<li><code>{e(m['id'])}</code> {e(m.get('kind'))} · {e(m.get('commitment'))} · "
                         f"{e(m.get('owner'))} · {sp(m)}<blockquote>{e(m['quote'])}</blockquote></li>")
        parts.append(f"</ul><h3>Must not create ({len(t['must_not_create'])})</h3><ul>")
        for m in t["must_not_create"]:
            parts.append(f"<li><code>{e(m['id'])}</code> {e(m.get('why'))} · {sp(m)}<blockquote>{e(m['quote'])}</blockquote></li>")
        parts.append("</ul>")
    return "\n".join(parts)


DEFAULT_TIMEOUT_S = 1800


def timeout_for(task: dict) -> int:
    """Twice the task's budget in minutes when it has one, else half an hour."""
    minutes = (task.get("budget") or {}).get("minutes")
    return int(minutes * 2 * 60) if minutes else DEFAULT_TIMEOUT_S


def _write_json(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")


def _empty_trial(task: dict, n: int, status: str, reason: str = "") -> dict:
    return {"schema": "eval-trial/1", "task": task["task"], "trial": n, "status": status, "pass": False, "checks": [],
            "unscored": [], "unlocated": [], "latency_s": 0,
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, "cost_usd": 0.0,
            "errors": {"tool": 0, "refusals": 0, "blocked": 0, "acks_without_reread": 0}, "reason": reason}


def _chain_copy(src: Path, evidence: str, dest: Path) -> Path:
    """A copy of the copy an earlier trial left. Its wrapper points at the new folder; a meeting the harness had only
    closed as 'not a key meeting' is queued again, so the model reads it on top of the earlier trial's tree."""
    shutil.copytree(src, dest, symlinks=True)
    RUN.write_wrapper(dest)
    store = C.load(dest).store
    last = [e for e in S.events(store) if e.get("type") in ("processed", "reprocess") and e.get("evidence") == evidence]
    if last and last[-1]["type"] == "processed" and str(last[-1].get("note", "")).startswith("eval copy"):
        S.requeue(store, [evidence], "chained eval trial")
    return dest


def _close_others(copy: Path, evidence: str) -> None:
    """Close every item still waiting in a trial copy but the task's own meeting, earlier ones included (the snapshot
    keeps a meeting before the cut that the project never processed), with the note `_chain_copy` requeues by."""
    store = C.load(copy).store
    ev, done = S.evidence(store), S.processed(S.events(store))
    for evid in S.order(ev):
        if evid != evidence and evid not in done and ev[evid]["kind"] != "message":
            L.mark_processed(store, evid, "eval copy: not a key meeting")


def trial(project: Path, task: dict, n: int, tier: str, ai: list[str], out_root: Path, timeout_s: int,
          invalid: str = "", copies_root: Path | None = None, from_copy: Path | None = None) -> dict:
    """One headless read of one meeting on its own snapshot copy, graded and written to out_root/trials/<task>-<n>/.
    done; timeout (the process ran out of time); invalid (no route to the model: no copy, no call); error (the model
    process did not start or left no session). Only `done` is ever a pass or a miss. `from_copy` (a chained task)
    starts from a copy of the copy an earlier trial left, with this task's meeting back in the queue."""
    project, out_root = Path(project), Path(out_root)
    folder = out_root / "trials" / f"{task['task']}-{n}"
    folder.mkdir(parents=True, exist_ok=True)
    if invalid:
        result = _empty_trial(task, n, "invalid", invalid)
        _write_json(folder / "grade.json", result)
        return result
    copies = Path(copies_root) if copies_root else project / "evals" / "runs" / f"bank-{out_root.name}"
    try:
        if from_copy:
            copy = _chain_copy(Path(from_copy), task["evidence"], copies / f"{task['task']}-{n}")
        else:
            copy = RUN.prepare_copy(project, task["evidence"], {task["evidence"]}, copies / f"{task['task']}-{n}")
            _close_others(copy, task["evidence"])
    except Exception as e:   # a copy that cannot be made is the harness's failure, never a miss
        result = _empty_trial(task, n, "error", f"the copy could not be made: {type(e).__name__}: {e}")
        _write_json(folder / "grade.json", result)
        return result
    why = transcript_refusal(task, S.evidence(C.load(copy).store).get(task["evidence"]))
    if why:   # the model is never paid for a task that cannot be graded
        result = _empty_trial(task, n, "invalid", why)
        _write_json(folder / "grade.json", result)
        return result
    started, status, reason = time.time(), "done", ""
    session = None
    meta = {"tier": tier, "model": RUN.TIERS[tier], "copy": str(copy), "session": None, "status": status}
    try:
        status, reason = _run_model(ai, copy, tier, timeout_s)
        session = RUN.find_session(copy, started)
        if status == "done" and session is None:
            status, reason = "error", "no session file names this copy"
        result = _empty_trial(task, n, status, reason)
        meta |= {"session": str(session) if session else None, "status": status}
        if session and status in ("done", "timeout"):
            trace = evaltrace.from_run(TJ.load_run(session), S.evidence(C.load(copy).store))
            graded = evaltrace.link(grade(task, copy, code_has_second_read=True), task, trace)
            result |= evaltrace.metrics(trace) | {"turns": sum(a.get("turns") or 0 for a in trace.get("agents", [])),
                                                  "tool_calls": len(trace.get("spans", [])),
                                                  "checks": graded["checks"], "unscored": graded["unscored"],
                                                  "unlocated": graded["unlocated"]}
            agent_turns = [a["turns"] for a in trace.get("agents", []) if a.get("turns") is not None]
            if agent_turns:
                result["agent_turns_max"] = max(agent_turns)
            if graded.get("refused"):
                result |= {"reason": graded["refused"], "status": "invalid"}
            result["pass"] = result["status"] == "done" and bool(graded["pass"])
            _write_json(folder / "trace.json", trace)
            _write_json(folder / "perfetto.json", evaltrace.perfetto(trace))
            keep_transcript(session, folder)
            (folder / "transcript.html").write_text(bankview.transcript_page(trace, f"Trial {folder.name}"), encoding="utf-8")
    except Exception as e:   # whatever failed after the model, the trial says so and the run goes on
        result = _empty_trial(task, n, "error", f"{type(e).__name__}: {e}")
        meta["status"] = "error"
    _write_json(folder / "meta.json", meta)
    _write_json(folder / "grade.json", result)
    return result


def _run_model(ai: list[str], copy: Path, tier: str, timeout_s: int) -> tuple[str, str]:
    """One headless session in its own process group: a timeout kills the model and everything it started."""
    cmd = RUN.model_cmd(ai, Path(copy), tier)
    try:
        with open(f"{copy}.run.txt", "w", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, cwd=copy, stdout=log, stderr=subprocess.STDOUT, env=RUN.model_env(tier),
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                if hasattr(os, "killpg"):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.wait()
                return "timeout", f"timed out after {timeout_s} s"
    except OSError as e:
        return "error", f"the model process did not start: {e}"
    return ("done", "") if rc == 0 else ("error", f"the model process failed with exit code {rc}")


def chain_closure(ids: list[str], bank: list[dict]) -> list[str]:
    """`ids` plus every earlier task of their chains, earlier tasks first: a chain's later task never runs alone."""
    by = {t["task"]: t for t in bank}
    out: list[str] = []

    def add(tid: str) -> None:
        for a in by[tid].get("after", []):
            add(a)
        if tid not in out:
            out.append(tid)
    for tid in ids:
        add(tid)
    return out


def _chains(chosen: list[dict]) -> list[list[dict]]:
    """One group per chain, in run order, whatever order the bank lists it in; a task with no `after` is its own."""
    by_id = {t["task"]: t for t in chosen}

    def root(tid: str) -> str:
        seen = set()
        while by_id[tid].get("after") and tid not in seen:
            seen.add(tid)
            tid = by_id[tid]["after"][0]
        return tid

    def depth(tid: str) -> int:
        return 0 if not by_id[tid].get("after") else 1 + depth(by_id[tid]["after"][0])
    members: dict[str, list[dict]] = {}
    for t in chosen:
        members.setdefault(root(t["task"]), []).append(t)
    return [sorted(g, key=lambda t: depth(t["task"])) for g in members.values()]


ONE_BLOCK = "one block"


def _minutes_key(t: dict) -> str:
    """Where a task's measured minutes live: its size, or `<size>|one block` for a one-block task."""
    klass = t.get("class") or {}
    return f"{klass.get('size')}|{ONE_BLOCK}" if klass.get("structure") == ONE_BLOCK else str(klass.get("size"))


def _task_minutes(t: dict, minutes_by_class: dict[str, float]) -> tuple[float, bool]:
    """A task's expected minutes, and whether a one-block task fell back to its size's minutes."""
    size = (t.get("class") or {}).get("size")
    if not size:
        raise ValueError(f"{t['task']} has no size class, so its minutes cannot be expected")
    key = _minutes_key(t)
    if key in minutes_by_class:
        return minutes_by_class[key], False
    if size not in minutes_by_class:
        raise ValueError(f"no measured minutes for size {size} ({t['task']}): record a run of that class first")
    return minutes_by_class[size], key != size


def _group_minutes(group: list[dict], minutes_by_class: dict[str, float]) -> float:
    return sum(_task_minutes(t, minutes_by_class)[0] for t in group)


def job_order(tasks: list[dict], k: int, minutes_by_class: dict[str, float]) -> list[tuple[list[dict], int]]:
    """The (chain, trial) jobs longest expected first, ties in bank order: the order run_bank submits them and the
    order expected_minutes schedules them."""
    jobs = [(g, n) for g in _chains(tasks) for n in range(1, k + 1)]
    return sorted(jobs, key=lambda j: -_group_minutes(j[0], minutes_by_class))


def _schedule(durations: list[float], parallel: int) -> float:
    """Wall time of jobs taken in order by `parallel` workers, each job to the first worker that is free."""
    load = [0.0] * max(1, parallel)
    for d in durations:
        load[load.index(min(load))] += d
    return max(load)


def expected_minutes(tasks: list[dict], k: int, parallel: int, minutes_by_class: dict[str, float]) -> float:
    """The wall minutes of a run's trials: each chain's tasks in order, k times, in run_bank's own job order over
    `parallel` workers; a task's minutes are its class's measured minutes. Run overhead is added by budget_plan."""
    return round(_schedule([_group_minutes(g, minutes_by_class) for g, _ in job_order(tasks, k, minutes_by_class)],
                           parallel), 2)


def _run_folders(run_dirs: list[Path], tier: str, model: str | None) -> list[tuple[Path, dict]]:
    """Recorded runs (a run folder, or a folder of runs) of this tier, and of this model when both name one."""
    folders: list[Path] = []
    for d in map(Path, run_dirs):
        folders += [d] if (d / "trials").is_dir() else sorted(x for x in d.glob("*") if (x / "trials").is_dir())
    out = []
    for f in folders:
        head = _read(f / "run.json") or {}
        if head.get("tier") == tier and not (model and head.get("model") and head["model"] != model):
            out.append((f, head))
    return out


def _done_latencies(folder: Path) -> list[dict]:
    rows = [json.loads(g.read_text(encoding="utf-8")) for g in sorted(folder.glob("trials/*/grade.json"))]
    return [t for t in rows if t.get("status") == "done" and t.get("latency_s") is not None]


def measured_minutes(run_dirs: list[Path], tasks: dict[str, dict], tier: str, model: str | None = None) -> dict[str, float]:
    """Median minutes per class over the done trials of recorded runs of the same tier (and model): keyed by size,
    and by `<size>|one block` for one-block tasks. A class with no recorded trial is absent, never guessed."""
    per: dict[str, list[float]] = {}
    for folder, _ in _run_folders(run_dirs, tier, model):
        for t in _done_latencies(folder):
            task = tasks.get(t.get("task", ""))
            if task and (task.get("class") or {}).get("size"):
                per.setdefault(_minutes_key(task), []).append(t["latency_s"] / 60)
    return {key: round(statistics.median(v), 4) for key, v in sorted(per.items())}


def measured_overhead(run_dirs: list[Path], tasks: dict[str, dict], tier: str, model: str | None = None) -> tuple[float | None, int]:
    """Run overhead: a run's wall minutes (run.json started/finished) beyond the schedule its own trial latencies
    give, replayed in the order the run submitted its jobs (run.json `order`) at its recorded parallelism, averaged
    over the runs of the same tier (and model). A run without an order is skipped; (None, 0) when no run can be
    measured: it is never defaulted."""
    found: list[float] = []
    for folder, head in _run_folders(run_dirs, tier, model):
        wall, parallel, order = round_minutes(head), head.get("parallel"), head.get("order")
        if wall is None or not parallel or not order:
            continue
        lat = {(t["task"], t["trial"]): t["latency_s"] / 60 for t in _done_latencies(folder)}
        chosen = [tasks.get(tid) or {"task": tid} for tid in head.get("tasks") or []]
        chosen = [t if all(a in head.get("tasks", []) for a in t.get("after", [])) else {**t, "after": []} for t in chosen]
        groups = {g[0]["task"]: g for g in _chains(chosen)}
        jobs = [[lat.get((t["task"], n)) for t in groups.get(first, [])] or [None] for first, n in order]
        if any(x is None for j in jobs for x in j):
            continue
        found.append(wall - _schedule([sum(j) for j in jobs], parallel))
    return (round(statistics.fmean(found), 4), len(found)) if found else (None, 0)


def budget_plan(tasks: list[dict], k: int, parallel: int, minutes_by_class: dict[str, float], budget: float,
                overhead: tuple[float | None, int] = (None, 0)) -> dict:
    """The expected minutes (schedule plus measured overhead) against the budget, and when over it the longest
    chains to drop until it fits. Unmeasured overhead adds nothing and says so; a one-block task measured only by
    its size is named."""
    extra, runs = overhead
    total = lambda ts: round(expected_minutes(ts, k, parallel, minutes_by_class) + (extra or 0.0), 2)
    expected = total(tasks)
    plan = {"expected_minutes": expected, "schedule_minutes": expected_minutes(tasks, k, parallel, minutes_by_class),
            "overhead_minutes": extra,
            "overhead": (f"{'−' if extra < 0 else '+'}{abs(extra):g} min, measured over {runs} runs" if extra is not None
                         else "overhead not measured"),
            "size_fallback": [t["task"] for t in tasks if _task_minutes(t, minutes_by_class)[1]],
            "budget": budget, "k": k, "parallel": parallel, "minutes_by_class": minutes_by_class,
            "over": expected > budget, "drop": []}
    left = sorted(_chains(tasks), key=lambda g: _group_minutes(g, minutes_by_class))
    while left and total([t for g in left for t in g]) > budget:
        plan["drop"] += [t["task"] for t in left.pop()]
    return plan


def run_bank(project: Path, bank_path: Path, tier: str, k: int, tasks: list[str] | None = None, parallel: int = 4,
             ai: list[str] | None = None, out_root: Path | None = None, invalid: str = "",
             minutes_by_class: dict[str, float] | None = None, tags: dict[str, str | bool] | None = None) -> Path:
    """k trials of every (selected) task, `parallel` at a time, recorded under <project>/reports/evals/bank/<run id>/.
    Jobs go longest expected first (job_order, the order expected_minutes schedules), from `minutes_by_class` or the
    project's recorded runs of this tier; when a class has no measured minutes, in bank order. `tags` (a smart round's
    `round` and `comparable`) go into run.json."""
    project = Path(project)
    head, bank = load_bank(bank_path)
    chosen = [t for t in bank if not tasks or t["task"] in tasks]
    missing = sorted(set(tasks or []) - {t["task"] for t in chosen})
    if missing:
        raise ValueError(f"not in {bank_path}: {', '.join(missing)}")
    names = {t["task"] for t in chosen}
    absent = [tid for tid in chain_closure(sorted(names), bank) if tid not in names]
    if absent:
        raise ValueError(f"the selected tasks run after {', '.join(absent)}; select them too")
    check_transcripts(project, chosen)
    ai = ai or [*RUN.TASKA, "--project", str(project), "ai", "--"]
    run_id = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{tier}"
    out = Path(out_root) if out_root else project / "reports" / "evals" / "bank" / run_id
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.datetime.now().isoformat(timespec="seconds")
    meta = {"kind": "trials", "bank_version": head["version"], "bank_sha256": head["sha256"],
            "code_version": code_version(RUN_DIR), "prompts_sha256": record.prompts_sha(RUN_DIR), "tier": tier,
            "model": RUN.TIERS[tier], "k": k, "parallel": parallel, "tasks": [t["task"] for t in chosen], "started": started,
            "finished": None} | (tags or {})
    _write_json(out / "run.json", meta)
    copies = project / "evals" / "runs" / f"bank-{out.name}"

    def chain(group: list[dict], n: int) -> None:
        results: dict[str, dict] = {}
        for t in group:
            after = t.get("after", [])
            bad = next((a for a in after if results[a].get("status") != "done"), None)
            if bad:
                reason = f"the earlier task {bad} was {results[bad].get('status')} in trial {n}, so this one has nothing to continue from"
                results[t["task"]] = trial(project, t, n, tier, ai, out, timeout_for(t), reason)
                continue
            src = None
            if after:
                src = Path(json.loads((out / "trials" / f"{after[-1]}-{n}" / "meta.json").read_text())["copy"])
            results[t["task"]] = trial(project, t, n, tier, ai, out, timeout_for(t), invalid, copies_root=copies, from_copy=src)

    if minutes_by_class is None:
        minutes_by_class = measured_minutes([project / "reports" / "evals" / "bank"], {t["task"]: t for t in bank},
                                            tier, RUN.TIERS[tier])
    try:
        jobs = job_order(chosen, k, minutes_by_class)
    except ValueError:
        jobs = [(g, n) for g in _chains(chosen) for n in range(1, k + 1)]
    meta["order"] = [[g[0]["task"], n] for g, n in jobs]
    _write_json(out / "run.json", meta)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = [pool.submit(chain, g, n) for g, n in jobs]
        for f in futures:
            f.result()
    _write_json(out / "run.json", meta | {"finished": datetime.datetime.now().isoformat(timespec="seconds")})
    return out


COUNTS = ("tool", "refusals", "blocked", "acks_without_reread")


def _stat(values: list[float]) -> dict:
    """mean/median/p90 (nearest rank) over the valid trials; n is how many."""
    if not values:
        return {"mean": None, "median": None, "p90": None, "n": 0}
    v = sorted(values)
    return {"mean": round(statistics.fmean(v), 4), "median": statistics.median(v),
            "p90": v[max(0, math.ceil(0.9 * len(v)) - 1)], "n": len(v)}


def _total_tokens(t: dict) -> int | None:
    return sum(t["tokens"].values()) if t.get("tokens") else None


def _present(values: list) -> list:
    """A replay trial has no latency, cost or tokens of its own: they are absent, never zero."""
    return [v for v in values if v is not None]


def _pct(values: list[float]) -> dict:
    """p50/p95 (nearest rank) with n."""
    if not values:
        return {"p50": None, "p95": None, "n": 0}
    v = sorted(values)
    return {"p50": statistics.median(v), "p95": v[max(0, math.ceil(0.95 * len(v)) - 1)], "n": len(v)}


def _golden_pass(t: dict) -> bool | None:
    """Does the trial pass on its golden checks alone? None when it has none."""
    gold = [c for c in t.get("checks") or [] if (c.get("status") == "golden" or c.get("type") == "outcome")
            and not c.get("pending_ruling") and c.get("result") != "n/a"]
    return all(c.get("result") == "pass" for c in gold) if gold else None


def _minutes(t: dict) -> float | None:
    return None if t.get("latency_s") is None else t["latency_s"] / 60


def _done(ts: list[dict]) -> list[dict]:
    return [t for t in ts if t.get("status") == "done"]


def _passed_must_create(trials: list[dict]) -> int:
    return sum(1 for t in trials for c in t.get("checks") or []
               if c.get("type") == "must_create" and c.get("result") == "pass")


def _by_class(out_tasks: dict, by: dict, tasks: dict | None) -> tuple[dict, dict, dict | None]:
    if tasks is None:
        return {}, {}, None
    members: dict[str, dict[str, list[str]]] = {d: {v: [] for v in vals} for d, vals in CLASSES.items()}
    unclassed = []
    for tid in out_tasks:
        klass = (tasks.get(tid) or {}).get("class")
        if not klass:
            unclassed.append(tid)
            continue
        for dim, val in klass.items():
            if dim in members and val in members[dim]:
                members[dim][val].append(tid)
    result: dict = {}
    for dim, vals in members.items():
        for val, ids in vals.items():
            if not ids:
                continue
            valid = [t for tid in ids for t in _done(by[tid])]
            scored = [out_tasks[tid] for tid in ids if out_tasks[tid]["valid"]]
            cost = sum(t.get("cost_usd") or 0 for t in valid)
            passed = _passed_must_create(valid)
            result.setdefault(dim, {})[val] = {
                "tasks": len(ids), "valid": len(valid),
                "pass_hat_k": {"ok": sum(r["pass_hat_k"] for r in scored), "n": len(scored)},
                "turns": _pct(_present([t.get("turns") for t in valid])),
                "minutes": _pct(_present([_minutes(t) for t in valid])),
                "tokens": _pct(_present([_total_tokens(t) for t in valid])),
                "cost_usd": _pct(_present([t.get("cost_usd") for t in valid])),
                "usd_per_passed_must_create": {"value": round(cost / passed, 4) if passed else None,
                                               "cost_usd": round(cost, 4), "passed": passed, "n": len(valid)}}
    empty = {}
    if any((tasks.get(tid) or {}).get("class") for tid in tasks):
        taken = {(d, (t.get("class") or {}).get(d)) for t in tasks.values() for d in CLASSES}
        empty = {d: [v for v in vals if (d, v) not in taken] for d, vals in CLASSES.items()}
        empty = {d: vs for d, vs in empty.items() if vs}
    return result, empty, ({"tasks": len(unclassed), "ids": sorted(unclassed)} if unclassed else None)


TOKEN_KINDS = ("input", "output", "cache_read", "cache_write")
PER_SIZE = ("minutes_p95_max", "tokens_p95_max", "turns_p95_max", "cost_per_meeting_max")
HARD = ("turn_cap", "complete_required")
VERDICT_BARS = ("committed_lb_min", "decisions_lb_min", "committed_strict_lb_min", "decisions_strict_lb_min",
                "precision_lb_min", "wasted_rate_max")   # verdict.py's, 0..1
BAR_KEYS = ("minutes_per_round_max", "tokens_per_round_max", *PER_SIZE, "turn_cap", "complete_required",
            "minutes_per_meeting_avg_max", "tool_error_rate_max", "run_cost_max", "usd_per_correct_item_max",
            "regression.median_rise_max", "regression.recall_drop_max", *VERDICT_BARS)
_API = ("cost_per_meeting_max", "run_cost_max", "usd_per_correct_item_max")


def _usage(trials: list[dict]) -> dict:
    """Tokens of each kind, turns and minutes (p50/p95, n); a field a trial does not carry is absent (n=0), never 0."""
    return {"tokens": {k: _pct(_present([(t.get("tokens") or {}).get(k) for t in trials])) for k in TOKEN_KINDS},
            "turns": _pct(_present([t.get("turns") for t in trials])),
            "minutes": _pct(_present([_minutes(t) for t in trials]))}


def _klass(tasks: dict | None, tid: str) -> dict:
    return ((tasks or {}).get(tid) or {}).get("class") or {}


def _usage_block(by: dict[str, list[dict]], tasks: dict | None, trials: list[dict], round_minutes: float | None) -> dict:
    by_class: dict = {}
    for dim, vals in CLASSES.items():
        for val in vals:
            ids = [tid for tid in by if _klass(tasks, tid).get(dim) == val]
            if ids:
                by_class.setdefault(dim, {})[val] = {"tasks": len(ids), **_usage([t for tid in ids for t in _done(by[tid])])}
    kinds = {k: _present([(t.get("tokens") or {}).get(k) for t in trials]) for k in TOKEN_KINDS}
    return {"tasks": {tid: _usage(_done(by[tid])) for tid in sorted(by)}, "by_class": by_class,
            "round": {"tokens": {k: (sum(v) if v else None) for k, v in kinds.items()},
                      "tokens_n": {k: len(v) for k, v in kinds.items()},
                      "minutes": {"value": round_minutes, "n": int(round_minutes is not None)}}}


def _api_equivalent(trials: list[dict]) -> dict:
    costs = _present([t.get("cost_usd") for t in trials])
    valid = _done(trials)
    cost, passed = sum(_present([t.get("cost_usd") for t in valid])), _passed_must_create(valid)
    return {"note": "API-equivalent $ from the session's cost record; usage above is the cost, this is never a bill",
            "total": round(sum(costs), 4) if costs else None, "n": len(costs),
            "per_meeting": _pct(_present([t.get("cost_usd") for t in valid])),
            "usd_per_correct_item": round(cost / passed, 4) if passed else None}


_NUMBER_BARS = ("minutes_per_round_max", "tokens_per_round_max", "turn_cap", "minutes_per_meeting_avg_max",
                "tool_error_rate_max", "run_cost_max", "usd_per_correct_item_max")
_REGRESSION = ("median_rise_max", "recall_drop_max")


def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_bars(bars: dict, where: str = "bars.json") -> dict:
    """Refuse an unknown key or a wrong type, naming it: a misspelt bar would otherwise read `not set` silently."""
    if not isinstance(bars, dict):
        raise ValueError(f"{where}: the bars are an object of bar keys")
    for key, v in bars.items():
        if key in VERDICT_BARS:   # judged on the verdict page (lower bounds pass at ≥), only checked here
            if not _number(v) or not 0 <= v <= 1:
                raise ValueError(f"{where}: {key} is a number from 0 to 1, not {v!r}")
            ok = True
        elif key in _NUMBER_BARS:
            ok = _number(v)
        elif key in PER_SIZE:
            if isinstance(v, dict):
                for size, x in v.items():
                    if size not in CLASSES["size"] or not _number(x):
                        raise ValueError(f"{where}: {key}.{size} is not a size class ({', '.join(CLASSES['size'])}) "
                                         f"with a number")
                ok = True
            else:
                ok = _number(v)
        elif key == "complete_required":
            ok = isinstance(v, bool)
        elif key == "regression":
            if not isinstance(v, dict):
                raise ValueError(f"{where}: regression is an object of {', '.join(_REGRESSION)}")
            for sub, x in v.items():
                if sub not in _REGRESSION or not _number(x):
                    raise ValueError(f"{where}: regression.{sub} is not one of {', '.join(_REGRESSION)} with a number")
            ok = True
        else:
            raise ValueError(f"{where}: unknown bar {key!r}; known: {', '.join([*_NUMBER_BARS, *PER_SIZE, 'complete_required', 'regression', *VERDICT_BARS])}")
        if not ok:
            raise ValueError(f"{where}: {key} has the wrong type ({type(v).__name__}): "
                             f"{'true or false' if key == 'complete_required' else 'a number, or numbers per size' if key in PER_SIZE else 'a number'}")
    return bars


def load_bars(project: Path) -> tuple[dict | None, Path]:
    """The project's bars (`<project>/evals/bars.json`), checked, or None when it has none: no bar has a default."""
    path = Path(project) / "evals" / "bars.json"
    return (check_bars(json.loads(path.read_text(encoding="utf-8")), str(path)) if path.is_file() else None), path


def _result(value: float | bool | None, bar: float | bool | None, n: int) -> str:
    if bar is None or bar is False:
        return "not set"
    if value is None or n == 0:
        return "no data"
    if bar is True:
        return "pass" if value else "fail"
    return "pass" if value <= bar else "fail"


PROVISIONAL_N = 20


def _bar_row(key: str, scope: str | None, value: float | bool | None, bar: float | bool | None, n: int,
             measure: str = "", unit: str = "trials") -> dict:
    kind = "hard" if key in HARD else "api_equivalent" if key in _API else "budget"
    return {"key": key, "kind": kind, "hard": key in HARD, "scope": scope, "measure": measure, "value": value,
            "bar": None if bar is False else bar, "n": n, "unit": unit, "result": _result(value, bar, n)}


def _per_size_bar(bar: float | dict | None, size: str | None) -> float | None:
    return bar.get(size) if isinstance(bar, dict) else bar


Measure = Callable[[dict], float | None]
_SIZE_MEASURE: dict[str, tuple[str, Measure]] = {
    "minutes_p95_max": ("minutes p95", _minutes),
    "tokens_p95_max": ("tokens p95, all four kinds", _total_tokens),
    "turns_p95_max": ("turns p95", lambda t: t.get("turns")),
    "cost_per_meeting_max": ("API-equivalent $ p95 per meeting", lambda t: t.get("cost_usd"))}


def _median_of(trials: list[dict], measure: Measure) -> float | None:
    v = _present([measure(t) for t in trials])
    return statistics.median(v) if v else None


def _recall(trials: list[dict]) -> float | None:
    checks = [c for t in trials for c in t.get("checks") or [] if c.get("type") == "must_create"
              and c.get("result") != "n/a" and not c.get("pending_ruling")]
    return sum(c.get("result") == "pass" for c in checks) / len(checks) if checks else None


def _regression_rows(main: dict[str, list[dict]], tasks: dict | None, sizes: list[str | None], bars: dict,
                     baseline: list[dict] | None) -> list[dict]:
    reg = bars.get("regression") or {}
    rise_bar, drop_bar = reg.get("median_rise_max"), reg.get("recall_drop_max")
    base: dict[str, list[dict]] = {}
    for t in baseline or []:
        base.setdefault(t["task"], []).append(t)
    common = sorted(tid for tid in main if _done(main[tid]) and _done(base.get(tid, [])))
    rows = []

    def row(key: str, scope: str | None, measure: str, value: float | None, bar: float | None, n: int) -> dict:
        r = _bar_row(key, scope, value, bar, n, measure, unit="tasks") | {"kind": "regression", "hard": False}
        if r["result"] != "not set" and baseline is None:
            r["result"] = "no baseline"
        return r
    for size in sizes:
        ids = [tid for tid in common if size is None or _klass(tasks, tid).get("size") == size]
        cur = [t for tid in ids for t in _done(main[tid])]
        old = [t for tid in ids for t in _done(base[tid])]
        for name, measure in (("turns", lambda t: t.get("turns")), ("minutes", _minutes)):
            a, b = _median_of(cur, measure), _median_of(old, measure)
            rise = round((a - b) / b, 4) if a is not None and b else None
            rows.append(row("regression.median_rise_max", size, name, rise, rise_bar, len(ids) if rise is not None else 0))
    cur = [t for tid in common for t in _done(main[tid])]
    old = [t for tid in common for t in _done(base[tid])]
    a, b = _recall(cur), _recall(old)
    drop = round(b - a, 4) if a is not None and b is not None else None
    rows.append(row("regression.recall_drop_max", "main", "must_create recall", drop, drop_bar,
                    len(common) if drop is not None else 0))
    return rows


def _bars(by: dict[str, list[dict]], tasks: dict | None, trials: list[dict], missing: list[str], bars: dict | None,
          baseline: list[dict] | None, round_minutes: float | None, one_block: set[str], source: str | None) -> dict:
    """One row per bar key: `not set` when the bars name no value (there are no defaults), `no data` when the run
    measured nothing for it, else pass/fail. A hard bar's failure fails the run; value rows are information only."""
    b = bars or {}
    main = {tid: ts for tid, ts in by.items() if tid not in one_block}
    valid = _done(trials)
    rows: list[dict] = []
    tokens = [t for t in trials if all(_number((t.get("tokens") or {}).get(k)) for k in TOKEN_KINDS)]
    rows.append(_bar_row("minutes_per_round_max", "round", round_minutes, b.get("minutes_per_round_max"),
                         int(round_minutes is not None), "wall minutes of the run", unit="run"))
    rows.append(_bar_row("tokens_per_round_max", "round", sum(_total_tokens(t) or 0 for t in tokens) if tokens else None,
                         b.get("tokens_per_round_max"), len(tokens), "tokens, trials carrying all four kinds")
                | {"of": len(trials)})
    present = [s for s in CLASSES["size"] if any(_klass(tasks, tid).get("size") == s for tid in main)]
    for key in PER_SIZE:
        bar = b.get(key)
        named = [s for s in CLASSES["size"] if isinstance(bar, dict) and s in bar]
        sizes: list[str | None] = [s for s in CLASSES["size"] if s in present or s in named] or [None]
        label, measure = _SIZE_MEASURE[key]
        for size in sizes:
            ts = [t for tid, xs in main.items() if size is None or _klass(tasks, tid).get("size") == size for t in _done(xs)]
            p = _pct(_present([measure(t) for t in ts]))
            row = _bar_row(key, size, p["p95"], _per_size_bar(bar, size), p["n"], label)
            if 0 < p["n"] < PROVISIONAL_N:
                row["provisional"] = True   # under 20 trials the nearest-rank p95 is the maximum
            rows.append(row)
    turns = _present([t.get("agent_turns_max") for t in trials])
    rows.append(_bar_row("turn_cap", "agent", max(turns) if turns else None, b.get("turn_cap"), len(turns),
                         "most turns of one agent") | {"of": len(trials)})
    rows.append(_bar_row("complete_required", "run", bool(trials) and not missing and
                         all(t.get("status") == "done" for t in trials), b.get("complete_required"), len(trials),
                         "every trial done, none missing"))
    mins = _present([_minutes(t) for t in valid])
    rows.append(_bar_row("minutes_per_meeting_avg_max", "run", round(statistics.fmean(mins), 4) if mins else None,
                         b.get("minutes_per_meeting_avg_max"), len(mins), "mean wall minutes per meeting"))
    called = [t for t in valid if t.get("tool_calls") is not None]
    calls = sum(t["tool_calls"] for t in called)
    rows.append(_bar_row("tool_error_rate_max", "run",
                         round(sum((t.get("errors") or {}).get("tool", 0) for t in called) / calls, 4) if calls else None,
                         b.get("tool_error_rate_max"), len(called) if calls else 0, "tool errors per tool call"))
    api = _api_equivalent(trials)
    rows.append(_bar_row("run_cost_max", "run", api["total"], b.get("run_cost_max"), api["n"], "API-equivalent $"))
    rows.append(_bar_row("usd_per_correct_item_max", "run", api["usd_per_correct_item"],
                         b.get("usd_per_correct_item_max"), len(valid), "API-equivalent $ per passed must_create"))
    rows += _regression_rows(main, tasks, present or [None], b, baseline)
    with_turns = [t for t in valid if t.get("turns")]
    passed = _passed_must_create(with_turns)
    rows.append({"key": "correct_per_10_turns", "kind": "value", "hard": False, "scope": "run",
                 "measure": "passed must_create per 10 turns", "bar": None, "n": len(with_turns), "unit": "trials",
                 "result": "info",
                 "value": round(10 * passed / sum(t["turns"] for t in with_turns), 4) if with_turns else None})
    rows.append({"key": "api_equivalent_usd_per_correct_item", "kind": "value", "hard": False, "scope": "run",
                 "measure": "API-equivalent $ per passed must_create", "bar": None, "n": len(valid), "unit": "trials",
                 "result": "info",
                 "value": api["usd_per_correct_item"]})
    hard = [r["result"] for r in rows if r["hard"]]
    run = ("fail" if "fail" in hard else "not set" if all(h == "not set" for h in hard)
           else "unverified" if "no data" in hard else "pass")
    return {"source": source, "rows": rows, "run": run}


def aggregate(trials: list[dict], tasks: dict | None = None, missing: list[str] | None = None,
              gate: str | None = "golden", bars: dict | None = None, baseline: list[dict] | None = None,
              round_minutes: float | None = None, bars_source: str | None = None) -> dict:
    """Per task pass@k (any valid trial passed) and pass^k (every valid trial passed), per capability, overall,
    latency/cost/tokens stats, error rates and budget breaches. Only `done` trials count in a denominator; timeout,
    error and invalid trials are counted and shown, never a miss. `missing` (from load_run) is shown as a count."""
    by: dict[str, list[dict]] = {}
    for t in trials:
        by.setdefault(t["task"], []).append(t)
    if gate not in (None, "golden"):
        raise ValueError(f"unknown gate {gate!r}; golden or None")
    out_tasks, caps, breaches, pending = {}, {}, [], {"ok": 0, "n": 0}
    for tid in sorted(by):
        ts = by[tid]
        valid = [t for t in ts if t.get("status") == "done"]
        row = {"trials": len(ts), "valid": len(valid),
               **{s: sum(1 for t in ts if t.get("status") == s) for s in ("timeout", "error", "invalid")},
               "pass_at_k": (int(any(t["pass"] for t in valid)) if valid else None),
               "pass_hat_k": (int(all(t["pass"] for t in valid)) if valid else None),
               "latency_s": _stat(_present([t.get("latency_s") for t in valid])),
               "cost_usd": _stat(_present([t.get("cost_usd") for t in valid])),
               "tokens": _stat(_present([_total_tokens(t) for t in valid])),
               "error_rates": {c: (round(sum((t.get("errors") or {}).get(c, 0) for t in valid) / len(valid), 4)
                                   if valid else None) for c in COUNTS}}
        budget = ((tasks or {}).get(tid) or {}).get("budget") or {}
        for t in valid:
            if budget.get("minutes") and t.get("latency_s") is not None and t["latency_s"] > budget["minutes"] * 60:
                breaches.append({"task": tid, "trial": t["trial"], "what": "minutes", "value": t["latency_s"],
                                 "budget": budget["minutes"]})
            if budget.get("cost_usd") and t.get("cost_usd") is not None and t["cost_usd"] > budget["cost_usd"]:
                breaches.append({"task": tid, "trial": t["trial"], "what": "cost_usd", "value": t["cost_usd"],
                                 "budget": budget["cost_usd"]})
            for c in t.get("checks") or []:
                if c.get("pending_ruling"):
                    pending["n"] += 1
                    pending["ok"] += c.get("ruled_result") == "pass"
                    continue
                if c.get("result") == "n/a":
                    continue
                for cap in c.get("caps") or []:
                    slot = caps.setdefault(cap, {"ok": 0, "n": 0})
                    slot["n"] += 1
                    slot["ok"] += c.get("result") == "pass"
        if gate:
            gp = [g for g in (_golden_pass(t) for t in valid) if g is not None]
            row["gate"] = ({"pass_at_k": int(any(gp)), "pass_hat_k": int(all(gp)), "n": len(gp)} if gp else "no gate")
        out_tasks[tid] = row
    one_block = {tid for tid in out_tasks if (((tasks or {}).get(tid) or {}).get("class") or {}).get("structure") == "one block"}
    scored = [r for tid, r in out_tasks.items() if r["valid"] and tid not in one_block]
    overall = {"tasks": len(out_tasks), "trials": len(trials), "missing": len(missing or []), "valid": sum(r["valid"] for r in out_tasks.values()),
               **{s: sum(r[s] for r in out_tasks.values()) for s in ("timeout", "error", "invalid")},
               "pass_at_k": {"ok": sum(r["pass_at_k"] for r in scored), "n": len(scored)},
               "pass_hat_k": {"ok": sum(r["pass_hat_k"] for r in scored), "n": len(scored)}}
    if one_block:
        overall["one_block_excluded"] = len(one_block)
        blocks = [r for tid, r in out_tasks.items() if r["valid"] and tid in one_block]
        overall["main"] = {"tasks": len(out_tasks) - len(one_block), "pass_at_k": overall["pass_at_k"],
                           "pass_hat_k": overall["pass_hat_k"]}
        overall["one_block"] = {"tasks": len(one_block), "gate": "none: reported, outside the main bar",
                                **{k: {"ok": sum(r[k] for r in blocks), "n": len(blocks)} for k in ("pass_at_k", "pass_hat_k")}}
    out = {"usage": _usage_block(by, tasks, trials, round_minutes), "tasks": out_tasks, "caps": dict(sorted(caps.items())), "overall": overall, "breaches": breaches,
           "pending_ruling": pending}
    labelled = any("status" in c for t in trials for c in t.get("checks") or [])   # a bank from before status has no gate
    if gate and not labelled:
        gate = None
        for r in out_tasks.values():
            r.pop("gate", None)
    if gate:
        g = [r["gate"] for tid, r in out_tasks.items() if tid not in one_block and r["valid"] and r["gate"] != "no gate"]
        out["gate"] = {"gate": gate, "pass_at_k": {"ok": sum(x["pass_at_k"] for x in g), "n": len(g)},
                       "pass_hat_k": {"ok": sum(x["pass_hat_k"] for x in g), "n": len(g)},
                       "no_gate": sum(1 for tid, r in out_tasks.items() if tid not in one_block and r["valid"] and r["gate"] == "no gate")}
    out["gate"] = out.get("gate")
    out["by_class"], out["empty_classes"], out["unclassed"] = _by_class(out_tasks, by, tasks)
    out["bars"] = _bars(by, tasks, trials, missing or [], bars, baseline, round_minutes, one_block, bars_source)
    out["api_equivalent_usd"] = _api_equivalent(trials)
    return out


def describe(tasks: list[dict]) -> dict:
    """What a bank holds, with no run: checks per capability (n all, pending split out), tasks per class, empty
    classes, pending rulings and checks per status."""
    caps: dict[str, dict[str, int]] = {}
    status: dict[str, int] = {}
    pending = 0
    for t in tasks:
        for typ in ("must_create", "must_not_create", "no_duplicate"):
            for c in t.get(typ, []):
                pend = bool(c.get("pending_ruling"))
                pending += pend
                status[c.get("status", "draft")] = status.get(c.get("status", "draft"), 0) + 1
                for cap in c.get("caps") or []:
                    slot = caps.setdefault(cap, {"n": 0, "pending": 0})
                    slot["n"] += 1
                    slot["pending"] += pend
    by_class, empty, _ = _by_class({t["task"]: {"valid": 0} for t in tasks}, {t["task"]: [] for t in tasks},
                                   {t["task"]: t for t in tasks})
    return {"tasks": len(tasks), "per_capability": dict(sorted(caps.items())),
            "per_class": {d: {v: r["tasks"] for v, r in vals.items()} for d, vals in by_class.items()},
            "empty_classes": empty, "pending_ruling": pending, "per_status": dict(sorted(status.items()))}


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    """A run folder: its run.json and every trial's grade.json."""
    run_dir = Path(run_dir)
    head = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    trials = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run_dir / "trials").glob("*/grade.json"))]
    have = {f"{t['task']}-{t['trial']}" for t in trials}
    head["missing"] = [] if head.get("kind") == "replay" else [f"{t}-{n}" for t in head.get("tasks") or [] for n in range(1, (head.get("k") or 1) + 1)
                       if f"{t}-{n}" not in have]
    return head, trials


def _same(a: dict, b: dict, keys: tuple[str, ...]) -> None:
    for k in keys:
        if a.get(k) != b.get(k):
            raise ValueError(f"the runs differ in {k}: {a.get(k)} vs {b.get(k)}")


def _comparable(a: dict, b: dict) -> None:
    """A smart round's subset (run.json `comparable: false`) is compared only with another such round, never with a
    full run."""
    if (a.get("comparable") is False) != (b.get("comparable") is False):
        smoke = a if a.get("comparable") is False else b
        raise ValueError(f"a {smoke.get('round') or 'smoke'} round is never compared with a full run: its tasks are a "
                         "subset, reported per class")


def load_baseline(run_dir: Path, baseline_dir: Path) -> list[dict]:
    """The baseline's trials, refused unless it ran the same bank on the same tier (compare's provenance check) and
    is not a smoke round against a full run or the other way round."""
    _comparable(run := load_run(run_dir)[0], (base := load_run(baseline_dir))[0])
    _same(run, base[0], ("bank_sha256", "tier"))
    return base[1]


def _hat(agg: dict) -> dict[str, int]:
    return {t: r["pass_hat_k"] for t, r in agg["tasks"].items() if r["pass_hat_k"] is not None}


def _lost_gained(ha: dict[str, int], hb: dict[str, int]) -> tuple[list[str], list[str]]:
    both = sorted(set(ha) & set(hb))
    return [t for t in both if ha[t] and not hb[t]], [t for t in both if not ha[t] and hb[t]]


def _pairing(aa: dict, ab: dict) -> dict:
    """Which tasks pair (valid on both sides) and which do not, and why."""
    ta, tb = aa["tasks"], ab["tasks"]
    ha, hb = _hat(aa), _hat(ab)
    lost, gained = _lost_gained(ha, hb)
    paired = sorted(set(ha) & set(hb))
    return {"lost": lost, "gained": gained, "paired": len(paired), "unchanged": len(paired) - len(lost) - len(gained),
            "only_in_a": sorted(set(ta) - set(tb)), "only_in_b": sorted(set(tb) - set(ta)),
            "no_valid_a": sorted(t for t in set(ta) & set(tb) if ta[t]["pass_hat_k"] is None),
            "no_valid_b": sorted(t for t in set(ta) & set(tb) if tb[t]["pass_hat_k"] is None),
            "p": round(evalview.mcnemar_exact(len(lost), len(gained)), 3)}


def compare(a: Path, b: Path) -> dict:
    """Two runs of one kind, bank and tier: per task lost/gained on pass^k and the paired McNemar p."""
    (ha, ta), (hb, tb) = load_run(a), load_run(b)
    _comparable(ha, hb)
    _same(ha, hb, ("kind", "bank_version", "bank_sha256", "tier", "k"))
    return _pairing(aggregate(ta), aggregate(tb))


def _delta(x, y):
    return None if x is None or y is None else round(y - x, 4)


def _side(row: dict) -> dict:
    return {"valid": row["valid"], "trials": row["trials"], "latency_n": row["latency_s"]["n"],
            "tokens_n": row["tokens"]["n"], "cost_n": row["cost_usd"]["n"], "pass_at_k": row["pass_at_k"], "pass_hat_k": row["pass_hat_k"], "latency_median_s": row["latency_s"]["median"],
            "tokens_median": row["tokens"]["median"], "cost_median_usd": row["cost_usd"]["median"],
            "error_rates": row["error_rates"]}


def _diff(x: dict, y: dict) -> dict:
    return {k: ({c: _delta(x[k][c], y[k][c]) for c in x[k]} if k == "error_rates" else _delta(x[k], y[k]))
            for k in ("pass_at_k", "pass_hat_k", "latency_median_s", "tokens_median", "cost_median_usd", "error_rates")}


def compare_models(a: Path, b: Path) -> dict:
    """The same bank, code and k on two tiers: both sides and b minus a per task, paired McNemar on pass^k. Latency,
    tokens, cost and error rates are per trial, so they are compared per task only; a capability is compared on its
    check pass share ({ok, n} each side, delta = b - a of ok/n)."""
    (ha, ta), (hb, tb) = load_run(a), load_run(b)
    for h in (ha, hb):
        if h.get("kind") != "trials":
            raise ValueError(f"a model comparison needs two trials runs, not {h.get('kind')}")
    _same(ha, hb, ("bank_version", "bank_sha256", "code_version", "prompts_sha256", "k"))
    if ha.get("tier") == hb.get("tier"):
        raise ValueError(f"a model comparison needs two different tiers, both runs are the same tier: {ha.get('tier')}")
    aa, ab = aggregate(ta), aggregate(tb)
    rows = []
    for tid in sorted(set(aa["tasks"]) | set(ab["tasks"])):
        if tid in aa["tasks"] and tid in ab["tasks"]:
            x, y = _side(aa["tasks"][tid]), _side(ab["tasks"][tid])
            rows.append({"task": tid, "a": x, "b": y, "delta": _diff(x, y)})
    share = lambda c: (c["ok"] / c["n"]) if c and c["n"] else None
    caps = {}
    for cap in sorted(set(aa["caps"]) | set(ab["caps"])):
        x, y = aa["caps"].get(cap), ab["caps"].get(cap)
        caps[cap] = {"a": x, "b": y, "delta": {"share": _delta(share(x), share(y))}}
    pair = _pairing(aa, ab)
    def shared(o: dict, key: str) -> dict:
        return o[key] | {"share": round(o[key]["ok"] / o[key]["n"], 4) if o[key]["n"] else None}
    sides = {s: aggregate_o | {k: shared(aggregate_o, k) for k in ("pass_at_k", "pass_hat_k")}
             for s, aggregate_o in (("a", aa["overall"]), ("b", ab["overall"]))}
    overall = sides | {"lost": len(pair["lost"]), "gained": len(pair["gained"]),
                       "delta": {k: _delta(sides["a"][k]["share"], sides["b"][k]["share"])
                                 for k in ("pass_at_k", "pass_hat_k")}}
    return {"a": {"tier": ha["tier"], "model": ha.get("model")}, "b": {"tier": hb["tier"], "model": hb.get("model")},
            "tasks": rows, "caps": caps, "overall": overall,
            **{k: pair[k] for k in ("paired", "only_in_a", "only_in_b", "no_valid_a", "no_valid_b", "p")}}


def _words(e: dict, names: dict[str, str]) -> dict:
    return evaltrace.event_words(e, names)


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_transcripts(run_dir: Path) -> None:
    """trials/<task>-<n>/transcript.html for every trial of a trials run that has a trace.json (the raw copy in
    transcript/ stays as it is)."""
    run_dir = Path(run_dir)
    if (_read(run_dir / "run.json") or {}).get("kind") == "replay":
        return
    for tf in sorted((run_dir / "trials").glob("*/trace.json")):
        trace = _read(tf)
        if trace:
            (tf.parent / "transcript.html").write_text(bankview.transcript_page(trace, f"Trial {tf.parent.name}"), encoding="utf-8")


def round_minutes(head: dict) -> float | None:
    """The run's wall minutes from run.json's started/finished; None while it has not finished."""
    if not head.get("started") or not head.get("finished"):
        return None
    span = datetime.datetime.fromisoformat(head["finished"]) - datetime.datetime.fromisoformat(head["started"])
    return round(span.total_seconds() / 60, 2)


def page_doc(run_dir: Path, tasks: dict | None = None, bars: dict | None = None, baseline: list[dict] | None = None,
             bars_source: str | None = None) -> dict:
    """Everything the results page needs about one run (bankview.PAGE renders it): provenance, the aggregate (usage
    first, the bars' rows, status counts and missing), and per task its trials with every failed check, its why steps
    in words and the transcript link at the moment the check was decided."""
    run_dir = Path(run_dir)
    head, trials = load_run(run_dir)
    agg = aggregate(trials, tasks, head["missing"], bars=bars, baseline=baseline, round_minutes=round_minutes(head),
                    bars_source=bars_source)
    agg["missing"] = head["missing"]
    kind = head.get("kind") or "trials"
    prov = {k: head.get(k) for k in ("kind", "bank_version", "bank_sha256", "code_version", "tier", "model", "k",
                                      "started", "finished", "key_version", "key_sha", "round", "comparable")}
    out = []
    for tid in sorted({t["task"] for t in trials}):
        rows = []
        for t in sorted((x for x in trials if x["task"] == tid), key=lambda x: str(x["trial"])):
            folder = run_dir / "trials" / f"{tid}-{t['trial']}"
            trace = _read(folder / "trace.json")
            names = {a["id"]: a.get("name") or a["id"] for a in (trace or {}).get("agents", [])}
            has_transcript = kind != "replay" and (folder / "transcript.html").is_file()
            failed = []
            for c in t.get("checks") or []:
                if c.get("result") != "fail":
                    continue
                steps = [_words(e, names) for e in evaltrace.why(trace, t, c["id"])] if trace else []
                at = c.get("at") or next((s["at"] for s in steps if s["at"]), "")
                link = (f"trials/{quote(f'{tid}-{t['trial']}', safe='')}/transcript.html" + (f"#t={at}" if at else "")) if has_transcript else None
                failed.append({"id": c["id"], "type": c.get("type"), "caps": c.get("caps") or [], "node": c.get("node"),
                               "quote": c.get("quote"), "node_quote": c.get("node_quote"), "at": at, "why": steps, "transcript": link})
            rows.append({"trial": t["trial"], "status": t.get("status"), "pass": bool(t.get("pass")),
                         "reason": t.get("reason") or "", "checks_n": len(t.get("checks") or []), "failed": failed,
                         "unscored": len(t.get("unscored") or []), "latency_s": t.get("latency_s"),
                         "cost_usd": t.get("cost_usd")})
        out.append({"task": tid, "trials": rows})
    doc = {"schema": "bank-page/1", "kind": kind, "run": run_dir.name, "provenance": prov, "aggregate": agg, "tasks": out}
    if head.get("comparable") is False:
        chosen = "a set chosen by hand" if head.get("round") == "explicit" else "a subset of the bank chosen for a change"
        doc["label"] = (f"{head.get('round') or 'smoke'} round: {chosen}, reported per class and never compared with a "
                        "full run")
    if kind == "replay":
        doc["label"] = "replay: grades the stored run copies of past records, not comparable with trials (no fresh runs, no latency, tokens or cost per trial)"
    return doc


def _has_second_read(evs: list[dict]) -> bool:
    """The copy's code had the second read: the ledger holds a decisions read for some meeting (the legacy
    decisions_read counts as version 1, as in L.reads_of)."""
    return any("decisions" in r for r in L.reads_of(evs).values())


def _record_summary(rec: Path) -> dict:
    try:
        body = json.loads((rec / "trajectory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return body.get("summary") if isinstance(body.get("summary"), dict) else body


def replay(bank_path: Path, records: list[Path], tasks: list[str] | None = None) -> dict:
    """Free: grade every bank task (or only `tasks`) on each stored record's run copy (meta.json `project`). Writes
    nothing. One trial per (task, record), the record's name as the trial; latency and cost belong to the record (one
    session read every meeting), so they sit under per_record and stay None on the trials. A record whose meta.json
    carries `code_has_second_read` is graded with that flag instead of the ledger's; one marked `as_trial` is graded
    like a trial, whether or not its meeting was processed."""
    head, bank = load_bank(bank_path)
    bank = [t for t in bank if tasks is None or t["task"] in tasks]
    trials, per_record, names = [], {}, []
    for rec in map(Path, records):
        name = rec.name
        names.append(name)
        try:
            meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        summ = _record_summary(rec)
        copy = Path(meta["project"]) if meta.get("project") else None
        info = {"tier": meta.get("model_tier"), "code": meta.get("git_commit"), "key_version": meta.get("key_version"),
                "key_sha": meta.get("key_sha"), "copy": str(copy) if copy else None,
                "latency_s": summ.get("duration_s"), "cost_usd": summ.get("cost_usd"), "code_has_second_read": None}
        per_record[name] = info
        usable = copy is not None and (copy / "taska.toml").is_file()
        evs, done = [], set()
        if usable:
            try:
                evs = S.events(C.load(copy).store)
                done = S.processed(evs)
                flag = meta.get("code_has_second_read")
                info["code_has_second_read"] = flag if isinstance(flag, bool) else _has_second_read(evs)
            except Exception as e:
                usable, why = False, f"the copy could not be read: {type(e).__name__}: {e}"
        elif not copy:
            why = "meta.json names no copy"
        else:
            why = f"the copy is missing: {copy}"
        for t in bank:
            r = {"schema": "eval-trial/1", "task": t["task"], "trial": name, "status": "error", "pass": False,
                 "checks": [], "unscored": [], "unlocated": [], "latency_s": None, "tokens": None, "cost_usd": None,
                 "errors": None, "reason": ""}
            if not usable:
                r["reason"] = why
            elif t["evidence"] not in done and meta.get("as_trial") is not True:
                r["reason"] = f"{t['evidence']} was never processed in this copy"
            else:
                try:
                    g = grade(t, copy, code_has_second_read=bool(info["code_has_second_read"]))
                    r |= {"status": "invalid" if g.get("refused") else "done", "pass": bool(g["pass"]) and not g.get("refused"),
                          "checks": g["checks"], "unscored": g["unscored"], "unlocated": g["unlocated"],
                          "reason": g.get("refused") or ""}
                except Exception as e:
                    r["reason"] = f"grading failed: {type(e).__name__}: {e}"
            trials.append(r)
    return {"kind": "replay", "bank": {"version": head["version"], "sha": head["sha256"]}, "records": names,
            "per_record": per_record, "trials": trials}


def default_out(records: list[Path], version: str) -> Path:
    """<project>/reports/evals/bank/replay-<version>-<timestamp>, the project being the parent of the nearest ancestor
    of the records named `reports`; ValueError (pass --out) when there is none or the records differ."""
    homes = set()
    for rec in records:
        up = next((q for q in Path(rec).resolve().parents if q.name == "reports"), None)
        if up is None:
            raise ValueError(f"{rec} is not under a `reports` folder: pass --out")
        homes.add(up)
    if len(homes) != 1:
        raise ValueError("the records come from different projects: pass --out")
    return homes.pop() / "evals" / "bank" / f"replay-{version}-{datetime.datetime.now():%Y%m%d-%H%M%S}"


def write_replay(result: dict, out: Path, bank_path: Path | None = None) -> Path:
    """<out>/run.json (kind replay; bank_version/bank_sha256 so compare can match), one trials/<task>-<record>/grade.json
    per trial and bank.json (aggregate)."""
    out = Path(out)
    (out / "trials").mkdir(parents=True, exist_ok=True)
    for t in result["trials"]:
        d = out / "trials" / f"{t['task']}-{t['trial']}"
        d.mkdir(parents=True, exist_ok=True)
        _write_json(d / "grade.json", t)
    meta = {k: v for k, v in result.items() if k != "trials"} | {
        "bank_version": result["bank"]["version"], "bank_sha256": result["bank"]["sha"],
        "tasks": sorted({t["task"] for t in result["trials"]}),
        "created": datetime.datetime.now().isoformat(timespec="seconds")}
    _write_json(out / "run.json", meta)
    tasks = {t["task"]: t for t in load_bank(bank_path)[1]} if bank_path else None
    bankview.write(out, page_doc(out, tasks))
    return out


def trial_metrics(session: Path) -> dict:
    """What one trial cost, summed over the session and its subagents: the eval-trace/1 model's `metrics`, which finds
    refusals, blocked commands and acknowledgements without a re-read from the calls themselves."""
    return evaltrace.metrics(evaltrace.from_run(TJ.load_run(Path(session))))


def keep_transcript(session: Path, out: Path) -> Path:
    """Copy the session file and its subagents folder into out/transcript/, where the page opens it."""
    session = Path(session)
    dest = Path(out) / "transcript"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(session, dest / session.name)
    sub = session.parent / session.stem
    if sub.is_dir():
        shutil.copytree(sub, dest / session.stem, dirs_exist_ok=True)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Task bank tools.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="generate a bank version from the key and adjudications")
    i.add_argument("--project", required=True, type=Path)
    i.add_argument("--key", required=True, type=Path)
    i.add_argument("--adjudications", nargs="+", default=[], help="CASES VERDICTS [CASES VERDICTS ...]")
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--version", default="v1")
    i.add_argument("--note", default="generated from the key and adjudications")
    i.add_argument("--out", type=Path)
    gr = sub.add_parser("grow", help="a new bank version from the owner's eval-labels/1 files")
    gr.add_argument("--bank", required=True, type=Path)
    gr.add_argument("--labels", required=True, nargs="+", type=Path)
    gr.add_argument("--project", required=True, type=Path)
    gr.add_argument("--dry-run", action="store_true")
    gr.add_argument("--version", required=True)
    gr.add_argument("--note", required=True)
    w = sub.add_parser("why", help="the trace events linked to one check of a trial, as JSON")
    w.add_argument("--trial", required=True, type=Path)
    w.add_argument("--check", required=True)
    r = sub.add_parser("run", help="paid: k headless trials per task on snapshot copies")
    r.add_argument("--project", required=True, type=Path)
    r.add_argument("--bank", required=True, type=Path)
    r.add_argument("--tier", required=True, choices=list(RUN.TIERS))
    r.add_argument("--k", type=int, default=3)
    r.add_argument("--tasks", nargs="+")
    r.add_argument("--parallel", type=int, default=4)
    r.add_argument("--ai", nargs="+", help="the command that reaches Claude (default: taska ai)")
    r.add_argument("--yes", action="store_true", help="this costs model calls")
    r.add_argument("--budget", type=float, help="refuse, before any model call, a run expected to take more minutes")
    r.add_argument("--minutes-from", nargs="+", type=Path,
                   help="recorded runs whose minutes per size class set the expectation (default: the project's runs)")
    ag = sub.add_parser("aggregate", help="free: pass@k, pass^k, capabilities, cost of one run; writes bank.json")
    ag.add_argument("--run", required=True, type=Path)
    ag.add_argument("--bank", type=Path, help="the bank file, for the tasks' budgets")
    ag.add_argument("--project", type=Path, help="the project whose evals/bars.json sets the bars (none: not set)")
    ag.add_argument("--baseline", type=Path, help="a run of the same meetings to check regression against")
    pg = sub.add_parser("page", help="free: write index.html and bank.json (the page's data) of one run")
    pg.add_argument("--run", required=True, type=Path)
    pg.add_argument("--bank", type=Path, help="the bank file, for the tasks' budgets")
    pg.add_argument("--project", type=Path, help="the project whose evals/bars.json sets the bars (none: not set)")
    pg.add_argument("--baseline", type=Path, help="a run of the same meetings to check regression against")
    for name, h in (("compare", "two runs of one bank and tier"), ("compare-models", "one bank, two tiers")):
        c = sub.add_parser(name, help=h)
        c.add_argument("--a", required=True, type=Path)
        c.add_argument("--b", required=True, type=Path)
        if name == "compare-models":
            c.add_argument("--out", type=Path, help="write the comparison page (index.html + bank.json) here")
    rp = sub.add_parser("replay", help="free: grade the stored run copies of records against the bank")
    rp.add_argument("--bank", required=True, type=Path)
    rp.add_argument("--records", required=True, nargs="+", type=Path)
    rp.add_argument("--out", type=Path)
    g = sub.add_parser("grade", help="free: grade one existing copy against the bank")
    g.add_argument("--bank", required=True, type=Path)
    g.add_argument("--copy", required=True, type=Path)
    g.add_argument("--task")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        try:
            bars, bars_path = load_bars(a.project)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        bank = load_bank(a.bank)[1]
        by_task = {t["task"]: t for t in bank}
        measured = None
        if a.budget is not None:
            chosen = [t for t in bank if not a.tasks or t["task"] in a.tasks]
            sources = a.minutes_from or [a.project / "reports" / "evals" / "bank"]
            measured = measured_minutes(sources, by_task, a.tier, RUN.TIERS[a.tier])
            try:
                plan = budget_plan(chosen, a.k, a.parallel, measured, a.budget,
                                   measured_overhead(sources, by_task, a.tier, RUN.TIERS[a.tier]))
            except ValueError as e:
                print(f"--budget {a.budget:g} cannot be checked: {e}")
                return 2
            print(json.dumps(plan))
            if plan["over"]:
                print(f"expected {plan['expected_minutes']:g} minutes is over the budget of {a.budget:g} at k = {a.k}, "
                      f"parallel = {a.parallel}: drop {', '.join(plan['drop'])} (or lower --k, or raise --parallel)")
                return 2
        if not a.yes:
            print("run costs model calls: re-run with --yes")
            return 2
        try:
            check_transcripts(a.project, [t for t in bank if not a.tasks or t["task"] in a.tasks])
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        probe = [*a.ai, "--probe"] if a.ai else [*RUN.TASKA, "--project", str(a.project), "ai", "--probe"]
        try:
            failed = subprocess.run(probe).returncode != 0
        except OSError as e:
            print(f"Claude could not be reached: {e}")
            failed = True
        try:
            out = run_bank(a.project, a.bank, a.tier, a.k, a.tasks, a.parallel, a.ai,
                           invalid="Claude could not be reached: see the reasons above" if failed else "",
                           minutes_by_class=measured)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        head, trials = load_run(out)
        verdict = aggregate(trials, by_task, head["missing"], bars=bars, round_minutes=round_minutes(head))["bars"]
        print(json.dumps({"run": str(out), "invalid": failed, "bars": verdict["run"]}))
        if failed:
            return 1
        if verdict["run"] in ("fail", "unverified"):
            hard = [f"{r['key']} {r['result']}" for r in verdict["rows"] if r["hard"] and r["result"] in ("fail", "no data")]
            print(f"the hard bars of {bars_path} are {verdict['run']}: {', '.join(hard)}")
            return 3
        if verdict["run"] == "not set":
            print(f"no hard bar is set ({bars_path}): the run is not judged against bars")
        return 0
    if a.cmd == "grow":
        try:
            tasks, diff = grow(a.bank, a.labels, a.project)
            if not a.dry_run:
                diff["written"] = str(write_bank(a.bank.parent, tasks, a.version, datetime.date.today().isoformat(), a.note))
        except (ValueError, OSError) as e:
            print(str(e), file=sys.stderr)
            return 2
        print(json.dumps(diff, ensure_ascii=False, indent=1))
        return 0
    if a.cmd in ("aggregate", "page"):
        tasks = {t["task"]: t for t in load_bank(a.bank)[1]} if a.bank else None
        try:
            bars, source = load_bars(a.project) if a.project else (None, None)
            baseline = load_baseline(a.run, a.baseline) if a.baseline else None
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        write_transcripts(a.run)
        doc = page_doc(a.run, tasks, bars=bars, baseline=baseline,
                       bars_source=str(source) if bars is not None else None)
        page = bankview.write(a.run, doc)
        if a.cmd == "page":
            print(str(page.parent / "index.html"))
            return 0
        print(json.dumps(doc["aggregate"], ensure_ascii=False, indent=1))
        return 0
    if a.cmd in ("compare", "compare-models"):
        try:
            result = (compare if a.cmd == "compare" else compare_models)(a.a, a.b)
            if a.cmd == "compare-models" and a.out:
                bankview.write(a.out, {"schema": "bank-page/1", "kind": "models", "run": a.out.name} | result)
            print(json.dumps(result, ensure_ascii=False, indent=1))
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        return 0
    if a.cmd == "replay":
        result = replay(a.bank, a.records)
        try:
            out = a.out or default_out(a.records, result["bank"]["version"])
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        write_replay(result, out, a.bank)
        print(json.dumps({"run": str(out), "records": result["records"], "trials": len(result["trials"]),
                          "errors": sum(t["status"] == "error" for t in result["trials"])}))
        return 0
    if a.cmd == "grade":
        _, bank = load_bank(a.bank)
        graded = [grade(t, a.copy) for t in bank if not a.task or t["task"] == a.task]
        print(json.dumps(graded[0] if a.task and graded else graded, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "why":
        load = lambda name: json.loads((a.trial / name).read_text(encoding="utf-8"))
        print(json.dumps(evaltrace.why(load("trace.json"), load("grade.json"), a.check), ensure_ascii=False, indent=1))
        return 0
    if len(a.adjudications) % 2:
        ap.error("--adjudications takes CASES VERDICTS pairs")
    head, key = ES.load_gold(a.key)
    pairs = [(json.loads(Path(c).read_text(encoding="utf-8")), json.loads(Path(v).read_text(encoding="utf-8")))
             for c, v in zip(a.adjudications[::2], a.adjudications[1::2])]
    tasks, problems = init(a.project, key, pairs, key_version=head.get("version") or "")
    out = a.out or a.project / "reports" / "evals" / "bank" / f"review-{a.version}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(review_page(tasks, problems), encoding="utf-8")
    written = None
    if not a.dry_run:
        written = str(write_bank(a.project / "evals" / "bank", tasks, a.version,
                                 datetime.date.today().isoformat(), a.note))
    print(json.dumps({"tasks": len(tasks), "must_create": sum(len(t["must_create"]) for t in tasks),
                      "must_not_create": sum(len(t["must_not_create"]) for t in tasks), "problems": problems,
                      "per_task": [{"task": t["task"], "must_create": [m["id"] for m in t["must_create"]],
                                    "must_not_create": [m["id"] for m in t["must_not_create"]]} for t in tasks],
                      "review": str(out / "index.html"), "written": written}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
