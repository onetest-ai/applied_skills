"""Prepare and apply the per-slide comment reviews (step 6d) to findings.json: deck-fact-check only.

    python apply_slide_review.py <run dir>/findings.json --run <run dir>/run.json --prepare <work dir>
    python apply_slide_review.py <run dir>/findings.json --run <run dir>/run.json <work dir>/slide_review_<n>.json ...

--prepare writes <work dir>/slide_findings_<n>.json for every slide with at least one commented finding: each of the
slide's findings with the fields the reviewer judges by (PREPARE_FIELDS) and `commented`; it prints {"slides": [n…]}.

A slide review sees one slide's image, its context and its findings, and says, for each finding that would get a
comment, one of: keep; duplicate_of <id> (the same root fact as another finding on the slide); minor (a simplification
or wording the author need not act on); verified_in_context (the finding's own evidence supports the statement within
the slide's scenario). Each entry needs a one-sentence reason. This script applies them deterministically and can only
lower: duplicate_of and minor set the severity to Minor; verified_in_context sets the verdict to Verified, the severity
to Minor and the fix to "None." — except on a finding with recorded conflicts, which is never Verified: there it is
applied as minor (logged with "requested": "verified_in_context"). It never adds a finding, raises a severity or touches
a finding without a comment.
Every change is appended to the finding's trail ("Slide review: …") and listed in run.json `slide_review`. Any
malformed entry, unknown id or other action refuses the whole set: nothing is written (exit 2).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling imports work under python -I too

from fact_check_invariants import wants_comment  # noqa: E402

ACTIONS = ("keep", "duplicate_of", "minor", "verified_in_context")
PREPARE_FIELDS = ("id", "p_id", "quote", "claim", "verdict", "severity", "evidence", "conflicts", "fix")


def _slide(p_id) -> int | None:
    m = re.fullmatch(r"s(\d+)(?:-.*)?", str(p_id or ""))
    return int(m.group(1)) if m else None


def _write(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def prepare(findings_path, run_path, work_dir) -> list[int]:
    """Write slide_findings_<n>.json for each slide with a commented finding; returns those slide numbers."""
    findings = json.loads(Path(findings_path).read_text(encoding="utf-8"))
    run_path = Path(run_path)
    policy = (json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}).get("comments", "major")
    by_slide: dict[int, list[dict]] = {}
    for f in findings:
        n = _slide(f.get("p_id"))
        if n is not None:
            by_slide.setdefault(n, []).append({**{k: f.get(k) for k in PREPARE_FIELDS}, "commented": wants_comment(f, policy)})
    slides = sorted(n for n, rows in by_slide.items() if any(r["commented"] for r in rows))
    for n in slides:
        _write(Path(work_dir) / f"slide_findings_{n}.json", by_slide[n])
    return slides


def slide_images(work_dir, slides) -> dict[str, str]:
    """slide number -> its page image, relative to the work dir. Hidden slides are not in the PDF, so slide N is page N minus the
    hidden slides before it (with slide 2 hidden, p02 is slide 3); a name built from the slide number shows the wrong slide."""
    w = Path(work_dir)
    try:
        hidden = json.loads((w / "stats.json").read_text(encoding="utf-8")).get("hidden_slides") or []
    except (OSError, ValueError):
        hidden = []
    pages: dict[int, str] = {}
    for pj in sorted((w / "render").glob("*/pages.json")):
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pages = {p["page"]: f"render/{p['image']}" for p in data.get("pages", []) if isinstance(p.get("page"), int) and p.get("image")}
        if pages:
            break
    return {str(n): pages[n - sum(1 for h in hidden if h < n)] for n in slides if n - sum(1 for h in hidden if h < n) in pages}


def apply(findings_path, run_path, review_paths) -> list[str]:
    """Apply every review file; returns the errors (nothing is written when there are any)."""
    findings_path, run_path = Path(findings_path), Path(run_path)
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    run = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}
    policy = run.get("comments", "major")
    by_id = {f["id"]: f for f in findings}
    commented = {fid for fid, f in by_id.items() if wants_comment(f, policy)}
    errors, changes, seen, lowered = [], [], set(), set()
    staged = []
    for rp in map(Path, review_paths):
        m = re.fullmatch(r"slide_review_(\d+)\.json", rp.name)
        if not m:
            errors.append(f"{rp.name}: a review file is named slide_review_<slide number>.json")
            continue
        n = int(m.group(1))
        try:
            entries = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errors.append(f"{rp.name}: cannot read ({e})")
            continue
        if not isinstance(entries, list):
            errors.append(f"{rp.name}: must be a top-level JSON list")
            continue
        on_slide = lambda fid, n=n: re.fullmatch(rf"s{n}(?:-.*)?", str(by_id[fid].get("p_id") or "")) is not None
        for x in entries:
            if not isinstance(x, dict) or not isinstance(x.get("id"), str) or ("of" in x and not isinstance(x["of"], str)):
                errors.append(f"{rp.name}: entry {x!r}: `id` (and `of`) must be finding ids as strings")
                continue
            fid, action, reason = x["id"], x.get("action"), str(x.get("reason") or "").strip()
            if fid not in by_id:
                errors.append(f"{rp.name}: {fid!r} is not a finding")
            elif not on_slide(fid):
                errors.append(f"{rp.name}: {fid} is not on slide {n}; a slide review judges its own slide only")
            elif fid not in commented:
                errors.append(f"{rp.name}: {fid} gets no comment; a slide review only judges commented findings")
            elif fid in seen:
                errors.append(f"{rp.name}: {fid} reviewed twice")
            elif action not in ACTIONS:
                errors.append(f"{rp.name}: {fid}: action {action!r} is not one of {', '.join(ACTIONS)}")
            elif not reason:
                errors.append(f"{rp.name}: {fid}: a reason is required")
            else:
                seen.add(fid)
                if action != "keep":
                    lowered.add(fid)
                    changes.append((fid, action, x.get("of"), reason))
                staged.append((rp.name, fid, action, x.get("of"), on_slide))
    for name, fid, action, of, on_slide in staged:       # a duplicate points at a kept, commented finding of its slide
        if action == "duplicate_of" and (of not in commented or of == fid or of in lowered or not on_slide(of)):
            errors.append(f"{name}: {fid}: duplicate_of {of!r} must name another commented finding on the same slide "
                          "that this review keeps")
    if errors:
        return errors
    log = []
    for fid, action, of, reason in changes:
        f = by_id[fid]
        requested = None
        if action == "verified_in_context" and f.get("conflicts"):     # never Verified with recorded conflicts
            requested, action = action, "minor"
            note = f"verified in context, but it has recorded conflicts — applied as minor: {reason}"
            f["severity"] = "Minor"
        elif action == "verified_in_context":
            note = f"verified in context: {reason}"
            f.update(verdict="Verified", severity="Minor", fix="None.")
        elif action == "duplicate_of":
            note = f"duplicate of {of}: {reason}"
            f["severity"] = "Minor"
        else:
            note = f"minor: {reason}"
            f["severity"] = "Minor"
        f["trail"] = f"{f.get('trail', '')}\nSlide review: {note}".strip()
        log.append({"id": fid, "action": action, **({"of": of} if of else {}),
                    **({"requested": requested} if requested else {}), "reason": reason})
    run["slide_review"] = log
    _write(findings_path, findings)
    _write(run_path, run)
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("findings")
    ap.add_argument("reviews", nargs="*", help="slide_review_<n>.json files")
    ap.add_argument("--run", required=True, help="run.json beside findings.json")
    ap.add_argument("--clock", metavar="WORK_DIR", help="record the review stage's end (workers = review files)")
    ap.add_argument("--prepare", metavar="WORK_DIR", help="write slide_findings_<n>.json for the reviews and stop")
    a = ap.parse_args(argv)
    if a.prepare:
        slides = prepare(a.findings, a.run, a.prepare)
        print(json.dumps({"slides": slides, "images": slide_images(a.prepare, slides)}))
        return 0
    errors = apply(a.findings, a.run, a.reviews)
    if errors:
        print("apply_slide_review.py: nothing written:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 2
    run = json.loads(Path(a.run).read_text(encoding="utf-8"))
    print(json.dumps({"changed": run.get("slide_review", [])}, ensure_ascii=False))
    if a.clock:
        import run_clock
        run_clock.stamp(a.clock, "review", "end", workers=len(a.reviews))
    return 0


if __name__ == "__main__":
    sys.exit(main())
