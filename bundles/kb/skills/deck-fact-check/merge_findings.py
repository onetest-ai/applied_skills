"""Merge the fact-check stage-E claims and stage-V chunk findings into findings.json, coverage.json and run.json.

    python merge_findings.py <dir> --out <findings.json> [--scope all|risk] [--wave-size 10] [--comments major|all]

<dir> is the directory the reader (`sections.py` or `pptx_sections.py`) wrote. It must hold `batches.json` and `stats.json`
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
(mode, scope, comments, counts, skill_version, and the timings and workers of an optional `<dir>/timings.json`).

--comments major (default: Blocker and Major defects get a comment) or all is recorded in run.json; a finding
that gets a comment must fit it in MAX_COMMENT_WORDS (the exact comment text annotate writes), else merge names its claim.

Fails (exit 1, nothing written) and lists every problem when an input is missing or malformed, a
finding breaks the step-9 schema (including `conflicts`: Verified with a recorded conflict, or Controversial/Outdated without one), an in-scope claim has no finding (or has two), a finding names a
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

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling imports work under python -I too

import run_clock  # noqa: E402
from chunk_claims import FIGURES_CLAIMS_FILE, FIGURES_DROPPED_FILE, _risk_by_s_id, claim_risk, risk_coverage_errors
from fact_check_invariants import (COVERAGE_REASON, RISK_COVERAGE_REASON, UNVERIFIED_REASON, FIGURE_KEYS, FINDING_KEYS, LATE_KEYS,
                                   CLAIM_ONLY_KEYS, MAX_EVIDENCE_WORDS, SEVERITIES, VERDICTS, COPIED_FROM_CLAIM, _last_heading,
                                   COMMENT_POLICIES, MAX_COMMENT_WORDS, comment_text, comment_words, is_deck_finding, conflicts_problem, wants_comment,
                                   comment_required, why_problem,
                                   evidence_problem, name_check_problem, same_text, section_coverage, section_key)

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


def _check_finding(f, where: str, errors: list[str], comments: str = "major") -> str | None:
    """Validate one finding against the step-9 schema; return its id (or None if unusable)."""
    if not isinstance(f, dict):
        errors.append(f"{where}: a finding must be an object, got {type(f).__name__}")
        return None
    fid = str(f.get("id") or "")
    label = f"{where}: {fid or '(no id)'}"
    # No source links is a valid state (no inventory): default it here so the main session never hand-patches chunk files.
    f.setdefault("sources", [])
    f.setdefault("why", "")          # only a commented defect needs a plain-language why and a suggested sentence
    f.setdefault("suggested", "")
    for k in CLAIM_ONLY_KEYS:   # copied from the claim record; the merge reads them from the claim, never the finding
        f.pop(k, None)
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
    if "evidence" in f and (why := evidence_problem(f["evidence"], is_deck_finding(f))):
        errors.append(f"{label}: {why} (re-dispatch stage V for this chunk exactly as step 1b does, never a fix prompt; the main session never edits findings)")
    if why := name_check_problem(f):
        errors.append(f"{label}: {why} (re-dispatch stage V for this chunk exactly as step 1b does, never a fix prompt; the main session never edits findings)")
    if comment_required(f, comments) and (why := why_problem(f)):
        errors.append(f"{label}: {why} (re-dispatch stage V for this chunk exactly as step 1b does, never a fix prompt; the main session never edits findings)")
    if why := conflicts_problem(f):
        errors.append(f"{label}: {why} (re-dispatch stage V for this chunk exactly as step 1b does, never a fix prompt; the main session never edits findings)")
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
        expected |= {cid for cid, c in claims.items() if cid.startswith(f"B{k}-") and claim_risk(c, risk)}
        cname = f"coverage_batch_{k}.json"
        errors.extend(risk_coverage_errors(k, batch, [c for c in claims.values() if str(c.get("claim_id", "")).startswith(f"B{k}-")],
                                           _load_list(d / cname, [], required=False) or [], cname, "risk"))
    if expected != chunked:
        missing, extra = sorted(expected - chunked), sorted(chunked - expected)
        errors.append("chunks.json: in-scope claims differ from the batch files' risk tags "
                      f"(risk-tagged but not chunked: {', '.join(missing) or 'none'}; chunked but not risk-tagged: "
                      f"{', '.join(extra) or 'none'}); re-run chunk_claims.py --scope risk")


def _looks_unverified(rows: list) -> bool:
    """A stage-V file that was filled without verifying: >= 3 findings, all No Evidence, one shared evidence text."""
    fs = [f for f in rows if isinstance(f, dict)]
    if len(fs) < 3 or any(f.get("verdict") != "No Evidence" for f in fs):
        return False
    return len({" ".join(str(f.get("evidence", "")).split()).casefold() for f in fs}) == 1


def merge(d: Path, scope: str = "all", wave_size: int = 10, comments: str = "major",
          skip_chunks=frozenset()) -> tuple[list[dict], list[dict], dict, list[str]]:
    """skip_chunks: chunks refused twice; their claims are left out and reported in run.json `unverified`."""
    if comments not in COMMENT_POLICIES:
        raise ValueError(f"comments must be one of {', '.join(COMMENT_POLICIES)}, got {comments!r}")
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
    canon = {section_key(s.get("section")): s.get("section") for b in index if isinstance(b, dict)
             for s in (b.get("sections") or []) if isinstance(s, dict)}       # the reader's own section names
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
            if isinstance(c.get("section"), str) and section_key(c["section"]) in canon:
                c["section"] = canon[section_key(c["section"])]   # "Slide 3 > title" is the reader's "Slide 3: title"
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
    try:                                   # figure claims the chunker dropped as repeating the slide's text (spec §10 X3)
        dropped = json.loads((d / FIGURES_DROPPED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dropped = []
    dropped_ids = {x.get("claim_id") for x in dropped if isinstance(x, dict)}
    for c in _load_list(d / FIGURES_CLAIMS_FILE, errors, required=False) or []:
        cid = c.get("claim_id") if isinstance(c, dict) else None
        if cid in dropped_ids:
            continue
        if not isinstance(cid, str) or not cid.startswith("I"):
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid or '(no claim_id)'}: claim_id must start with I")
        elif cid in claims:
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: duplicate claim_id")
        else:
            claims[cid] = c

    # Stage V: every in-scope claim has exactly one finding, from its own chunk.
    in_scope: list[str] = []
    got: dict[str, dict] = {}
    unverified: list[dict] = []
    skipped: set[str] = set()
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
        if j in skip_chunks:                               # refused twice: left out, reported as not verified
            unverified.append({"chunk": j, "claim_ids": ids})
            skipped.update(ids)
            continue
        cfile = _load_obj(d / f"chunk_{j}.json", []) if (d / f"chunk_{j}.json").exists() else None
        if cfile is not None and cfile.get("scope") != scope:
            errors.append(f"chunk_{j}.json: chunked with scope {cfile.get('scope')!r} but merging with --scope {scope} "
                          f"(re-run chunk_claims.py with the same scope)")
        fname = f"findings_chunk_{j}.json"
        rows = _load_list(d / fname, errors, required=True) or []
        if _looks_unverified(rows):
            errors.append(f"{fname}: looks unverified: every finding is No Evidence with the same evidence text "
                          f"(re-dispatch stage V for chunk {j}; the main session never writes findings itself)")
        for f in rows:
            if isinstance(f, dict) and "id" not in f and f.get("claim_id") in claims:   # worker used the claim's key
                f["id"] = f.pop("claim_id")
            if isinstance(f, dict) and f.get("id") in claims:
                for k in COPIED_FROM_CLAIM:
                    f.setdefault(k, claims[f["id"]].get(k))
            fid = _check_finding(f, fname, errors, comments)
            if fid is None:
                continue
            if fid not in ids:
                errors.append(f"{fname}: {fid} is not a claim of chunk {j}")
                continue
            mismatch = False
            if fid in claims:
                if f.get("claim") != claims[fid].get("claim"):
                    f["claim"] = claims[fid].get("claim")   # what was checked is the extract's claim (spec §10 R2)
                if f.get("quote") != claims[fid].get("quote") and same_text(f.get("quote"), claims[fid].get("quote")):
                    f["quote"] = claims[fid].get("quote")   # a re-typed apostrophe or dash is the same quote
                if section_key(f.get("section")) == section_key(claims[fid].get("section")):
                    f["section"] = claims[fid].get("section")
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
        if cid in claims and cid not in got and cid not in skipped:
            errors.append(f"{cid}: in-scope claim has no finding (re-run its chunk)")
    known_chunks = {ch.get("chunk") for ch in chunks if isinstance(ch, dict)}
    errors.extend(f"--skip-chunk {j}: there is no chunk {j}" for j in sorted(set(skip_chunks) - known_chunks))
    if skipped:                                        # a section whose claims were all skipped: named, not uncovered
        done = {section_key(claims[c].get("section")) for c in got if c in claims}
        for key in sorted({section_key(claims[c].get("section")) for c in skipped if c in claims} - done):
            name = canon.get(key) or next(claims[c]["section"] for c in skipped if section_key(claims[c].get("section")) == key)
            if key not in seen_cov:
                seen_cov.add(key)
                coverage.append({"heading": _last_heading(name), "section": name, "reason": UNVERIFIED_REASON})

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
    renamed = {id(f): f"C{n:02d}" for n, f in enumerate(texts, 1)}
    out = []
    for f in texts + [f for f in ordered if str(f.get("id", "")).startswith("I")]:
        g = {**f, "id": renamed.get(id(f), f["id"])}
        # comment_text reads these keys; a missing one is already reported by _check_finding, never a crash here
        if (all(str(g.get(k) or "").strip() for k in ("verdict", "severity", "section", "evidence", "fix", "source")) and wants_comment(g, comments)
                and (n := comment_words(comment_text(g), is_deck_finding(g))) > MAX_COMMENT_WORDS):
            errors.append(f"{f['id']}: comment would be {n} words (limit {MAX_COMMENT_WORDS}); stage V writes "
                          f"comment-ready evidence and fix (re-dispatch its chunk)")
        out.append(g)
    run = {"comments": comments, "mode": "deep" if scope == "all" else "fast", "scope": scope, "wave_size": wave_size,
           "sections_total": stats.get("sections_total"), "statements_total": stats.get("statements_total"),
           "statements_risk": stats.get("statements_risk"), "claims_extracted": len(claims),
           "claims_in_scope": len(in_scope), "claims_verified": len(got), "chunks": len(chunks),
           "skill_version": hashlib.sha256(SKILL_MD.read_bytes()).hexdigest()[:12] if SKILL_MD.exists() else ""}
    if stats.get("source_sha256"):
        run["source_sha256"] = stats["source_sha256"]  # the draft as sections.py read it
    if stats.get("hidden_slides"):
        run["hidden_slides"] = stats["hidden_slides"]  # a deck's hidden slides: not checked, named in the coverage line
    if (stats.get("offscreen") or {}).get("blocks"):
        run["offscreen"] = stats["offscreen"]  # a deck's text that is not on the slide: left out, named in the coverage line
    if (stats.get("chrome") or {}).get("texts"):
        run["chrome"] = stats["chrome"]  # a deck's navigation repeated across slides: left out, named in the coverage line
    if dropped:
        run["dropped_duplicate_figure"] = dropped
    if unverified:
        run["unverified"] = unverified  # chunks refused twice: their claims were not verified, named in the coverage line  # figure claims repeating the slide's text: not verified, named
    tfile = d / "timings.json"
    if tfile.exists():
        try:
            t = json.loads(tfile.read_text(encoding="utf-8"))
            run["timings"], run["workers"] = t.get("timings", {}), t.get("workers", {})
        except (OSError, ValueError, AttributeError):
            errors.append("timings.json: malformed JSON (re-run run_clock.py)")
    return out, coverage, run, errors


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check_chunk(d: Path, j: int, scope: str, comments: str) -> int:
    """The merge's checks for one chunk's findings, nothing written: what a stage-V worker runs on its own file."""
    try:
        ids = next(c["claim_ids"] for c in json.loads((d / "chunks.json").read_text(encoding="utf-8")) if c.get("chunk") == j)
    except (OSError, ValueError, StopIteration, KeyError, AttributeError):
        print(f"chunk {j}: not in chunks.json")
        return 2
    _, _, _, errors = merge(d, scope, comments=comments)
    mine = [e for e in errors if e.startswith((f"findings_chunk_{j}.json", f"chunk_{j}.json"))
            or any(e.startswith(f"{cid}:") for cid in ids)]
    if not mine:
        print(f"chunk {j}: OK")
        return 0
    print(f"chunk {j}: {len(mine)} problem(s) in findings_chunk_{j}.json; fix them in that file and run this again:")
    for e in mine:
        print(f"  - {e.split(' (re-dispatch')[0]}")
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", help="the directory the reader (sections.py or pptx_sections.py) wrote, holding the stage-E and stage-V files")
    ap.add_argument("--out", help="path of findings.json (coverage.json and run.json go beside it); not with --check-chunk")
    ap.add_argument("--scope", choices=("all", "risk"), default="all", help="all (Deep) or risk (Fast)")
    ap.add_argument("--wave-size", type=int, default=10, help="recorded in run.json (default 10)")
    ap.add_argument("--comments", choices=COMMENT_POLICIES, default="major", help="major (Blocker+Major, default) or all")
    ap.add_argument("--check-chunk", type=int, metavar="J", help="a stage-V worker's self-check: list only chunk J's "
                    "problems and write nothing (exit 0 and 'chunk J: OK' when there are none)")
    ap.add_argument("--clock", metavar="WORK_DIR", help="record this step's start and end in <work dir>/timings.json")
    ap.add_argument("--skip-chunk", type=int, action="append", default=[], metavar="J",
                    help="leave out a chunk the merge refused twice; its claims are reported as not verified")
    a = ap.parse_args(argv)
    if a.wave_size < 1:
        ap.error("--wave-size must be at least 1")
    if a.check_chunk is not None:
        return check_chunk(Path(a.dir), a.check_chunk, a.scope, a.comments)
    if not a.out:
        ap.error("--out is required (except with --check-chunk)")
    if a.clock:
        run_clock.stamp(a.clock, "merge", "start")
    findings, coverage, run, errors = merge(Path(a.dir), a.scope, a.wave_size, a.comments, set(a.skip_chunk))
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
    if a.clock:
        run_clock.stamp(a.clock, "merge", "end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
