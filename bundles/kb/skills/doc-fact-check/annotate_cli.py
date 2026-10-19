"""The command line both fact-check annotators share (step 8): choose the comments, check them, write them with the
format's own `annotate` function, then record every finding's destination in findings.json.

    python annotate.py <draft.docx> <out.docx> --select major|all --findings findings.json
    python pptx_annotate.py <draft.pptx> <out.pptx> --select major|all --findings findings.json
    (or --approved approved.json --comments major|all, when a Deep reviewer edited the set)

This file ships byte-identical in doc-fact-check/ and deck-fact-check/ (a drift test pins the copies).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling imports work under python -I too

import run_clock  # noqa: E402
from fact_check_invariants import NOT_ANCHORED, comment_text  # noqa: E402

PROG = "annotate.py"     # set by run() to the calling script's name, for its error messages


def mark_destinations(findings, written_ids, approved_ids=None, policy=None, label='Word comment', skipped_ids=()):
    """Call after annotate; pure, returns a new list.

    approved_ids/policy are given only for an edited (--approved) set: a finding the policy would comment that the
    reviewer left out is "declined by reviewer"; a written comment the policy would not make is marked "added by
    reviewer". The invariants exempt exactly these two markers from the comment-policy checks. `label` names the
    format's comment ("Word comment", "Deck comment"); the invariants read any destination holding "comment".
    """
    from fact_check_invariants import wants_comment
    out = []
    for f in findings:
        wanted = approved_ids is not None and policy is not None and wants_comment(f, policy)
        if f['id'] in written_ids:
            dest = label
            if approved_ids is not None and policy is not None and not wanted:
                dest = f'{label} (added by reviewer)'
        elif f['id'] in skipped_ids:
            dest = f'{NOT_ANCHORED}: its quote or shape was not found (logged)'
        elif wanted and f['id'] not in approved_ids:
            dest = 'declined by reviewer'
        else:
            dest = 'count only' if f.get('verdict') == 'Verified' else 'log only'
        out.append({**f, 'destination': dest})
    return out


def _load_list(path, what, need_anchor=False):
    """Load a JSON list of finding objects; exit 2 with '<script>: <file>: <reason>' when it is not usable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.exit(_bad(path, f"not valid JSON ({exc})"))
    if not isinstance(data, list):
        sys.exit(_bad(path, f"{what} must be a JSON list of finding objects"))
    for i, f in enumerate(data):
        if not isinstance(f, dict) or not f.get("id") or not isinstance(f["id"], str):
            sys.exit(_bad(path, f"item {i} is not an object with a non-empty id"))
        reason = _comment_error(f) if need_anchor else None
        if reason:
            sys.exit(_bad(path, f"{f['id']}: {reason}"))
    return data


COMMENT_KEYS = ("verdict", "severity", "section", "evidence", "fix", "source")


def _comment_error(f) -> str | None:
    """Why an approved finding cannot become a comment, or None: the comment's keys and its anchor."""
    for key in COMMENT_KEYS:
        v = f.get(key)
        if key == "section":                     # a heading path or a section number: any non-empty value
            if v is None or not str(v).strip() or v in ([], {}):
                return "`section` must be non-empty"
        elif not (isinstance(v, str) and v.strip()):
            return f"`{key}` must be a non-empty string"
    if f.get("anchor") == "drawing":
        fig = f.get("figure")
        if not (type(fig) is int and fig >= 1):
            return 'anchor "drawing" needs an integer figure >= 1'
    elif not f.get("quote"):
        return 'needs a quote or anchor: "drawing"'
    return None


def _bad(path, reason):
    print(f"{PROG}: {path}: {reason}", file=sys.stderr)
    return 2


