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
    raw_snapshot,
    read_state,
    read_synced_files,
    resolve_instance_inputs,
    sha256_file,
)
from scribe_lib.merge import parse_header


def run_report_path(config: Config) -> Path:
    """`out/_runs/<SCRIBE_NOW date>.json` — one run report per (replayed) day."""
    return config.out_root / "_runs" / f"{config.now[:10]}.json"


def append_run(config: Config, entry: dict[str, Any]) -> None:
    """Append one row to the run report. Also used by `scribe.py report` for
    rows publish never sees (noop, failures before publish, skipped tasks)."""
    path = run_report_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    rows.append(entry)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


_append_run = append_run


def cited_from_section(config: Config, body: str, instances: dict[str, Any] | None = None) -> dict[str, Any]:
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
                full = config.raw_root / path
                cited_raw[path] = sha256_file(full) if full.is_file() else None
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

    state = read_state(config, instance)
    prev_version = state.get("version") or 0

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

    publish_mode = instance.get("publish", "auto")
    src_dir = out_dir / "_src"
    src_dir.mkdir(parents=True, exist_ok=True)

    if publish_mode == "propose":
        pending_dir = out_dir / "_pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        (pending_dir / f"v{new_version:03d}.md").write_text(next_text, encoding="utf-8")
        if not no_render:
            shutil.copy2(docx_src, pending_dir / f"v{new_version:03d}.docx")
            shutil.copy2(pdf_src, pending_dir / f"v{new_version:03d}.pdf")

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
        (pending_dir / f"v{new_version:03d}.diff.md").write_text(diff + "\n", encoding="utf-8")

        _append_run(config, {"task": task_id, "version": new_version, "status": "pending", "reasons": []})
        return {"status": "ok", "task": task_id, "published": False, "pending_version": new_version}

    # -- auto --
    stable_docx = out_dir / f"{instance['title']}.docx"
    stable_pdf = out_dir / f"{instance['title']}.pdf"
    docx_sha = pdf_sha = None
    if not no_render:
        # Only move the current stable files aside once we have new ones to
        # replace them with — --no-render must never leave out_dir with
        # neither a stable file nor a fresh one.
        if prev_version and (stable_docx.is_file() or stable_pdf.is_file()):
            versions_dir = out_dir / "_versions"
            versions_dir.mkdir(parents=True, exist_ok=True)
            if stable_docx.is_file():
                shutil.move(str(stable_docx), str(versions_dir / f"v{prev_version:03d}_{config.now[:10]}.docx"))
            if stable_pdf.is_file():
                shutil.move(str(stable_pdf), str(versions_dir / f"v{prev_version:03d}_{config.now[:10]}.pdf"))

        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(docx_src, stable_docx)
        shutil.copy2(pdf_src, stable_pdf)
        docx_sha = sha256_file(stable_docx)
        pdf_sha = sha256_file(stable_pdf)

    (src_dir / f"v{new_version:03d}.md").write_text(next_text, encoding="utf-8")
    md_sha = hashlib.sha256(next_text.encode("utf-8")).hexdigest()

    fp_path = work_dir / "fingerprint.json"
    fp_sections = json.loads(fp_path.read_text(encoding="utf-8")).get("sections", {}) if fp_path.is_file() else {}

    next_sections = split_by_section_id(next_text)
    sections_state: dict[str, Any] = {}
    all_cited_chunks: dict[str, str] = {}
    all_cited_raw: dict[str, str] = {}
    for sid, body in next_sections.items():
        cited = cited_from_section(config, body, instances)
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

    new_state = {
        **state,
        "human_deleted": _human_deleted(state, work_dir, next_sections),
        "version": new_version,
        "built_at": header["built_at"],
        "published": {"docx_sha256": docx_sha, "pdf_sha256": pdf_sha, "md_sha256": md_sha},
        "sections": sections_state,
        "cited_chunks": all_cited_chunks,
        "cited_raw": all_cited_raw,
        "brain_snapshot": read_synced_files(config.brain_db),
        "raw_snapshot": raw_snapshot(config, raw_inputs),
        "upstream_versions": upstream_versions,
        "upstream_claims_snapshot": live_upstream_claims(config, instances, edges.get(task_id, [])),
    }
    (src_dir / "state.json").write_text(json.dumps(new_state, indent=2), encoding="utf-8")

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
