"""merge: base.md + drafted stale sections -> work/<task>/next.md.

Reads `work/<task>/pack/plan.json` (written by `prepare`) for the stale/carried
section split, `work/<task>/base.md` for the previous content, and
`work/<task>/sections/<sid>.md` (written by the agent, then rewritten in place
by `check-file`) for every stale section — refusing if one is missing.

- **Carried** sections are copied from `base.md` byte-for-byte (asserted).
- **Drafted** sections keep the agent's text; every claim (bullet/paragraph)
  is matched to the base section's claims by `claims.match_claims` — the one
  claim-continuity rule (spec A5): the agent's own `<!-- c:xxxx -->` counts
  only when it names a still-unmatched base claim (an invented, copied or
  duplicate id is ignored), else the most similar unmatched base claim
  (normalized-text ratio >= 0.9, ties by id) lends its id, else a fresh
  `sha256(task|section|normalized)[:8]` id is minted (re-hashed on a clash, so
  ids are unique in the section). Any `sup=c:yyyyyyyy` the agent wrote is
  passed through verbatim. Categories: `kept` (same text AND tags), `recited`
  (same text, different tags), `reworded`, `added`, `superseded`.
  Origin: only a `kept` claim keeps its base claim's origin in its comment
  (`<!-- c:xxxx origin=human -->`); a re-cited or reworded human claim is
  the agent's claim now. An id-less base claim (a legacy human addition, from
  before origin was persisted in the comment) matched and kept this way gets
  a fresh id and `origin=human`. The agent's own `origin=` is ignored. Tag
  bodies are written unescaped (`claims` reads them that way, Defect A).
  `superseded` counts only a claim becoming superseded THIS version — a base
  claim that was already superseded and is carried forward unchanged counts
  as `kept`, not `superseded` again (a base claim that was already superseded
  and simply disappears — never carried, in any form — still counts as
  `dropped`; its content is gone from the document either way, even though it
  was not "live" content).
- `## Changes in this version {#changes}` is generated from the base-vs-next
  claim sets per section (script-owned; the agent never drafts it).
- `work/<task>/merge.json`: `{task, version, noop, sections: {sid: {status:
  "carried"|"drafted", claims_before, claims_after, kept, reworded, recited,
  dropped, added, superseded}}}`. `dropped` = base claims no draft claim matched.
- Noop: if `next.md` would be byte-identical to the previous version (ignoring
  the header comment and the Changes section), nothing is written and the
  result carries `"noop": true`.
"""
from __future__ import annotations

import json
import re
from typing import Any

from scribe_lib import claims
from scribe_lib.basedoc import split_by_section_id
from scribe_lib.config import Config, ScribeError, parse_template_ref, read_state, substitute_params

_CHANGES_RE = re.compile(r"## Changes in this version \{#changes\}.*?(?=\n## |\Z)", re.DOTALL)
_HEADER_RE = re.compile(
    r"^<!-- scribe: task=(?P<task>\S+) version=(?P<version>\d+) built_at=(?P<built_at>\S+) "
    r"template=(?P<template>\S+) base=(?P<base>.*?) -->\s*$"
)


def _built_at(config: Config) -> str:
    return config.now if "T" in config.now else f"{config.now}T00:00:00"


def parse_header(text: str) -> dict[str, Any]:
    """Parse the `<!-- scribe: task=... version=N built_at=... template=... base=... -->`
    line at the top of a Document Markdown file. Raises ScribeError if absent/malformed."""
    first_line = text.splitlines()[0] if text.splitlines() else ""
    m = _HEADER_RE.match(first_line)
    if not m:
        raise ScribeError(f"missing/malformed scribe header comment: {first_line!r}")
    return {
        "task": m.group("task"),
        "version": int(m.group("version")),
        "built_at": m.group("built_at"),
        "template": m.group("template"),
        "base": m.group("base"),
    }


def _base_field(base_version: int | None, base_edited: bool) -> str:
    if base_version is None:
        return "none"
    return f"v{base_version:03d}" + (" (human-edited)" if base_edited else "")


