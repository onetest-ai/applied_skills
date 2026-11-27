from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Sequence

from register import Enforcement, Rule, inside_bundle, load

_KINDS = ("check", "agent", "skill")
_AREA_ORDER = ("language", "evidence", "story", "visual", "process")

_CSS = """
:root{--bg:#fafaf7;--fg:#1d1d1b;--muted:#5e5e58;--card:#fff;--line:#d9d8d0;--ok:#1b6e3c;--ok-bg:#e1f2e7;--bad:#a12626;--bad-bg:#fbe5e3;--warn:#85600a;--warn-bg:#fbf0d3;--link:#1a4f9c}
@media (prefers-color-scheme: dark){:root{--bg:#161614;--fg:#ecebe5;--muted:#a3a29a;--card:#1f1f1c;--line:#37362f;--ok:#7fd49e;--ok-bg:#1c3326;--bad:#f09a94;--bad-bg:#3b1f1d;--warn:#e6c26a;--warn-bg:#3a2f12;--link:#8db8f5}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.7rem;margin:0 0 4px}
h2{font-size:1.2rem;margin:32px 0 12px;border-bottom:1px solid var(--line);padding-bottom:4px}
.sub{color:var(--muted);margin:0 0 16px}
.counts{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}
.count{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.count strong{display:block;font-size:1.5rem}
.count span{color:var(--muted);font-size:.85rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:0 0 10px;overflow-wrap:anywhere}
.card.gap{border-left:4px solid var(--bad)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.rid{font-weight:600}
.rule{margin:6px 0 4px}
.meta{color:var(--muted);font-size:.85rem}
.badge{display:inline-block;border-radius:999px;padding:1px 9px;font-size:.78rem;font-weight:600}
.b-ok{background:var(--ok-bg);color:var(--ok)}
.b-bad{background:var(--bad-bg);color:var(--bad)}
.b-warn{background:var(--warn-bg);color:var(--warn)}
ul{margin:6px 0 0;padding-left:20px}
a{color:var(--link)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
td.ok{color:var(--ok)}td.bad{color:var(--bad)}td.none{color:var(--muted)}
"""


@dataclass(frozen=True)
class Coverage:
    rule_id: str
    covered: bool
    reasons: tuple[str, ...]
    broken: tuple[Enforcement, ...] = ()


def _enforcement_problems(e: Enforcement, bundle_root: Path) -> list[str]:
    problems: list[str] = []
    if not inside_bundle(bundle_root, e.ref):
        problems.append(f"{e.kind} {e.ref} is outside the bundle")
    elif not (bundle_root / e.ref).is_file():
        problems.append(f"{e.kind} {e.ref} does not exist")
    if e.kind == "check":
        if not e.test:
            problems.append(f"check {e.ref} names no test")
        elif not inside_bundle(bundle_root, e.test):
            problems.append(f"test {e.test} is outside the bundle")
        elif not (bundle_root / e.test).is_file():
            problems.append(f"test {e.test} does not exist")
    return problems


def coverage(rules: list[Rule], bundle_root: Path) -> list[Coverage]:
    results: list[Coverage] = []
    for rule in rules:
        if not rule.enforced_by:
            results.append(Coverage(rule.id, False, ("no enforcement is listed",)))
            continue
        reasons: list[str] = []
        broken: list[Enforcement] = []
        for e in rule.enforced_by:
            problems = _enforcement_problems(e, bundle_root)
            if problems:
                reasons.extend(problems)
                broken.append(e)
        results.append(Coverage(rule.id, not broken, tuple(reasons), tuple(broken)))
    return results


def _is_gap(rule: Rule, cov: Coverage) -> bool:
    return rule.status == "approved" and not cov.covered


def _badge(rule: Rule, cov: Coverage) -> str:
    if cov.covered:
        return '<span class="badge b-ok">Covered</span>'
    if rule.status == "approved":
        return '<span class="badge b-bad">Uncovered</span>'
    return '<span class="badge b-warn">Proposed, not yet enforced</span>'


def _path(path: str, missing: bool) -> str:
    if missing:
        return f"{escape(path)} (missing)"
    return f'<a href="{escape(path, quote=True)}">{escape(path)}</a>'


def _link(e: Enforcement, cov: Coverage) -> str:
    ref_missing = e in cov.broken and (
        f"{e.kind} {e.ref} does not exist" in cov.reasons
        or f"{e.kind} {e.ref} is outside the bundle" in cov.reasons
    )
    text = _path(e.ref, ref_missing)
    if e.test:
        test_missing = e in cov.broken and (
            f"test {e.test} does not exist" in cov.reasons
            or f"test {e.test} is outside the bundle" in cov.reasons
        )
        text += f" with test {_path(e.test, test_missing)}"
    return f"{escape(e.kind)} {text}"


