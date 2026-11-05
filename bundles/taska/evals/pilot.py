"""Agent-led labelling, the deterministic steps: validate each reader's labels against the transcript, pair two
readers' labels into agreements and disagreements (reader names hidden), draw the preregistered agreement sample,
and measure provisional labels against a development key.

  pilot.py validate --labels A.json --inputs DIR
  pilot.py union --a A.json --b B.json --inputs DIR --out union.json [--seed N]
  pilot.py check --union union.json --resolutions resolutions.json --provisional provisional.json --inputs DIR --out check.json
  pilot.py compare --labels provisional.json --key items.vN.jsonl --out compare.json

Artifact contract v2 (what role instructions must ask for; older artifacts are read through the normalize_* functions,
which record every mapping):
  resolutions  a list of {id, decision: include|exclude|merge|policy_unresolved, duplicate_of (merge only: an included
               candidate), evidence, rule}
  reviews      {candidate, by, verdict: uphold_removal|restore_candidate|keep_label|remove_label|fix_fields|unresolved,
               evidence, rule}: the verdict names the action
  findings     {id, targets: [candidate ids], type, verdict, ...}: one finding however many targets
  assessments  {label, work, representation, fields, policy, source, provenance: {dimension: [{role, stage, basis, value,
               resolution?}]}}: an unstated dimension is not_assessed; a judged one names who judged it and what they
               said (`value`; a v2 entry without it asserts the record's value); label_quality reports each provenance
               view apart
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS.parent / "skills" / "run"))
from taska_lib import evalscore as ES  # noqa: E402
from taska_lib.text import closest_span, norm  # noqa: E402

KINDS = ("task", "story", "decision", "risk")
COMMITMENTS = ("committed", "proposed", "declined")
STATUSES = ("open", "progress", "done", "held", None)
REQUIRED = ("quote", "ts", "speaker", "title", "kind", "commitment", "owner", "due", "status", "client_owed", "note")
COMPARED = ("kind", "commitment", "owner", "due", "status", "client_owed", "representation", "linked_to")
NODE_ID = re.compile(r"[A-Z]\d{2,}")

# Artifact contract v2. A candidate is named by `id` in every artifact. A resolution's decision is one of DECISIONS; a
# merge names its destination, an included candidate, in `duplicate_of`. A review verdict states the action itself.
DECISIONS = ("include", "exclude", "merge", "policy_unresolved")
REVIEW_VERDICTS = ("uphold_removal", "restore_candidate", "keep_label", "remove_label", "fix_fields", "unresolved")
CID = re.compile(r"\bc\d{3}\b")
MULTI_LINK = re.compile(r"\s/\s|;")   # "billing work / RMS replacement": two targets; "CI/CD", "C++", "build or buy" are one
NEAR_EDITS = 2      # a non-verbatim quote this many word edits from a transcript span gets that span as a suggestion


def _field(i: dict, k: str) -> object:
    """A label's value for comparison: representation defaults to item; a link is a node id when it is one (a stable
    identity), else its words with case and spacing normalised."""
    v = i.get(k)
    if k == "representation":
        return v or "item"
    if k == "linked_to" and isinstance(v, str):
        return v.strip() if NODE_ID.fullmatch(v.strip()) else " ".join(v.lower().split())
    return v


def _rule_refs(labels: list[dict]) -> dict | None:
    """Rule references apart from meaning: the ids (with any version) not cited by every label, order ignored."""
    sets = [set(l.get("rule_ids") or []) for l in labels]
    odd = sorted(set().union(*sets) - set.intersection(*sets)) if sets else []
    return {"only_in_some": odd} if odd else None


def _items(labels: list[dict]) -> list[dict]:
    return [dict(i, evidence=m["evidence"]) for m in labels for i in m.get("items") or []]


def _single_link(link: str, known: set[str]) -> bool:
    """A link names one target when it is a node id or exactly one known title (case and spacing ignored); only
    otherwise does a spaced slash or a semicolon mean two targets."""
    v = " ".join(link.lower().split())
    return bool(NODE_ID.fullmatch(link.strip())) or v in known or not MULTI_LINK.search(link)


def validate(labels: list[dict], inputs: Path, known_titles: set[str] | None = None) -> list[dict]:
    """Each label's problems (empty list when valid): the quote verbatim in its meeting, 8-40 words, every field
    present, kind/commitment/status values, due as YYYY-MM-DD, and a link naming one target: a node id or an exact
    known title (the labels' own titles and `known_titles`) is one; otherwise a spaced "/" or a ";" joins two."""
    texts: dict[str, str] = {}
    raw: dict[str, str] = {}
    out = []
    known = {" ".join(t.lower().split()) for t in (known_titles or set()) | {i.get("title") or "" for i in _items(labels)} if t}
    for n, i in enumerate(_items(labels)):
        ev = i["evidence"]
        if ev not in texts:
            f = inputs / f"{ev}.md"
            raw[ev] = f.read_text(encoding="utf-8") if f.exists() else ""
            texts[ev] = norm(raw[ev])
        p = [f"missing {k}" for k in REQUIRED if k not in i]
        q = norm(i.get("quote") or "")
        if not texts[ev]:
            p.append(f"no transcript {ev}.md")
        elif q not in texts[ev]:
            p.append("quote not verbatim in the transcript")
        if i.get("due_text") and i.get("due") is None and not i.get("due_unresolved"):
            p.append("due_text without a due needs due_unresolved")
        if not 8 <= len(q.split()) <= 40:
            p.append(f"quote has {len(q.split())} words (8-40)")
        if i.get("kind") not in KINDS:
            p.append(f"kind {i.get('kind')!r}")
        if i.get("commitment") not in COMMITMENTS:
            p.append(f"commitment {i.get('commitment')!r}")
        if i.get("status") not in STATUSES:
            p.append(f"status {i.get('status')!r}")
        c = i.get("contributors")
        if c is not None and not (isinstance(c, list) and all(isinstance(x, str) and x for x in c)):
            p.append(f"contributors {c!r}")
        link = i.get("linked_to")
        if isinstance(link, str) and not _single_link(link, known):
            p.append(f"linked_to names more than one target: {link!r}")
        d = i.get("due")
        if d is not None and not (isinstance(d, str) and len(d) == 10 and d[4] == "-" and d[7] == "-"):
            p.append(f"due {d!r}")
        row = {"n": n, "evidence": ev, "quote": i.get("quote"), "problems": p}
        if texts[ev] and q and q not in texts[ev]:
            near = near_verbatim(raw[ev], i.get("quote") or "")
            if near:
                row["near_verbatim"] = near
        out.append(row)
    return out


def _word_edits(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for x in a:
        cur = [prev[0] + 1]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def near_verbatim(text: str, quote: str) -> dict | None:
    """An evidence-format failure's bounded correction path: the transcript span, as written, within NEAR_EDITS word
    edits of a non-verbatim quote. It is a suggestion for a reader to re-emit or an adjudicator to adopt with its own
    provenance; the label stays invalid and its quote is never rewritten here."""
    span = closest_span(text, quote)
    if span is None:
        return None
    edits = _word_edits(norm(quote).split(), norm(span).split())
    return {"span": span, "edits": edits, "applied": False} if 0 < edits <= NEAR_EDITS else None


def add_omissions(union_doc: dict, omission: list[dict]) -> dict:
    """The omission reader's items joined to the union: one that names a candidate's span confirms it (found_by gains
    "omission"); any other becomes a new candidate of source "omission"."""
    doc = json.loads(json.dumps(union_doc))
    n = len(doc["candidates"])
    for i in _items(omission):
        hit = next((c for c in doc["candidates"] if any(same_item(i, l) for l in c["labels"])), None)
        if hit is not None:
            if "omission" not in doc["found_by"][hit["id"]]:
                doc["found_by"][hit["id"]].append("omission")
            continue
        n += 1
        cid = f"c{n:03d}"
        doc["candidates"].append({"id": cid, "source": "omission", "labels": [i]})
        doc["found_by"][cid] = ["omission"]
    doc["counts"] = dict(doc.get("counts") or {}, omission_new=sum(c["source"] == "omission" for c in doc["candidates"]))
    return doc


def normalize_resolutions(doc: dict | list) -> dict:
    """The adjudicator's resolutions in the v2 form: a list keyed by `id`, each decision one of DECISIONS. Older forms
    map, each mapping recorded per record: a wrapping {"resolutions": [...]}, `candidate` for `id`, `merged` for merge,
    `exclude` with a duplicate_of (round 2) for merge. A record without an id, with two ids that differ, with an id
    already seen or with an unknown decision is a problem, never dropped silently or overwritten."""
    rows = doc.get("resolutions") or [] if isinstance(doc, dict) else doc
    out, log, problems, seen = [], [], [], set()
    for n, r in enumerate(rows):
        rid, cand = r.get("id"), r.get("candidate")
        if rid and cand and rid != cand:
            problems.append(f"record {n}: id {rid} and candidate {cand} differ")
            continue
        cid = rid or cand
        if not cid:
            problems.append(f"record {n}: no candidate id")
            continue
        if cid in seen:
            problems.append(f"{cid}: two resolutions")
            continue
        maps = [] if rid else ["candidate->id"]
        d = r.get("decision")
        if d == "merged":
            d = "merge"
            maps.append("merged->merge")
        elif d == "exclude" and r.get("duplicate_of"):
            d = "merge"
            maps.append("exclude+duplicate_of->merge")
        if d not in DECISIONS:
            problems.append(f"{cid}: decision {r.get('decision')!r}")
            continue
        seen.add(cid)
        out.append({k: v for k, v in r.items() if k != "candidate"} | {"id": cid, "decision": d})
        if maps:
            log.append({"id": cid, "mappings": maps})
    return {"resolutions": out, "mappings": log, "problems": problems, "counts": {"in": len(rows), "out": len(out)}}


def merges(resolutions: list[dict] | dict) -> list[tuple[str, str]]:
    """(merged candidate, its destination) for every merge, read through the v2 adapter: the one place both the
    stage ledger and the comparison learn that a merged candidate's span is held by its destination."""
    return [(r["id"], r["duplicate_of"]) for r in normalize_resolutions(resolutions)["resolutions"]
            if r["decision"] == "merge" and r.get("duplicate_of")]


_OLD_REVIEW = {("remove", "confirmed"): "remove_label", ("remove", "rejected"): "keep_label",
               ("restore", "rejected"): "uphold_removal", ("fix", "confirmed"): "fix_fields", ("fix", "rejected"): "keep_label"}


def normalize_reviews(records: list[dict], interpretations: dict[tuple[str, str], dict] | None = None) -> dict:
    """Review records in the v2 form: `verdict` states the action (REVIEW_VERDICTS). An older record (`action` with
    confirmed/rejected/unresolved) maps only where its meaning is not in doubt. restore + confirmed is ambiguous
    (reviewers have written it for "the removal stands" and for "restore it"), so it stays unresolved unless
    `interpretations[(by, candidate)]` gives a verdict and the basis read from that record's own explanation."""
    out, mapped = [], []
    for r in records:
        rec = {k: v for k, v in r.items() if k != "action"}
        if "action" in r:
            v = _OLD_REVIEW.get((r["action"], r.get("verdict")))
            rec["as_written"] = {"action": r["action"], "verdict": r.get("verdict")}
            told = (interpretations or {}).get((r.get("by"), r.get("candidate")))
            if told:
                v = told["verdict"]
                rec["basis"] = told["basis"]
            elif v is None:
                v = "unresolved"
                rec["why"] = f"ambiguous older verdict {r['action']} + {r.get('verdict')}: its meaning is read from its explanation, never mapped globally"
            rec["verdict"] = v
            mapped.append(r.get("candidate"))
        if rec.get("verdict") not in REVIEW_VERDICTS:
            raise ValueError(f"{r.get('candidate')}: review verdict {rec.get('verdict')!r}")
        out.append(rec)
    return {"reviews": out, "mapped_from_older_form": mapped}


def normalize_findings(findings: list[dict]) -> list[dict]:
    """Each finding once, its targets a list: `targets` as given, else the candidate ids an older free-text `target`
    names ("c003,c026", "c019 (absorbs c005), c001"). A range ("c001..c066") or words without ids name a scope, not
    targets, and are kept as `scope`."""
    out = []
    for f in findings:
        if isinstance(f.get("targets"), list):
            out.append(dict(f))
            continue
        t = str(f.get("target") or "")
        ids = [] if ".." in t else list(dict.fromkeys(CID.findall(t)))
        out.append({k: v for k, v in f.items() if k != "target"} | {"targets": ids} | ({} if ids else {"scope": t}))
    return out


def stage_ledger(union_doc: dict, resolutions: list[dict], corrections: list[dict], key: list[dict] | None = None) -> dict:
    """Each candidate's path through readers, omission reader, adjudication and review to the final labels. Review
    verdicts are read in the v2 form (normalize_reviews). A recovery (restore_candidate) or removal (remove_label)
    counts once: the first role to state it is credited, later ones are not recoveries; uphold_removal recovers
    nothing. With a key, reference misses are reported apart from final labels outside it; unknown omissions stay
    unmeasured."""
    resolutions = normalize_resolutions(resolutions)["resolutions"]
    corrections = normalize_reviews(corrections)["reviews"]
    by = {r["id"]: r for r in resolutions}
    roles: dict[str, dict[str, int]] = {}
    credited: dict[str, dict] = {}
    for c in corrections:
        r = roles.setdefault(c["by"], {"credited": 0})
        r[c["verdict"]] = r.get(c["verdict"], 0) + 1
        if c["verdict"] in ("restore_candidate", "remove_label") and c["candidate"] not in credited:
            credited[c["candidate"]] = c
            r["credited"] += 1
    rows, final = [], []
    for cand in union_doc["candidates"]:
        cid, found = cand["id"], union_doc["found_by"][cand["id"]]
        decided = (by.get(cid) or {}).get("decision")
        fix = credited.get(cid)
        action = fix and {"restore_candidate": "restore", "remove_label": "remove"}[fix["verdict"]]
        kept = (decided == "include") != bool(action)
        readers = [f for f in found if f in ("A", "B")]
        if action == "restore":
            path = f"adjudication loss recovered ({fix['by']})"
        elif action == "remove":
            path = f"unsupported label removed ({fix['by']})"
        elif not readers:
            path = "first-pass miss, reviewer recovery (omission reader), no final miss" if kept else "omission reader's item not kept"
        else:
            path = "found by readers, kept" if kept else "found by readers, excluded"
        rows.append({"id": cid, "found_by": found, "adjudication": decided, "correction": action, "path": path, "final": kept})
        if kept:
            final.append(cid)
    out = {"candidates": rows, "by_role": roles, "final": sorted(final)}
    if key is not None:
        cands = {c["id"]: c for c in union_doc["candidates"]}
        spans = {cid: list(cands[cid]["labels"]) for cid in final}
        for src, dest in merges(resolutions):   # a candidate merged into a final label is held by that label
            if dest in spans and src in cands:
                spans[dest] += cands[src]["labels"]
        held = lambda g: [cid for cid in final if any(same_item({"evidence": g["evidence"], "quote": g.get("quote") or ""}, l)
                                                      for l in spans[cid])]
        holds = [g["cid"] for g in key if held(g)]
        in_key = {cid for g in key for cid in held(g)}
        out["reference"] = {"key_items": len(key), "final_holds": holds, "final_misses": [g["cid"] for g in key if g["cid"] not in holds],
                            "outside_key_final": sorted(set(final) - in_key),
                            "unknown_omissions": "not measured: no independent reference beyond the key and the omission reader"}
    return out


def dispositions(union_doc: dict, resolutions: list[dict] | dict) -> dict:
    """Every union candidate's recorded disposition (included, excluded, merged into another candidate, unresolved)
    and the problems: a candidate with none, a merge with no destination or into one that is not an included candidate,
    a resolution that cannot be read. A recorded disposition says a decision exists, never that the transcript
    justified it."""
    norm_ = normalize_resolutions(resolutions)
    ids = {c["id"] for c in union_doc["candidates"]}
    by = {r["id"]: r for r in norm_["resolutions"]}
    names = {"include": "included", "exclude": "excluded", "merge": "merged", "policy_unresolved": "unresolved"}
    out, problems = {}, list(norm_["problems"])
    for cid in sorted(ids):
        r = by.get(cid)
        out[cid] = names[r["decision"]] if r else None
        if r is None:
            problems.append(f"{cid}: no disposition")
        elif r["decision"] == "merge":
            dest = r.get("duplicate_of")
            if not dest:
                problems.append(f"{cid}: a merge names no destination")
            elif dest not in ids:
                problems.append(f"{cid}: merged into {dest}, which is not a candidate")
            elif (by.get(dest) or {}).get("decision") != "include":
                problems.append(f"{cid}: merged into {dest}, which is not included")
    problems += [f"{r['id']}: a disposition for no candidate" for r in norm_["resolutions"] if r["id"] not in ids]
    return {"by_candidate": out, "problems": problems}


def content_sha(label: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(label, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def removal_packets(union_doc: dict, resolutions: list[dict] | dict, under_review: list[dict], views: dict[str, dict],
                    sources: dict[str, str], details: dict[str, list[str]] | None = None) -> list[dict]:
    """One review packet per exclusion or merge, assembled from the version being reviewed: the candidate's every
    label, its disposition and rationale, each source quote located in the paginated view (pages to read for the
    surrounding context), the destination's id and content with its hash (a merge), or `not_applicable` (an
    exclusion), and the details the destination must keep. `missing` names what could not be assembled; a packet with
    anything missing is incomplete and its removal cannot be certified (`certify`)."""
    import pages as G
    res = normalize_resolutions(resolutions)["resolutions"]
    cands = {c["id"]: c for c in union_doc["candidates"]}
    labels = {i.get("candidate"): i for i in _items(under_review) if i.get("candidate")}
    out = []
    for r in res:
        if r["decision"] not in ("exclude", "merge"):
            continue
        cid, missing = r["id"], []
        cand = cands.get(cid)
        if cand is None or not cand.get("labels"):
            missing.append("candidate labels")
        rationale = {k: v for k, v in r.items() if k not in ("id", "decision", "duplicate_of", "final")}
        if not (r.get("rule") or r.get("evidence")):
            missing.append("rationale")
        spans = []
        for l in (cand or {}).get("labels") or []:
            m = views.get(l["evidence"])
            hit = G.locate(m, sources[l["evidence"]], l["quote"]) if m and l["evidence"] in sources else None
            spans.append({"evidence": l["evidence"], "quote": l["quote"], "located": hit})
            if hit is None:
                missing.append(f"source span {l['evidence']}: {l['quote'][:40]}")
        dest = r.get("duplicate_of") if r["decision"] == "merge" else None
        if r["decision"] == "merge":
            d = labels.get(dest)
            destination = {"id": dest, "content": d, "sha256": content_sha(d) if d else None}
            if d is None:
                missing.append(f"destination {dest} content")
        else:
            destination = "not_applicable"
        out.append({"candidate": cid, "disposition": r["decision"], "candidate_labels": (cand or {}).get("labels"),
                    "rationale": rationale, "sources": spans, "pages": sorted({(s["evidence"], n) for s in spans if s["located"]
                                                                                for n in s["located"]["pages"]}),
                    "destination": destination, "required_details": (details or {}).get(cid, []),
                    "missing": missing, "complete": not missing})
    return out


def certify(packet: dict, review: dict, delivered: set[tuple[str, int]], final_labels: list[dict]) -> dict:
    """Whether a removal review can be certified. Any final verdict needs a complete packet and every page holding
    its source spans delivered whole to the reviewer; an uphold of a merge also needs the destination unchanged since
    review (else the changed destination is reviewed first) and each required detail judged retained in
    `review["details"]` ({detail: retained|lost|unresolved}). Refused reasons name what a targeted retry must supply;
    nothing here judges meaning."""
    why = [f"packet missing {m}" for m in packet["missing"]]
    why += [f"page {e} p{n:02d} not delivered whole" for e, n in packet["pages"] if (e, n) not in delivered]
    if review.get("verdict") == "unresolved":
        why.append("unresolved is not a final verdict")
    if review.get("verdict") == "uphold_removal" and isinstance(packet["destination"], dict):
        judged = review.get("details") or {}
        named = list(dict.fromkeys(list(packet["required_details"]) + list(judged)))
        if not named:
            why.append("an upheld merge names no detail the destination keeps")
        final = {i.get("candidate"): i for i in _items(final_labels)}.get(packet["destination"]["id"])
        if final is None:
            why.append(f"destination {packet['destination']['id']} is not in the final labels")
        elif content_sha(final) != packet["destination"]["sha256"]:
            why.append(f"destination {packet['destination']['id']} changed after review: review the changed destination")
        why += [f"detail not judged retained: {d}" for d in named if judged.get(d) != "retained"]
    if review.get("verdict") not in REVIEW_VERDICTS:
        why.append(f"verdict {review.get('verdict')!r}")
    return {"candidate": packet["candidate"], "verdict": review.get("verdict"), "certified": not why, "why": why}


DROPPED = ("dropped", "without_resolution", "without resolution", "no decision", "no resolution")


def check_claims(findings: list[dict], union_doc: dict, resolutions: list[dict] | dict, provisional: list[dict], inputs: Path) -> dict:
    """The facts in a reviewer's findings that the files can settle, per target: the candidate exists, a claimed missing
    decision is really missing, a merge target exists; the quote occurs in the source; and whether the counts agree.
    A finding with several targets stays one finding (`finding_counts`). Its meaning (was the exclusion right?) is
    never settled here."""
    resolutions = normalize_resolutions(resolutions)["resolutions"]
    ids = {c["id"] for c in union_doc["candidates"]}
    by = {r["id"]: r for r in resolutions}
    texts = " ".join(norm(f.read_text(encoding="utf-8")) for f in sorted(Path(inputs).glob("*.md")) if f.name != "READER.md")
    out, affected = [], set()
    for f in normalize_findings(findings):
        per, why = [], []
        for t in f["targets"]:
            w = []
            if t not in ids:
                w.append(f"{t} is not a candidate")
            r = by.get(t)
            if t in ids and any(k in str(f.get("type", "")).lower() for k in DROPPED) and r is not None:
                kind = "merge" if r["decision"] == "merge" else "exclusion" if r["decision"] == "exclude" else r["decision"]
                w.append(f"{t} has a recorded {kind} ({r.get('rule') or r.get('duplicate_of') or ''})")
            if r is not None and r.get("duplicate_of") and r["duplicate_of"] not in ids:
                w.append(f"{t} is merged into {r['duplicate_of']}, which is not a candidate")
            per.append({"id": t, "factual": "rejected" if w else "holds", "evidence": w})
            why += w
            affected.add(t)
        if f.get("quote") and norm(f["quote"]) not in texts:
            why.append("quote not in the transcript")
        bad = [p for p in per if p["factual"] == "rejected"]
        factual = ("rejected" if why and (not per or len(bad) == len(per) or "quote not in the transcript" in why)
                   else "partly" if bad else "holds")
        out.append(dict(f, targets=per, factual=factual, evidence=why,
                        semantic="not checked by code: a recorded exclusion is not a justified one"))
    inc = sum(r["decision"] == "include" for r in resolutions)
    items = len(_items(provisional))
    return {"findings": out, "counts": {"included_resolutions": inc, "provisional_items": items, "match": inc == items},
            "finding_counts": {"findings": len(out), "affected_labels": len(affected & ids),
                               "with_scope_only": sum(not f["targets"] and "scope" in f for f in out)}}


def _containment(a: str, b: str) -> tuple[float, float]:
    """(the larger, the smaller) 5-word containment of either quote in the other: the closer span ranks first."""
    sa, sb = ES.quote_shingles(norm(a)), ES.quote_shingles(norm(b))
    x, y = (ES.containment(sa, sb), ES.containment(sb, sa)) if sa and sb else (0.0, 0.0)
    return max(x, y), min(x, y)


def same_item(a: dict, b: dict) -> bool:
    """Two labels name the same source span: one meeting, and 5-word containment of either quote in the other >= 0.5."""
    if a["evidence"] != b["evidence"]:
        return False
    sa, sb = ES.quote_shingles(norm(a["quote"])), ES.quote_shingles(norm(b["quote"]))
    return bool(sa and sb) and max(ES.containment(sa, sb), ES.containment(sb, sa)) >= 0.5


def union(a: list[dict], b: list[dict], seed: int) -> dict:
    """Candidates with reader names hidden: `both` (paired one-to-one, with the fields they disagree on) or `one`
    (only one reader listed it). The agreement sample is drawn by the preregistered rule."""
    ia, ib = _items(a), _items(b)
    used: set[int] = set()
    cands = []
    for x in ia:
        j = next((k for k, y in enumerate(ib) if k not in used and same_item(x, y)), None)
        if j is None:
            cands.append({"source": "one", "labels": [x], "_by": ["A"]})
            continue
        used.add(j)
        y = ib[j]
        diff = [k for k in COMPARED if _field(x, k) != _field(y, k)]
        cands.append({"source": "both", "labels": [x, y], "differ_on": diff, "rule_refs_differ": _rule_refs([x, y]), "_by": ["A", "B"]})
    cands += [{"source": "one", "labels": [y], "_by": ["B"]} for k, y in enumerate(ib) if k not in used]
    rng = random.Random(seed)
    for c in cands:   # hide which reader said what: shuffle each pair's order with the seeded generator
        rng.shuffle(c["labels"])
    cands.sort(key=lambda c: (c["labels"][0]["evidence"], c["labels"][0].get("ts") or "", norm(c["labels"][0]["quote"])))
    found_by = {}
    for n, c in enumerate(cands, 1):
        c["id"] = f"c{n:03d}"
        found_by[c["id"]] = c.pop("_by")
    agree = [c["id"] for c in cands if c["source"] == "both" and not c["differ_on"]]
    k = min(len(agree), max(3, math.ceil(0.3 * len(agree))))
    return {"candidates": cands, "found_by": found_by, "agreements": agree, "agreement_sample": sorted(random.Random(seed).sample(agree, k)),
            "counts": {"both_agree": len(agree), "both_differ": sum(c["source"] == "both" and bool(c["differ_on"]) for c in cands),
                       "one_reader": sum(c["source"] == "one" for c in cands)}}


def _merged_spans(items: list[dict], resolutions: list[dict] | dict, union_doc: dict | None) -> list[tuple[int, str, dict]]:
    """(index of the kept label, the merged candidate's id, one of its spans) for each candidate the adjudicator
    merged into a kept one: its quotes (from the union when given) stand for the kept label."""
    at = {i.get("candidate"): n for n, i in enumerate(items) if i.get("candidate")}
    cands = {c["id"]: c for c in (union_doc or {}).get("candidates") or []}
    evidence = {r["id"]: r.get("evidence") or "" for r in normalize_resolutions(resolutions)["resolutions"]}
    out = []
    for src, dest in merges(resolutions):
        kept = at.get(dest)
        if kept is None:
            continue
        quotes = [l["quote"] for l in cands[src]["labels"]] if src in cands else [evidence[src]]
        out += [(kept, src, {"evidence": items[kept]["evidence"], "quote": q}) for q in quotes]
    return out


def compare(labels: list[dict], key: list[dict], resolutions: list[dict] | dict | None = None, union_doc: dict | None = None) -> dict:
    """Provisional labels against a development key, by source span: key items covered (and whether kind and
    commitment agree), key items no label holds, labels the key lacks. With the adjudicator's resolutions, a key item
    whose span was merged into a kept label as a duplicate counts for that label (`via_duplicate`)."""
    items = _items(labels)
    merged = _merged_spans(items, resolutions or [], union_doc)
    held = []
    rows = []
    for g in key:
        gq = {"evidence": g["evidence"], "quote": g.get("quote") or ""}
        hit = next((n for n, i in enumerate(items) if same_item(gq, i)), None)
        via = None
        if hit is None:
            hit, via = next(((k, cid) for k, cid, span in merged if same_item(gq, span)), (None, None))
        if hit is not None:
            held.append(hit)
        i = items[hit] if hit is not None else {}
        rows.append({"cid": g.get("cid"), "kind": g.get("kind"), "commitment": g.get("commitment"), "covered": hit is not None,
                     "kind_same": hit is not None and ES.same_kind(i.get("kind"), g.get("kind")),
                     "commitment_same": hit is not None and i.get("commitment") == g.get("commitment"),
                     "label_title": i.get("title"), "key_title": g.get("title"), "via_duplicate": via,
                     "representation": i.get("representation"), "linked_to": i.get("linked_to")})
    extra = [{"evidence": i["evidence"], "quote": i["quote"], "title": i.get("title"), "kind": i.get("kind"),
              "commitment": i.get("commitment")} for n, i in enumerate(items) if n not in held]
    for r in rows:
        rep = r["representation"] or "item"
        r["legacy_match"] = r["covered"] and rep == "item" and r["kind_same"] and r["commitment_same"]
        r["policy_match"] = r["legacy_match"] or (r["covered"] and rep in ("checklist", "reminder")
                                                  and r["linked_to"] not in (None, "", "unresolved") and r["kind"] in ("task", "story"))
    differs = [r["cid"] for r in rows if r["covered"] and (r["representation"] or "item") != "item"]
    return {"validation": "agent_adjudicated span coverage, not validated correctness",
            "legacy_key": {"matches": sum(r["legacy_match"] for r in rows), "of": len(rows)},
            "policy_aware": {"matches": sum(r["policy_match"] for r in rows), "of": len(rows), "representation_differs_from_key": differs},
            "key_items": len(key), "covered": sum(r["covered"] for r in rows),
            "kind_same": sum(r["kind_same"] for r in rows), "commitment_same": sum(r["commitment_same"] for r in rows),
            "rows": rows, "labels_not_in_key": extra}


SHARED_BLOCK = re.compile(r"<!-- READER-BLOCK:([\w-]+):START -->\n(.*?)\n<!-- READER-BLOCK:\1:END -->", re.S)


def shared_blocks(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in SHARED_BLOCK.finditer(text)}


def rule_delivery(source: str, delivered: dict[str, list[Path]], names: tuple[str, ...]) -> dict:
    """Whether each role's delivered prompt files carry each named shared block byte-identical to the source's
    (skills/run/EXTRACT.md, the production rule): per role and block `same`, `differs` or `missing`, and each block's
    sha256 fingerprint. It proves the instruction rendered into what the role was given, never that a model
    followed it."""
    want = shared_blocks(source)
    missing = [n for n in names if n not in want]
    if missing:
        raise ValueError(f"source has no block {missing}")
    roles = {}
    for role, files in delivered.items():
        got: dict[str, str] = {}
        for f in files:
            for n, b in shared_blocks(Path(f).read_text(encoding="utf-8")).items():
                got.setdefault(n, b)
        roles[role] = {n: "missing" if n not in got else "same" if got[n] == want[n] else "differs" for n in names}
    import hashlib
    return {"fingerprints": {n: hashlib.sha256(want[n].encode()).hexdigest() for n in names}, "roles": roles,
            "ok": all(v == "same" for r in roles.values() for v in r.values())}


def _same_person(a: str | None, b: str | None) -> bool:
    """One person named the same way (a first name matches the full name); several people never match one."""
    if a is None or b is None:
        return a is b
    if any(re.search(r"\band\b|[,&/]", x) for x in (a, b)):
        return False
    x, y = set(norm(a).split()), set(norm(b).split())
    return bool(x and y) and (x <= y or y <= x)


def owner_probe(labels: list[dict], expected: dict) -> dict:
    """The ownership rule's behavioural check on planted probe meetings: per case, whether a label holds the span
    (same_item) and states the expected commitment and owner (a first name matches the full name). `followed` is
    model behaviour on these cases only; `not_labelled` is neither a pass nor a failure of the rule."""
    items = _items(labels)
    out = []
    for c in expected["cases"]:
        g = {"evidence": c["evidence"], "quote": c["quote"]}
        hits = [i for i in items if same_item(g, i)]
        hit = max(hits, key=lambda i: _containment(g["quote"], i["quote"]) + (-abs(len(norm(i["quote"]).split()) - len(norm(c["quote"]).split())),),
                  default=None)
        row = {"case": c["case"], "expected_owner": c["owner"], "expected_commitment": c["commitment"]}
        if hit is None:
            out.append(row | {"result": "not_labelled"})
            continue
        ok = _same_person(hit.get("owner"), c["owner"]) and hit.get("commitment") == c["commitment"]
        out.append(row | {"owner": hit.get("owner"), "commitment": hit.get("commitment"), "result": "followed" if ok else "not_followed"})
    return {"rule": expected["rule"], "cases": out, "counts": dict(Counter(r["result"] for r in out))}


EQUIVALENCE = ("same_work", "different_work", "insufficient_evidence")


def reviewed_overlay(compare_doc: dict, labels: list[dict], key: list[dict], verdicts: list[dict]) -> dict:
    """The automated comparison with independently reviewed equivalences laid over it, never merged into it: a key
    item the span match missed counts as held only on a reviewer's `same_work` verdict ({key, label: candidate id,
    same_claim, fields, basis, by}), its kind, commitment and representation then compared with the key exactly as
    compare() does. Each changed row is listed against the automated figures; field verdicts stay the reviewer's."""
    by_label = {i.get("candidate"): i for i in _items(labels) if i.get("candidate")}
    gold = {g["cid"]: g for g in key}
    seen = {v["key"]: v for v in verdicts}
    bad = [v for v in verdicts if v.get("same_claim") not in EQUIVALENCE or v["key"] not in gold or v.get("label") not in by_label or not v.get("by")]
    if bad:
        raise ValueError(f"unreadable verdicts: {[v.get('key') for v in bad]}")
    rows, changed = [], []
    for r in compare_doc["rows"]:
        v = seen.get(r["cid"])
        row = {"cid": r["cid"], "automated": {k: r[k] for k in ("covered", "legacy_match", "policy_match")}, "review": v}
        if v and not r["covered"] and v["same_claim"] == "same_work":
            i, g = by_label[v["label"]], gold[r["cid"]]
            rep = i.get("representation") or "item"
            kind_same, commit_same = ES.same_kind(i.get("kind"), g.get("kind")), i.get("commitment") == g.get("commitment")
            legacy = rep == "item" and kind_same and commit_same
            policy = legacy or (rep in ("checklist", "reminder") and i.get("linked_to") not in (None, "", "unresolved") and g.get("kind") in ("task", "story"))
            row["reviewed"] = {"covered": True, "legacy_match": legacy, "policy_match": policy, "kind_same": kind_same, "commitment_same": commit_same}
            changed.append(r["cid"])
        else:
            row["reviewed"] = dict(row["automated"])
        rows.append(row)
    count = lambda k, w: sum(x[w][k] for x in rows)
    return {"validation": "automated span coverage plus independently reviewed equivalences; the key and the automated result are unchanged",
            "automated": {k: {"n": count(k, "automated"), "of": len(rows)} for k in ("covered", "legacy_match", "policy_match")},
            "reviewed": {k: {"n": count(k, "reviewed"), "of": len(rows)} for k in ("covered", "legacy_match", "policy_match")},
            "changed_by_review": changed, "reviewed_but_unchanged": sorted(set(seen) - set(changed)), "rows": rows}


COLLECTIVE = re.compile(r"\bwe(?:['‘’]ll| will| are going to|['‘’]re going to| shall)\b", re.I)
SINGULAR = re.compile(r"\bI(?:['‘’]ll| will| am going to|['‘’]m going to)\b")


def collective_owner(labels: list[dict], quality: dict, view: str = "independent") -> dict:
    """Ownership on collective commitments: eligible labels quote a "we will" with no "I will"; per label whether
    the owner is the quote's speaker (an opportunity code can see), and, among those whose owner was judged in the
    view, how many were judged incorrect. The error rate's denominator is the reviewed eligible labels, never all."""
    vals = quality["views"][view]["values"]
    rows = []
    for i in _items(labels):
        cid = i.get("candidate")
        if not cid or not COLLECTIVE.search(i.get("quote") or "") or SINGULAR.search(i.get("quote") or ""):
            continue
        judged = vals.get(cid, {}).get("owner")
        rows.append({"label": cid, "owner": i.get("owner"), "speaker": i.get("speaker"),
                     "speaker_as_owner": i.get("owner") is not None and _same_person(i.get("owner"), i.get("speaker")),
                     "owner_judged": judged})
    reviewed = [r for r in rows if r["owner_judged"] in ("correct", "incorrect")]
    return {"view": view, "eligible": len(rows), "speaker_as_owner": [r["label"] for r in rows if r["speaker_as_owner"]],
            "reviewed": len(reviewed), "owner_incorrect": rate(sum(r["owner_judged"] == "incorrect" for r in reviewed), len(reviewed)),
            "unreviewed": [r["label"] for r in rows if r not in reviewed], "rows": rows}


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(inputs: Path, files: dict[str, Path], snapshot: Path, manifests: dict, models: dict) -> dict:
    """What a round is run with: every input's hash, every named file's hash, the code manifests, the snapshot of the
    (possibly uncommitted) tree and each role's resolved model id; verify() compares a later state with it."""
    return {"inputs": {f.name: _sha(f) for f in sorted(Path(inputs).iterdir()) if f.is_file()},
            "files": {k: _sha(v) for k, v in files.items()}, "manifests": manifests,
            "snapshot": {"file": Path(snapshot).name, "sha256": _sha(snapshot)}, "models": models}


def verify(frozen: dict, files: dict[str, Path], manifests: dict, inputs: Path, snapshot: Path) -> list[str]:
    """Every difference between now and the frozen round (empty: the round runs on what was frozen)."""
    now = {f.name: _sha(f) for f in sorted(Path(inputs).iterdir()) if f.is_file()}
    out = [f"input {n} changed" if n in now else f"input {n} missing" for n, h in frozen["inputs"].items() if now.get(n) != h]
    out += [f"input {n} added" for n in now if n not in frozen["inputs"]]
    out += [f"file {k} changed" for k, h in frozen["files"].items() if _sha(files[k]) != h]
    out += [f"manifest {k} changed" for k, v in frozen["manifests"].items() if manifests.get(k) != v]
    if _sha(snapshot) != frozen["snapshot"]["sha256"]:
        out.append("snapshot changed")
    return out


WORK = ("supported", "unsupported", "unresolved", "not_assessed")
FIELD = ("correct", "incorrect", "unresolved", "not_assessed", "not_applicable")
POLICY = ("resolved", "policy_unresolved")
REPRESENTATION = ("compliant", "noncompliant", "unresolved", "not_assessed")
QUALITY_FIELDS = ("owner", "commitment", "status", "due", "kind", "client_owed")
INITIAL_ROLES = ("reader_A", "reader_B", "omission_reader", "adjudicator")   # whose acceptance is a judgment, not a review
RETROSPECTIVE_ROLES = ("retrospective",)                                      # the analyst's own rulings after the round
INDEPENDENT_ROLES = ("skeptic", "removal_reviewer", "packet_reviewer", "domain_owner")   # explicit review of another role's output
VIEWS = ("initial", "independent", "retrospective")
SCALES = {"work": WORK, "representation": REPRESENTATION} | {f: FIELD for f in QUALITY_FIELDS}
DECIDED = {"work": ("supported", "unsupported"), "representation": ("compliant", "noncompliant")} | \
          {f: ("correct", "incorrect", "not_applicable") for f in QUALITY_FIELDS}
UNKNOWN = "unknown (0 assessed)"


def view_of(role: str) -> str:
    """The provenance view a role's judgment belongs to: the readers' and adjudicator's acceptance (initial), the
    analyst's later rulings (retrospective), every other role an explicit independent review."""
    if role in INITIAL_ROLES:
        return "initial"
    if role in RETROSPECTIVE_ROLES:
        return "retrospective"
    if role in INDEPENDENT_ROLES:
        return "independent"
    raise ValueError(f"unknown assessment role {role!r}")


def rate(n: int, of: int) -> dict[str, int] | str:
    return {"n": n, "of": of} if of else UNKNOWN


def _entries(a: dict) -> tuple[dict[str, list[dict]], int]:
    """An assessment record's judgments per dimension, each entry {role, stage, basis, value}. A contract-v2 entry
    without `value` asserts the record's own value for that dimension (counted as inherited). Every stated value
    other than not_assessed needs an entry, and every entry a role, a stage and a basis: a not_applicable ruling is
    a judgment too and needs its evidence."""
    rec = {"work": a.get("work", "not_assessed"), "representation": a.get("representation", "not_assessed")} | dict(a.get("fields") or {})
    bad = [f"{d} {v!r}" for d, v in rec.items() if v not in SCALES.get(d, ())]
    if a.get("policy", "resolved") not in POLICY:
        bad.append(f"policy {a.get('policy')!r}")
    if bad:
        raise ValueError(f"{a['label']}: " + ", ".join(bad))
    prov = a.get("provenance") or {}
    out, inherited = {}, 0
    for d, v in rec.items():
        if v != "not_assessed" and not prov.get(d):
            raise ValueError(f"{a['label']}: {d} {v!r} has no assessment provenance")
    for d, entries in prov.items():
        if d not in SCALES:
            raise ValueError(f"{a['label']}: provenance for unknown dimension {d!r}")
        for e in entries:
            if not (e.get("role") and e.get("stage") and e.get("basis")):
                raise ValueError(f"{a['label']}: {d} judgment without role, stage and basis: {e}")
            view_of(e["role"])
            v = e.get("value", rec.get(d, "not_assessed"))
            if v not in SCALES[d] or v == "not_assessed":
                raise ValueError(f"{a['label']}: {d} judgment value {v!r}")
            inherited += "value" not in e
            out.setdefault(d, []).append(dict(e, value=v))
    return out, inherited


def _decide(entries: list[dict]) -> tuple[str, dict | None]:
    """One view's value for one dimension: not_assessed without entries, the value they agree on, else the latest
    entry that states a `resolution` linking the conflicting evidence, provided no later entry disagrees with it;
    conflicting values otherwise stay unresolved."""
    if not entries:
        return "not_assessed", None
    values = list(dict.fromkeys(e["value"] for e in entries))
    if len(values) == 1:
        return values[0], None
    last = max((n for n, e in enumerate(entries) if e.get("resolution")), default=None)
    if last is not None and all(e["value"] == entries[last]["value"] for e in entries[last:]):
        return entries[last]["value"], {"values": values, "resolved_by": entries[last]["resolution"]}
    return "unresolved", {"values": values, "resolved_by": None}


def _view(rows: dict[str, dict], view: str) -> dict:
    """Every count of one provenance view over all eligible labels: per dimension the scale's counts, coverage
    (decided of eligible) and the rate on decided values (unsupported of supported + unsupported; incorrect of
    correct + incorrect); complete items (fully assessed: policy resolved, work and representation decided and every
    required field decided; fully correct: the subset with work supported, representation compliant and no field
    incorrect); and which labels are unassessed, blocked by policy, or carry a conflict."""
    labels = sorted(rows)
    vals = {l: {d: _decide([e for e in rows[l]["entries"].get(d, []) if view_of(e["role"]) == view]) for d in SCALES} for l in labels}
    dims = {}
    for d, scale in SCALES.items():
        got = [vals[l][d][0] for l in labels]
        decided = sum(v in DECIDED[d] for v in got)
        bad = "unsupported" if d == "work" else "noncompliant" if d == "representation" else "incorrect"
        dims[d] = {v: got.count(v) for v in scale} | {
            "eligible": len(labels), "coverage": rate(decided, len(labels)),
            f"{bad}_rate": rate(got.count(bad), got.count(bad) + got.count(DECIDED[d][0])),
            f"{bad}_labels": [l for l in labels if vals[l][d][0] == bad]}
    full = [l for l in labels if rows[l]["policy"] == "resolved" and all(vals[l][d][0] in DECIDED[d] for d in SCALES)]
    right = [l for l in full if vals[l]["work"][0] == "supported" and vals[l]["representation"][0] == "compliant"
             and all(vals[l][f][0] != "incorrect" for f in QUALITY_FIELDS)]
    roles: dict[str, int] = {}
    for l in labels:
        for entries in rows[l]["entries"].values():
            for e in entries:
                if view_of(e["role"]) == view:
                    roles[e["role"]] = roles.get(e["role"], 0) + 1
    return {"eligible": len(labels), "work": dims["work"], "representation": dims["representation"],
            "fields": {f: dims[f] for f in QUALITY_FIELDS},
            "complete_items": {"eligible": len(labels), "fully_assessed": len(full), "fully_correct": len(right),
                               "not_fully_assessed": len(labels) - len(full), "fully_correct_rate": rate(len(right), len(full)),
                               "fully_assessed_labels": full, "fully_correct_labels": right,
                               "policy_blocked": [l for l in labels if rows[l]["policy"] != "resolved"]},
            "unassessed_labels": [l for l in labels if all(vals[l][d][0] == "not_assessed" for d in SCALES)],
            "values": {l: {d: v for d, (v, _) in vals[l].items() if v != "not_assessed"} for l in labels},
            "conflicts": {l: {d: c for d, (_, c) in vals[l].items() if c} for l in labels if any(c for _, c in vals[l].values())},
            "judgments_by_role": roles}


def assessment_records(raw: list[dict], role: str, stage: str) -> list[dict]:
    """A reviewer's per-label judgments ({label, work: {value, basis}, representation: {...}, fields: {owner: {...}, ...},
    policy, source}) as contract assessment records: each judged dimension gets one provenance entry naming this role,
    stage, basis and value; a dimension left out or given not_assessed stays not_assessed."""
    out = []
    for r in raw:
        dims = {d: r.get(d) for d in ("work", "representation")} | dict(r.get("fields") or {})
        rec = {"label": r["label"], "fields": {}, "provenance": {}, "policy": r.get("policy") or "resolved", "source": r.get("source")}
        for d, j in dims.items():
            if j is None:
                continue
            if not isinstance(j, dict):
                raise ValueError(f"{r['label']}: {d} must be {{value, basis}}, got {j!r}")
            if j.get("value") in (None, "not_assessed"):
                continue
            if d in ("work", "representation"):
                rec[d] = j["value"]
            else:
                rec["fields"][d] = j["value"]
            rec["provenance"][d] = [{"role": role, "stage": stage, "basis": j.get("basis") or "", "value": j["value"]}]
        out.append(rec)
    return out


def label_quality(assessments: list[dict], labels: list[str]) -> dict:
    """Label quality from assessment records, one summary per provenance view (initial adjudication, explicit
    independent review, retrospective rulings), every count over all eligible labels. Absence of criticism is not
    validation: a dimension nobody in a view judged is not_assessed there, and a judgment from another view is no
    evidence in this one (a reviewer assessing a date says nothing about the work claim). Records of one label are
    merged by its id; conflicting values in a view stay unresolved until an entry's `resolution` links them. A rate
    with nothing decided is "unknown (0 assessed)". Schema label-quality/3: the per-view shape replaced v2's single
    pooled summary, whose `independent` mixed review and retrospective rulings."""
    rows: dict[str, dict] = {l: {"entries": {}, "policy": "resolved", "source": None, "records": 0} for l in labels}
    inherited, outside = 0, set()
    for a in assessments:
        entries, n = _entries(a)
        if a["label"] not in rows:
            outside.add(a["label"])
            continue
        inherited += n
        r = rows[a["label"]]
        r["records"] += 1
        for d, es in entries.items():
            r["entries"].setdefault(d, []).extend(es)
        if a.get("policy") == "policy_unresolved":
            r["policy"] = "policy_unresolved"
        r["source"] = r["source"] or a.get("source")
    return {"schema": "label-quality/3", "eligible": len(rows), "labels": sorted(rows),
            "views": {v: _view(rows, v) for v in VIEWS},
            "policy_unresolved": sorted(l for l, r in rows.items() if r["policy"] == "policy_unresolved"),
            "source_limited": sorted(l for l, r in rows.items() if r["source"]),
            "merged_records": sorted(l for l, r in rows.items() if r["records"] > 1),
            "outside_labels": sorted(outside),
            "inherited_values": inherited}


AGENT_ERROR = re.compile(r"InputValidationError|invalid (argument|parameter|input)|is required|unexpected (keyword|parameter)"
                         r"|Traceback \(most recent call last\)|\bsyntax error\b|SyntaxError", re.I)   # incl. the agent's own script crashing
TRANSIENT = re.compile(r"overloaded|rate.?limit|timed? ?out|ECONN|\b(429|500|502|503|529)\b|temporar", re.I)


def classify_calls(calls: list) -> dict[str, int]:
    """Failed tool calls by cause: the same call repeated after it failed (avoidable repetition), a schema or argument
    error (avoidable agent error), a transient service failure (recovery overhead), else unclassified."""
    out = {"avoidable_agent_error": 0, "avoidable_repetition": 0, "recovery_overhead": 0, "unclassified": 0,
           "succeeded": 0, "failed": 0}
    failed: set[tuple[str, str]] = set()
    for c in calls:
        if not c.is_error:
            out["succeeded"] += 1
            continue
        out["failed"] += 1
        sig = (c.name, json.dumps(c.input, sort_keys=True))
        kind = ("avoidable_repetition" if sig in failed else "avoidable_agent_error" if AGENT_ERROR.search(c.output)
                else "recovery_overhead" if TRANSIENT.search(c.output) else "unclassified")
        out[kind] += 1
        failed.add(sig)
    return out


def role_telemetry(role_files: dict[str, Path], meeting_words: dict[str, int]) -> dict:
    """Per pilot role, from its subagent transcript through trajectory's parser: the model the transcript names, model
    and tool calls, tokens, elapsed seconds, failed calls by cause. A missing transcript is unknown, never zero; cost
    stays unknown without a recorded price table."""
    import trajectory as TJ
    roles, spans = {}, []
    for role, f in role_files.items():
        if not Path(f).exists():
            roles[role] = {"telemetry": "unknown: no transcript"}
            continue
        run = TJ.Run(session_id=role)
        TJ._agent_file(Path(f), role, run)
        a = run.agents[0]
        calls = classify_calls(run.calls)
        failed = calls["failed"]
        spans.append((run.started, run.ended))
        roles[role] = {"model": a.model, "model_calls": a.turns, "tool_calls": len(run.calls),
                       "tokens": {"input": a.input_tokens, "output": a.output_tokens, "cache_read": a.cache_read_tokens,
                                  "cache_write": a.cache_write_tokens},
                       "elapsed_s": TJ.seconds(run.started, run.ended) if run.started and run.ended else None,
                       "meeting_words": meeting_words.get(role),
                       "unknown": [],
                       "calls": calls, "tool_error_rate": {"n": failed, "of": len(run.calls)},
                       "avoidable_call_rate": {"n": calls["avoidable_agent_error"] + calls["avoidable_repetition"], "of": len(run.calls)},
                       "classification_coverage": {"n": failed - calls["unclassified"], "of": failed},
                       "cost": {"actual_usd": None, "estimated_usd": None, "why": "no price table is recorded for these models: unknown, not zero"}}
        roles[role]["unknown"] = sorted(k for k in ("model", "elapsed_s", "meeting_words") if roles[role][k] is None)
    known = len(spans) == len(role_files) and spans and all(a and b for a, b in spans)
    wall = TJ.seconds(min(a for a, _ in spans), max(b for _, b in spans)) if known else None
    return {"roles": roles, "wall_clock_s": wall,
            "note": "evaluation overhead only (the labelling pilot), never Taska production execution"}


READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
SHELL_TOOLS = ("Bash",)


def access_audit(calls: list, permitted_reads: set[str], permitted_writes: set[str]) -> dict:
    """Each file a role's tools touched, against what its instruction permitted: a Read/Glob/Grep path is
    `verified` inside the permitted set or `outside` it; a Write/Edit path is a permitted output or `outside`; a shell
    call is `unverified`, because what a shell command read cannot be known from the call. Nothing is inferred
    from what a role said it did."""
    reads, writes, shell, other = {"verified": [], "outside": []}, {"permitted": [], "outside": []}, [], []
    for c in calls:
        path = c.input.get("file_path") or c.input.get("path") or c.input.get("notebook_path") or ""
        if c.name in READ_TOOLS:
            reads["verified" if path in permitted_reads else "outside"].append(path or c.input.get("pattern", ""))
        elif c.name in WRITE_TOOLS:
            writes["permitted" if path in permitted_writes else "outside"].append(path)
        elif c.name in SHELL_TOOLS:
            shell.append(c.input.get("command", "")[:300])
        else:
            other.append(c.name)
    return {"reads": {k: sorted(set(v)) for k, v in reads.items()}, "writes": {k: sorted(set(v)) for k, v in writes.items()},
            "shell_unverified": shell, "other_tools": sorted(set(other)),
            "verdict": "outside" if reads["outside"] or writes["outside"] else "unverified" if shell else "verified"}


TIMEOUT = re.compile(r"timed? ?out|timeout", re.I)
HANDBACK = ("SubagentHandback",)   # a subagent's final report, delivered as a tool call


def role_operations(transcript: Path) -> dict:
    """Completion, retries and timeouts from a role's recorded events, never from its reply: `completed` when every
    tool call got a result and the role ended, either with a last assistant turn of text and no tool call or with a
    hand-back call (HANDBACK) that succeeded as its last call (`ended_by`); a retry is a call repeating
    an earlier failed call (same tool, same input); a timeout is a failed result that says so. A missing transcript
    makes every value unknown."""
    if not Path(transcript).exists():
        return {"completed": "unknown", "retries": "unknown", "timeouts": "unknown", "why": "no transcript"}
    import trajectory as TJ
    recs = TJ._records(Path(transcript))
    run = TJ.Run(session_id="role")
    TJ._agent_file(Path(transcript), "role", run)
    last = next((r for r in reversed(recs) if r.get("type") == "assistant" and isinstance(r.get("message"), dict)), None)
    blocks = (last or {}).get("message", {}).get("content") or []
    ends_in_text = any(isinstance(b, dict) and b.get("type") == "text" and str(b.get("text", "")).strip() for b in blocks) and \
        not any(isinstance(b, dict) and b.get("type") == "tool_use" for b in blocks)
    unanswered = [c.name for c in run.calls if not c.end]
    failed, retries = set(), 0
    for c in run.calls:
        sig = (c.name, json.dumps(c.input, sort_keys=True))
        retries += sig in failed
        if c.is_error:
            failed.add(sig)
    timeouts = sum(c.is_error and bool(TIMEOUT.search(c.output)) for c in run.calls)
    handed = bool(run.calls) and run.calls[-1].name in HANDBACK and bool(run.calls[-1].end) and not run.calls[-1].is_error
    ended_by = "handback" if handed else "text" if last is not None and ends_in_text else None
    return {"completed": ended_by is not None and not unanswered, "ended_by": ended_by, "unanswered_calls": unanswered,
            "retries": retries, "timeouts": timeouts, "failed_calls": sum(c.is_error for c in run.calls), "tool_calls": len(run.calls)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--labels", required=True)
    v.add_argument("--inputs", required=True)
    u = sub.add_parser("union")
    u.add_argument("--a", required=True)
    u.add_argument("--b", required=True)
    u.add_argument("--out", required=True)
    u.add_argument("--seed", type=int, default=20261104)
    k = sub.add_parser("check", help="every candidate disposed; the facts in a reviewer's findings; the counts")
    k.add_argument("--union", required=True)
    k.add_argument("--resolutions", required=True)
    k.add_argument("--provisional", required=True)
    k.add_argument("--findings", help="a reviewer's findings.json")
    k.add_argument("--inputs", required=True)
    k.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--labels", required=True)
    c.add_argument("--key", required=True)
    c.add_argument("--evidence", action="append", default=[], help="repeatable: only these meetings of the key")
    c.add_argument("--resolutions", help="the adjudicator's resolutions.json: merged duplicates count for the kept label")
    c.add_argument("--union", help="the union.json the resolutions refer to (the merged candidates' full quotes)")
    c.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    if a.cmd == "validate":
        res = validate(read(a.labels), Path(a.inputs))
        bad = [r for r in res if r["problems"]]
        print(json.dumps({"labels": len(res), "invalid": bad}, ensure_ascii=False, indent=1))
        return 1 if bad else 0
    if a.cmd == "union":
        doc = union(read(a.a), read(a.b), a.seed)
        found = doc.pop("found_by")   # who found what stays with the orchestrator: the blinded file never names a reader
        Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        Path(a.out).with_suffix(".found_by.json").write_text(json.dumps(found, indent=1), encoding="utf-8")
        print(json.dumps(doc["counts"] | {"sample": doc["agreement_sample"]}))
        return 0
    if a.cmd == "check":
        disp = dispositions(read(a.union), read(a.resolutions))
        claims = check_claims(read(a.findings)["findings"] if a.findings else [], read(a.union), read(a.resolutions),
                              read(a.provisional), Path(a.inputs))
        doc = {"dispositions": disp, **claims}
        Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        bad = disp["problems"] or not claims["counts"]["match"]
        print(json.dumps({"disposition_problems": disp["problems"], "counts": claims["counts"],
                          "claims_rejected": [{"id": f.get("id"), "targets": [t["id"] for t in f["targets"]]}
                                              for f in claims["findings"] if f["factual"] == "rejected"]}))
        return 1 if bad else 0
    _, key = ES.load_gold(a.key)
    key = [g for g in key if not a.evidence or g["evidence"] in a.evidence]
    doc = compare(read(a.labels), key, read(a.resolutions) if a.resolutions else None, read(a.union) if a.union else None)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: doc[k] for k in ("key_items", "covered", "kind_same", "commitment_same")} | {"labels_not_in_key": len(doc["labels_not_in_key"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
