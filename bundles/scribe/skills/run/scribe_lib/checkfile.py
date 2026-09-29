"""check-file: deterministically verify every `[FILE:]` claim an agent drafted.

For every claim (bullet/paragraph block) in `work/<task>/sections/<sid>.md`
that carries a `[FILE:<path>#<locator>]` tag, the tag is either **carried**
or **new/changed**.

A tag is **carried** when the claim it sits on matches a claim already in
`work/<task>/base.md`'s SAME section — by the claim's `<!-- c:xxxxxxxx -->`
id comment when the draft claim has one, else by normalized-text equality —
and that base claim carries the exact same `[FILE:...]` tag. Per the drafting
contract (`skills/run/SKILL.md`) a claim is only carried with its id comment
when its text is kept verbatim, so this also naturally excludes a reworded
claim even when the agent kept (or wrote) an id comment on it: reworded text
never equals the base claim's normalized text, so it falls through to the
new/changed path below. A carried tag needs no fresh quote: it is verified by
re-checking that the cited raw file's sha256 still equals the one recorded
for this path in the task's published `state.json` (`state.sections.<sid>.
cited_raw`) — the same evidence that was good enough to publish that claim is
still good enough now, as long as the file has not changed underneath it. No
usable state record for the path (never published, or the path was cited
under a different section) falls through to new/changed too — there is
nothing to verify a "carry" against.

**Human-authored claims are skipped**: a draft claim that
matches (same way) a base claim of human origin (`origin=human|
human_modified` in its comment, or a legacy id-less base claim — see
`claims.base_claim_origin`) with the same `claims.claim_key` — text AND tags
unchanged — is not checked at all and needs no quote; a human's own citation
is the human's call. A human claim whose citation changed is the agent's
citation now, and is checked like any other (spec A5). They are counted in
`human_origin_skipped`, not in `checked`.

Every other `[FILE:]` tag (new, changed, or on a claim not found in base) is
new/changed and needs a fresh quote:
  1. `path` must be a parsed, `status: "ok"` entry in `work/<task>/raw/manifest.json`.
  2. The sidecar `work/<task>/sections/<sid>.evidence.json` must have an entry
     `{"claim_ref": <index>, "tag": "[FILE:...]", "quote": "..."}` for this
     claim's 0-based index (among claim blocks in that file) and this tag.
  3. That quote (whitespace-normalized, case-insensitive) must occur in the
     parsed raw Markdown (`work/<task>/raw/<path>.md`).

A claim that fails any of these (for any of its `[FILE:]` tags, carried or
not) is rewritten
in place to `Not modeled: <reason>. <!-- cf:N -->` (keeping its bullet/
paragraph shape, dropping its tags and any claim id — it is no longer a
claim) and recorded in `work/<task>/check-file.json`:

    {"checked": <int>, "passed": <int>, "human_origin_skipped": <int>,
     "failed": [{"section", "claim_ref", "claim", "normalized", "tag", "reason"}],
     "needs_quote": [{"section", "claim", "tag"}],
     "raw_offline_notes": [{"section", "claim_ref", "tag"}],
     "modality": [{"section", "claim", "tag", "quote_marker"}]}

`failed`'s `claim` (the block's `claim_id`, `None` for an id-less legacy
claim) and `normalized` (its normalized text, BEFORE the block is rewritten
to `Not modeled:` below) exist so `merge.py`'s `dropped_by_check`/
`dropped_by_model` attribution (spec A12) can match a base claim this run
drops against this file's failures by id or by text — `claim_ref` alone (a
positional index into THIS drafted file, not the base) can't answer "is the
BASE claim this claim id/text drops the SAME claim a check-file failure just
rewrote".

`checked`/`passed` count *claims* (not tags): a claim with two failing
`[FILE:]` tags is one failure, not two.

**m2 / review fix round 1 — an offline raw root never fails a carried
`[FILE:]` claim.** A carried tag is normally re-verified against
`config.raw_root / path` directly (`_carried_raw_reason`); when
`raw_root_available(config)` is `False` (the synced folder is offline) that
check cannot mean anything — every path would read as missing, which is
exactly the "offline reads as mass deletion" bug this task closes. So a
carried tag is left exactly as drafted and counted `passed` (not `failed`,
not `needs_quote` — it WAS verified once, at publish time, and that
evidence is simply unreachable to re-check right now, not gone), with an
entry appended to `raw_offline_notes` so the run is auditable. A brand-new
or reworded `[FILE:]` tag is unaffected by this — it is checked against
`work/<task>/raw/manifest.json`/the parsed `.md`, which `gather_raw_task`
now freezes rather than empties when offline (see `raw.py`'s module
docstring), so a genuinely new claim still needs a real quote from whatever
was parsed the last time the root was reachable.

**A7/I4 — never silently rewrite a carried `[FILE:]` claim for want of a
quote.** A claim that is carried forward byte-for-byte from base (same
`claims.claim_key`: text AND tags unchanged) but whose `[FILE:]` tag has no
usable `cited_raw` state record (never published, or published under a
different section) and no fresh quote in the evidence sidecar is neither
checked nor failed: it is left exactly as drafted and its
`{"section", "claim", "tag"}` is appended to `needs_quote` instead. This is
the fix for PoC finding I4, where two such claims were dropped (rewritten to
`Not modeled:`) instead of being handed back to the agent/human to supply a
quote for. A claim that is NOT fully carried (new or reworded) still needs a
quote outright and fails as before — `needs_quote` only covers the "this was
fine before, we just have nothing to re-verify it against" case.

**Idempotency.** `evidence.json`'s `claim_ref` is a positional index into the
ORIGINAL drafted file (assigned once, by the agent, before any check-file
run). A naive re-run that only counts *current* claim blocks (bullet/para) to
recompute that index breaks the moment one claim is rewritten to
`Not modeled:` — every later real claim then shifts down by one against the
unchanged sidecar and gets misjudged as "missing evidence quote", silently
destroying valid content on a second pass (fix round 1). The `<!-- cf:N -->`
comment appended to a rewritten claim's `Not modeled:` line is how a later
run recovers the ORIGINAL index at that position without re-counting from
scratch: scanning resets the running index to `N` whenever it hits a
`cf:`-marked block (and does not re-check it — it is already resolved), so
every subsequent real claim's recomputed index still lines up with the
`claim_ref`s the sidecar was written against. A second run on unchanged
inputs changes nothing.

**F2 — a hedged transcript quote under an unhedged claim is flagged, never
rewritten.** A meeting transcript's own words often carry a question, a
guess, a hypothesis or a proposal — and a `[FILE:]` claim that quotes one of
those but states it as a plain fact silently launders it into a finding.
`_find_hedge_marker` reads a claim's fresh evidence quote (only a quote
actually present in `evidence.json` — a carried tag's re-verified-by-hash
quote is not re-examined here) for a trailing `?` or a documented hedge word
(`maybe`, `might`, `probably`, ... — see `_HEDGE_MARKERS`); when it finds
one, `_claim_has_modality_marker` checks whether the claim's own text
already carries the speaker's modality (`asked`, `suggested`, `whether`,
`unconfirmed`, ... — see `_CLAIM_MODALITY_MARKERS`). A hedged quote under an
unhedged claim is appended to `modality` (never rewritten — that's a
drafting decision, not a deterministic one): `[{"section", "claim", "tag",
"quote_marker"}]`. `check-file.json`'s top-level shape gains this one key;
every other field is unchanged. `merge.py` counts these per section as
`modality_flagged` (not a drop, not a failure) and `accept.py` never reads
`modality` at all — a claim left flagged after the SKILL's revise-once retry
does not fail the task; the verifier's `overstated` rejection is the actual
gate."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scribe_lib import claims
from scribe_lib.basedoc import split_by_section_id
from scribe_lib.config import Config, raw_root_available, read_state, sha256_file

_CF_MARKER_RE = re.compile(r"<!--\s*cf:(\d+)\s*-->\s*$")

# F2 — deterministic hedge pre-check. A quote carrying one of these (or
# ending `?`) is a guess/question/proposal/hypothesis in the transcript's own
# words, not a stated fact. Longest phrases first so `_find_hedge_marker`
# reports "most likely" rather than the "likely" substring within it.
_HEDGE_MARKERS = (
    "most likely", "i think", "i guess", "i believe", "my hypothesis",
    "should we", "could we", "what if", "not sure",
    "hypothesis", "maybe", "might", "probably", "possibly", "likely",
    "perhaps", "assume", "assumption", "tbd",
)

# A claim whose own text already carries one of these already attributes or
# hedges the statement (a question mark in the claim itself is handled by
# the same `?` case as the quote). "hypothes" is deliberately a prefix match
# (hypothesis/hypothesized/hypothesizing/...).
_CLAIM_MODALITY_MARKERS = (
    "according to", "asked", "suggested", "proposed", "unconfirmed",
    "whether", "may", "might", "possibly", "likely", "reportedly", "said",
    "believes", "thinks", "question", "hypothes",
)


def _word_re(marker: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(marker)}" + (r"" if marker == "hypothes" else r"(?!\w)"))


def _find_hedge_marker(quote: str) -> str | None:
    """The hedge marker (or `"?"`) found in `quote`, or `None`. Case-
    insensitive, whole-word; longest phrase wins when more than one marker
    is present."""
    text = (quote or "").strip()
    if text.endswith("?"):
        return "?"
    lowered = text.lower()
    for marker in _HEDGE_MARKERS:
        if _word_re(marker).search(lowered):
            return marker
    return None


def _claim_has_modality_marker(text: str) -> bool:
    """True if the claim's own visible text already keeps the speaker's
    modality (attributed, hedged, or itself a question)."""
    lowered = (text or "").strip().lower()
    if lowered.endswith("?"):
        return True
    for marker in _CLAIM_MODALITY_MARKERS:
        if _word_re(marker).search(lowered):
            return True
    return False


def _modality_flags(
    sid: str,
    claim_idx: int,
    claim_id: str | None,
    claim_text: str,
    file_tags: list[str],
    evidence_by_key: dict[tuple[int, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """F2: for each of the claim's `[FILE:]` tags with a fresh evidence
    quote, flag (never rewrite) the tag when that quote is hedged but the
    claim's own text carries no attribution/hedge marker of its own —
    otherwise the meeting's guess/question/proposal reads as a stated fact.
    A claim that already keeps the speaker's modality is never flagged, no
    matter how its quote reads."""
    if _claim_has_modality_marker(claim_text):
        return []
    flags: list[dict[str, Any]] = []
    for tag in file_tags:
        entry = evidence_by_key.get((claim_idx, tag))
        if entry is None:
            continue
        marker = _find_hedge_marker(entry.get("quote", ""))
        if marker is None:
            continue
        flags.append({"section": sid, "claim": claim_id, "tag": tag, "quote_marker": marker})
    return flags


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _cf_marker(block: dict[str, Any]) -> int | None:
    """The original claim index a previously-rewritten `Not modeled:` block
    stood at, if it carries a `<!-- cf:N -->` marker from an earlier run."""
    if block["kind"] != "not_modeled":
        return None
    m = _CF_MARKER_RE.search(block["raw"])
    return int(m.group(1)) if m else None


def _load_manifest(raw_dir: Path) -> dict[str, dict[str, Any]]:
    path = raw_dir / "manifest.json"
    if not path.is_file():
        return {}
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {e["path"]: e for e in entries}


def _load_evidence(sections_dir: Path, sid: str) -> list[dict[str, Any]]:
    path = sections_dir / f"{sid}.evidence.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _base_claims_by_section(work_dir: Path) -> dict[str, list[dict[str, Any]]]:
    base_path = work_dir / "base.md"
    if not base_path.is_file():
        return {}
    base_sections = split_by_section_id(base_path.read_text(encoding="utf-8"))
    return {
        sid: [b for b in claims.parse_blocks(body) if claims.is_claim(b)]
        for sid, body in base_sections.items()
    }


def _find_carried_base_claim(
    base_claims: list[dict[str, Any]], block: dict[str, Any]
) -> dict[str, Any] | None:
    """The base claim (same section) this draft claim carries forward, or
    None. Matched by claim id when the draft claim has one (and its text is
    still exactly the base claim's — a reworded claim never equals the base
    text even if the agent kept/wrote an id comment on it), else by
    normalized-text equality."""
    claim_id = block["claim_id"]
    for base_block in base_claims:
        if claim_id:
            if base_block["claim_id"] == claim_id and base_block["normalized"] == block["normalized"]:
                return base_block
        elif base_block["normalized"] == block["normalized"]:
            return base_block
    return None


def _carried_raw_reason(config: Config, path: str, prior_sha: str) -> str | None:
    """None if `path` under raw_root still hashes to `prior_sha`; else a
    human-readable reason."""
    full = config.raw_root / path
    if not full.is_file():
        return f"cited raw file missing: {path}"
    if sha256_file(full) != prior_sha:
        return f"cited raw file changed since publish: {path}"
    return None


def check_file_task(config: Config, task_id: str, instance: dict[str, Any] | None = None) -> dict[str, Any]:
    work_dir = config.work_dir / task_id
    sections_dir = work_dir / "sections"
    raw_dir = work_dir / "raw"
    manifest = _load_manifest(raw_dir)
    base_claims_by_section = _base_claims_by_section(work_dir)
    state_sections = (read_state(config, instance).get("sections") or {}) if instance else {}
    raw_available = raw_root_available(config)

    checked = 0
    passed = 0
    human_skipped = 0
    failed: list[dict[str, Any]] = []
    needs_quote: list[dict[str, Any]] = []
    raw_offline_notes: list[dict[str, Any]] = []
    modality: list[dict[str, Any]] = []

    if not sections_dir.is_dir():
        result = {
            "task": task_id,
            "checked": 0,
            "passed": 0,
            "human_origin_skipped": 0,
            "failed": [],
            "needs_quote": [],
            "raw_offline_notes": [],
            "modality": [],
        }
        (work_dir / "check-file.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    for section_path in sorted(sections_dir.glob("*.md")):
        sid = section_path.stem
        evidence = _load_evidence(sections_dir, sid)
        # Keyed on the unescaped tag, like `block["tags"]`: the agent may copy an
        # escaped tag (`\_`, `\>` from a docx round trip) from the prior text.
        evidence_by_key = {(e["claim_ref"], claims.unescape_tags(e["tag"])): e for e in evidence}
        base_claims = base_claims_by_section.get(sid, [])
        cited_raw = (state_sections.get(sid) or {}).get("cited_raw") or {}

        body = section_path.read_text(encoding="utf-8")
        blocks = claims.parse_blocks(body)

        changed = False
        claim_idx = -1
        for block in blocks:
            marker = _cf_marker(block)
            if marker is not None:
                # Already resolved by a previous run — restore the running
                # index to its original position and skip re-checking it.
                claim_idx = marker
                continue
            if not claims.is_claim(block):
                continue
            claim_idx += 1
            file_tags = [t for t in block["tags"] if t.startswith("[FILE:")]
            if not file_tags:
                continue

            modality.extend(
                _modality_flags(sid, claim_idx, block["claim_id"], block["text"], file_tags, evidence_by_key)
            )

            base_match = _find_carried_base_claim(base_claims, block)
            if (
                base_match is not None
                and claims.claim_key(base_match) == claims.claim_key(block)
                and claims.base_claim_origin(base_match) in claims.HUMAN_ORIGINS
            ):
                human_skipped += 1
                continue

            # A7/I4 — a claim that is carried forward byte-for-byte (same
            # `claim_key`: text AND tags unchanged) but has no usable
            # `cited_raw` state record for a `[FILE:]` tag (never published,
            # or published under a different section) and no fresh quote is
            # reported under `needs_quote`, not silently rewritten to
            # `Not modeled:` — the PoC (I4) dropped exactly this case instead
            # of asking for a quote.
            claim_fully_carried = (
                base_match is not None and claims.claim_key(base_match) == claims.claim_key(block)
            )

            reason: str | None = None
            failing_tag: str | None = None
            pending_quote_tag: str | None = None
            for tag in file_tags:
                _, value = claims.parse_tag(tag)
                path = value.split("#", 1)[0]

                carried = (
                    base_match is not None
                    and tag in base_match["tags"]
                    and path in cited_raw
                )
                if carried:
                    if not raw_available:
                        # The synced folder is offline — every path would
                        # read as missing, which is not evidence the file is
                        # gone. Leave the claim as drafted, count it passed,
                        # and note it for the run report (m2).
                        raw_offline_notes.append({"section": sid, "claim_ref": claim_idx, "tag": tag})
                        continue
                    reason = _carried_raw_reason(config, path, cited_raw[path])
                    if reason is not None:
                        failing_tag = tag
                        break
                    continue

                entry = evidence_by_key.get((claim_idx, tag))
                if entry is None:
                    if claim_fully_carried:
                        pending_quote_tag = tag
                        break
                    reason = "missing evidence quote"
                    failing_tag = tag
                    break
                manifest_entry = manifest.get(path)
                if manifest_entry is None or manifest_entry.get("status") != "ok":
                    reason = f"raw file not in manifest: {path}"
                    failing_tag = tag
                    break
                md_rel = manifest_entry.get("md") or f"{path}.md"
                raw_md_path = raw_dir / md_rel
                if not raw_md_path.is_file():
                    reason = f"parsed raw file missing: {md_rel}"
                    failing_tag = tag
                    break
                raw_text = raw_md_path.read_text(encoding="utf-8")
                quote = entry.get("quote", "")
                if _normalize_ws(quote) not in _normalize_ws(raw_text):
                    reason = f"quote not found in {path}"
                    failing_tag = tag
                    break

            if pending_quote_tag is not None:
                needs_quote.append({"section": sid, "claim": block["claim_id"], "tag": pending_quote_tag})
                continue

            checked += 1
            if reason is None:
                passed += 1
                continue

            failed.append(
                {
                    "section": sid,
                    "claim_ref": claim_idx,
                    "claim": block["claim_id"],
                    "normalized": block["normalized"],
                    "tag": failing_tag,
                    "reason": reason,
                }
            )
            prefix = "- " if block["kind"] == "bullet" else ""
            block["raw"] = f"{prefix}Not modeled: {reason}. <!-- cf:{claim_idx} -->"
            block["text"] = f"Not modeled: {reason}."
            block["kind"] = "not_modeled"
            block["tags"] = []
            block["claim_id"] = None
            block["sup_ref"] = None
            block["superseded"] = False
            changed = True

        if changed:
            rendered = [(b, b["raw"]) for b in blocks]
            section_path.write_text(claims.render_blocks(rendered), encoding="utf-8")

    result = {
        "task": task_id,
        "checked": checked,
        "passed": passed,
        "human_origin_skipped": human_skipped,
        "failed": failed,
        "needs_quote": needs_quote,
        "raw_offline_notes": raw_offline_notes,
        "modality": modality,
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "check-file.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
