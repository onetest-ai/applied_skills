"""prepare: delta + base + gather-raw + fingerprint in one go, writing
`work/<task>/pack/` — the one directory the drafting agent reads.

  pack/plan.json        {"task", "stale": [{"section", "reasons"}], "carried": [sid, ...], "noop": bool}
  pack/base.md           copy of work/<task>/base.md
  pack/<sid>.pack.md     one per STALE section only: the template section's
                          intent/must/lanes, the template's drafting-guidance
                          body, the prior section text from base (with its
                          carried claim ids, so the agent can see what a
                          citation is replacing), the top evidence (brain +
                          raw hits from fingerprint.json, each item preceded
                          by the exact tag to cite — `[RAG:<id>]` or
                          `[FILE:<path>#<locator>]` — text truncated to
                          ~1200 chars; brain hits are tag-filtered ∪ untagged
                          (R10) and ordered tag-filtered-first-by-score, then
                          untagged-only-by-score, so a newly synced,
                          not-yet-classified chunk stays visible without
                          crowding out tagged evidence), a note for every
                          cited chunk/raw file
                          fingerprint found changed or gone/removed, and — for
                          a task with `inputs.tasks` — its upstream tasks'
                          latest published claims, each tagged
                          `[TASK:<task id>#c:<claim id>]`. When the prior
                          text holds human-authored claims (comment
                          `origin=human|human_modified`, or no id at all),
                          they are also listed under "Human-authored claims"
                          so the agent copies them verbatim and never sends
                          them to the verifier. Claims a person deleted from
                          the published docx (`base.json.human_deleted` ∪
                          `state.json.human_deleted`, as normalized text) are
                          listed under "Removed by a person — do not re-add:"
                          (merge drops them anyway).

If `base` refuses (a section heading renamed or deleted in the docx), prepare
returns base's `{"status": "failed", "reason": "section_heading_changed", ...}`
and writes no pack.

`noop` = no stale sections AND base.json's `base_edited` is False — nothing
for the agent to do this run.

prepare starts a run, so it first deletes the previous run's per-run files
under `work/<task>/` (`sections/`, `render/`, `next.md`, `merge.json`,
`check-file.json`, `verifier.json`).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from scribe_lib import claims as claim_parser
from scribe_lib.basedoc import base_task, parse_units, split_by_section_id
from scribe_lib.config import (
    Config,
    ScribeError,
    compute_brain_delta,
    compute_raw_delta,
    parse_template_ref,
    read_state,
    resolve_instance_inputs,
    substitute_params,
    validate_all,
)
from scribe_lib.fingerprint import fingerprint_task
from scribe_lib.raw import gather_raw_task

EVIDENCE_TRUNCATE = 1200


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _collect_upstream_claims(config: Config, instances: dict[str, Any], upstream_tasks: list[str]) -> dict[str, str]:
    claims: dict[str, str] = {}
    for up in upstream_tasks:
        inst = instances.get(up)
        if not inst:
            continue
        state = read_state(config, inst)
        version = state.get("version")
        if not version:
            continue
        src = config.out_root / inst["out"] / "_src" / f"v{version:03d}.md"
        if not src.is_file():
            continue
        sections = split_by_section_id(src.read_text(encoding="utf-8"))
        for body in sections.values():
            for unit in parse_units(body, with_ids=True):
                if unit["claim_id"]:
                    claims[f"[TASK:{up}#c:{unit['claim_id']}]"] = unit["text"]
    return claims


def _render_pack_section(
    sec: dict[str, Any],
    template: dict[str, Any],
    prior_text: str,
    info: dict[str, Any],
    upstream_claims: dict[str, str],
    removed: list[str] | None = None,
) -> str:
    lines: list[str] = [f"# Section: {sec.get('title', sec['id'])} ({sec['id']})", ""]
    if sec.get("intent"):
        lines += ["## Intent", "", sec["intent"], ""]
    if sec.get("kind"):
        lines += [f"Kind: {sec['kind']}", ""]
    if sec.get("lanes"):
        lines += [f"Lanes: {', '.join(str(l) for l in sec['lanes'])}", ""]
    if sec.get("must"):
        lines += ["## Must", ""] + [f"- {m}" for m in sec["must"]] + [""]
    guidance = (template.get("_body") or "").strip()
    if guidance:
        lines += ["## Drafting guidance", "", guidance, ""]

    lines += ["## Prior text (from base)", ""]
    lines += [prior_text.strip() or "(none — first run or section not present in base)", ""]

    human = [b for b in claim_parser.parse_blocks(prior_text) if claim_parser.base_claim_origin(b) in claim_parser.HUMAN_ORIGINS]
    if human:
        lines += [
            "## Human-authored claims (copy verbatim; never verify, never turn into Not modeled)",
            "",
        ]
        for b in human:
            ident = f"c:{b['claim_id']}" if b["claim_id"] else "no id"
            lines.append(f"- {ident} origin={claim_parser.base_claim_origin(b)}: {_truncate(b['content'], 160)}")
        lines.append("")

    if removed:
        lines += ["## Removed by a person — do not re-add:", ""]
        lines += [f"- {_truncate(text, 160)}" for text in removed]
        lines.append("")

    lines += ["## Evidence", ""]
    if not info.get("brain_hits") and not info.get("raw_hits"):
        lines += ["(no hits for this section's queries)", ""]
    # Tag-filtered hits first (by score desc), then hits found only via the
    # untagged search (by score desc) — controller ruling R10: a tag-filtered
    # hit is preferred evidence over one that surfaced only because the
    # chunk isn't classified yet.
    ordered_brain_hits = sorted(
        info.get("brain_hits", []),
        key=lambda h: (
            not any(str(v).startswith("tag:") for v in h.get("via") or []),
            -(h.get("score") or 0),
        ),
    )
    for h in ordered_brain_hits:
        tag = f"[RAG:{h['chunk_id']}]"
        lines += [
            f"- {tag} (source={h.get('source')}, section={h.get('section')}, score={h.get('score')})",
            "",
            _truncate(h.get("text", ""), EVIDENCE_TRUNCATE),
            "",
        ]
    for h in info.get("raw_hits", []):
        tag = f"[FILE:{h['path']}#{h['locator']}]"
        lines += [f"- {tag}", "", _truncate(h.get("text", ""), EVIDENCE_TRUNCATE), ""]

    changed_notes = [f"- {cid}: {status}" for cid, status in info.get("cited_chunk_status", {}).items() if status != "same"]
    changed_notes += [f"- {path}: {status}" for path, status in info.get("cited_raw_status", {}).items() if status != "same"]
    lines += ["## Changed/gone cited chunks", ""]
    lines += (changed_notes or ["(none)"]) + [""]

    if upstream_claims:
        lines += ["## Upstream claims", ""]
        lines += [f"- {tag} {_truncate(text, EVIDENCE_TRUNCATE)}" for tag, text in sorted(upstream_claims.items())]
        lines += [""]

    return "\n".join(lines).strip() + "\n"


# Per-run outputs of the steps after prepare. prepare starts every run, so it
# removes them: a noop night must not leave the previous night's drafted
# sections (check-file would re-check them), merge.json/verifier.json (metrics
# would count them again) or render output (publish must only ship tonight's).
_PER_RUN_FILES = ("next.md", "merge.json", "check-file.json", "verifier.json")
_PER_RUN_DIRS = ("sections", "render")


def _clear_previous_run(work_dir: Path) -> None:
    for name in _PER_RUN_FILES:
        (work_dir / name).unlink(missing_ok=True)
    for name in _PER_RUN_DIRS:
        if (work_dir / name).is_dir():
            shutil.rmtree(work_dir / name)


def prepare_task(config: Config, task_id: str) -> dict[str, Any]:
    data = validate_all(config)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    instance = instances[task_id]
    template_id, _ = parse_template_ref(instance["template"])
    template = templates[template_id]
    state = read_state(config, instance)

    _clear_previous_run(config.work_dir / task_id)

    brain_delta = compute_brain_delta(config, state)
    raw_inputs = resolve_instance_inputs(instance, template).get("raw") or {}
    raw_delta = compute_raw_delta(config, raw_inputs, state)

    base_result = base_task(config, task_id, instance, template)
    if base_result.get("status") == "failed":
        # No pack at all, not even last run's: nothing downstream may run on it.
        shutil.rmtree(config.work_dir / task_id / "pack", ignore_errors=True)
        return {"task": task_id, **base_result}
    raw_result = gather_raw_task(config, task_id, instance, raw_inputs)
    fp_result = fingerprint_task(config, task_id, instances, templates, edges)

    work_dir = config.work_dir / task_id
    pack_dir = work_dir / "pack"
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True)

    params = instance.get("params") or {}
    sections_spec = [
        substitute_params(sec, params) for sec in ((template.get("output") or {}).get("sections") or [])
    ]
    stale_entries: list[dict[str, Any]] = []
    carried: list[str] = []
    for sec in sections_spec:
        sid = sec["id"]
        info = fp_result["sections"].get(sid, {})
        if info.get("stale"):
            stale_entries.append({"section": sid, "reasons": info.get("stale_reasons", [])})
        else:
            carried.append(sid)

    noop = not stale_entries and not base_result.get("base_edited")
    plan = {"task": task_id, "stale": stale_entries, "carried": carried, "noop": noop}
    (pack_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

    base_md_path = work_dir / "base.md"
    base_md_text = base_md_path.read_text(encoding="utf-8") if base_md_path.is_file() else ""
    (pack_dir / "base.md").write_text(base_md_text, encoding="utf-8")
    base_sections = split_by_section_id(base_md_text)

    upstream_tasks = resolve_instance_inputs(instance, template).get("tasks") or []
    upstream_claims = _collect_upstream_claims(config, instances, upstream_tasks) if upstream_tasks else {}

    base_json_path = work_dir / "base.json"
    base_json = json.loads(base_json_path.read_text(encoding="utf-8")) if base_json_path.is_file() else {}
    removed_by_section: dict[str, list[str]] = {}
    for t in (state.get("human_deleted") or []) + (base_json.get("human_deleted") or []):
        texts = removed_by_section.setdefault(t.get("section", ""), [])
        if t.get("normalized") and t["normalized"] not in texts:
            texts.append(t["normalized"])

    sections_by_id = {s["id"]: s for s in sections_spec}
    for entry in stale_entries:
        sid = entry["section"]
        sec = sections_by_id[sid]
        info = fp_result["sections"].get(sid, {})
        content = _render_pack_section(
            sec, template, base_sections.get(sid, ""), info, upstream_claims, removed_by_section.get(sid)
        )
        (pack_dir / f"{sid}.pack.md").write_text(content, encoding="utf-8")

    return {
        "status": "ok",
        "task": task_id,
        "plan": plan,
        "delta": {
            "brain": {k: v for k, v in brain_delta.items() if k != "current"},
            "raw": {k: v for k, v in raw_delta.items() if k != "current"},
        },
        "base": {k: v for k, v in base_result.items()},
        "raw_counts": raw_result["counts"],
    }