def _merge_drafted_section(task_id: str, sid: str, base_body: str, draft_text: str) -> dict[str, Any]:
    base_blocks = [b for b in claims.parse_blocks(base_body) if claims.is_claim(b)]
    draft_blocks = claims.parse_blocks(draft_text)
    results = {id(r["block"]): r for r in claims.match_claims(base_blocks, draft_blocks, task_id, sid)}

    counts = dict.fromkeys(("kept", "reworded", "recited", "added", "superseded"), 0)
    examples: dict[str, list[str]] = {"added": [], "reworded": [], "recited": [], "dropped": [], "superseded": []}
    rendered: list[tuple[dict[str, Any], str]] = []

    for block in draft_blocks:
        r = results.get(id(block))
        if r is None:  # not a claim: code fence, `Not modeled:` line
            rendered.append((block, claims.render_claim(block, None)))
            continue
        category = r["category"]
        counts[category] += 1
        if category != "kept":
            examples[category].append(block["normalized"][:160])
        # Origin comes only from the matched BASE claim, and only while text
        # AND tags are unchanged (`kept`): an `origin=` the agent wrote is never
        # trusted, and a human claim the agent reworded or re-cited is no
        # longer human-authored.
        origin = claims.base_claim_origin(r["base"]) if category == "kept" else None
        rendered.append((block, claims.render_claim(block, r["claim_id"], block["sup_ref"], origin)))

    matched = {id(r["base"]) for r in results.values() if r["base"] is not None}
    dropped_blocks = [b for b in base_blocks if id(b) not in matched]
    for b in dropped_blocks:
        examples["dropped"].append(b["normalized"][:160])

    return {
        "body": claims.render_blocks(rendered),
        "claims_before": len(base_blocks),
        "claims_after": sum(counts.values()),
        **counts,
        "dropped": len(dropped_blocks),
        "examples": examples,
    }


def _render_changes(
    sections_spec: list[dict[str, Any]],
    section_reports: dict[str, dict[str, Any]],
    examples_by_section: dict[str, dict[str, list[str]]],
    base_json: dict[str, Any],
) -> str:
    lines: list[str] = []
    for sec in sections_spec:
        sid = sec["id"]
        rep = section_reports[sid]
        if rep["status"] == "carried":
            continue
        added, dropped, reworded, superseded = rep["added"], rep["dropped"], rep["reworded"], rep["superseded"]
        recited = rep.get("recited", 0)
        if not (added or dropped or reworded or superseded or recited):
            continue
        lines.append(
            f"- **{sec['title']}**: {added} added, {dropped} removed, "
            f"{reworded} reworded, {superseded} superseded" + (f", {recited} re-cited" if recited else "")
        )
        examples = examples_by_section.get(sid, {})
        shown = 0
        for cat in ("superseded", "dropped", "reworded", "recited", "added"):
            for ex in examples.get(cat, []):
                if shown >= 5:
                    break
                lines.append(f"  - {cat}: {ex}")
                shown += 1
            if shown >= 5:
                break

    for h in (base_json.get("human_added") or []):
        lines.append(f"- kept human edit: {h.get('text', '')[:160]}")
    for h in (base_json.get("human_modified") or []):
        lines.append(f"- kept human edit: {h.get('text', '')[:160]}")

    if not lines:
        lines.append("- no changes")
    return "\n".join(lines)


