"""review: propose-mode review queue (task 12).

`publish_task`'s `propose` mode (`publish.py`) stages a version's Markdown
+ render + diff into `out/<task>/_pending/vNNN/` (`doc.md`, `doc.docx`,
`doc.pdf`, `diff.md`, `pending.json`: `{task, version, built_at, merge}`,
and `state.json`: the FULL `_src/state.json` this version would get if
published right now — sections/fingerprints/cited_*/snapshots, computed
once at propose time by the same `_compute_new_state` an `auto` publish
uses) without touching `_src`/`state.json`. This module is the only thing
that turns a staged proposal into a real publish (`approve`) or discards
one (`reject`) — `list_pending` just reads what is on disk.

**`approve` builds state ONLY from the staged proposal** (review fix round
1, Important #2), never from `work/<task_id>/` at approval time — a
proposal can sit for days while nightly `prepare` runs keep overwriting
`work/<task_id>/`, and reading it live at approve time would record
fingerprints/snapshots the approved Markdown was never actually built
from. `state.json` staged in the proposal, and `pending.json["merge"]`,
are the only inputs `commit_fresh` uses for this commit's `state.json` and
run-row `merge` counts.

**`approve` commits through the SAME journaled path `publish.py`'s `auto`
mode uses** (`publish.resume_leftover_journal` / `publish.commit_fresh`,
task 9's `_src/.pending/journal.json` — stage, archive_previous,
place_new, write_src, write_state), never a second ad-hoc writer. A crash
mid-`approve` leaves exactly the journal a crash mid-`auto`-`publish`
would, and either a retried `approve` or a plain `publish` resumes it the
same way (`resume_leftover_journal` is called first, unconditionally, by
both).

**A stale proposal is refused, never silently committed.** `approve`
refuses when the pending version is not exactly `state.version + 1` — an
intervening `auto` publish (or a second proposal that got approved first
on some other task graph) means this proposal's base has moved; the user
must re-propose."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from scribe_lib import publish as publish_mod
from scribe_lib.config import Config, ScribeError, parse_template_ref, read_state, validate_all
from scribe_lib.index import build_index
from scribe_lib.lineage import lineage_task
from scribe_lib.merge import parse_header
from scribe_lib.report import append_run


HUMAN_EDIT_SINCE_PROPOSAL = "human_edit_since_proposal"


def _stable_docx(config: Config, instance: dict[str, Any]) -> Path:
    return config.out_root / instance["out"] / f"{instance['title']}.docx"


def _pending_root(config: Config, instance: dict[str, Any]) -> Path:
    return config.out_root / instance["out"] / "_pending"


def _latest_pending_dir(config: Config, instance: dict[str, Any]) -> Path | None:
    """The one reviewable proposal for a task, or `None`. `publish_task`'s
    propose mode already removes every older `vNNN/` when it stages a new
    one, so at most one should ever exist — `sorted(...)[-1]` is just
    defense in depth, not the mechanism that keeps only one around."""
    root = _pending_root(config, instance)
    if not root.is_dir():
        return None
    dirs = sorted(d for d in root.iterdir() if d.is_dir() and d.name.startswith("v"))
    return dirs[-1] if dirs else None


def _read_meta(pending_dir: Path) -> dict[str, Any]:
    meta_path = pending_dir / "pending.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def list_pending(config: Config) -> list[dict[str, Any]]:
    """One row per task with a reviewable proposal: `task`, `version`,
    `path` (the `_pending/vNNN/` dir), `diff` (the unified diff text),
    `stale` (review fix round 1, Minor: `True` when an intervening publish
    has already moved `state.version` past what this proposal was built
    against — `version != state.version + 1` — the same condition `approve`
    itself refuses on. Surfaced here so the user learns it before asking to
    approve, not from `approve`'s refusal)."""
    data = validate_all(config)
    out: list[dict[str, Any]] = []
    for task_id, instance in data["instances"].items():
        pending_dir = _latest_pending_dir(config, instance)
        if pending_dir is None:
            continue
        meta = _read_meta(pending_dir)
        diff_path = pending_dir / "diff.md"
        diff = diff_path.read_text(encoding="utf-8") if diff_path.is_file() else ""
        version = meta.get("version")
        state = read_state(config, instance)
        prev_version = state.get("version") or 0
        stale_reason = None
        if version != prev_version + 1:
            stale_reason = "version_moved"
        elif publish_mod.docx_edited(_stable_docx(config, instance), state):
            stale_reason = HUMAN_EDIT_SINCE_PROPOSAL
        out.append(
            {
                "task": task_id,
                "version": version,
                "path": str(pending_dir),
                "diff": diff,
                "stale": stale_reason is not None,
                "stale_reason": stale_reason,
            }
        )
    return out


def approve(config: Config, task_id: str) -> dict[str, Any]:
    """Commit the task's pending proposal through the journaled publish
    path and delete the pending dir. Refuses (never commits) a proposal
    whose version isn't exactly `state.version + 1`."""
    data = validate_all(config)
    instances, templates, edges = data["instances"], data["templates"], data["edges"]
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    instance = instances[task_id]
    template_id, _ = parse_template_ref(instance["template"])
    template = templates[template_id]

    out_dir = config.out_root / instance["out"]
    src_dir = out_dir / "_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    state = read_state(config, instance)

    # Resume-before-anything-else (same rule as `publish_task`'s `auto`
    # mode): a leftover journal from a prior crashed commit — whether that
    # commit was an `auto` publish or a prior `approve` — always takes
    # priority over starting a new one.
    resumed = publish_mod.resume_leftover_journal(
        config, task_id, instance, template, instances, edges, out_dir, src_dir, state, no_render=False
    )
    if resumed is not None:
        if resumed.get("status") == "ok" and isinstance(resumed.get("version"), int):
            # The journal being resumed may be one a PRIOR `approve` call
            # left behind (rather than an `auto` publish) — its proposal
            # dir under `_pending/` is otherwise never cleaned up, since
            # this call is returning here rather than reaching the normal
            # post-commit cleanup below.
            stale_pending = _pending_root(config, instance) / f"v{resumed['version']:03d}"
            resumed.update(_lineage_and_index(
                config, task_id, instance, template, instances,
                stale_pending if stale_pending.is_dir() else None,
            ))
            if stale_pending.is_dir():
                shutil.rmtree(stale_pending, ignore_errors=True)
        return resumed

    pending_dir = _latest_pending_dir(config, instance)
    if pending_dir is None:
        reason = f"no pending proposal for task '{task_id}'"
        append_run(config, {"task": task_id, "version": None, "status": "failed", "reasons": [reason]})
        return {"status": "error", "task": task_id, "reason": reason}

    meta = _read_meta(pending_dir)
    version = meta.get("version")
    prev_version = state.get("version") or 0
    if version != prev_version + 1:
        reason = (
            f"pending v{version} for task '{task_id}' is stale — v{prev_version} is already "
            f"published (expected v{prev_version + 1}); re-propose"
        )
        append_run(config, {"task": task_id, "version": None, "status": "failed", "reasons": [reason]})
        return {"status": "error", "task": task_id, "reason": reason}

    # Final-review I1: a person edited the LIVE docx after this proposal was built (a
    # reviewer fixing something in place, then approving). Committing would archive
    # their fix away — refuse; the proposal is stale (`list` says so) and the next
    # `prepare` reads the edit as its base.
    if publish_mod.docx_edited(_stable_docx(config, instance), state):
        meta["stale_reason"] = HUMAN_EDIT_SINCE_PROPOSAL
        (pending_dir / "pending.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        reason = (
            f"published docx for task '{task_id}' was edited since pending v{version} was "
            f"proposed; re-propose (the next run drafts from the edit)"
        )
        append_run(config, {"task": task_id, "version": None, "status": "failed",
                            "reasons": [HUMAN_EDIT_SINCE_PROPOSAL, reason]})
        return {"status": "error", "task": task_id, "reason": HUMAN_EDIT_SINCE_PROPOSAL, "detail": reason}

    next_text = (pending_dir / "doc.md").read_text(encoding="utf-8")
    header = parse_header(next_text)
    docx_src = pending_dir / "doc.docx"
    pdf_src = pending_dir / "doc.pdf"
    docx_staged, pdf_staged = docx_src.is_file(), pdf_src.is_file()
    if docx_staged != pdf_staged:
        # Review fix round 1, Minor: a proposal with one rendered file but
        # not the other (a prior copy that got interrupted, or on-disk
        # tampering) must be refused, not silently treated as unrendered
        # (which would then hit `shutil.copy2` on the missing file inside
        # `commit_fresh` and raise an uncaught `FileNotFoundError`, never a
        # clean `failed` result).
        reason = f"pending proposal for task '{task_id}' is partially staged (docx={docx_staged}, pdf={pdf_staged})"
        append_run(config, {"task": task_id, "version": None, "status": "failed", "reasons": [reason]})
        return {"status": "error", "task": task_id, "reason": reason}
    no_render = not docx_staged

    # Task 12 review, Important #2: `state.json` staged in the proposal
    # (computed by `publish_task`'s propose branch, AT PROPOSE TIME, from
    # `work/<task_id>/` as it stood then) is the ONLY source `approve` uses
    # to build the published `state.json` — never a live recompute from
    # `work/<task_id>/`, which may hold a different `prepare`'s output by
    # now. Same for the run row's `merge` counts (`pending.json["merge"]`).
    state_snapshot_path = pending_dir / "state.json"
    if not state_snapshot_path.is_file():
        reason = f"pending proposal for task '{task_id}' is missing its staged state.json — re-propose"
        append_run(config, {"task": task_id, "version": None, "status": "failed", "reasons": [reason]})
        return {"status": "error", "task": task_id, "reason": reason}
    precomputed_state = json.loads(state_snapshot_path.read_text(encoding="utf-8"))
    merge_override = meta.get("merge")

    result = publish_mod.commit_fresh(
        config, task_id, instance, template, instances, edges, out_dir, src_dir, state, version,
        next_text, header, docx_src, pdf_src, no_render,
        precomputed_state=precomputed_state, merge_override=merge_override,
    )
    if result.get("status") == "ok":
        result.update(_lineage_and_index(config, task_id, instance, template, instances, pending_dir))
        shutil.rmtree(pending_dir, ignore_errors=True)
    return result


def _lineage_and_index(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
    instances: dict[str, Any],
    pending_dir: Path | None,
) -> dict[str, Any]:
    """Final-review I5: an approved version gets the same `vNNN.lineage.json`/
    `sources.json` and reverse-index entry an `auto` publish gets from run step 7
    (`scribe.py lineage` + `scribe.py index`), or `index --query` never sees a
    propose-mode task's claims. Built from the fingerprint/base the proposal froze at
    propose time when present (else `work/<task>/`, a pre-fix proposal). The version is
    already committed, so a failure here is reported (`lineage_error`), never raised."""
    kwargs: dict[str, Path] = {}
    if pending_dir is not None:
        for key, name in (("fingerprint_path", "fingerprint.json"), ("base_json_path", "base.json")):
            if (pending_dir / name).is_file():
                kwargs[key] = pending_dir / name
    try:
        lineage = lineage_task(config, task_id, instance, template, instances, **kwargs)
        keys = len(build_index(config))
    except Exception as exc:  # noqa: BLE001 — the commit already happened; report, don't mask it
        return {"lineage_error": f"{type(exc).__name__}: {exc}"}
    return {"lineage_path": lineage["lineage_path"], "index_keys": keys}


def reject(config: Config, task_id: str, reason: str) -> dict[str, Any]:
    """Discard the task's pending proposal and record why."""
    data = validate_all(config)
    instances = data["instances"]
    if task_id not in instances:
        raise ScribeError(f"unknown task '{task_id}'")
    instance = instances[task_id]

    pending_dir = _latest_pending_dir(config, instance)
    if pending_dir is None:
        # Review fix round 1, Minor: a mistyped task id, or a proposal
        # already approved/rejected by someone else, must not write a false
        # "rejected" audit row for something that was never reviewed.
        reason_missing = f"no pending proposal for task '{task_id}'"
        append_run(config, {"task": task_id, "version": None, "status": "failed", "reasons": [reason_missing]})
        return {"status": "error", "task": task_id, "reason": reason_missing}

    version = _read_meta(pending_dir).get("version")
    shutil.rmtree(pending_dir, ignore_errors=True)

    append_run(
        config,
        {"task": task_id, "version": version, "status": "rejected", "reason": reason, "reasons": [reason]},
    )
    return {"status": "ok", "task": task_id, "rejected": True, "version": version}
