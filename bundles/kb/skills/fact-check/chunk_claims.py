"""Turn stage-E claim files into stage-V verify chunks for /kb:fact-check (spec §15).

    python chunk_claims.py <dir> [--scope all|risk] [--chunk-size 8]

<dir> is the directory sections.py wrote. For every batch k in batches.json it reads
claims_batch_<k>.json (claims of that batch, written by stage E), fills each claim's `risk` from the
batch file by `s_id` (never trusted from the model), keeps the claims in scope (`all`, or `risk` =
non-empty risk), and writes chunk_<j>.json ({chunk, scope, claims}) of at most --chunk-size claims
in document order, plus chunks.json ([{chunk, file, claim_ids}]). Exits 1 and lists every problem
(missing/malformed file, claim id not prefixed B<k>-, duplicate claim id, s_id not in its batch, a section
of a batch with neither a claim nor a `coverage_batch_<k>.json` entry, a section entry for a section that has
claims, and, with --scope risk, a risk-tagged sentence that is no claim's s_id/s_ids and has no waiver).
A claim may list every sentence it spans in an optional `s_ids` (anchor `s_id` included). It deletes the stale chunk_*.json and
findings_chunk_*.json first: after re-running a batch's stage E, every chunk is verified again.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fact_check_invariants import COVERAGE_REASON, _norm, section_coverage


def _load(path: Path, errors: list[str]):
    if not path.exists():
        errors.append(f"{path.name}: missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{path.name}: malformed JSON ({e})")
        return None


def _risk_by_s_id(batch: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sec in batch.get("sections", []) or []:
        for p in sec.get("paragraphs", []) or []:
            for s in p.get("sentences", []) or []:
                if isinstance(s, dict) and s.get("s_id"):
                    out[s["s_id"]] = list(s.get("risk", []))
        for t in sec.get("tables", []) or []:
            for r in t.get("rows", []) or []:
                key = r.get("s_id") or r.get("r_id") if isinstance(r, dict) else None
                if key:
                    out[key] = list(r.get("risk", []))
    return out


def risk_coverage_errors(k: int, batch: dict, claims: list, cov: list, cname: str, scope: str) -> list[str]:
    """Per-statement coverage for batch k; the one copy, called by chunk_claims.py (before stage V) and by
    merge_findings.py. Always validates the batch's waivers (`{s_id, reason: "no checkable statement"}`, the
    s_id a real one of the batch). In scope `risk`, every risk-tagged sentence or row must also be the `s_id`
    of a claim, listed in a claim's `s_ids`, waived, or inside a section a coverage entry covers."""
    errors: list[str] = []
    risk = _risk_by_s_id(batch)
    waived: set[str] = set()
    covered_sections: set[str] = set()
    for c in cov:
        if not isinstance(c, dict):
            continue
        if c.get("section_id"):
            if isinstance(c["section_id"], str):
                covered_sections.add(c["section_id"])
        elif c.get("s_id"):
            if not isinstance(c["s_id"], str):
                errors.append(f"{cname}: waiver s_id must be a string, got {c['s_id']!r}")
            elif _norm(str(c.get("reason", ""))) != COVERAGE_REASON:
                errors.append(f"{cname}: waiver for {c['s_id']!r}: reason {c.get('reason')!r} is not {COVERAGE_REASON!r}")
            elif c["s_id"] not in risk:
                errors.append(f"{cname}: waiver s_id {c['s_id']!r} is not a sentence or row of batch {k}")
            else:
                waived.add(c["s_id"])
    if scope != "risk":
        return errors
    claimed: set[str] = set()
    for c in claims:
        if isinstance(c, dict):
            if isinstance(c.get("s_id"), str):
                claimed.add(c["s_id"])
            if isinstance(c.get("s_ids"), list):
                claimed.update(x for x in c["s_ids"] if isinstance(x, str))
    for sec in batch.get("sections", []) or []:
        if not isinstance(sec, dict) or sec.get("section_id") in covered_sections:
            continue
        for s_id, tags in _risk_by_s_id({"sections": [sec]}).items():
            if tags and s_id not in claimed and s_id not in waived:
                errors.append(f"batch {k}: high-risk statement {s_id} ({', '.join(tags)}) in section "
                              f"{sec.get('section')!r} is neither claimed (anchor s_id or listed in a claim's s_ids) "
                              f"nor waived with {{s_id, reason: {COVERAGE_REASON!r}}} in {cname}")
    return errors


