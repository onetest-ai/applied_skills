"""accept: run acceptance checks on `work/<task>/next.md`.

Checks (from the template's `acceptance` list, all three always run for the
three checks — `sections_present`, `zero_unverified`, `diagrams_render`):

- `sections_present`: every template section id appears as a `## Title {#id}`
  heading with a non-empty body (either at least one claim or a
  `Not modeled:` line).
- `zero_unverified`: no claim contains a literal `[?]` or `UNVERIFIED`
  marker, and every claim (bullet/paragraph) carries >= 1 citation tag —
  unless it is human-authored (a human's own prose legitimately has no
  machine tag): its claim comment says `origin=human|human_modified`
  (persisted by base/merge, so it holds in every later version), it is an
  id-less claim whose text is also an id-less claim in the
  same section of `work/<task>/base.md` (a legacy human addition carried
  from an already-published version; see `claims.base_claim_origin`), or it
  is recorded in this run's `work/<task>/base.json` `human_added`/
  `human_modified`.
- `diagrams_render`: reads `work/<task>/render/render.json` if present
  (`{"ok": bool, "diagrams": [...]}`); if absent, skipped with a note.

Returns `{"status": "ok"|"error", "passed": bool, "checks": {...}}`.
"""
from __future__ import annotations

import json
import re
from typing import Any

from scribe_lib import claims
from scribe_lib.basedoc import split_by_section_id
from scribe_lib.config import Config, parse_template_ref, substitute_params

_UNVERIFIED_RE = re.compile(r"\[\?\]|UNVERIFIED")


def _human_exempt_keys(base_json: dict[str, Any]) -> tuple[set[str], set[str]]:
    modified_ids = {h["claim_id"] for h in (base_json.get("human_modified") or []) if h.get("claim_id")}
    added_norms = {claims.normalize_text(h.get("text", ""))[:200] for h in (base_json.get("human_added") or [])}
    return modified_ids, added_norms


def _legacy_human_claims(work_dir) -> set[tuple[str, str]]:
    """(section, normalized) of every id-less claim in base.md."""
    base_path = work_dir / "base.md"
    if not base_path.is_file():
        return set()
    out: set[tuple[str, str]] = set()
    for sid, body in split_by_section_id(base_path.read_text(encoding="utf-8")).items():
        for b in claims.parse_blocks(body):
            if claims.is_claim(b) and not b["claim_id"]:
                out.add((sid, b["normalized"]))
    return out


def accept_task(config: Config, task_id: str, instance: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    work_dir = config.work_dir / task_id
    next_path = work_dir / "next.md"
    if not next_path.is_file():
        return {
            "status": "error",
            "task": task_id,
            "reason": "no work/<task>/next.md to accept (run merge first; a noop merge has nothing to accept)",
        }
    next_text = next_path.read_text(encoding="utf-8")
    sections = split_by_section_id(next_text)

    params = instance.get("params") or {}
    sections_spec = [substitute_params(sec, params) for sec in (template.get("output") or {}).get("sections") or []]

    base_json_path = work_dir / "base.json"
    base_json = json.loads(base_json_path.read_text(encoding="utf-8")) if base_json_path.is_file() else {}
    modified_ids, added_norms = _human_exempt_keys(base_json)
    legacy_human = _legacy_human_claims(work_dir)

    # -- sections_present --
    missing: list[str] = []
    empty: list[str] = []
    for sec in sections_spec:
        sid = sec["id"]
        if sid not in sections:
            missing.append(sid)
            continue
        blocks = claims.parse_blocks(sections[sid])
        if not any(b["kind"] in ("bullet", "para", "not_modeled", "code") for b in blocks):
            empty.append(sid)
    sections_present_ok = not missing and not empty
    check_sections_present = {
        "passed": sections_present_ok,
        "missing": missing,
        "empty": empty,
    }

    # -- zero_unverified --
    unverified: list[dict[str, Any]] = []
    for sec in sections_spec:
        sid = sec["id"]
        for block in claims.parse_blocks(sections.get(sid, "")):
            if not claims.is_claim(block):
                continue
            if _UNVERIFIED_RE.search(block["text"]):
                unverified.append({"section": sid, "reason": "unverified marker", "text": block["normalized"][:160]})
                continue
            if block["tags"]:
                continue
            exempt = (
                block["origin"] in claims.HUMAN_ORIGINS
                or (not block["claim_id"] and (sid, block["normalized"]) in legacy_human)
                or (block["claim_id"] and block["claim_id"] in modified_ids)
                or block["normalized"][:200] in added_norms
            )
            if not exempt:
                unverified.append({"section": sid, "reason": "no citation tag", "text": block["normalized"][:160]})
    check_zero_unverified = {"passed": not unverified, "claims": unverified}

    # -- diagrams_render --
    render_path = work_dir / "render" / "render.json"
    if render_path.is_file():
        render_result = json.loads(render_path.read_text(encoding="utf-8"))
        # `render.json`'s top-level "ok" is strictly "docx and pdf were
        # produced" — a failed mermaid diagram alone does not clear it (render
        # degrades that diagram to a code block rather than refusing to build
        # the document). diagrams_render is specifically about diagrams, so it
        # also checks each entry in "diagrams" individually.
        diagrams_ok = bool(render_result.get("ok", True)) and all(
            d.get("ok", True) for d in render_result.get("diagrams", [])
        )
        check_diagrams_render = {"passed": diagrams_ok, "skipped": False, "render": render_result}
    else:
        check_diagrams_render = {"passed": True, "skipped": True, "note": "no work/<task>/render/render.json (render not run yet)"}

    checks = {
        "sections_present": check_sections_present,
        "zero_unverified": check_zero_unverified,
        "diagrams_render": check_diagrams_render,
    }
    passed = all(c["passed"] for c in checks.values())

    reasons: list[str] = []
    if missing:
        reasons.append(f"sections_present: missing {missing}")
    if empty:
        reasons.append(f"sections_present: empty {empty}")
    if unverified:
        reasons.append(f"zero_unverified: {len(unverified)} claim(s) failed")
    if not check_diagrams_render["passed"]:
        reasons.append("diagrams_render: failed")

    result = {
        "status": "ok" if passed else "error",
        "task": task_id,
        "passed": passed,
        "checks": checks,
    }
    if reasons:
        result["reasons"] = reasons
    return result
