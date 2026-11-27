from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Sequence

Scope = Literal["epam-practice", "personal-style"]
Status = Literal["proposed", "approved"]
Area = Literal["language", "evidence", "story", "visual", "process"]
Kind = Literal["check", "agent", "skill"]

_ID = re.compile(r"^[A-Z][A-Z0-9-]{2,40}$")
_SCOPES = ("epam-practice", "personal-style")
_STATUSES = ("proposed", "approved")
_AREAS = ("language", "evidence", "story", "visual", "process")
_KINDS = ("check", "agent", "skill")
_ABBREVIATIONS = re.compile(
    r"\b(?i:e\.g\.|i\.e\.|vs\.|etc\.|approx\.|no\.)(?=\s+[a-z0-9]|\s*,)"
)
_SENTENCE_BREAK = re.compile(r"[.?!]\s+(?=[A-Z0-9])|;\s")


@dataclass(frozen=True)
class Enforcement:
    kind: Kind
    ref: str
    test: str | None


@dataclass(frozen=True)
class Source:
    session: str
    uuid: str | None
    timestamp: str


@dataclass(frozen=True)
class Rule:
    id: str
    version: int
    status: Status
    approved_on: str | None
    area: Area
    scope: Scope
    rule: str
    why: str
    sources: tuple[Source, ...]
    enforced_by: tuple[Enforcement, ...]
    see_also: str | None


@dataclass(frozen=True)
class Problem:
    rule_id: str
    message: str


def _from_dict(data: dict) -> Rule:
    return Rule(
        id=data["id"],
        version=data["version"],
        status=data.get("status", "proposed"),
        approved_on=data.get("approved_on"),
        area=data["area"],
        scope=data["scope"],
        rule=data["rule"],
        why=data["why"],
        sources=tuple(
            Source(s["session"], s.get("uuid"), s["timestamp"]) for s in data.get("sources", [])
        ),
        enforced_by=tuple(
            Enforcement(e["kind"], e["ref"], e.get("test")) for e in data.get("enforced_by", [])
        ),
        see_also=data.get("see_also"),
    )


def _to_dict(rule: Rule) -> dict:
    return {
        "id": rule.id,
        "version": rule.version,
        "status": rule.status,
        "approved_on": rule.approved_on,
        "area": rule.area,
        "scope": rule.scope,
        "rule": rule.rule,
        "why": rule.why,
        "sources": [
            {"session": s.session, "uuid": s.uuid, "timestamp": s.timestamp} for s in rule.sources
        ],
        "enforced_by": [
            {"kind": e.kind, "ref": e.ref, "test": e.test} for e in rule.enforced_by
        ],
        "see_also": rule.see_also,
    }


