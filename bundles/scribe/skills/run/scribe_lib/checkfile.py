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
     "modality": [{"section", "claim", "claim_ref", "tag", "quote_marker"}],
     "relocated": [{"section", "claim", "from", "to"}],
     "deduped": [{"section", "claim", "tag"}]}

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

**F3 — a `[FILE:]` tag's locator is corrected to the section that actually
holds its fresh quote, and an exact-duplicate tag on the same claim is
removed.** Both run once per claim, once the claim is known NOT to be a
human-authored one left exactly as drafted (C4, F3 review fix round 1,
Critical 4 — dedupe/relocation run AFTER the human-origin skip above, on
the claim exactly as the human wrote it; running them first would strip a
human's own duplicate tag before that comparison and make the claim fall
through to "missing evidence quote" instead of being skipped, and even
when it survives, merge would then see a `recited` category and drop its
`origin=human`). Before the pass/fail decision below, and only touching a
tag that has a fresh `evidence.json` quote — a carried tag (sha-verified,
no fresh quote) is never relocated or deduped away from a matching
duplicate check, since it has no entry in `evidence_by_key`. `evidence_by_
key` is looked up by the EXACT `(claim_idx, tag)` key ONLY (C2/C3, F3
review fix round 1, Critical 2/3 — never a `(claim_idx, path)` fallback: a
claim that cites the same file at two different cues must never have one
cue's quote silently verify the other, and a tag with no evidence entry of
its own must still fail "missing evidence quote" exactly as before F3).
`_dedupe_file_tags` removes every `[FILE:]` occurrence after the first with
the IDENTICAL tag string (path AND locator), recording each removal in
`deduped: [{"section", "claim", "tag"}]`. `_relocate_file_tags` then, for
each remaining `[FILE:]` tag with a quote, re-derives the parsed raw file's
sections the SAME way `raw.py` built `raw.sqlite` (`chunking.section_
records`, locator = `breadcrumb_path` or `§<ordinal>`) and checks whether
the quote is in the section the tag names; if not — including when the
tag's locator names no section this parse produced AT ALL (C1(c)) — but the
quote IS in exactly one section, the tag's locator is rewritten to it;
several equally-close matches (by section-index distance from the named
locator, or all equally near when the locator named no section) rewrite to
the first in document order and add `"ambiguous": true` to the `relocated`
entry (`[{"section", "claim", "from", "to"}]`). A quote found in no section
at all is left alone and falls through to fail exactly as before ("quote
not found in `<path>`"). When a tag IS relocated, its `evidence_by_key`
entry is re-keyed to the new tag and the entry's own `"tag"` field
rewritten to match, and the section's `evidence.json` is rewritten to
disk — so a later run's exact-tag lookup still finds the same quote under
the tag as it now reads, without any path-based guessing (this is what
C2/C3's exact-only lookup needs to stay idempotent). Because a
correctly-relocated tag's named section already holds the quote, a second
run makes no further change to it. `report.py`'s `report --summary` totals
both as `locators_fixed` (relocated + deduped), same style as
`modality_flagged` — I5 (F3 review fix round 1): since `check-file.json` is
overwritten on every run, `_merge_dedup` carries an earlier run's
`relocated`/`deduped` entries (from THIS same prepare cycle — `prepare`
clears `check-file.json` between actual cycles) forward into this run's
result, so a fix made on an earlier, now-idempotent-and-silent run is
still counted.

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
`unconfirmed`, ... — see `_CLAIM_MODALITY_MARKERS`) — matched against
`block["normalized"]` (tags and the claim-id comment already stripped),
never `block["text"]`: the raw text ends with its `[FILE:path#loc]` tag, so
matching the untouched text makes the trailing-`?` case dead and lets a word
inside the cited PATH (a transcript named after a date, e.g.
`2026-may-12 sync.vtt`, contains `may`) suppress every flag for every claim
citing it (fix round 1, Important #2). A hedged quote under an unhedged
claim is appended to `modality` (never rewritten — that's a drafting
decision, not a deterministic one): `[{"section", "claim", "claim_ref",
"tag", "quote_marker"}]` (`claim_ref` — the positional index `merge.json`'s
`dropped_by_*` accounting also uses — locates a freshly drafted claim, whose
`claim` is `None` until `merge` assigns an id). `check-file.json`'s
top-level shape gains this one key; every other field is unchanged. A
human-authored claim is never flagged (skipped before the check runs), and
a claim this same run rewrites to `Not modeled:` or leaves in
`needs_quote` is never flagged either — only a claim that PASSES check-file
gets its flags merged into `modality` (fix round 1, Minor). `merge.py`
counts these per section as `modality_flagged` (not a drop, not a failure)
and `accept.py` never reads `modality` at all — a claim left flagged after
the SKILL's revise-once retry does not fail the task; the verifier's
`overstated` rejection is the actual gate. The deterministic pre-check
itself stays `[FILE:]`-only (only those claims carry a fresh quote to
examine); the SKILL's drafting rule and the verifier's `overstated` clause
are scoped wider, to any claim whose evidence is conversational
(`[FILE:]` or a `[RAG:]` chunk from a transcript source) — see
`skills/run/SKILL.md` steps 4 and 6."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scribe_lib import claims
from scribe_lib import parsing
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
    claim_normalized: str,
    file_tags: list[str],
    evidence_by_key: dict[tuple[int, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """F2: for each of the claim's `[FILE:]` tags with a fresh evidence
    quote, flag (never rewrite) the tag when that quote is hedged but the
    claim's own text carries no attribution/hedge marker of its own —
    otherwise the meeting's guess/question/proposal reads as a stated fact.
    A claim that already keeps the speaker's modality is never flagged, no
    matter how its quote reads.

    `claim_normalized` MUST be `block["normalized"]` (`claims.normalize_text`
    with tags already stripped), never `block["text"]`: a raw claim's text
    ends with its `[FILE:...]` tag, so matching against it makes the
    trailing-`?` check dead (the tag always comes last) and lets a word
    inside the cited PATH — a transcript is routinely named after a date,
    e.g. `2026-may-12 sync.vtt` — suppress every flag for every claim citing
    it (fix round 1, Important #2). `normalized` has the tag gone entirely,
    so only the claim's own prose can suppress a flag."""
    if _claim_has_modality_marker(claim_normalized):
        return []
    flags: list[dict[str, Any]] = []
    for tag in file_tags:
        entry = evidence_by_key.get((claim_idx, tag))
        if entry is None:
            continue
        marker = _find_hedge_marker(entry.get("quote", ""))
        if marker is None:
            continue
        flags.append(
            {"section": sid, "claim": claim_id, "claim_ref": claim_idx, "tag": tag, "quote_marker": marker}
        )
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


def _dedupe_file_tags(raw: str) -> tuple[str, list[str]]:
    """Remove every `[FILE:...]` occurrence after the first with an
    IDENTICAL (unescaped) tag string from `raw`; returns `(new_raw,
    removed_tags)` in the order removed. A single leading space before a
    removed tag is swallowed too, so no double space is left behind."""
    seen: set[str] = set()
    spans: list[tuple[int, int]] = []
    removed: list[str] = []
    for m in claims.TAG_RE.finditer(raw):
        kind, body = m.group(1), m.group(2)
        if kind != "FILE":
            continue
        tag = f"[{kind}:{claims.unescape_tag_body(body)}]"
        if tag in seen:
            spans.append(m.span())
            removed.append(tag)
        else:
            seen.add(tag)
    if not spans:
        return raw, []
    new_raw = raw
    for start, end in sorted(spans, reverse=True):
        s = start
        if s > 0 and new_raw[s - 1] == " ":
            s -= 1
        new_raw = new_raw[:s] + new_raw[end:]
    return new_raw, removed


def _replace_tag_in_raw(raw: str, old_tag: str, new_tag: str) -> str:
    """Replace the first occurrence of `old_tag` (matched after unescaping)
    in `raw` with the literal `new_tag` text."""
    for m in claims.TAG_RE.finditer(raw):
        kind, body = m.group(1), m.group(2)
        if f"[{kind}:{claims.unescape_tag_body(body)}]" == old_tag:
            return raw[: m.start()] + new_tag + raw[m.end() :]
    return raw


def _refresh_block_from_raw(block: dict[str, Any], new_raw: str) -> None:
    """Recompute `text`/`content`/`tags` after an in-place tag-only edit to
    `raw` (dedupe/relocation) — the claim id comment, `kind`, `superseded`
    and `normalized` are untouched by a tag-only edit, so they are left as
    they were parsed."""
    block["raw"] = new_raw
    text = new_raw
    m = claims.CLAIM_ID_RE.search(text)
    if m:
        text = claims.CLAIM_ID_RE.sub("", text).rstrip()
    if block["kind"] != "code":
        text = claims.unescape_tags(text)
    content = text
    if block["kind"] == "bullet" and content.lstrip().startswith("- "):
        content = content.lstrip()[2:]
    block["text"] = text
    block["content"] = content
    block["tags"] = claims.tags_in(content) if block["kind"] != "code" else []


def _file_sections(config: Config, raw_text: str) -> list[tuple[str, str]]:
    """`[(locator, body), ...]` in document order — the SAME locator scheme
    `raw.py` writes into `raw.sqlite` (`breadcrumb_path`, else `§<ordinal>`,
    1-based per file), recomputed from `chunking.section_records` on the
    parsed raw Markdown so a `[FILE:<path>#<locator>]` tag can be relocated
    to a locator string that means the same thing `raw.py` would have meant
    by it."""
    chunking = parsing.load_chunking(config)
    return [
        (rec.get("breadcrumb_path") or f"§{i}", rec.get("body", ""))
        for i, rec in enumerate(chunking.section_records(raw_text), start=1)
    ]


def _relocate_file_tags(
    config: Config,
    sid: str,
    block: dict[str, Any],
    claim_idx: int,
    evidence_by_key: dict[tuple[int, str], dict[str, Any]],
    manifest: dict[str, dict[str, Any]],
    raw_dir: Path,
    relocated: list[dict[str, Any]],
) -> tuple[bool, bool]:
    """F3: rewrite a `[FILE:]` tag's locator to the section that actually
    contains its fresh evidence quote, when the section it currently names
    does not (or names no section at all — C1(c), F3 review fix round 1:
    relocated to the containing/nearest section rather than left alone,
    since there is no section it names that could hold the quote, so the
    drafted locator is simply wrong, not merely displaced). Mutates `block`
    in place; appends to `relocated`. Returns `(block_changed,
    evidence_changed)`.

    C2/C3 (F3 review fix round 1, Critical 2/3): looks up a tag's quote by
    the EXACT (claim_idx, tag) key ONLY — never falling back to another tag
    on the same path — so a tag with no evidence entry of its own never
    borrows a sibling tag's quote (a claim that cites the same file twice,
    at two different cues, must not have one cue's quote silently verify
    the other). When a tag IS relocated, its `evidence_by_key` entry is
    re-keyed to the NEW tag and the entry's own `"tag"` field is rewritten
    to match (the caller persists this to `evidence.json`), so a later run's
    exact-tag lookup still finds the same quote under the tag as it now
    reads — this is what makes relocation idempotent without a path
    fallback. Never touches a tag with no fresh quote (a carried tag,
    verified by sha not text) or a quote that is found nowhere in the
    parsed raw file at all — that still fails, unchanged, in the normal
    check below."""
    block_changed = False
    evidence_changed = False
    for tag in [t for t in block["tags"] if t.startswith("[FILE:")]:
        entry = evidence_by_key.get((claim_idx, tag))
        quote = (entry or {}).get("quote") or ""
        if not quote:
            continue
        _, value = claims.parse_tag(tag)
        path, _, locator = value.partition("#")
        manifest_entry = manifest.get(path)
        if manifest_entry is None or manifest_entry.get("status") != "ok":
            continue
        md_rel = manifest_entry.get("md") or f"{path}.md"
        raw_md_path = raw_dir / md_rel
        if not raw_md_path.is_file():
            continue
        sects = _file_sections(config, raw_md_path.read_text(encoding="utf-8"))
        norm_quote = _normalize_ws(quote)
        named_idx = next((i for i, (loc, _) in enumerate(sects) if loc == locator), None)
        if named_idx is not None and norm_quote in _normalize_ws(sects[named_idx][1]):
            continue  # already in the section it names
        matches = [i for i, (_, body) in enumerate(sects) if norm_quote in _normalize_ws(body)]
        if not matches:
            continue  # nowhere in the parsed file — falls through to fail as before
        # `named_idx is None` (the locator names no section at all) picks
        # the nearest by treating every match as equally near (first in
        # document order), flagged ambiguous along with the rest when it
        # isn't the only one.
        if named_idx is not None:
            best = min(abs(i - named_idx) for i in matches)
            candidates = [i for i in matches if abs(i - named_idx) == best]
        else:
            candidates = matches
        new_locator = sects[candidates[0]][0]
        if new_locator == locator:
            continue
        new_tag = f"[FILE:{path}#{new_locator}]"
        entry_out: dict[str, Any] = {"section": sid, "claim": block["claim_id"], "from": locator, "to": new_locator}
        if len(candidates) > 1:
            entry_out["ambiguous"] = True
        relocated.append(entry_out)
        _refresh_block_from_raw(block, _replace_tag_in_raw(block["raw"], tag, new_tag))
        block_changed = True
        del evidence_by_key[(claim_idx, tag)]
        entry["tag"] = new_tag
        evidence_by_key[(claim_idx, new_tag)] = entry
        evidence_changed = True
    return block_changed, evidence_changed


def _load_prev_check_file(work_dir: Path) -> dict[str, Any]:
    path = work_dir / "check-file.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _merge_dedup(
    prev: list[dict[str, Any]], new: list[dict[str, Any]], key_fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    """`prev + new`, deduped by `key_fields`, prev entries first — I5 (F3
    review fix round 1): a re-run within the same prepare cycle overwrites
    `check-file.json`, so a fix an earlier run in this cycle already made
    (and which this run's idempotent re-check therefore finds nothing new
    to do) must be carried forward or `report --summary`'s `locators_fixed`
    undercounts it. `prepare` clears `check-file.json` between actual
    cycles, so this never accumulates across nights."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for entry in prev + new:
        key = tuple(entry.get(k) for k in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def check_file_task(config: Config, task_id: str, instance: dict[str, Any] | None = None) -> dict[str, Any]:
    work_dir = config.work_dir / task_id
    sections_dir = work_dir / "sections"
    raw_dir = work_dir / "raw"
    manifest = _load_manifest(raw_dir)
    base_claims_by_section = _base_claims_by_section(work_dir)
    state_sections = (read_state(config, instance).get("sections") or {}) if instance else {}
    raw_available = raw_root_available(config)
    prev_result = _load_prev_check_file(work_dir)

    checked = 0
    passed = 0
    human_skipped = 0
    failed: list[dict[str, Any]] = []
    needs_quote: list[dict[str, Any]] = []
    raw_offline_notes: list[dict[str, Any]] = []
    modality: list[dict[str, Any]] = []
    relocated: list[dict[str, Any]] = []
    deduped: list[dict[str, Any]] = []

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
            "relocated": [],
            "deduped": [],
        }
        (work_dir / "check-file.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    for section_path in sorted(sections_dir.glob("*.md")):
        sid = section_path.stem
        evidence = _load_evidence(sections_dir, sid)
        # Keyed on the unescaped tag, like `block["tags"]`: the agent may copy an
        # escaped tag (`\_`, `\>` from a docx round trip) from the prior text.
        evidence_by_key = {(e["claim_ref"], claims.unescape_tags(e["tag"])): e for e in evidence}
        evidence_dirty = False
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

            # C4 (F3 review fix round 1, Critical 4): the human-origin skip
            # runs BEFORE dedupe/relocation, on the claim exactly as
            # drafted — a human-authored claim (text AND tags unchanged
            # from base) is never touched by check-file at all, dedupe and
            # relocation included. Running dedupe first would strip a
            # human's own duplicate tag before this comparison, making the
            # claim no longer match its base claim's `claim_key` and fall
            # through to a "missing evidence quote" rewrite instead of
            # being skipped — destroying a human-authored claim and, when
            # it survives, stripping its `origin=human` at merge.
            base_match = _find_carried_base_claim(base_claims, block)
            if (
                base_match is not None
                and claims.claim_key(base_match) == claims.claim_key(block)
                and claims.base_claim_origin(base_match) in claims.HUMAN_ORIGINS
            ):
                human_skipped += 1
                continue

            # F3 — dedupe exact-duplicate [FILE:] tags, then relocate any
            # remaining tag whose named section doesn't hold its fresh
            # quote to the section that does. A carried (non-human) tag is
            # untouched by relocation (no fresh evidence quote to check it
            # against).
            new_raw, removed_tags = _dedupe_file_tags(block["raw"])
            if removed_tags:
                _refresh_block_from_raw(block, new_raw)
                for tag in removed_tags:
                    deduped.append({"section": sid, "claim": block["claim_id"], "tag": tag})
                changed = True
            block_relocated, tags_evidence_changed = _relocate_file_tags(
                config, sid, block, claim_idx, evidence_by_key, manifest, raw_dir, relocated,
            )
            if block_relocated:
                changed = True
            if tags_evidence_changed:
                evidence_dirty = True
            file_tags = [t for t in block["tags"] if t.startswith("[FILE:")]

            # Fix round 1, Minor: computed here (a human-authored claim is
            # never flagged — the `continue` above already skipped it) but
            # only merged into `modality` once the claim is known to PASS
            # (below) — a claim check-file itself rewrites to `Not modeled:`
            # this run needs no modality flag, and a `needs_quote` claim has
            # no evidence entry to judge yet.
            pending_modality = _modality_flags(
                sid, claim_idx, block["claim_id"], block["normalized"], file_tags, evidence_by_key
            )

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
                modality.extend(pending_modality)
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

        if evidence_dirty:
            # C2 (F3 review fix round 1, Critical 2) — persist a relocated
            # tag's renamed `evidence.json` entry, so a later run's
            # exact-tag lookup finds it under the tag as it now reads.
            (sections_dir / f"{sid}.evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    # I5 (F3 review fix round 1): carry forward an earlier run's
    # `relocated`/`deduped` entries from THIS same prepare cycle — this
    # run's own idempotent re-check finds nothing left to fix for a tag an
    # earlier run in the cycle already relocated/deduped, so without this
    # merge `locators_fixed` would undercount every cycle that needed the
    # prescribed check-file re-run (SKILL.md steps 5's `needs_quote`/
    # `modality` retries).
    relocated = _merge_dedup(prev_result.get("relocated") or [], relocated, ("section", "claim", "from", "to"))
    deduped = _merge_dedup(prev_result.get("deduped") or [], deduped, ("section", "claim", "tag"))

    result = {
        "task": task_id,
        "checked": checked,
        "passed": passed,
        "human_origin_skipped": human_skipped,
        "failed": failed,
        "needs_quote": needs_quote,
        "raw_offline_notes": raw_offline_notes,
        "modality": modality,
        "relocated": relocated,
        "deduped": deduped,
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "check-file.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
