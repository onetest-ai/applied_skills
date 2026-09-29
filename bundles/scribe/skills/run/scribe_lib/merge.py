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
  claim sets per section (script-owned; the agent never drafts it), plus a
  leading `stale_brain_line` note (spec A1) when the latest unattended Brain
  hand-off run aborted — written here, not at publish time, so `render`
  (which builds the docx/pdf straight from `next.md`) includes it too.
- `work/<task>/merge.json`: `{task, version, noop, sections: {sid: {status:
  "carried"|"drafted", claims_before, claims_after, kept, reworded, recited,
  added, superseded, dropped_by_check, dropped_by_model, suppressed_tombstone,
  false_stale, modality_flagged}}}` (spec A12; PoC finding I7). A base claim no draft claim
  matched is `dropped_by_check` when its id or normalized text appears in
  this run's `check-file.json.failed`, `check-task.json.failed` or
  `verifier.json.rejected` (a deterministic check or the verifier is why it's
  gone) — everything else `dropped_by_model` (the drafting model silently
  dropped it). `false_stale` (a `"drafted"` section that redrafted and
  changed nothing visible) is `status == "drafted" and added + reworded +
  recited + superseded == 0` — a section can be false-stale and still have
  drops; R4 keeps drops counted within `dropped_by_*` either way.
- Tombstones (spec A8): a drafted claim whose `normalized` text equals a claim
  a person deleted from the published docx in this section
  (`base.json.human_deleted` ∪ `state.json.human_deleted`) is dropped before
  matching and counted in `suppressed_tombstone` — the agent may not bring
  back what a person removed. A tombstoned text the base holds again (a person
  re-added it) is not dropped.
- `modality_flagged` (F2) is this section's count of entries in this run's
  `check-file.json.modality` (the deterministic hedge pre-check: a `[FILE:]`
  claim whose fresh transcript quote is a guess/question/proposal but whose
  own text states it as fact) — counted here for `report --summary`, never
  a drop and never a reason to fail `accept`; the verifier's `overstated`
  rule is the actual gate on those claims.
- `locators_fixed` (F3) is this section's count of this run's `check-file.
  json.relocated` plus `check-file.json.deduped` entries — a `[FILE:]` tag's
  locator corrected to the section that actually holds its quote, or an
  exact-duplicate tag removed. Same style as `modality_flagged`: counted
  here for `report --summary` only, never a drop.
- Noop (A9): if `next.md`'s content — ignoring the header comment, the Changes
  section, and anything a reader would never see (claim-id/origin comments,
  Markdown backslash escapes, curly vs straight quotes, whitespace runs; see
  `claims.visible_text`) — reads identically to the previous version, nothing
  is written and the result carries `"noop": true`. A re-minted claim id or an
  `origin=` flip alone is not a visible change.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scribe_lib import claims
from scribe_lib.basedoc import split_by_section_id
from scribe_lib.config import Config, ScribeError, parse_template_ref, read_state, substitute_params
from scribe_lib.doctor import handoff_status

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


def tombstones(base_json: dict[str, Any], state: dict[str, Any]) -> dict[str, set[str]]:
    """{section: {normalized text}} of every claim a person deleted from the
    published docx (spec A8): this run's `base.json.human_deleted` plus every
    earlier one `publish` kept in `state.json.human_deleted`."""
    out: dict[str, set[str]] = {}
    for t in (base_json.get("human_deleted") or []) + (state.get("human_deleted") or []):
        if t.get("section") and t.get("normalized"):
            out.setdefault(t["section"], set()).add(t["normalized"])
    return out


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _check_failure_keys(work_dir: Path) -> tuple[set[str], list[str]]:
    """`(ids, texts)` gathered from this run's `check-file.json.failed`,
    `check-task.json.failed` and `verifier.json.rejected` — every candidate
    a dropped base claim can be attributed to (A12).

    Three producers, three shapes, all handled:
      - `check-file.json.failed`: `{"section", "claim_ref", "claim",
        "normalized", "tag", "reason"}` (`checkfile.py`) — `claim` is the
        rewritten block's claim id (`None` for a legacy id-less claim),
        `normalized` its exact normalized text, both captured before the
        block was rewritten to `Not modeled:`.
      - `check-task.json.failed`: `{"section", "claim", "tag", "reason"}`
        (`checktask.py`) — `claim` is the claim id.
      - `verifier.json.rejected`: `{"section", "claim", "verdict", "reason"}`
        (`skills/run/SKILL.md` step 6) — `claim` is "`<c:id or first 8
        words>`": either a claim id or a text snippet.

    `claim` (falling back to `claim_id`/`claim_ref` only when a producer has
    neither `claim` nor `normalized` — legacy `check-file.json` from before
    this fix) is recorded both as a candidate id (`c:` prefix stripped) and
    as normalized text, since `verifier.json` conflates the two; `normalized`,
    when present, is recorded as an additional, exact text candidate — this
    is what makes a `check-file` drop with a legacy id-less base claim
    (`claim: None`) still attributable by text."""
    ids: set[str] = set()
    texts: list[str] = []
    entries = (
        _load_json(work_dir / "check-file.json").get("failed") or []
    ) + (
        _load_json(work_dir / "check-task.json").get("failed") or []
    ) + (
        _load_json(work_dir / "verifier.json").get("rejected") or []
    )
    for entry in entries:
        candidate = entry.get("claim")
        normalized = entry.get("normalized")
        if candidate is None and normalized is None:
            candidate = entry.get("claim_id")
            if candidate is None:
                candidate = entry.get("claim_ref")
        if candidate is not None:
            candidate = str(candidate)
            ids.add(candidate[2:] if candidate.startswith("c:") else candidate)
            texts.append(claims.normalize_text(candidate))
        if normalized:
            texts.append(claims.normalize_text(normalized))
    return ids, texts