def load(path: Path) -> list[Rule]:
    return [_from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def save(path: Path, rules: list[Rule]) -> None:
    ordered = sorted(rules, key=lambda r: r.id)
    text = json.dumps([_to_dict(r) for r in ordered], indent=1, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def read_terms(path: Path) -> list[str]:
    terms: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def _one_sentence(text: str) -> bool:
    flat = _ABBREVIATIONS.sub("x", text.strip())
    return _SENTENCE_BREAK.search(flat) is None


def _has_term(text: str, term: str) -> bool:
    words = r"[\s\-]+".join(re.escape(w) for w in term.split())
    pattern = r"(?<!\w)" + words + r"(?:['\u2019]s|es|s)?(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def inside_bundle(bundle_root: Path, rel: str) -> bool:
    if Path(rel).is_absolute():
        return False
    return (bundle_root / rel).resolve().is_relative_to(bundle_root.resolve())


def validate(rules: list[Rule], bundle_root: Path, terms: list[str]) -> list[Problem]:
    problems: list[Problem] = []
    seen: set[str] = set()
    for r in rules:
        def bad(message: str) -> None:
            problems.append(Problem(r.id, message))

        if any(_has_term(r.id, t) for t in terms):
            bad("private term in field id")
        if not _ID.match(r.id):
            bad("id must match ^[A-Z][A-Z0-9-]{2,40}$")
        if r.id in seen:
            bad("duplicate id")
        seen.add(r.id)
        if r.scope not in _SCOPES:
            bad(f"scope {r.scope!r} is not allowed (client-specific rules never enter the register)")
        if r.status not in _STATUSES:
            bad(f"status {r.status!r} is not allowed")
        if r.area not in _AREAS:
            bad(f"area {r.area!r} is not allowed")
        if r.version < 1:
            bad("version must be 1 or higher")
        if r.status == "approved" and not r.approved_on:
            bad("approved rule needs approved_on")
        if not r.rule.strip() or not _one_sentence(r.rule):
            bad("rule must be one sentence")
        if not r.why.strip():
            bad("why is empty")
        if not r.sources:
            bad("sources required")
        for field, value in (("rule", r.rule), ("why", r.why), ("see_also", r.see_also or "")):
            if any(_has_term(value, t) for t in terms):
                bad(f"private term in field {field}")
        for e in r.enforced_by:
            if e.kind not in _KINDS:
                bad(f"enforcement kind {e.kind!r} is not allowed")
            for field, value in (("ref", e.ref), ("test", e.test or "")):
                if any(_has_term(value.replace("_", " "), t) for t in terms):
                    bad(f"private term in field {field}")
            if r.status == "approved" and e.kind == "check":
                if not inside_bundle(bundle_root, e.ref):
                    bad(f"check ref {e.ref} is outside the bundle")
                elif not (bundle_root / e.ref).is_file():
                    bad(f"check ref {e.ref} does not exist")
                if e.test and not inside_bundle(bundle_root, e.test):
                    bad(f"check test {e.test} is outside the bundle")
                elif not e.test or not (bundle_root / e.test).is_file():
                    bad(f"check test {e.test} does not exist")
    return problems


def _read_proposals(path: Path) -> list[Rule]:
    rules: list[Rule] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rules.append(_from_dict(json.loads(line)))
    return rules


def _version_problem(existing: Rule, new: Rule) -> Problem | None:
    if new.version < existing.version:
        return Problem(new.id, f"version {new.version} is older than saved version {existing.version}")
    if new.version == existing.version:
        if new.rule != existing.rule:
            return Problem(new.id, "rule text changed without a new version")
        return Problem(new.id, "already in the register at this version")
    return None


def add_proposals(
    register: Path, proposals: Path, bundle_root: Path, terms: list[str]
) -> list[Problem]:
    saved = {r.id: r for r in load(register)}
    incoming = [replace(r, status="proposed", approved_on=None) for r in _read_proposals(proposals)]
    problems = validate(incoming, bundle_root, terms)
    refused = {p.rule_id for p in problems}
    for r in incoming:
        if r.id in saved:
            problem = _version_problem(saved[r.id], r)
            if problem:
                problems.append(problem)
                refused.add(r.id)
    for r in incoming:
        if r.id not in refused:
            saved[r.id] = r
    save(register, list(saved.values()))
    return problems


def approve(register: Path, ids: list[str], date: str) -> list[Problem]:
    rules = load(register)
    known = {r.id for r in rules}
    problems = [Problem(i, "unknown id") for i in ids if i not in known]
    if problems:
        return problems
    approved = [
        replace(r, status="approved", approved_on=date) if r.id in ids else r for r in rules
    ]
    save(register, approved)
    return []


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consult rule register.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--register", type=Path, required=True)
    check.add_argument("--bundle", type=Path, required=True)
    check.add_argument("--terms", type=Path, required=True)
    add = sub.add_parser("add")
    add.add_argument("--register", type=Path, required=True)
    add.add_argument("--proposals", type=Path, required=True)
    add.add_argument("--bundle", type=Path, required=True)
    add.add_argument("--terms", type=Path, required=True)
    appr = sub.add_parser("approve")
    appr.add_argument("--register", type=Path, required=True)
    appr.add_argument("--ids", required=True)
    appr.add_argument("--date", required=True)
    args = parser.parse_args(argv)

    if args.command == "check":
        problems = validate(load(args.register), args.bundle, read_terms(args.terms))
    elif args.command == "add":
        problems = add_proposals(args.register, args.proposals, args.bundle, read_terms(args.terms))
    else:
        problems = approve(args.register, [i for i in args.ids.split(",") if i], args.date)
    for p in problems:
        print(f"{p.rule_id}: {p.message}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(_main())
