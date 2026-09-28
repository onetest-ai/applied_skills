"""publish: version + write `work/<task>/next.md` into `out/<task.out>/`.

Requires `work/<task>/next.md` (from `merge`) and, unless `--no-render`,
`work/<task>/render/<title>.docx` + `.pdf` (from `render`; `--no-render`
publishes the Markdown-only version so the pipeline can be exercised end to
end without a render pass).

`instance["publish"]` (`auto` | `propose`) decides the path:

- **auto**: the current stable `<title>.docx/.pdf` (if any) moves to
  `_versions/vMMM_<date>.<ext>` (MMM = the version being replaced), the new
  ones are written under the stable name, `_src/vNNN.md` is written, and
  `_src/state.json` advances — see `_build_state` for the exact shape (kept
  compatible with what `fingerprint`/`prepare` read: `version`, `built_at`, `published`,
  `sections` (per-section `fingerprint`/`cited_chunks`/`cited_raw`/`cited_task_claims`, per
  `fingerprint.py`'s documented contract), `brain_snapshot`, `raw_snapshot`,
  `upstream_versions`, `upstream_claims_snapshot` (task-wide: every LIVE claim
  currently cite-able across this task's upstream tasks, spec A6 controller
  ruling — `checktask.live_upstream_claims`, compared by `fingerprint.py`
  against a fresh recomputation to catch NEW upstream material for a
  `tasks`-lane section, independent of `upstream_versions`); `cited_chunks`/
  `cited_raw` are ALSO written at the top level as a whole-document union —
  an addition, not a rename; `human_deleted` is every tombstone so far,
  `_human_deleted`).
- **propose**: writes `_pending/vNNN.md[.docx/.pdf]` + `vNNN.diff.md` (a
  unified diff of the Markdown against the previous published version); state
  is not advanced.

Refuses (never publishes) if `next.md`'s header version isn't exactly
`state.version + 1` — a stale `next.md` (left over from an earlier noop merge,
or from a version already published) must never be republished. Also refuses
when `next.md` is simply absent, distinguishing "merge was a noop" (status
`noop` in the run log) from "merge was never run" (status `failed`).

Every call appends one row to `out/_runs/<date>.json`.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from scribe_lib import brain as brain_mod
from scribe_lib import claims
from scribe_lib.basedoc import split_by_section_id
from scribe_lib.checktask import _upstream_claims, live_upstream_claims
from scribe_lib.config import (
    Config,
    ScribeError,
    raw_root_available,
    raw_snapshot,
    read_state,
    read_synced_files,
    resolve_instance_inputs,
    sha256_file,
)
from scribe_lib.merge import parse_header

# Journal step order (A10 — journaled atomic publish, PoC m1). `publish_task`
# ("auto" mode) records each finished step to `journal.json` before starting
# the next, and on entry finishes any journal left by a prior crashed run
# before considering whether a NEW publish is due. This is what lets a
# publish that dies partway (process kill, `_write_state` raising, a locked
# file) resume exactly where it stopped, rather than re-doing already-applied
# moves (which would double-archive) or leaving the stable docx/pdf missing
# (which would leave the synced folder with neither the old nor the new
# version — the failure mode this task exists to close).
_JOURNAL_STEPS = ("stage", "archive_previous", "place_new", "write_src", "write_state")

# `run_report_path`/`append_run` now live in `report.py` (A12 — that module
# owns the run report end to end, including embedding each task's
# `merge.json` counts onto its row). Re-imported here, not redefined, so
# every existing `_append_run(...)` call in this file keeps working, and a
# published row picks up its `merge` counts the same way `report --status`'s
# rows do — through `append_run` itself, not a second code path.
from scribe_lib.report import append_run, merge_counts_for_row, run_report_path  # noqa: E402

_append_run = append_run


def cited_from_section(
    config: Config,
    body: str,
    instances: dict[str, Any] | None = None,
    prior_cited_raw: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """What a section's merged claims actually cite, keyed by tag kind:
    `{"cited_chunks": {chunk_id: text_hash|None}, "cited_raw": {path: sha256|None},
    "cited_task_claims": {"<up>#<claim_id>": text_hash|None}}` (renamed from
    the private `_cited_from_section`, now a dict so a later kind extends the
    shape without another positional return value). Used by both `publish`
    (recording what a version cites) and `observe` (recomputing a noop's
    cited_* from the still-published text, spec A9).

    A tag is recorded with a `None` value, never omitted, when its evidence
    is gone (RAG) or its raw file no longer exists (FILE) — review fix round
    1, Important 2: `observe` overwrites the prior per-section `cited_*` with
    this result every run, so a chunk/file that dropped out of the dict
    entirely would silently stop being checked at all, and `fingerprint.py`
    would never report `cited_chunk_gone`/`raw_file_removed` for it again.
    Keeping the key (value `None`) keeps it checked; `fingerprint.py` already
    treats any non-`ok` evidence as `gone` regardless of the prior hash, and
    a missing raw file as `removed` regardless of the prior sha.

    **Review fix round 2, Important #1**: a `[FILE:]` tag's sha is recorded
    as `None` only when the raw root is REACHABLE and the file is genuinely
    gone. When `raw_root_available(config)` is `False`, the file's presence
    can't be checked at all — writing `None` here would carry the mass-drop
    bug from round 1 exactly one run later: the NEXT fingerprint run would
    read that `None` as `raw_file_changed`/gone (fingerprint compares the
    RECORDED sha, not the raw root's availability), and `check-file`'s
    `_carried_raw_reason` would compare against a `None` prior sha and fail
    every carried claim. So while offline, this function carries the PRIOR
    state's sha for that path forward unchanged (`prior_cited_raw`, this
    section's `state.sections.<sid>.cited_raw` before this run) — the same
    "freeze, don't delete" contract `raw.py`'s `gather_raw_task` already
    applies to the parsed corpus. `prior_cited_raw` is `None`/omitted for a
    caller that has no prior state to carry (first publish) or genuinely
    doesn't care (a few narrow tests) — the tag is then recorded `None`,
    same as before this fix, since there is nothing to carry forward.

    `[TASK:up#c:id]` tags (spec A6) record `up#id -> text_hash(normalize_text(
    upstream claim's content))`, read from `up`'s latest published `_src` via
    `checktask._upstream_claims`. `None` when `up` is unknown, that claim id
    no longer exists there, OR it exists but is now `**Superseded (...):**`
    (review fix round 1, Important 1: `normalize_text` strips the superseded
    marker, so a naive hash comparison reports "same" for a claim that became
    superseded — recording `None` instead forces `fingerprint.py` to treat it
    as needing attention on every run until the citing section is redrafted,
    the same "record, never omit" rule as a gone claim). `instances` is
    optional (callers that never cite `[TASK:]`, or tests that only exercise
    RAG/FILE, may omit it) — with no `instances`, any `[TASK:]` tag is simply
    recorded as gone (`None`), never silently dropped."""
    cited_chunks: dict[str, str | None] = {}
    cited_raw: dict[str, str | None] = {}
    cited_task_claims: dict[str, str | None] = {}
    upstream_cache: dict[str, dict[str, dict]] = {}
    raw_available = raw_root_available(config)
    for block in claims.parse_blocks(body):
        for tag in block.get("tags", []):
            kind, value = claims.parse_tag(tag)
            if kind == "RAG":
                ev = brain_mod.evidence(config, value)
                cited_chunks[str(value)] = (
                    brain_mod.text_hash(ev.get("text", "")) if ev.get("status") == "ok" else None
                )
            elif kind == "FILE":
                path = value.split("#", 1)[0]
                if raw_available:
                    full = config.raw_root / path
                    cited_raw[path] = sha256_file(full) if full.is_file() else None
                else:
                    cited_raw[path] = (prior_cited_raw or {}).get(path)
            elif kind == "TASK":
                up, _, cid = value.partition("#c:")
                if up not in upstream_cache:
                    upstream_cache[up] = (
                        _upstream_claims(config, instances[up]) if instances and up in instances else {}
                    )
                target = upstream_cache[up].get(cid)
                cited_task_claims[f"{up}#{cid}"] = (
                    brain_mod.text_hash(claims.normalize_text(target["content"]))
                    if target is not None and not target.get("superseded")
                    else None
                )
    return {"cited_chunks": cited_chunks, "cited_raw": cited_raw, "cited_task_claims": cited_task_claims}


def _human_deleted(state: dict[str, Any], work_dir: Path, next_sections: dict[str, str]) -> list[dict[str, Any]]:
    """Every tombstone so far (spec A8): the previous `state.human_deleted`
    plus this run's `base.json.human_deleted`, de-duplicated, minus any whose
    text the published section holds again (a person re-added it; merge only
    lets that through from the base)."""
    base_json_path = work_dir / "base.json"
    base_json = json.loads(base_json_path.read_text(encoding="utf-8")) if base_json_path.is_file() else {}
    live = {
        sid: {b["normalized"] for b in claims.parse_blocks(body) if claims.is_claim(b)}
        for sid, body in next_sections.items()
    }
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for t in (state.get("human_deleted") or []) + (base_json.get("human_deleted") or []):
        key = (t.get("section", ""), t.get("normalized", ""))
        if key in seen or key[1] in live.get(key[0], set()):
            continue
        seen.add(key)
        out.append(t)
    return out


def _move(src: Path, dst: Path) -> None:
    """Wraps `os.replace` — an atomic rename on the same filesystem (which
    `out_root`'s `_versions`/stable files and `_src`/`.pending` always are,
    both under the task's `out_dir`). A locked destination or source (Word
    has the docx open) raises `PermissionError`, which `publish_task` catches
    to report `"locked: <path>"` and leave the journal for a retry — never
    caught here, so a test (or a caller) can monkeypatch this one function to
    simulate a lock without touching the real filesystem calls elsewhere in
    this module."""
    os.replace(src, dst)


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic temp-file + `os.replace` — `state.json` is never observed
    half-written, whether by a concurrent reader or by a crash between the
    `write` and the `close`."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _move(tmp, path)


def _journal_path(pending_dir: Path) -> Path:
    return pending_dir / "journal.json"


def _read_journal(pending_dir: Path) -> dict[str, Any] | None:
    path = _journal_path(pending_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_journal(pending_dir: Path, journal: dict[str, Any]) -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = _journal_path(pending_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(journal, indent=2), encoding="utf-8")
    _move(tmp, path)


def _mark_step(pending_dir: Path, journal: dict[str, Any], step: str) -> None:
    journal["steps_done"].append(step)
    _write_journal(pending_dir, journal)


def _compute_new_state(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
    instances: dict[str, Any],
    edges: dict[str, list[str]],
    state: dict[str, Any],
    version: int,
    next_text: str,
    header: dict[str, Any],
    docx_sha: str | None,
    pdf_sha: str | None,
) -> tuple[dict[str, Any], str]:
    """Build the `state.json` dict for `version`, from `next_text` (the
    published Markdown for THAT version — the staged `.pending/vNNN.md` copy
    on a journal resume, `work/<task>/next.md` on a fresh publish; review fix
    round 1, Important #2: never let this be a DIFFERENT version's text).
    Returns `(new_state, md_sha)`."""
    work_dir = config.work_dir / task_id
    md_sha = hashlib.sha256(next_text.encode("utf-8")).hexdigest()

    fp_path = work_dir / "fingerprint.json"
    fp_sections = (
        json.loads(fp_path.read_text(encoding="utf-8")).get("sections", {}) if fp_path.is_file() else {}
    )

    next_sections = split_by_section_id(next_text)
    prior_sections_state = state.get("sections") or {}
    sections_state: dict[str, Any] = {}
    all_cited_chunks: dict[str, str] = {}
    all_cited_raw: dict[str, str] = {}
    for sid, body in next_sections.items():
        prior_cited_raw = (prior_sections_state.get(sid) or {}).get("cited_raw") or {}
        cited = cited_from_section(config, body, instances, prior_cited_raw)
        cited_chunks, cited_raw, cited_task_claims = (
            cited["cited_chunks"], cited["cited_raw"], cited["cited_task_claims"]
        )
        sections_state[sid] = {
            "fingerprint": fp_sections.get(sid, {}).get("fingerprint"),
            "cited_chunks": cited_chunks,
            "cited_raw": cited_raw,
            "cited_task_claims": cited_task_claims,
        }
        all_cited_chunks.update(cited_chunks)
        all_cited_raw.update(cited_raw)

    raw_inputs = resolve_instance_inputs(instance, template).get("raw") or {}
    upstream_versions = {
        up: (read_state(config, instances[up]).get("version") or 0) for up in edges.get(task_id, [])
    }
    # `raw_snapshot` returns `None` — not `{}` — when the synced raw folder is
    # offline (m2); keep the previous snapshot rather than recording what
    # would look like every raw file having vanished.
    fresh_raw_snapshot = raw_snapshot(config, raw_inputs)

    new_state = {
        **state,
        "human_deleted": _human_deleted(state, work_dir, next_sections),
        "version": version,
        "built_at": header["built_at"],
        "published": {"docx_sha256": docx_sha, "pdf_sha256": pdf_sha, "md_sha256": md_sha},
        "sections": sections_state,
        "cited_chunks": all_cited_chunks,
        "cited_raw": all_cited_raw,
        "brain_snapshot": read_synced_files(config.brain_db),
        "raw_snapshot": fresh_raw_snapshot if fresh_raw_snapshot is not None else state.get("raw_snapshot"),
        "upstream_versions": upstream_versions,
        "upstream_claims_snapshot": live_upstream_claims(config, instances, edges.get(task_id, [])),
    }
    return new_state, md_sha


def _permission_error_result(task_id: str, exc: PermissionError) -> dict[str, Any]:
    # `os.replace` sets `.filename` on a real lock (Word has the docx open);
    # a synthetic `PermissionError("locked: ...")` from a test does not, so
    # fall back to the exception's own message — either way the journal is
    # left exactly as far as it got, for a retry.
    detail = str(exc.filename) if exc.filename else str(exc)
    reason = detail if detail.startswith("locked:") else f"locked: {detail}"
    return {"status": "failed", "task": task_id, "reason": reason}


def _resume_journal(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
    instances: dict[str, Any],
    edges: dict[str, list[str]],
    out_dir: Path,
    src_dir: Path,
    pending_dir: Path,
    journal: dict[str, Any],
    state: dict[str, Any],
    no_render: bool,
) -> dict[str, Any]:
    """Finish a journal a prior 'auto' publish left behind (review fix round
    1, Important #2). Built EXCLUSIVELY from the journal's own staged
    content — `.pending/vNNN.{docx,pdf,md}` (or, once `write_src` has run,
    `_src/vNNN.md`) — never from `work/<task>/next.md`, which may already
    hold a different, newer draft by the time this runs (a merge could have
    produced vN+1 while `.pending/` for vN was still sitting there after a
    crash). The caller (`publish_task`) has already confirmed
    `state.version < journal["version"]`, so this journal genuinely still
    needs finishing; a journal already covered by `state.version` is
    discarded by the caller before this is ever called.

    Task 12: `no_render` is read from the journal itself
    (`journal["no_render"]`, written by `commit_fresh` when the journal was
    first created) when present, overriding whatever the CALLER of this
    resume passed — a resumed `review.approve` has no way to know whether
    the leftover journal's ORIGINAL commit (which might have been an `auto`
    publish, or an earlier `approve`) was rendered or not, so the journal
    must carry its own truth rather than trust a possibly-mismatched
    caller-supplied flag. A pre-task-12 journal (no `no_render` key) keeps
    using the caller's value, unchanged."""
    version = journal["version"]
    prev_version = version - 1
    steps_done = set(journal.get("steps_done") or [])
    no_render = journal.get("no_render", no_render)

    stable_docx = out_dir / f"{instance['title']}.docx"
    stable_pdf = out_dir / f"{instance['title']}.pdf"
    staged_docx = pending_dir / f"v{version:03d}.docx"
    staged_pdf = pending_dir / f"v{version:03d}.pdf"
    staged_md = pending_dir / f"v{version:03d}.md"
    written_md = src_dir / f"v{version:03d}.md"
    versions_dir = out_dir / "_versions"
    dst_versions_docx = versions_dir / f"v{prev_version:03d}_{config.now[:10]}.docx"
    dst_versions_pdf = versions_dir / f"v{prev_version:03d}_{config.now[:10]}.pdf"

    try:
        if "archive_previous" not in steps_done:
            if not no_render and prev_version and (stable_docx.is_file() or stable_pdf.is_file()):
                versions_dir.mkdir(parents=True, exist_ok=True)
                if stable_docx.is_file() and not dst_versions_docx.exists():
                    _move(stable_docx, dst_versions_docx)
                if stable_pdf.is_file() and not dst_versions_pdf.exists():
                    _move(stable_pdf, dst_versions_pdf)
            _mark_step(pending_dir, journal, "archive_previous")
            steps_done.add("archive_previous")

        if "place_new" not in steps_done:
            if not no_render:
                out_dir.mkdir(parents=True, exist_ok=True)
                _move(staged_docx, stable_docx)
                _move(staged_pdf, stable_pdf)
            _mark_step(pending_dir, journal, "place_new")
            steps_done.add("place_new")

        if "write_src" not in steps_done:
            _move(staged_md, written_md)
            _mark_step(pending_dir, journal, "write_src")
            steps_done.add("write_src")

        docx_sha = sha256_file(stable_docx) if not no_render and stable_docx.is_file() else None
        pdf_sha = sha256_file(stable_pdf) if not no_render and stable_pdf.is_file() else None

        if "write_state" not in steps_done:
            # `write_src` is guaranteed done by this point (either just now,
            # or in an earlier attempt) — `written_md` is the one and only
            # source of truth for this journal's text from here on.
            next_text = written_md.read_text(encoding="utf-8")
            header = parse_header(next_text)
            new_state, md_sha = _compute_new_state(
                config, task_id, instance, template, instances, edges, state, version,
                next_text, header, docx_sha, pdf_sha,
            )
            _write_state(src_dir / "state.json", new_state)
            _mark_step(pending_dir, journal, "write_state")
            steps_done.add("write_state")
        else:
            md_sha = hashlib.sha256(written_md.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    except PermissionError as exc:
        return _permission_error_result(task_id, exc)

    shutil.rmtree(pending_dir, ignore_errors=True)
    _append_run(config, {"task": task_id, "version": version, "status": "published", "reasons": ["resumed"]})
    return {
        "status": "ok",
        "task": task_id,
        "published": True,
        "version": version,
        "docx_sha256": docx_sha,
        "pdf_sha256": pdf_sha,
        "md_sha256": md_sha,
    }


def resume_leftover_journal(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
    instances: dict[str, Any],
    edges: dict[str, list[str]],
    out_dir: Path,
    src_dir: Path,
    state: dict[str, Any],
    no_render: bool,
) -> dict[str, Any] | None:
    """Resolve any journal `_src/.pending/journal.json` left by a prior
    crashed commit — resume it (finish whatever steps remain) or discard it
    (already applied, or unreadable), before anything else happens. Returns
    the resume result, or `None` when there was nothing to resume (the
    caller then proceeds with its own new commit).

    Public (task 12) so `review.approve` can call it first, exactly as
    `publish_task`'s "auto" mode does — the journal at `_src/.pending/` is
    the SAME file regardless of whether the commit that left it behind was
    an `auto`-mode `publish` or a propose-mode `review.approve` (both go
    through `commit_fresh`/`_resume_journal` below), so a crash during
    either is resumable by a retry of either."""
    pending_dir = src_dir / ".pending"
    journal = _read_journal(pending_dir)
    if journal is None:
        return None
    prev_version = state.get("version") or 0
    journal_version = journal.get("version")
    if not isinstance(journal_version, int):
        # Corrupt/malformed journal (review fix round 2, Minor): never
        # resume from a version we can't trust — discard it the same way an
        # already-applied one is discarded, rather than risk
        # `_resume_journal` formatting/comparing a non-int version.
        shutil.rmtree(pending_dir, ignore_errors=True)
        return None
    if prev_version >= journal_version:
        # `state.json` already reflects this version (or a later one) — the
        # journal is stale bookkeeping only (A10: "a retry finds
        # `.pending/` and completes or discards it").
        shutil.rmtree(pending_dir, ignore_errors=True)
        return None
    # This call's entire job is to resolve the leftover journal (complete
    # it, or report why it couldn't) — never to also decide, in the same
    # call, what a possibly-different `next.md`/pending proposal means. A
    # NEW publish/approve is a separate concern for the next call.
    return _resume_journal(
        config, task_id, instance, template, instances, edges,
        out_dir, src_dir, pending_dir, journal, state, no_render,
    )


def commit_fresh(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
    instances: dict[str, Any],
    edges: dict[str, list[str]],
    out_dir: Path,
    src_dir: Path,
    state: dict[str, Any],
    new_version: int,
    next_text: str,
    header: dict[str, Any],
    docx_src: Path,
    pdf_src: Path,
    no_render: bool,
) -> dict[str, Any]:
    """The journaled atomic commit (A10; PoC m1) — stage `docx_src`/`pdf_src`
    + `next_text` into `_src/.pending/`, then archive_previous, place_new,
    write_src, write_state, each recorded to the journal before the next
    step starts. Used by `publish_task`'s `auto` mode (staging from
    `work/<task>/render/`) AND `review.approve` (task 12; staging from an
    already-proposed `out/<task>/_pending/vNNN/`) — the ONE place a version
    is actually committed through `_src`/`state.json`, so a crash partway
    leaves the same resumable journal `resume_leftover_journal` above
    finishes, regardless of which caller started it.

    Caller has already confirmed a leftover journal was resolved (there was
    none, or it named an older version) — see `resume_leftover_journal`."""
    prev_version = state.get("version") or 0
    stable_docx = out_dir / f"{instance['title']}.docx"
    stable_pdf = out_dir / f"{instance['title']}.pdf"
    pending_dir = src_dir / ".pending"
    staged_docx = pending_dir / f"v{new_version:03d}.docx"
    staged_pdf = pending_dir / f"v{new_version:03d}.pdf"
    staged_md = pending_dir / f"v{new_version:03d}.md"
    written_md = src_dir / f"v{new_version:03d}.md"
    versions_dir = out_dir / "_versions"
    dst_versions_docx = versions_dir / f"v{prev_version:03d}_{config.now[:10]}.docx"
    dst_versions_pdf = versions_dir / f"v{prev_version:03d}_{config.now[:10]}.pdf"

    # `no_render` rides on the journal itself (task 12) so a resume — by
    # `publish_task`'s own retry OR `review.approve` — reads the ORIGINAL
    # commit's rendered-ness back, rather than trusting whatever the resume
    # caller happens to pass (see `_resume_journal`'s docstring).
    journal = {"version": new_version, "steps_done": [], "no_render": no_render}

    try:
        pending_dir.mkdir(parents=True, exist_ok=True)
        if not no_render:
            shutil.copy2(docx_src, staged_docx)
            shutil.copy2(pdf_src, staged_pdf)
        staged_md.write_text(next_text, encoding="utf-8")
        _mark_step(pending_dir, journal, "stage")

        # Only archive the current stable files once new ones are staged to
        # replace them — --no-render must never leave out_dir with neither a
        # stable file nor a fresh one.
        if not no_render and prev_version and (stable_docx.is_file() or stable_pdf.is_file()):
            versions_dir.mkdir(parents=True, exist_ok=True)
            if stable_docx.is_file() and not dst_versions_docx.exists():
                _move(stable_docx, dst_versions_docx)
            if stable_pdf.is_file() and not dst_versions_pdf.exists():
                _move(stable_pdf, dst_versions_pdf)
        _mark_step(pending_dir, journal, "archive_previous")

        if not no_render:
            out_dir.mkdir(parents=True, exist_ok=True)
            _move(staged_docx, stable_docx)
            _move(staged_pdf, stable_pdf)
        _mark_step(pending_dir, journal, "place_new")

        _move(staged_md, written_md)
        _mark_step(pending_dir, journal, "write_src")

        docx_sha = sha256_file(stable_docx) if not no_render and stable_docx.is_file() else None
        pdf_sha = sha256_file(stable_pdf) if not no_render and stable_pdf.is_file() else None

        new_state, md_sha = _compute_new_state(
            config, task_id, instance, template, instances, edges, state, new_version,
            next_text, header, docx_sha, pdf_sha,
        )
        _write_state(src_dir / "state.json", new_state)
        _mark_step(pending_dir, journal, "write_state")
    except PermissionError as exc:
        return _permission_error_result(task_id, exc)

    shutil.rmtree(pending_dir, ignore_errors=True)

    _append_run(config, {"task": task_id, "version": new_version, "status": "published", "reasons": []})
    return {
        "status": "ok",
        "task": task_id,
        "published": True,
        "version": new_version,
        "docx_sha256": docx_sha,
        "pdf_sha256": pdf_sha,
        "md_sha256": md_sha,
    }


def publish_task(
    config: Config,
    task_id: str,
    instance: dict[str, Any],
    template: dict[str, Any],
    instances: dict[str, Any],
    edges: dict[str, list[str]],
    *,
    no_render: bool,
) -> dict[str, Any]:
    work_dir = config.work_dir / task_id
    next_path = work_dir / "next.md"
    out_dir = config.out_root / instance["out"]
    publish_mode = instance.get("publish", "auto")
    src_dir = out_dir / "_src"
    src_dir.mkdir(parents=True, exist_ok=True)

    state = read_state(config, instance)
    prev_version = state.get("version") or 0

    # -- resume (or discard) a leftover journal FIRST, before anything that
    # depends on `next.md` (review fix round 1, Important #2). A journal is
    # read and finished (or thrown away) using ONLY its own staged content —
    # `work/<task>/next.md` may already hold a different, newer draft by the
    # time this runs, and must never be consulted for a journal that names
    # an OLDER version than what `next.md` currently has.
    if publish_mode == "auto":
        resumed = resume_leftover_journal(
            config, task_id, instance, template, instances, edges, out_dir, src_dir, state, no_render
        )
        if resumed is not None:
            return resumed

    if not next_path.is_file():
        merge_path = work_dir / "merge.json"
        if merge_path.is_file() and json.loads(merge_path.read_text(encoding="utf-8")).get("noop"):
            reason = "merge was a noop — nothing to publish"
            run_status = "noop"
        else:
            reason = "no work/<task>/next.md to publish (run merge first)"
            run_status = "failed"
        _append_run(config, {"task": task_id, "version": None, "status": run_status, "reasons": [reason]})
        return {"status": "error", "task": task_id, "reason": reason}

    next_text = next_path.read_text(encoding="utf-8")
    header = parse_header(next_text)
    new_version = header["version"]

    # A stale next.md (e.g. left over from a run whose merge was a noop, or
    # from a version that already got published through some other path)
    # must never be republished — merge.py now deletes next.md on noop, but
    # this is the load-bearing guard: publish only accepts a next.md for
    # EXACTLY the version after the one currently published.
    if new_version != prev_version + 1:
        reason = (
            f"work/{task_id}/next.md is for v{new_version}, but v{prev_version} is already "
            f"published (expected v{prev_version + 1}) — stale next.md, re-run merge"
        )
        _append_run(config, {"task": task_id, "version": None, "status": "failed", "reasons": [reason]})
        return {"status": "error", "task": task_id, "reason": reason}

    render_dir = work_dir / "render"
    docx_src = render_dir / f"{instance['title']}.docx"
    pdf_src = render_dir / f"{instance['title']}.pdf"
    if not no_render:
        missing = [p.name for p in (docx_src, pdf_src) if not p.is_file()]
        if missing:
            reason = f"missing rendered files {missing} under work/{task_id}/render/ (run render, or pass --no-render)"
            _append_run(config, {"task": task_id, "version": None, "status": "failed", "reasons": [reason]})
            return {"status": "error", "task": task_id, "reason": reason}

    if publish_mode == "propose":
        # Task 12: stage into `out_dir/_pending/vNNN/` — a directory
        # distinct from the journaled `_src/.pending/` above (`state.json`
        # is NOT advanced) — and record `pending.json` so `review.py` can
        # list/approve/reject it later. A new proposal replaces any older
        # one for this task (only the latest is reviewable): every
        # existing `vNNN/` under `_pending/` is removed first.
        pending_root = out_dir / "_pending"
        if pending_root.is_dir():
            for child in pending_root.iterdir():
                if child.is_dir() and child.name.startswith("v"):
                    shutil.rmtree(child, ignore_errors=True)
        pending_dir = pending_root / f"v{new_version:03d}"
        pending_dir.mkdir(parents=True, exist_ok=True)
        (pending_dir / "doc.md").write_text(next_text, encoding="utf-8")
        if not no_render:
            shutil.copy2(docx_src, pending_dir / "doc.docx")
            shutil.copy2(pdf_src, pending_dir / "doc.pdf")

        prev_text = ""
        if prev_version:
            prev_path = src_dir / f"v{prev_version:03d}.md"
            prev_text = prev_path.read_text(encoding="utf-8") if prev_path.is_file() else ""
        diff = "\n".join(
            difflib.unified_diff(
                prev_text.splitlines(),
                next_text.splitlines(),
                fromfile=f"v{prev_version:03d}.md",
                tofile=f"v{new_version:03d}.md",
                lineterm="",
            )
        )
        (pending_dir / "diff.md").write_text(diff + "\n", encoding="utf-8")
        (pending_dir / "pending.json").write_text(
            json.dumps(
                {
                    "task": task_id,
                    "version": new_version,
                    "built_at": header["built_at"],
                    "merge": merge_counts_for_row(config, task_id),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        _append_run(config, {"task": task_id, "version": new_version, "status": "pending", "reasons": []})
        return {"status": "pending", "task": task_id, "published": False, "pending_version": new_version}

    # -- auto: journaled atomic publish (A10; PoC m1) --
    #
    # `out_dir/_src/.pending/` holds this run's staged output plus
    # `journal.json`. Any journal left by a PRIOR run was already resolved
    # (resumed or discarded) above, before `next.md` was even read — so
    # starting here always means a brand-new journal for `new_version`.
    return commit_fresh(
        config, task_id, instance, template, instances, edges, out_dir, src_dir, state, new_version,
        next_text, header, docx_src, pdf_src, no_render,
    )
