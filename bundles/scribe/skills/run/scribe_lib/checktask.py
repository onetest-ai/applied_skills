"""check-task: every `[TASK:up#c:id]` claim must cite a claim that is still
live in `up`'s latest published `_src/vNNN.md` (spec A6, PoC findings I5 /
I7-design).

A drafted claim's `[TASK:]` tags are checked against the upstream task's
current published claims, keyed by claim id (`_upstream_claims`, built from
that task's published `_src` text — the PoC published dangling `[TASK:]`
citations because nothing checked them at all):

  - the upstream claim id no longer exists there at all -> `upstream_claim_gone`
  - the upstream claim exists but is now `**Superseded (...):**` -> `upstream_claim_superseded`
  - otherwise the citation passes

A claim with ANY failing `[TASK:]` tag is rewritten in place to
`Not modeled: upstream claim <up>#c:<id> is no longer published.` (naming the
FIRST failing tag), dropping every tag/claim-id comment — it is no longer a
claim, and carries no `[TASK:]` tag, so a second run leaves it alone
(idempotent).

Writes `work/<task>/check-task.json`:

    {"checked": <int>, "passed": <int>,
     "failed": [{"section", "claim", "tag", "reason"}]}

`checked`/`passed` count `[TASK:]` TAGS (a claim with two `[TASK:]` tags,
each checked separately), unlike `check-file`'s per-claim counting — there is
no shared evidence lookup across a claim's tags here, so counting per tag
keeps the numbers meaningful for a claim citing two different upstream
claims.
"""
from __future__ import annotations

import json
from typing import Any

from scribe_lib import brain as brain_mod
from scribe_lib import claims
from scribe_lib.basedoc import split_by_section_id
from scribe_lib.config import Config, read_state

REASON_GONE = "upstream_claim_gone"
REASON_SUPERSEDED = "upstream_claim_superseded"


def _upstream_claims(config: Config, inst: dict[str, Any]) -> dict[str, dict]:
    """`{claim_id: block}` for every claim in `inst`'s latest published
    `_src/vNNN.md`, across all sections. `{}` if the task has never
    published."""
    state = read_state(config, inst)
    if not state.get("version"):
        return {}
    src = config.out_root / inst["out"] / "_src" / f"v{state['version']:03d}.md"
    if not src.is_file():
        return {}
    out: dict[str, dict] = {}
    for body in split_by_section_id(src.read_text(encoding="utf-8")).values():
        for b in claims.parse_blocks(body):
            if claims.is_claim(b) and b.get("claim_id"):
                out[b["claim_id"]] = b
    return out


def live_upstream_claims(config: Config, instances: dict[str, Any], upstream_tasks: list[str]) -> dict[str, str]:
    """`{"<up>#<claim_id>": text_hash(normalize_text(content))}` for every
    LIVE (non-superseded) claim across `upstream_tasks`'s latest published
    versions — a task-wide snapshot of "what could be cited right now",
    independent of what any section actually cites. Two callers:

      - `fingerprint.fingerprint_task` recomputes it every run and compares
        it against `state["upstream_claims_snapshot"]` (written by `publish`/
        `observe`) to decide whether an upstream-consuming section (spec A6
        controller ruling: `tasks` in the section's `lanes`, or it currently
        holds any `cited_task_claims`) should go stale because NEW upstream
        material appeared, not just because a claim it already cites changed.
      - `publish`/`observe` write its result as `state["upstream_claims_snapshot"]`
        so the next fingerprint run has something to compare against.

    A superseded claim is excluded here for the same reason `pack` never
    offers one to cite (spec A6 + Important-1 fix): it is not something a
    section should newly pick up."""
    out: dict[str, str] = {}
    for up in upstream_tasks:
        inst = instances.get(up)
        if not inst:
            continue
        for cid, block in _upstream_claims(config, inst).items():
            if not block.get("superseded"):
                out[f"{up}#{cid}"] = brain_mod.text_hash(claims.normalize_text(block["content"]))
    return out


def check_task_task(config: Config, task_id: str, instances: dict[str, Any]) -> dict[str, Any]:
    work_dir = config.work_dir / task_id
    sec_dir = work_dir / "sections"
    cache: dict[str, dict[str, dict]] = {}
    checked = passed = 0
    failed: list[dict[str, Any]] = []

    if sec_dir.is_dir():
        for path in sorted(sec_dir.glob("*.md")):
            sid = path.stem
            blocks = claims.parse_blocks(path.read_text(encoding="utf-8"))
            changed = False
            for block in blocks:
                task_tags = [t for t in block["tags"] if t.startswith("[TASK:")]
                if not task_tags or not claims.is_claim(block):
                    continue
                reason: str | None = None
                failing_value: str | None = None
                for tag in task_tags:
                    checked += 1
                    _, value = claims.parse_tag(tag)
                    up, _, cid = value.partition("#c:")
                    if up not in cache:
                        cache[up] = _upstream_claims(config, instances[up]) if up in instances else {}
                    target = cache[up].get(cid)
                    if target is None:
                        tag_reason = REASON_GONE
                    elif target.get("superseded"):
                        tag_reason = REASON_SUPERSEDED
                    else:
                        tag_reason = None
                    if tag_reason is None:
                        passed += 1
                        continue
                    failed.append({"section": sid, "claim": block.get("claim_id"), "tag": tag, "reason": tag_reason})
                    if reason is None:
                        reason, failing_value = tag_reason, value
                if reason is None:
                    continue
                changed = True
                prefix = "- " if block["kind"] == "bullet" else ""
                block["raw"] = f"{prefix}Not modeled: upstream claim {failing_value} is no longer published."
                block["text"] = f"Not modeled: upstream claim {failing_value} is no longer published."
                block["kind"] = "not_modeled"
                block["tags"] = []
                block["claim_id"] = None
                block["sup_ref"] = None
                block["origin"] = None
                block["superseded"] = False
            if changed:
                rendered = [(b, b["raw"]) for b in blocks]
                path.write_text(claims.render_blocks(rendered), encoding="utf-8")

    result = {"checked": checked, "passed": passed, "failed": failed}
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "check-task.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