def _modality_counts(work_dir: Path) -> dict[str, int]:
    """F2: `{section: count}` of this run's `check-file.json.modality`
    entries — the deterministic hedge pre-check's flags, never a drop.
    Read once per merge and folded into each drafted section's report as
    `modality_flagged`."""
    counts: dict[str, int] = {}
    for entry in _load_json(work_dir / "check-file.json").get("modality") or []:
        sid = entry.get("section")
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    return counts


def _locators_fixed_counts(work_dir: Path) -> dict[str, int]:
    """F3: `{section: count}` of this run's `check-file.json.relocated` plus
    `check-file.json.deduped` entries — a corrected `[FILE:]` locator or a
    removed exact-duplicate tag, never a drop. Folded into each drafted
    section's report as `locators_fixed`, same style as `_modality_counts`."""
    counts: dict[str, int] = {}
    check_file = _load_json(work_dir / "check-file.json")
    for entry in (check_file.get("relocated") or []) + (check_file.get("deduped") or []):
        sid = entry.get("section")
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    return counts


def _dropped_by_check(block: dict[str, Any], failed_ids: set[str], failed_texts: list[str]) -> bool:
    claim_id = block.get("claim_id")
    if claim_id and claim_id in failed_ids:
        return True
    normalized = block["normalized"]
    return any(t and (normalized.startswith(t) or t.startswith(normalized)) for t in failed_texts)


def _merge_drafted_section(
    task_id: str,
    sid: str,
    base_body: str,
    draft_text: str,
    tombstoned: set[str] | frozenset[str] = frozenset(),
    failed_ids: set[str] | frozenset[str] = frozenset(),
    failed_texts: tuple[str, ...] = (),
    modality_flagged: int = 0,
    locators_fixed: int = 0,
) -> dict[str, Any]:
    base_blocks = [b for b in claims.parse_blocks(base_body) if claims.is_claim(b)]
    # A drafted claim a person deleted is dropped before matching — unless the
    # base itself holds that text again (a person re-added it after deleting it).
    live = {b["normalized"] for b in base_blocks}
    all_draft = claims.parse_blocks(draft_text)
    draft_blocks = [
        b for b in all_draft if not (claims.is_claim(b) and b["normalized"] in tombstoned and b["normalized"] not in live)
    ]
    suppressed = len(all_draft) - len(draft_blocks)
    results = {id(r["block"]): r for r in claims.match_claims(base_blocks, draft_blocks, task_id, sid)}

    counts = dict.fromkeys(("kept", "reworded", "recited", "added", "superseded"), 0)
    examples: dict[str, list[str]] = {
        "added": [], "reworded": [], "recited": [], "dropped_by_check": [], "dropped_by_model": [], "superseded": [],
    }
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
    dropped_by_check = 0
    dropped_by_model = 0
    for b in dropped_blocks:
        if _dropped_by_check(b, failed_ids, failed_texts):
            dropped_by_check += 1
            examples["dropped_by_check"].append(b["normalized"][:160])
        else:
            dropped_by_model += 1
            examples["dropped_by_model"].append(b["normalized"][:160])

    false_stale = counts["added"] + counts["reworded"] + counts["recited"] + counts["superseded"] == 0

    return {
        "body": claims.render_blocks(rendered),
        "claims_before": len(base_blocks),
        "claims_after": sum(counts.values()),
        **counts,
        "dropped_by_check": dropped_by_check,
        "dropped_by_model": dropped_by_model,
        "suppressed_tombstone": suppressed,
        "false_stale": false_stale,
        "modality_flagged": modality_flagged,
        "locators_fixed": locators_fixed,
        "examples": examples,
    }