def _write_json_atomic(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(argv, *, prog: str, description: str, annotate, dependency_error, label: str) -> int:
    """The shared CLI. `annotate(src, dst, findings) -> (written, skipped)` writes the comments; `dependency_error()`
    returns the stop message when the format's library is missing, else None."""
    global PROG
    PROG = prog
    ap = argparse.ArgumentParser(prog=prog, description=description)
    ap.add_argument("draft")
    ap.add_argument("out")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--approved", help="JSON list of the findings the user approved (Deep, when the user edited the set)")
    src.add_argument("--select", choices=("major", "all"),
                     help="take the comments from findings.json: major = Blocker + Major defects, all = every defect")
    ap.add_argument("--findings", required=True, help="findings.json; rewritten with destination set")
    ap.add_argument("--comments", choices=("major", "all"),
                    help="required with --approved, and only with it: the run's comment policy, to record reviewer decisions")
    ap.add_argument("--sections", help="deck-fact-check only: <work dir>/sections.json, to pin each comment where its "
                                       "statement renders")
    ap.add_argument("--clock", metavar="WORK_DIR", help="record this step's start and end in <work dir>/timings.json")
    ap.add_argument("--fields", help="deck-fact-check only: the lines each comment carries after its header, "
                                     "comma-separated from quote, brain, fix, source (default quote,fix); a comment "
                                     "the extra lines push over the word limit keeps quote and fix")
    a = ap.parse_args(argv)
    from fact_check_invariants import COMMENT_FIELDS
    fields = tuple(x.strip() for x in a.fields.split(",") if x.strip()) if a.fields else None
    if fields is not None and (not fields or set(fields) - set(COMMENT_FIELDS)):
        print(f"{prog}: --fields takes any of {', '.join(COMMENT_FIELDS)}, got {a.fields!r}", file=sys.stderr)
        return 2
    import inspect
    if a.sections and "sections" not in inspect.signature(annotate).parameters:
        print(f"{prog}: --sections is only used by deck-fact-check", file=sys.stderr)
        return 2
    if a.approved and not a.comments:
        ap.error("--comments is required with --approved (the run's comment policy: major or all)")
    if a.comments and not a.approved:
        print(f"{prog}: --comments is only used with --approved (with --select the policy is the select value)",
              file=sys.stderr)
        return 2
    for p in (a.draft, a.findings, *([a.approved] if a.approved else [])):
        if not Path(p).is_file():
            print(f"{prog}: no such file: {p}", file=sys.stderr)
            return 2
    if Path(a.out).resolve() == Path(a.draft).resolve():
        print(f"{prog}: refusing to overwrite the original draft", file=sys.stderr)
        return 2
    findings = _load_list(a.findings, "findings")
    if a.select:
        from fact_check_invariants import wants_comment
        approved = [f for f in findings if wants_comment(f, a.select)]
        bad = [f"{f['id']}: {why}" for f in approved if (why := _comment_error(f))]
        if bad:
            print(f"{prog}: {a.findings}: " + "; ".join(bad), file=sys.stderr)
            return 2
    else:
        approved = _load_list(a.approved, "approved", need_anchor=True)
    from fact_check_invariants import MAX_COMMENT_WORDS, comment_words, is_deck_finding
    long = [f"{f['id']}: comment would be {n} words (limit {MAX_COMMENT_WORDS}); shorten its evidence or fix"
            for f in approved if (n := comment_words(comment_text(f), is_deck_finding(f))) > MAX_COMMENT_WORDS]
    if long:
        print(f"{prog}: {a.approved or a.findings}: " + "; ".join(long), file=sys.stderr)
        return 2
    if why := dependency_error():
        print(why, file=sys.stderr)
        return 2
    extra = {"sections": a.sections} if a.sections else {}
    if fields is not None:
        if "fields" not in inspect.signature(annotate).parameters:
            print(f"{prog}: --fields is only used by deck-fact-check", file=sys.stderr)
            return 2
        extra["fields"] = fields
    if a.clock:
        run_clock.stamp(a.clock, "annotate", "start")
    written, skipped = annotate(a.draft, a.out, approved, **extra)
    if a.approved:
        marked = mark_destinations(findings, written, {f["id"] for f in approved}, a.comments, label=label,
                                   skipped_ids=set(skipped))
    else:
        marked = mark_destinations(findings, written, label=label, skipped_ids=set(skipped))
    _write_json_atomic(a.findings, marked)
    print(json.dumps({"written": written, "skipped": skipped}))
    if a.clock:
        run_clock.stamp(a.clock, "annotate", "end")
    return 0


