"""observe: record what a noop run saw, without publishing anything.

Defect (replay Nights 2-4): a task can end a run as a noop — `prepare`'s
`plan.json` has no stale sections (`fingerprint.py` found nothing new against
the Brain/raw index), or `merge`'s `next.md` would be byte-identical to the
previous version — and in both cases nothing calls `publish`, so
`state.json`'s `raw_snapshot`/`brain_snapshot`/`upstream_versions` and every
section's `fingerprint` are left exactly as they were after the LAST publish.
The next night's `plan`/`fingerprint` then compares the Brain/raw as they are
NOW against that stale snapshot, finds the same new raw file or the same
already-considered Brain change again, and reports the task due for the same
reason forever — even though this run already looked and found nothing worth
drafting.

`observe_task` closes that loop: it updates ONLY the observation fields —
`raw_snapshot`, `brain_snapshot`, `upstream_versions`, each section's
`fingerprint` (from `work/<task>/fingerprint.json`, written by `prepare` on
every run, noop or not), and `last_checked` (an ISO timestamp) — leaving
`version`, `published`, and the top-level `cited_chunks`/`cited_raw` exactly
as they were. A noop must never look like a publish: no version bump, no
published-file hash, no citation is recorded as if new content had been
drafted and merged.

A9 / I6: a section's per-section `cited_chunks`/`cited_raw`/`cited_task_claims`
are the one exception. They are recomputed — via `publish.cited_from_section`,
never touching any published file — from the latest published `_src/vNNN.md`'s
section body, and written into `state["sections"][sid]`. Without this, a
section whose cited Brain chunk (or upstream `[TASK:]` claim, spec A6) changed
text stays "stale" forever after a noop run: `fingerprint.py` compares
`prior.get("cited_chunks")`/`prior.get("cited_task_claims")` against the
CURRENT chunk/upstream-claim text every night, and a noop never re-published
to refresh what "prior" means — so the same drift is reported night after
night even though the redraft that would fix it keeps coming out
byte-identical (a noop, per `merge.py`'s A9 visible-text comparison).
Recomputing against the text that is still actually published re-settles
that comparison; it is not a citation from new content, since nothing new
was drafted or merged.

Call this once a run has decided a task is a noop (`plan.json.noop` from
`prepare`, or `merge.json.noop` from `merge`) — see `scribe.py observe
<task>` and the `scribe:run` SKILL.md's noop paths.
"""
from __future__ import annotations

import json
from typing import Any

from scribe_lib.basedoc import split_by_section_id
from scribe_lib.config import (
    Config,
    read_state,
    read_synced_files,
    resolve_instance_inputs,
    raw_snapshot,
    state_path,
)
from scribe_lib.publish import cited_from_section


def _observed_at(config: Config) -> str:
    return config.now if "T" in config.now else f"{config.now}T00:00:00"


def observe_task(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
    instances: dict[str, Any],
    edges: dict[str, list[str]],
) -> dict[str, Any]:
    state = read_state(config, instance)
    raw_inputs = resolve_instance_inputs(instance, template).get("raw") or {}

    fp_path = config.work_dir / task_id / "fingerprint.json"
    fp_sections = (
        json.loads(fp_path.read_text(encoding="utf-8")).get("sections", {}) if fp_path.is_file() else {}
    )

    # `fingerprint` refreshes from tonight's search/raw scan. `cited_chunks`/
    # `cited_raw` never come from tonight's draft (nothing was merged) — they
    # are recomputed from the section bodies of the version that is STILL
    # published, so a noop settles rather than re-reporting the same drift
    # forever (A9/I6).
    version = state.get("version")
    published_sections: dict[str, str] = {}
    if version:
        src_path = config.out_root / instance["out"] / "_src" / f"v{version:03d}.md"
        if src_path.is_file():
            published_sections = split_by_section_id(src_path.read_text(encoding="utf-8"))

    sections_state: dict[str, Any] = {sid: dict(info) for sid, info in (state.get("sections") or {}).items()}
    for sid, info in fp_sections.items():
        entry = dict(sections_state.get(sid) or {})
        entry["fingerprint"] = info.get("fingerprint")
        if sid in published_sections:
            cited = cited_from_section(config, published_sections[sid], instances)
            entry["cited_chunks"] = cited["cited_chunks"]
            entry["cited_raw"] = cited["cited_raw"]
            entry["cited_task_claims"] = cited["cited_task_claims"]
        else:
            entry.setdefault("cited_chunks", {})
            entry.setdefault("cited_raw", {})
            entry.setdefault("cited_task_claims", {})
        sections_state[sid] = entry

    upstream_versions = {
        up: (read_state(config, instances[up]).get("version") or 0) for up in edges.get(task_id, [])
    }

    new_state = {
        **state,
        "sections": sections_state,
        "brain_snapshot": read_synced_files(config.brain_db),
        "raw_snapshot": raw_snapshot(config, raw_inputs),
        "upstream_versions": upstream_versions,
        "last_checked": _observed_at(config),
    }
    path = state_path(config, instance)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(new_state, indent=2), encoding="utf-8")

    return {
        "task": task_id,
        "observed": True,
        "sections": sorted(sections_state.keys()),
        "last_checked": new_state["last_checked"],
    }