def stale_brain_line(config: Config) -> str | None:
    """Spec A1: when the latest unattended Brain hand-off run aborted (nothing
    applied), the Changes section carries `"Brain was not refreshed on <date>:
    <reasons>"` so a reader of the published docx/pdf — not just next.md — sees
    why nothing new made it into this version. Computed here (merge), not at
    publish time: `render` builds the docx/pdf from `work/<task>/next.md`
    BEFORE `publish` ever runs, so a note added after render never reaches the
    rendered deliverable. `None` when the Brain is fresh (or hand-off mode
    isn't configured for this project) — the line disappears on the next
    version once the Brain recovers, since each merge re-renders the whole
    Changes block from scratch rather than accumulating past notes.
    `Path(latest).stem` is the hand-off report's own `<YYYY-MM-DD>.json` name
    (brain-maintenance's `handoff --out`), i.e. the date of the run that
    aborted, not today's merge/publish date.
    """
    status = handoff_status(config)
    if not status.get("stale_brain"):
        return None
    latest = status.get("latest")
    date = Path(latest).stem if latest else "unknown"
    reasons = "; ".join(status.get("reasons") or []) or "no reason recorded"
    return f"Brain was not refreshed on {date}: {reasons}"


def _render_changes(
    sections_spec: list[dict[str, Any]],
    section_reports: dict[str, dict[str, Any]],
    examples_by_section: dict[str, dict[str, list[str]]],
    base_json: dict[str, Any],
    stale_note: str | None = None,
) -> str:
    lines: list[str] = []
    if stale_note:
        lines.append(f"- {stale_note}")
    for sec in sections_spec:
        sid = sec["id"]
        rep = section_reports[sid]
        if rep["status"] == "carried":
            continue
        added, reworded, superseded = rep["added"], rep["reworded"], rep["superseded"]
        dropped = rep.get("dropped_by_check", 0) + rep.get("dropped_by_model", 0)
        recited = rep.get("recited", 0)
        if not (added or dropped or reworded or superseded or recited):
            continue
        lines.append(
            f"- **{sec['title']}**: {added} added, {dropped} removed, "
            f"{reworded} reworded, {superseded} superseded" + (f", {recited} re-cited" if recited else "")
        )
        examples = examples_by_section.get(sid, {})
        shown = 0
        for cat in ("superseded", "dropped_by_check", "dropped_by_model", "reworded", "recited", "added"):
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

    state = read_state(config, instance)
    tombstoned = tombstones(base_json, state)
    failed_ids, failed_texts = _check_failure_keys(work_dir)
    modality_counts = _modality_counts(work_dir)
    locators_fixed_counts = _locators_fixed_counts(work_dir)

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
            report = _merge_drafted_section(
                task_id, sid, base_sections.get(sid, ""), draft_text, tombstoned.get(sid, frozenset()),
                failed_ids, tuple(failed_texts), modality_counts.get(sid, 0),
                locators_fixed_counts.get(sid, 0),
            )
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
                "added": 0,
                "superseded": 0,
                "dropped_by_check": 0,
                "dropped_by_model": 0,
                "suppressed_tombstone": 0,
                "false_stale": False,
                "modality_flagged": 0,
                "locators_fixed": 0,
            }

    prev_version = state.get("version") or 0
    new_version = prev_version + 1
    base_version = base_json.get("base_version")
    base_edited = bool(base_json.get("base_edited"))

    header = (
        f"<!-- scribe: task={task_id} version={new_version} built_at={_built_at(config)} "
        f"template={template_id}@{template_version} base={_base_field(base_version, base_edited)} -->"
    )
    changes_text = _render_changes(
        sections_spec, section_reports, examples_by_section, base_json,
        stale_note=stale_brain_line(config),
    )

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
    noop = bool(prev_version) and claims.visible_text(
        _strip_header_and_changes(next_text)
    ) == claims.visible_text(_strip_header_and_changes(prev_text))

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
