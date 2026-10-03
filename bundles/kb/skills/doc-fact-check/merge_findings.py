"""Merge the fact-check stage-E claims and stage-V chunk findings into findings.json, coverage.json and run.json.

    python merge_findings.py <dir> --out <findings.json> [--scope all|risk] [--wave-size 10]

<dir> is the directory `sections.py` wrote. It must hold `batches.json` and `stats.json`
(sections.py), `claims_batch_<k>.json` for every batch k (stage E: a list of
{claim_id: "B<k>-C<n>", p_id, s_id, section, quote, type}), optionally `coverage_batch_<k>.json`
(a list of {section_id, reason: "no checkable statement"}), `chunks.json`
(chunk_claims.py: [{chunk, file, claim_ids}]) and `findings_chunk_<j>.json` for every chunk j
(stage V: a top-level list in the step-9 schema whose `id` is the claim's claim_id). An optional
`claims_figures.json` (the main session's figure and embedded-object claims, `I*` ids) is read too and its claims
are verified in stage V like any other. A `findings_figures.json` is an error: figure findings come from the chunks.

--scope all (Deep) or risk (Fast) is recorded in run.json; in risk mode the sections with no
in-scope claim are listed in coverage.json with reason "no high-risk statement".

Writes <findings.json> (one finding per in-scope claim in stage-E order, renumbered C01, C02, ...,
figure `I*` ids kept, after the text findings), coverage.json beside it ({heading, section, reason}) and run.json beside it
(mode, scope, counts, skill_version).

Fails (exit 1, nothing written) and lists every problem when an input is missing or malformed, a
finding breaks the step-9 schema, an in-scope claim has no finding (or has two), a finding names a
claim outside its chunk or changes its claim's section, quote or p_id, a section has neither a claim nor a
coverage entry, --scope differs from the chunk files' scope, or (risk) chunks.json differs from the batch files'
risk tags or a risk-tagged statement has neither a claim nor a section coverage entry. Figure (`I*`) findings may
also carry `anchor` and `figure`; batch_<k>.json files are read in risk mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from chunk_claims import FIGURES_CLAIMS_FILE, _risk_by_s_id, risk_coverage_errors
from fact_check_invariants import (COVERAGE_REASON, RISK_COVERAGE_REASON, FIGURE_KEYS, FINDING_KEYS, LATE_KEYS,
                                   SEVERITIES, VERDICTS, _last_heading, section_coverage, section_key)

REQUIRED = tuple(k for k in FINDING_KEYS if k not in LATE_KEYS)
OLD_FIGURES_FILE = "findings_figures.json"
SKILL_MD = Path(__file__).with_name("SKILL.md")


def _load_list(path: Path, errors: list[str], *, required: bool) -> list | None:
    if not path.exists():
        if required:
            errors.append(f"{path.name}: missing")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{path.name}: malformed JSON ({e})")
        return None
    if not isinstance(data, list):
        errors.append(f"{path.name}: must be a top-level JSON list, got {type(data).__name__}")
        return None
    return data


def _check_finding(f, where: str, errors: list[str]) -> str | None:
    """Validate one finding against the step-9 schema; return its id (or None if unusable)."""
    if not isinstance(f, dict):
        errors.append(f"{where}: a finding must be an object, got {type(f).__name__}")
        return None
    fid = str(f.get("id") or "")
    label = f"{where}: {fid or '(no id)'}"
    missing = [k for k in REQUIRED if k not in f]
    if missing:
        errors.append(f"{label}: missing key(s) {', '.join(missing)}")
    allowed = FINDING_KEYS + (FIGURE_KEYS if fid.startswith("I") else ())
    extra = sorted(k for k in f if k not in allowed)
    if extra:
        errors.append(f"{label}: unknown key(s) {', '.join(extra)} (step 9 allows exactly {', '.join(allowed)})")
    if not str(f.get("section") or "").strip():
        errors.append(f"{label}: empty section")
    if "verdict" in f and f["verdict"] not in VERDICTS:
        errors.append(f"{label}: unknown verdict {f['verdict']!r}")
    if f.get("verdict") != "Verified" and "severity" in f and f["severity"] not in SEVERITIES:
        errors.append(f"{label}: unknown severity {f['severity']!r}")
    if "sources" in f and not isinstance(f["sources"], list):
        errors.append(f"{label}: sources must be a list of {{name, link, folder}}")
    return fid or None


def _load_obj(path: Path, errors: list[str]) -> dict | None:
    if not path.exists():
        errors.append(f"{path.name}: missing")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{path.name}: malformed JSON ({e})")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path.name}: must be a JSON object")
        return None
    return data


def _check_risk_scope(d: Path, index: list, claims: dict[str, dict], chunked: set[str],
                      errors: list[str]) -> None:
    """Fast mode, from the batch files' own risk tags (never the model's): chunks.json holds exactly the
    risk-tagged claims, and every risk-tagged statement is covered (risk_coverage_errors, the helper
    chunk_claims.py already ran before stage V)."""
    expected: set[str] = set()
    for b in index:
        k = b.get("batch") if isinstance(b, dict) else None
        if not isinstance(k, int) or not isinstance(b.get("sections"), list):
            continue
        batch = _load_obj(d / f"batch_{k}.json", errors)
        if batch is None:
            continue
        risk = _risk_by_s_id(batch)
        expected |= {cid for cid, c in claims.items() if cid.startswith(f"B{k}-") and risk.get(c.get("s_id"))}
        cname = f"coverage_batch_{k}.json"
        errors.extend(risk_coverage_errors(k, batch, [c for c in claims.values() if str(c.get("claim_id", "")).startswith(f"B{k}-")],
                                           _load_list(d / cname, [], required=False) or [], cname, "risk"))
    if expected != chunked:
        missing, extra = sorted(expected - chunked), sorted(chunked - expected)
        errors.append("chunks.json: in-scope claims differ from the batch files' risk tags "
                      f"(risk-tagged but not chunked: {', '.join(missing) or 'none'}; chunked but not risk-tagged: "
                      f"{', '.join(extra) or 'none'}); re-run chunk_claims.py --scope risk")


def merge(d: Path, scope: str = "all", wave_size: int = 10) -> tuple[list[dict], list[dict], dict, list[str]]:
    errors: list[str] = []
    index = _load_list(d / "batches.json", errors, required=True)
    chunks = _load_list(d / "chunks.json", errors, required=True)
    stats = _load_obj(d / "stats.json", errors)
    if index is None or chunks is None or stats is None:
        return [], [], {}, errors

    # Stage E: every section of every batch has a claim or a coverage entry.
    claims: dict[str, dict] = {}
    coverage: list[dict] = []
    seen_cov: set[str] = set()
    for b in index:
        k = b.get("batch") if isinstance(b, dict) else None
        if not isinstance(k, int) or not isinstance(b.get("sections"), list):
            errors.append(f"batches.json: malformed batch record {b!r}")
            continue
        cname = f"coverage_batch_{k}.json"
        found = _load_list(d / f"claims_batch_{k}.json", errors, required=True)
        cov = _load_list(d / cname, errors, required=False) or []
        kept: list[dict] = []
        for c in found or []:
            if not isinstance(c, dict):
                errors.append(f"claims_batch_{k}.json: a claim must be an object, got {type(c).__name__}")
                continue
            cid = c.get("claim_id")
            if not isinstance(cid, str) or not cid.strip():
                errors.append(f"claims_batch_{k}.json: a claim has a missing or empty claim_id")
                continue
            if not cid.startswith(f"B{k}-"):
                errors.append(f"claims_batch_{k}.json: {cid}: claim_id must start with B{k}- (ids are prefixed by their batch)")
            if cid in claims:
                errors.append(f"claims_batch_{k}.json: {cid}: duplicate claim_id")
                continue
            claims[cid] = c
            kept.append(c)
        recs: list[dict] = []
        if found is not None:
            recs, cov_errors = section_coverage(k, b["sections"], kept, cov, cname)
            errors.extend(cov_errors)
            for r in recs:
                if section_key(r["section"]) not in seen_cov:
                    seen_cov.add(section_key(r["section"]))
                    coverage.append(r)

    if (d / OLD_FIGURES_FILE).exists():
        errors.append(f"{OLD_FIGURES_FILE}: no longer read; write the figure claims to {FIGURES_CLAIMS_FILE} "
                      f"(step 3) and let stage V verify them")
    for c in _load_list(d / FIGURES_CLAIMS_FILE, errors, required=False) or []:
        cid = c.get("claim_id") if isinstance(c, dict) else None
        if not isinstance(cid, str) or not cid.startswith("I"):
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid or '(no claim_id)'}: claim_id must start with I")
        elif cid in claims:
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: duplicate claim_id")
        else:
            claims[cid] = c

    # Stage V: every in-scope claim has exactly one finding, from its own chunk.
    in_scope: list[str] = []
    got: dict[str, dict] = {}
    for ch in chunks:
        j = ch.get("chunk") if isinstance(ch, dict) else None
        if not isinstance(j, int) or isinstance(j, bool) or not isinstance(ch.get("claim_ids"), list):
            errors.append(f"chunks.json: malformed chunk record {ch!r} (needs an int chunk and a claim_ids list)")
            continue
        ids = [str(c) for c in ch["claim_ids"]]
        for cid in ids:
            if cid in in_scope:
                errors.append(f"chunks.json: {cid}: claim is in more than one chunk")
                continue
            in_scope.append(cid)
            if cid not in claims:
                errors.append(f"chunks.json: chunk {j}: {cid} is not a stage-E claim")
        cfile = _load_obj(d / f"chunk_{j}.json", []) if (d / f"chunk_{j}.json").exists() else None
        if cfile is not None and cfile.get("scope") != scope:
            errors.append(f"chunk_{j}.json: chunked with scope {cfile.get('scope')!r} but merging with --scope {scope} "
                          f"(re-run chunk_claims.py with the same scope)")
        fname = f"findings_chunk_{j}.json"
        for f in _load_list(d / fname, errors, required=True) or []:
            fid = _check_finding(f, fname, errors)
            if fid is None:
                continue
            if fid not in ids:
                errors.append(f"{fname}: {fid} is not a claim of chunk {j}")
                continue
            mismatch = False
            if fid in claims:
                for key in ("section", "quote", "p_id"):
                    a, b = f.get(key), claims[fid].get(key)
                    if (section_key(a) != section_key(b)) if key == "section" else (a != b):
                        mismatch = True
                        errors.append(f"{fname}: {fid}: {key} {a!r} differs from its claim's {key} {b!r} "
                                      f"(stage V copies section, quote and p_id unchanged)")
            if fid in claims and fid.startswith("I"):
                want = "paragraph" if claims[fid].get("kind") == "embedded" else "drawing"
                if f.get("anchor") != want:
                    mismatch = True
                    errors.append(f"{fname}: {fid}: anchor {f.get('anchor')!r} must be {want!r} for a "
                                  f"{claims[fid].get('kind', 'figure')} claim")
                if want == "drawing" and f.get("figure") != claims[fid].get("figure"):
                    mismatch = True
                    errors.append(f"{fname}: {fid}: figure {f.get('figure')!r} must equal its claim's figure "
                                  f"{claims[fid].get('figure')!r}")
            if mismatch:
                continue
            if fid in got:
                errors.append(f"{fname}: {fid}: duplicate finding for one claim")
            else:
                got[fid] = f
    if scope == "all":
        for b in index:                                    # waivers are validated in both scopes (the risk path does it itself)
            k = b.get("batch") if isinstance(b, dict) else None
            batch = _load_obj(d / f"batch_{k}.json", []) if isinstance(k, int) and (d / f"batch_{k}.json").exists() else None
            if batch is not None:
                cname = f"coverage_batch_{k}.json"
                errors.extend(risk_coverage_errors(k, batch, [], _load_list(d / cname, [], required=False) or [], cname, "all"))
        listed_ids = set(in_scope)
        for cid in claims:
            if cid not in listed_ids:
                errors.append(f"{cid}: stage-E claim is in no chunk (scope all verifies every claim)")
        in_scope = list(claims)
    for cid in in_scope:
        if cid in claims and cid not in got:
            errors.append(f"{cid}: in-scope claim has no finding (re-run its chunk)")

    if scope == "risk":
        _check_risk_scope(d, index, claims, {c for c in in_scope if not c.startswith("I")}, errors)
        errors.extend(f"{c}: figure claim is in no chunk (figure claims are always in scope)"
                      for c in claims if c.startswith("I") and c not in in_scope)
        has_scope = {section_key(claims[c].get("section")) for c in in_scope if c in claims}
        listed = {section_key(c["section"]) for c in coverage}
        for b in index:
            for s in (b.get("sections", []) if isinstance(b, dict) and isinstance(b.get("sections"), list) else []):
                name = s.get("section", "") if isinstance(s, dict) else ""
                key = section_key(name)
                if name and key not in has_scope and key not in listed:
                    listed.add(key)
                    coverage.append({"heading": _last_heading(name), "section": name, "reason": RISK_COVERAGE_REASON})

    ordered = [got[c] for c in claims if c in got]          # document order = stage-E order
    texts = [f for f in ordered if not str(f.get("id", "")).startswith("I")]
    out = [{**f, "id": f"C{n:02d}"} for n, f in enumerate(texts, 1)] + [f for f in ordered if str(f.get("id", "")).startswith("I")]
    run = {"mode": "deep" if scope == "all" else "fast", "scope": scope, "wave_size": wave_size,
           "sections_total": stats.get("sections_total"), "statements_total": stats.get("statements_total"),
           "statements_risk": stats.get("statements_risk"), "claims_extracted": len(claims),
           "claims_in_scope": len(in_scope), "claims_verified": len(got), "chunks": len(chunks),
           "skill_version": hashlib.sha256(SKILL_MD.read_bytes()).hexdigest()[:12] if SKILL_MD.exists() else ""}
    if stats.get("source_sha256"):
        run["source_sha256"] = stats["source_sha256"]  # the draft as sections.py read it
    return out, coverage, run, errors


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", help="the directory sections.py wrote, holding the stage-E and stage-V files")
    ap.add_argument("--out", required=True, help="path of findings.json (coverage.json and run.json go beside it)")
    ap.add_argument("--scope", choices=("all", "risk"), default="all", help="all (Deep) or risk (Fast)")
    ap.add_argument("--wave-size", type=int, default=10, help="recorded in run.json (default 10)")
    a = ap.parse_args(argv)
    if a.wave_size < 1:
        ap.error("--wave-size must be at least 1")
    findings, coverage, run, errors = merge(Path(a.dir), a.scope, a.wave_size)
    if errors:
        print(f"merge_findings.py: {len(errors)} problem(s), nothing written:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _dump(out, findings)
    _dump(out.with_name("coverage.json"), coverage)
    _dump(out.with_name("run.json"), run)
    print(f"{len(findings)} findings, {len(coverage)} coverage entries, mode {run['mode']} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