def _reasons(cov: Coverage) -> str:
    if not cov.reasons:
        return ""
    items = "".join(f"<li>{escape(r)}</li>" for r in cov.reasons)
    return f"<ul>{items}</ul>"


def _gap_card(rule: Rule, cov: Coverage) -> str:
    return (
        '<div class="card gap">'
        f'<div class="row"><span class="rid">{escape(rule.id)}</span>{_badge(rule, cov)}</div>'
        f'<p class="rule">{escape(rule.rule)}</p>{_reasons(cov)}</div>'
    )


def _lesson_card(rule: Rule, cov: Coverage) -> str:
    enforcements = (
        "<ul>" + "".join(f"<li>{_link(e, cov)}</li>" for e in rule.enforced_by) + "</ul>"
        if rule.enforced_by
        else '<p class="meta">Nothing enforces this rule yet.</p>'
    )
    meta = (
        f"Status {escape(rule.status)}, version {rule.version}, scope {escape(rule.scope)}"
        + (f", approved on {escape(rule.approved_on)}" if rule.approved_on else "")
    )
    return (
        '<div class="card">'
        f'<div class="row"><span class="rid">{escape(rule.id)}</span>{_badge(rule, cov)}</div>'
        f'<p class="rule">{escape(rule.rule)}</p>'
        f'<p class="meta">Why. {escape(rule.why)}</p>'
        f'<p class="meta">{meta}</p>{enforcements}{_reasons(cov)}</div>'
    )


def _cell(rule: Rule, kind: str, cov: Coverage) -> str:
    entries = [e for e in rule.enforced_by if e.kind == kind]
    if not entries:
        return '<td class="none">none</td>'
    if any(e in cov.broken for e in entries):
        return '<td class="bad">broken</td>'
    return '<td class="ok">enforced</td>'


def _matrix(rules: list[Rule], cov_by_id: dict[str, Coverage]) -> str:
    head = "".join(f"<th>{k.capitalize()}</th>" for k in _KINDS)
    body = "".join(
        f"<tr><td>{escape(r.id)}</td>{''.join(_cell(r, k, cov_by_id[r.id]) for k in _KINDS)}</tr>"
        for r in rules
    )
    return (
        f'<div class="scroll"><table><thead><tr><th>Rule</th>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _counts(rules: list[Rule], cov: list[Coverage]) -> str:
    approved = sum(1 for r in rules if r.status == "approved")
    proposed = len(rules) - approved
    gaps = sum(1 for r, c in zip(rules, cov) if _is_gap(r, c))
    tiles = (
        (len(rules), "Rules"),
        (approved, "Approved"),
        (proposed, "Proposed"),
        (approved - gaps, "Approved and covered"),
        (gaps, "Approved and uncovered"),
    )
    return '<div class="counts">' + "".join(
        f'<div class="count"><strong>{n}</strong><span>{escape(label)}</span></div>' for n, label in tiles
    ) + "</div>"


def render(rules: list[Rule], cov: list[Coverage], generated: str) -> str:
    cov_by_id = {c.rule_id: c for c in cov}
    ordered = sorted(rules, key=lambda r: r.id)
    gaps = [r for r in ordered if _is_gap(r, cov_by_id[r.id])]
    gap_html = (
        "".join(_gap_card(r, cov_by_id[r.id]) for r in gaps)
        if gaps
        else '<p class="meta">No gaps. Every approved rule has working enforcement.</p>'
    )
    areas = [a for a in _AREA_ORDER if any(r.area == a for r in ordered)]
    areas += sorted({r.area for r in ordered} - set(areas))
    lessons = "".join(
        f"<h2>{escape(area.capitalize())}</h2>"
        + "".join(_lesson_card(r, cov_by_id[r.id]) for r in ordered if r.area == area)
        for area in areas
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Consult Lessons</title>"
        f"<style>{_CSS}</style></head><body><div class=\"wrap\">"
        "<h1>Consult Lessons</h1>"
        f'<p class="sub">Generated {escape(generated)}. Every approved rule needs a skill, agent or check that enforces it.</p>'
        f"{_counts(rules, cov)}"
        f"<h2>Gaps</h2>{gap_html}"
        f"{lessons}"
        f"<h2>Coverage Matrix</h2>{_matrix(ordered, cov_by_id)}"
        "</div></body></html>\n"
    )


def _main(argv: Sequence[str] | None = None) -> int:
    from datetime import date

    parser = argparse.ArgumentParser(description="Render the consult lessons page.")
    parser.add_argument("--register", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    rules = load(args.register)
    cov = coverage(rules, args.bundle)
    args.out.write_text(render(rules, cov, date.today().isoformat()), encoding="utf-8")
    by_id = {c.rule_id: c for c in cov}
    gaps = [r for r in rules if _is_gap(r, by_id[r.id])]
    for r in gaps:
        print(f"{r.id}: {'; '.join(by_id[r.id].reasons)}", file=sys.stderr)
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(_main())