def _strip_header_and_changes(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("<!-- scribe:"):
        lines = lines[1:]
    body = "\n".join(lines)
    body = _CHANGES_RE.sub("", body)
    return body.strip()


def merge_task(
    config: Config,
    task_id: str,
    instances: dict[str, Any],
    templates: dict[str, Any],
) -> dict[str, Any]:
    instance = instances[task_id]
    template_id, template_version = parse_template_ref(instance["template"])
    template = templates[template_id]
    params = instance.get("params") or {}
    sections_spec = [substitute_params(sec, params) for sec in (template.get("output") or {}).get("sections") or []]

    work_dir = config.work_dir / task_id
    # A leftover next.md from a previous run (a noop, or a run that stopped
    # before publish) must never survive into this run — publish keys off
    # next.md's presence/version, so a stale file here would let it
    # re-publish an old version. Every merge call starts from a clean slate.
    stale_next = work_dir / "next.md"
    if stale_next.is_file():
        stale_next.unlink()

    plan_path = work_dir / "pack" / "plan.json"
    if not plan_path.is_file():
        raise ScribeError(f"no pack/plan.json for '{task_id}' — run prepare first")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    stale_ids = {e["section"] for e in plan.get("stale", [])}

    base_md_path = work_dir / "base.md"
    base_text = base_md_path.read_text(encoding="utf-8") if base_md_path.is_file() else ""
    base_sections = split_by_section_id(base_text)

    base_json_path = work_dir / "base.json"
    base_json = json.loads(base_json_path.read_text(encoding="utf-8")) if base_json_path.is_file() else {}

    sections_dir = work_dir / "sections"
    for sid in sorted(stale_ids):
        draft_path = sections_dir / f"{sid}.md"
        if not draft_path.is_file():
            raise ScribeError(
                f"missing drafted section file for stale section '{sid}': work/{task_id}/sections/{sid}.md"
            )

    next_bodies: dict[str, str] = {}
    section_reports: dict[str, dict[str, Any]] = {}
    examples_by_section: dict[str, dict[str, list[str]]] = {}

    for sec in sections_spec:
        sid = sec["id"]
        if sid in stale_ids:
            draft_text = (sections_dir / f"{sid}.md").read_text(encoding="utf-8")
            report = _merge_drafted_section(task_id, sid, base_sections.get(sid, ""), draft_text)
            next_bodies[sid] = report["body"]
            examples_by_section[sid] = report.pop("examples")
            report.pop("body")
            section_reports[sid] = {"status": "drafted", **report}
        else:
            body = base_sections.get(sid, "")
            next_bodies[sid] = body
            claim_count = len([b for b in claims.parse_blocks(body) if claims.is_claim(b)])
            section_reports[sid] = {
                "status": "carried",
                "claims_before": claim_count,
                "claims_after": claim_count,
                "kept": claim_count,
                "reworded": 0,
                "recited": 0,
                "dropped": 0,
                "added": 0,
                "superseded": 0,
            }

    state = read_state(config, instance)
    prev_version = state.get("version") or 0
    new_version = prev_version + 1
    base_version = base_json.get("base_version")
    base_edited = bool(base_json.get("base_edited"))

    header = (
        f"<!-- scribe: task={task_id} version={new_version} built_at={_built_at(config)} "
        f"template={template_id}@{template_version} base={_base_field(base_version, base_edited)} -->"
    )
    changes_text = _render_changes(sections_spec, section_reports, examples_by_section, base_json)

    lines = [header, "", f"# {instance['title']}", "", "## Changes in this version {#changes}", "", changes_text, ""]
    for sec in sections_spec:
        sid = sec["id"]
        lines += [f"## {sec['title']} {{#{sid}}}", "", next_bodies[sid].strip(), ""]
    next_text = "\n".join(lines).rstrip() + "\n"

    reextracted = split_by_section_id(next_text)
    for sec in sections_spec:
        sid = sec["id"]
        if sid in stale_ids:
            continue
        if reextracted.get(sid, "") != base_sections.get(sid, ""):
            raise ScribeError(f"carried section '{sid}' was not copied byte-for-byte from base")

    prev_text = ""
    if prev_version:
        prev_path = config.out_root / instance["out"] / "_src" / f"v{prev_version:03d}.md"
        if prev_path.is_file():
            prev_text = prev_path.read_text(encoding="utf-8")
    noop = bool(prev_version) and _strip_header_and_changes(next_text) == _strip_header_and_changes(prev_text)

    merge_result = {
        "task": task_id,
        "version": new_version,
        "noop": noop,
        "sections": section_reports,
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "merge.json").write_text(json.dumps(merge_result, indent=2), encoding="utf-8")

    if noop:
        return {"status": "ok", "task": task_id, "noop": True}

    (work_dir / "next.md").write_text(next_text, encoding="utf-8")
    return {"status": "ok", "task": task_id, "noop": False, "version": new_version, "sections": section_reports}