def chunk(d: Path, scope: str = "all", size: int = 8) -> tuple[list[dict], list[str]]:
    if scope not in ("all", "risk"):
        raise ValueError(f"scope must be 'all' or 'risk', got {scope!r}")
    # A failed run must leave no chunk files behind, so clear them before validating.
    # Stage-V findings belong to the chunks being replaced; stale ones would be merged against new chunks.
    for stale in (*d.glob("chunk_*.json"), *d.glob("findings_chunk_*.json")):
        stale.unlink()
    (d / "chunks.json").unlink(missing_ok=True)
    errors: list[str] = []
    index = _load(d / "batches.json", errors)
    if index is None:
        index = []
    elif not isinstance(index, list):
        errors.append("batches.json: must be a top-level JSON list")
        index = []
    claims: list[dict] = []
    seen: set[str] = set()
    for b in index:
        k = b.get("batch") if isinstance(b, dict) else None
        if not isinstance(k, int) or isinstance(k, bool):
            errors.append(f"batches.json: record {b!r} has no integer 'batch'")
            continue
        batch = _load(d / f"batch_{k}.json", errors)
        found = _load(d / f"claims_batch_{k}.json", errors)
        if batch is None or found is None:
            continue
        if not isinstance(batch, dict):
            errors.append(f"batch_{k}.json: must be a JSON object")
            continue
        if not isinstance(found, list):
            errors.append(f"claims_batch_{k}.json: must be a top-level JSON list")
            continue
        risk = _risk_by_s_id(batch)
        cname = f"coverage_batch_{k}.json"
        cov = _load(d / cname, errors) if (d / cname).exists() else []
        if not isinstance(cov, list):
            if cov is not None:
                errors.append(f"{cname}: must be a top-level JSON list")
            cov = []
        if isinstance(b.get("sections"), list):
            _, cov_errors = section_coverage(k, b["sections"], [c for c in found if isinstance(c, dict)], cov, cname)
            errors.extend(cov_errors)
        errors.extend(risk_coverage_errors(k, batch, found, cov, cname, scope))
        for c in found:
            if not isinstance(c, dict):
                errors.append(f"claims_batch_{k}.json: item {c!r} is not a JSON object")
                continue
            cid = str(c.get("claim_id", ""))
            if not cid.startswith(f"B{k}-"):
                errors.append(f"claims_batch_{k}.json: {cid or '(no claim_id)'}: claim_id must start with B{k}-")
                continue
            if cid in seen:
                errors.append(f"claims_batch_{k}.json: {cid}: duplicate claim_id")
                continue
            sid = c.get("s_id")
            if not isinstance(sid, str) or not sid:
                errors.append(f"claims_batch_{k}.json: {cid}: missing s_id")
                continue
            if sid not in risk:
                errors.append(f"claims_batch_{k}.json: {cid}: s_id {sid!r} is not a sentence or row of batch {k}")
                continue
            extra = c.get("s_ids")
            if extra is not None:
                if not isinstance(extra, list):
                    errors.append(f"claims_batch_{k}.json: {cid}: s_ids must be a list of s_id strings")
                    continue
                bad = [x for x in extra if not isinstance(x, str) or x not in risk]
                if bad:
                    errors.append(f"claims_batch_{k}.json: {cid}: s_ids {bad!r} are not sentences or rows of batch {k}")
                    continue
            seen.add(cid)
            claims.append({**c, "risk": risk[sid]})
    if errors:
        return [], errors
    in_scope = [c for c in claims if scope == "all" or c["risk"]]
    chunks = [in_scope[i:i + size] for i in range(0, len(in_scope), size)]
    out = []
    for j, cs in enumerate(chunks, 1):
        name = f"chunk_{j}.json"
        (d / name).write_text(json.dumps({"chunk": j, "scope": scope, "claims": cs}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out.append({"chunk": j, "file": name, "claim_ids": [c["claim_id"] for c in cs]})
    (d / "chunks.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out, []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", help="the directory sections.py wrote, holding claims_batch_<k>.json")
    ap.add_argument("--scope", choices=("all", "risk"), default="all", help="all (Deep) or risk (Fast)")
    ap.add_argument("--chunk-size", type=int, default=8, help="claims per verify chunk (default 8)")
    a = ap.parse_args(argv)
    if a.chunk_size < 1:
        print("chunk_claims.py: --chunk-size must be at least 1", file=sys.stderr)
        return 2
    index, errors = chunk(Path(a.dir), a.scope, a.chunk_size)
    if errors:
        print(f"chunk_claims.py: {len(errors)} problem(s), no chunks written:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"{sum(len(c['claim_ids']) for c in index)} claims in scope ({a.scope}) -> {len(index)} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
