---
name: brain-maintenance
description: Use when the user asks to refresh, synchronize, maintain, rebuild changed sources, update the knowledge base, publish a new Brain image, deploy a Brain MCP revision, or roll back an update — safely updates and optionally deploys an existing Brain knowledge product. Orchestrates source registry, visual parsing, parsed-store delta, selective classification, conditional marts, verification, and project-profile-driven deployment. The agent owns judgment and gates; scripts perform deterministic operations.
---

# Brain Maintenance

Maintain an existing Brain from registered mother sources through verified deployment.

This skill orchestrates the update; it does not replace the component skills. Load these when their phase is active:

- `visual-parse` for changed visual documents;
- `corpus-taxonomy-extraction` for classification and taxonomy changes;
- `tabular-semantic-layer` when reporting sources or metric configuration changed;
- `knowledge-pipeline` for source registry and parsed-store synchronization.

## Responsibility boundary

The **agent** owns sequencing and judgment:

- classify source-plan actions as narrative/reporting;
- stop on unavailable roots, corruption, ambiguous moves, removal candidates, strict manifest/source mismatches, required empty lanes, or deletion-policy violations;
- ask before any source tombstone or taxonomy change;
- decide which changed documents need visual parsing;
- dispatch and validate all vision/classification subagents;
- decide whether reporting changes require marts rebuild;
- authorize deployment only after local verification.

Deterministic scripts own hashes, plans, source metadata writes, rendering, assembly, indexing, snapshots, SQLite writes, graph/related rebuilds, marts, deployment commands, and smoke checks.

Never collapse the process into a script that silently makes semantic, deletion, taxonomy, or release decisions.

## Required project contract

An update-ready project has the following. Start by copying `profile.example.toml` to project-local `brain-maintenance.toml`, then edit only project-relative paths and policy values; validate it before status. Run commands from the project root with these variables: `$PY` is the skills' venv interpreter from `install.sh --bundle brain --deps` (BRAIN.md's `$PY`, recorded as `[runtime].python` in the profile), `$SKILLS` is the installed skills directory (`[runtime].skills`), and `$DB` is `[paths].db`. With no venv, use `uv run --with-requirements <bundle>/requirements.txt python` in place of `"$PY"`.

```bash
cp "$SKILLS/brain-maintenance/profile.example.toml" "$PROJECT/brain-maintenance.toml"
"$PY" "$SKILLS/brain-maintenance/maintenance.py" validate-profile \
  --profile "$PROJECT/brain-maintenance.toml"
```

```text
brain.toml                         # portable source roots and modes
brain-maintenance.toml             # required maintenance planner profile
brain.deploy.toml                  # optional external-adapter profile; no secrets
parsed/manifest.json               # {source, md} mapping for final parsed documents
schema/knowledge.sqlite OR configured DB path
taxonomy/current.json              # copy of the latest ratified version, or of the draft while taxonomy/PROVISIONAL exists
./brain                            # launcher
```

The database may use another path inside the project, such as `.dsh/knowledge.sqlite`; set it in `[paths].db` in `brain-maintenance.toml`. The maintenance planner deliberately does not accept an ad-hoc `--db`, so the reviewed profile remains the single operational contract.

Source modes:

- `import`: additions/content changes are discovered; absence never implies deletion;
- `mirror`: absence becomes a removal candidate only while the root is available;
- `managed`: Brain-owned files; absence is corruption.

## Update workflow

### 0. Run the doctor

```bash
"$PY" "$SKILLS/knowledge-pipeline/brain_doctor.py" --config brain.toml
```

Stop on exit 1 and show the user the missing items and their install commands before doing
any registry or source work. If it reports `whisper-cli` REQUIRED, run the model question
from `visual-parse` → "Meeting recordings" before continuing.

### 1. Produce a read-only status report

```bash
"$PY" "$SKILLS/brain-maintenance/maintenance.py" status \
  --profile "$PROJECT/brain-maintenance.toml" \
  --out "$PROJECT/.brain-maintenance/runs/status.json"
```

Read the report before changing anything. `ready_to_stage=true` means only that deterministic staging may begin; it does **not** mean ready to apply or deploy. Stop if the report contains unavailable roots, corrupt managed sources, blocked parsed documents, ambiguous moves, or unreviewed removal candidates.

### 2. Review and apply safe source metadata actions

Generate and inspect the canonical source plan:

```bash
./brain source plan --root <root-key> --out source_plan.json
./brain source apply --plan source_plan.json
```

`source apply` applies only add/content-change/move metadata. It never applies `missing`, `corrupt`, or `remove_candidate`. Removal requires the human to approve an explicit:

```bash
./brain source remove <source-id> --yes
```

Do not interpret an unavailable root as an empty root.

Before applying, check the plan's `duplicate_content` groups: a synced source tree (SharePoint/OneDrive/Drive) commonly presents the same SHA-256 at multiple live paths, which would register and re-embed the same document several times. Every copy except the canonical one is a `skip_duplicate` action (`duplicate_of` names the canonical path: the registered copy if there is one, else the first path in sort order), so `apply` registers one copy; show the groups to the human, and `source adopt` a different copy first if they want it canonical; `missing` at an old path after a re-sync is a warning under `import`, never an inferred deletion.

### 3. Materialize changed narrative sources

For every added/changed narrative source:

1. resolve it through `brain.toml`, never by arbitrary filesystem search;
2. preserve the project's stable parsed document id;
3. render pages and inspect `pages.json`;
4. send only flagged uncached pages through `vision_prep` and vision subagents;
5. validate one non-empty result per requested `img_sha`;
6. run `vision_assemble` to produce final enriched Markdown;
7. update `parsed/manifest.json` atomically.

Use a fresh run directory for vision results. Never consume stale or partial `result_*.json`.

**`--formats`'s default now includes `md,markdown,txt,html,htm`.** On an existing corpus/project that predates this, a parse run with default `--formats` (i.e. omitting the flag) will pick up previously-skipped `.md`/`.txt`/`.html`/`.htm` files as new sources on this maintenance pass — a silent behaviour change for deployed projects, not a bug. Expect and review the resulting new/changed doc count in the parsed-store delta below rather than treating it as drift.

**An existing project with `.html`/`.htm` files under its docs root needs its `brain.toml` updated before this pass, or the run fails.** The registry only tracks what its include globs name; if an existing root's globs predate the HTML branch, `parse_corpus.py` will now emit parsed documents for those `.html`/`.htm` files, but `brain_sync.source_ids` has no registered source for them and raises `unmanaged parsed documents` under `--strict-sources` (step 4, below). Add `"**/*.html"` and `"**/*.htm"` to every existing root's `include` list in `brain.toml` before running this pass — `onboard.py`'s glob change only reaches brand-new scaffolds, not projects that already exist.

**The `# fidelity:` header makes every parsed document byte-different on the first pass after this upgrade, for every format, not just HTML.** `parse_corpus.py` now writes a third header line (`# fidelity: full` or `# fidelity: degraded`) on every document it parses. That changes every parsed document's bytes relative to the last run, so the parsed-store delta will show the WHOLE corpus as `changed` and re-embed it on this one pass. That is expected — it is not drift, and it is not a sign the source content changed — but an operator not told this will see a full-corpus re-embed and think something broke.

**Date-aware retrieval costs nothing to re-embed or re-classify on an existing project, but it is not a no-op upgrade — read this before assuming "no cutoff = no change."** `parse_corpus.py` now records an exact document date (`event_date`, with `date_source` of `text`, `filename`, or `none`) in `manifest.json` whenever the document's own text or filename carries a full day+month+year — never a partial date, and never for transcripts or spreadsheets — and the parsed `.md` bytes are unchanged, so this maintenance pass re-indexes and re-classifies nothing on its own (NFD-named files are the one exception — see below). `brain_sync` copies that date onto each chunk and, only if `brain.toml` sets `[corpus].supersede_before = "YYYY-MM-DD"`, marks chunks of exact-dated documents older than the cutoff `status='SUPERSEDED'` (recorded in `date_superseded` by chunk id, `valid_to` set to the cutoff) instead of deleting anything; `as_of` still reaches them for history. Five things actually change on upgrade, cutoff or not:

1. **`search_knowledge`'s default flips.** It now hides every `SUPERSEDED` chunk by default (`latest_only=True`), including one a human hid by hand with `knowledge_index.py supersede` long before this feature existed. Pass `latest_only=false` or `as_of` to see them again.
2. **Exact dates land on undated chunks regardless of the cutoff.** `brain_sync` writes `event_date`/`valid_from` onto every chunk whose `event_date` is still NULL as soon as `parse_corpus.py` finds one in the manifest — this happens on every apply/seed, cutoff set or not; it never overwrites a date `knowledge_index` already derived from the chunk's own text.
3. **NFD-named files re-embed and reclassify once.** A source whose on-disk filename is NFD-normalized (common from some export tools) is now written back as NFC by `parse_corpus.py`; that changes its `doc_id`, so the corpus-wide delta shows it as `added`/`changed` on the first pass after upgrading and it gets reclassified like any other changed document. This is a one-time cost, not a recurring one.
4. **The local `knowledge_index.py search` CLI is unaffected.** Unlike `search_knowledge` over MCP, the CLI's own `search` still defaults to not filtering by status — a local debugging query keeps seeing superseded chunks unless you pass `--latest-only` yourself.
5. **`MM/DD/YYYY` in content is read US-style**, not day-first — `10/24/2023` is October 24, never the 10th of month 24.

Leave `supersede_before` unset and no chunk is ever marked `SUPERSEDED` by the cutoff — but points 1–5 above still apply on that same upgrade.

For a genuinely text-only source, deterministic parsing is acceptable. Do not downgrade an existing visually enriched document to a text-only parse.

**HTML sources re-capture on refresh, not diff.** An HTML source has no stable rendered
artifact to reuse across runs the way a PDF/PPTX page does — re-run the visual-parse capture
step (`html_segments.js` → `html_capture.py plan` → screenshots → `html_capture.py assemble`)
against the current page for a changed HTML source, exactly as if it were new. A parsed
document whose header reads `fidelity: degraded` means no browser was available at the run
that ingested it: text-only, DOM-derived, no images, no VLM transcription. `vision_assemble.py` writes
the captured Markdown with no preamble unless given `--source` (then `fidelity: full`). So "upgrading" a source is really re-capturing it through the full-fidelity path,
which REPLACES the degraded parsed document with one that carries no `fidelity: degraded`
line, rather than any script rewriting a header in place. If a browser (or browser-capable
provider) is available on this maintenance pass, re-run that source through the capture path;
check for `fidelity: degraded` headers in the parsed corpus and treat them as a punch list, not
a permanent state.

**Meeting recordings.** A recording has no single "changed" signal — the video, its sidecar
transcript, or both can change independently — so what to re-run depends on which changed.
See `visual-parse` → "Meeting recordings" for the full per-recording command sequence
(`probe` / `transcribe` / `frames` / `vision_prep` → 🤖 → `review-prep` → 🤖 one blind reader per review item →
`assemble --review` / `forget`). Pass `assemble` the same `--fold-interjections 20 --pack-turns 1000`
on every run of a recording: changing them re-chunks it, and dropping them on a later run
splits it back into one section per speaker turn.

| What changed | Re-run |
|---|---|
| Video content | `probe`, `transcribe` (only if `probe.json` says `asr`), `frames`, VLM pass, `review-prep` + one blind reader per item, `assemble --review` |
| Sidecar `.vtt`/`.srt`/`.docx` (incl. a Teams `.docx` paired by its title, or a `<Meeting>.vtt` paired with its `<Meeting>-…-Meeting Recording` video) changed or added | `probe`, `assemble --db <db>` (frames unchanged: the stored review verdicts still apply). First update of a recording assembled before 0.9.2 (no stored verdicts): `review-prep --db <db>`, one blind reader per item, then `assemble --review` |
| Sidecar removed | `probe`, `transcribe`, `assemble` |
| Video removed | `./brain source remove <source-id> --yes` (tombstone), then `video_capture.py forget` |

**Existing projects must add the video globs to `brain.toml`'s `include` list**, the same way
an existing project needed `"**/*.html"`/`"**/*.htm"` added for the HTML branch — the doctor
warns when it finds videos in the corpus that no registered root's `include` matches.

**VTT/SRT sources require two separate parse passes** — `--merge-cues` only applies to transcripts and must not be passed for PDF/PPTX/DOCX. Recommended transcript flags:
`--formats vtt,srt --merge-cues 10 --fold-interjections 20 --pack-turns 1000`.
`--fold-interjections N` folds a turn shorter than N chars ("Mhm.", "Three.") into the previous
turn as `[Speaker: text]` instead of giving it its own chunk. `--pack-turns N` groups consecutive
turns (any speaker) into one section up to N chars, one `MM:SS Speaker: text` paragraph per turn,
so a short answer stays next to its question in one chunk — keep N below the indexer's
`--max-chars` (default 1200), or a pack gets split into `(part N)` chunks that each carry the
whole range heading and cue span. Measured on one corpus (77 VTT/SRT files, these flags, indexed
at the default 1200 max-chars): 2,978 chunks total.

```bash
# Pass 1 — transcripts only. A recording's transcript is skipped automatically (recorded as
# consumed-by-video) while it is in the `inputs` of that recording's video-lane manifest entry
# and the video doc exists in parsed/ — the same holds for a Teams .docx in pass 2; every
# other file, including one next to a video this Brain does not ingest, parses as before.
# Files not registered as active sources (e.g. a copy `source plan` marked skip_duplicate) are skipped as `unregistered`.
"$PY" "$SKILLS/corpus-taxonomy-extraction/parse_corpus.py" --corpus <root> --out parsed/ --formats vtt,srt --merge-cues 10 \
  --fold-interjections 20 --pack-turns 1000 \
  --registry-db "$DB" --root-key <root-key> \
  --exclude '<glob 1>' --exclude '<glob 2>'
# Pass 2 — narrative docs
"$PY" "$SKILLS/corpus-taxonomy-extraction/parse_corpus.py" --corpus <root> --out parsed/ --formats pptx,docx,pdf,md,markdown,txt,html,htm \
  --registry-db "$DB" --root-key <root-key> \
  --exclude '<glob 1>' --exclude '<glob 2>'
```

**Pass every glob in that root's `brain.toml` `exclude` list to both passes, one `--exclude`
per glob** (omit the flag when the root has no `exclude`). `source_registry` never registers
an excluded file, so a parsed doc for one is an unmanaged document that makes
`--strict-sources` (step 4) refuse the whole update. Scribe-marked files (a `scribe-task`
docx/pdf property or a `<!-- scribe:` first line) are skipped by `parse_corpus` without any
flag; both kinds of skip are recorded in `parsed/manifest.json` with `reason: "excluded"` or
`reason: "scribe_marker"`.


**Adopting `--fold-interjections`/`--pack-turns` on an existing Brain changes every
speaker-named transcript's parsed bytes once.** Like the `# fidelity:` header above, turning
these flags on for the first time re-chunks every VTT/SRT document that has speaker-named turns,
so the parsed-store delta shows those documents as `changed` and reclassifies them (paid) on
this one pass. That is expected — the underlying source did not change — but plan for it the
same way as the fidelity-header rollout.

**Entity decoding and hour-aware timestamps (this release) also change parsed bytes for
affected transcripts, once.** `parse_corpus.py` now decodes HTML entities in cue text (e.g.
`&amp;` -> `&`) and VTT inline markup, and renders a cue past the first hour as `H:MM:SS`
instead of wrapping back to `MM:SS`. In the test corpus this touched 18 of 73 transcripts
(entity decoding) plus any transcript with cues past 01:00:00 (hour labels). Their next
`brain update` shows them `changed` and reclassifies them once — expected, not drift.

**The breadcrumb/parent_heading fix changes chunking, not parsed bytes — `brain_sync` will not
see it.** Consecutive `##` sections used to inherit the first `##` title ever seen as their
`parent_heading`/breadcrumb head; that is now fixed to treat them as siblings. Because the
parsed Markdown bytes are unchanged, `brain_sync`'s hash-based delta detects no change and will
not re-index documents on its own. An existing Brain gets correct breadcrumbs only for documents
that are re-indexed for some other reason (a content change, a flag change above) or on a full
re-index — say this plainly to an operator who asks why an old chunk's breadcrumb still looks
wrong after upgrading.

**`plan` reports a `superseded_by_video` key.** These are transcript documents retired
because their video's parsed document now supersedes them — a deletion by design, not drift.
Do not treat them as an unexpected removal when reviewing the parsed-store delta below.

### 4. Review and apply parsed-store delta

```bash
./brain plan parsed \
  --manifest parsed/manifest.json --root-key <root-key> --strict-sources

./brain update parsed \
  --manifest parsed/manifest.json --root-key <root-key> --strict-sources \
  --out "$PROJECT"
```

The apply step must create a SQLite snapshot, update changed documents atomically, rebuild `related`, and write `sync_plan.json`. On failure, stop and confirm rollback/restoration before continuing.

### 5. Complete semantic work

Commands use `$PY`, `$SKILLS` and `$DB` as set under **Required project contract**; the taxonomy scripts are stdlib only.

**Preflight — Brains built before `current.json`.** If `taxonomy/current.json` is missing, or `build_graph` below stops because the store has tags and no `meta.taxonomy_version`, tell the user and follow `corpus-taxonomy-extraction` → "F. Upgrading an older Brain" (`taxonomy_review.py adopt`, with `--meta-only` when `current.json` already exists) with their confirmation. Then set `[paths].taxonomy = "taxonomy/current.json"` in `brain-maintenance.toml`. Pass `--force` only if the user explicitly says so.

**Preflight — first-build review not yet applied.** If `taxonomy/PROVISIONAL` exists, you stop: this update or deploy cannot proceed while the taxonomy is only provisionally adopted. Tell the user the first-build review has not been applied yet, and offer to run it with `corpus-taxonomy-extraction` → "A. First-build review". Do not deploy while `taxonomy/PROVISIONAL` exists.

If `sync_plan.json.reclassify_chunk_ids` is non-empty:

1. rebuild the taxonomy graph first, so categories added since the last build are in the graph (`classify_write` drops labels that are not graph nodes);
2. prepare batches in a fresh run directory (`classify_prep.py` caps each batch at 150 chunks / 60 KB, so one agent's reply stays under the 32K output-token limit);
3. dispatch Sonnet text subagents, one per `batch_k.json`: each reads that dir's `instructions.md`, `vocab.md` and its batch and writes `result_k.json`;
4. verify exact chunk-id coverage and valid labels;
5. run incremental `classify_write` without `--reset`.

```bash
"$PY" "$SKILLS/corpus-taxonomy-extraction/build_graph.py" --taxonomy taxonomy/current.json --db "$DB"
IDS=$("$PY" -c 'import json;print(",".join(map(str,json.load(open("sync_plan.json"))["reclassify_chunk_ids"])))')
RUN="classify/update-$(date +%Y%m%d-%H%M%S)"
"$PY" "$SKILLS/corpus-taxonomy-extraction/classify_prep.py" --db "$DB" --taxonomy taxonomy/current.json \
  --chunks "$IDS" --out "$RUN"
# dispatch and validate the subagents over $RUN, then:
"$PY" "$SKILLS/corpus-taxonomy-extraction/classify_write.py" --db "$DB" --results "$RUN"
```

If `taxonomy/work/reclassify.json` exists after `build_graph`, reclassify those chunk ids before verifying, with the sequence in `corpus-taxonomy-extraction` → "Reclassifying after a taxonomy change" (a fresh `classify/reclassify-v<N>` directory, then `classify_write --reclassify-done taxonomy/work/reclassify.json`). Never reuse the first-build `classify/` directory: its stale result files would overwrite the new tags and empty the queue.

Do not silently change taxonomy. Agents only add; renames, merges, moves, splits and removals are the user's decisions, made in the taxonomy review app, which migrates the affected tags.

**Offer the health review when the taxonomy needs it.** After the reclassification above, check coverage. A chunk the classifier verdicted `__no_topic__` (filler, boilerplate, off-goal; stored in `chunk_verdicts`) is not untagged, so the query excludes it and reports it separately; it guards for older stores that predate `chunk_verdicts`:

```bash
"$PY" -c 'import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);t=c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0];has_v=bool(c.execute("SELECT 1 FROM sqlite_master WHERE type=\"table\" AND name=\"chunk_verdicts\"").fetchone());nt=c.execute("SELECT COUNT(*) FROM chunk_verdicts WHERE verdict=\"no_topic\" AND chunk_id IN (SELECT id FROM chunks)").fetchone()[0] if has_v else 0;q="SELECT COUNT(*) FROM chunks WHERE id NOT IN (SELECT chunk_id FROM chunk_topics)"+(" AND id NOT IN (SELECT chunk_id FROM chunk_verdicts WHERE verdict=\"no_topic\")" if has_v else "");u=c.execute(q).fetchone()[0];print(u,"of",t,"chunks untagged (excludes",nt,"no-topic)")' "$DB"
"$PY" -c 'import json;print(len(json.load(open("taxonomy/current.json")).get("descriptions") or {}),"categories described")'
```

If a noticeable share of chunks is untagged, if the taxonomy has no descriptions, or if the user asked to refresh, clean up or check the taxonomy, tell the user what you found and offer the health review. Descriptions matter: they go into the classifier's vocabulary, the graph, `get_taxonomy` and the vault. Run the review only if the user agrees, following `corpus-taxonomy-extraction` → "B. Health review" end to end. It covers untagged sections, categories with no tags or no description, near-duplicate and off-axis labels, and similar or ungoverned metrics in one review, and it ends with the graph rebuild, the reclassification and the tag write. Offer its governed-metric drafts to the user; never add them to `schema/metrics.<corpus>.json` without their agreement. If the user wants to change the taxonomy themselves, use "D. Browse and edit" instead.

### 6. Rebuild numbers only when needed

If reporting sources or governed metric/family configuration changed, run the configured marts build with `--strict`. Otherwise preserve the current facts lane.

Never derive numeric authority from parsed narrative documents.

### 7. Verify locally

Run the project verification, MCP tests, and representative narrative/numeric smoke checks. Compare lane counts with the pre-update status. Do not deploy with empty required lanes, missing citations, incomplete classification, or a failed MCP contract.

- **Check the Brain is identifiable.** Read `meta.name`. If it is empty, warn the operator:
  a client with several Brains connected will show this one only by its connector name and
  goal. `name.txt` at the project root is the durable, reproducible source of truth for
  `meta.name` — the same convention as `goal.txt`/`audience`. Tell the operator to set
  `name.txt` (e.g. `echo 'ACME Contact Centre' > name.txt`) and re-run
  `brain_sync.py seed` (or `apply`), which carries it into `meta.name` on every run, the
  same way goal/audience refresh. A direct
  `INSERT OR REPLACE INTO meta(key,value) VALUES('name', '<name>')` (via `./brain sql`)
  also works for a one-off fix, but does not survive being reproduced from source files —
  prefer `name.txt` + seed. This is a warning, never a gate — an anonymous Brain is fully
  functional.

### 8. Plan and deploy through the project profile

First print a sanitized plan:

Deployment remains an **agent-owned external step**, not functionality implemented by `maintenance.py`. Use the project-owned deployment adapter named in `[deployment].adapter`. First request its sanitized `plan`; only after local checks and human approval invoke its `deploy` operation with the immutable release tag and the adapter's explicit confirmation flag. Run its `verify` operation afterward. The maintenance report always keeps `ready_to_deploy=false`; only the external adapter verification and human gate can change that operational decision.

The deployment adapter accepts resource identifiers from its project-owned profile but reads the verification API key only from the configured environment variable. It never prints the key. A deployment is complete only after revision health/traffic, health endpoint, missing/wrong-key rejection, typed seven-tool contract, governed metric, narrative search, and logs all pass.

**Resync every secondary copy of the store.** The rebuilt `knowledge.sqlite` is the source of truth, but a project often keeps additional distributable copies (a bundled instance shipped to a client, a Copilot-Studio instruction pack, a baked container image). Any such copy is stale the moment the primary store rebuilds. As part of deploy, enumerate and refresh (or explicitly re-cut) every secondary copy — and preserve `assets/` alongside it when page images / table evidence must travel — so no consumer reads a pre-rebuild snapshot. A copy built before the rebuild finished (a common cadence slip) must be re-cut, never shipped as-is.

Deployment failure does not mutate the local Brain. Keep the prior image/revision and SQLite snapshot for rollback.

## Resume protocol

Persist reports under the profile's configured `paths.runs` directory (default example: `.brain-maintenance/runs/`). `maintenance.py --out` rejects paths outside it:

```text
.brain-maintenance/runs/
  status.json
  source_plan.json
  sync_plan.json
  vision/<run-id>/
  classify/<run-id>/
  deploy-plan.json
```

After interruption, inspect these artifacts and current source/store state. Re-plan instead of assuming the last command completed.

## Unattended hand-off mode

A headless run (`claude -p "/brain:brain-maintenance handoff" --permission-mode bypassPermissions`,
e.g. from `scribe.py schedule`) has no human to ask, so every judgment call the interactive
workflow above makes by asking the user is instead made once, deterministically, by
`maintenance.py handoff` — a pure classification over the `status` report, never a mutation.
**In hand-off mode every human stop becomes apply, defer or abort exactly as `handoff`
classified it; the agent does not re-decide.**

**A hand-off run must always leave a report in `ops/handoff/<date>.json`**, even when it
never reaches step 3 — review fix round 2, Important #1: `scribe:run`'s stale-Brain
preflight reads only the LATEST file in that directory by name, so a step-0/1/2 failure
that leaves no report behind is indistinguishable, to scribe, from a Brain that was
never refreshed for the FIRST time — it silently reads yesterday's (or older) `apply`
report and treats today's stale Brain as fresh. `handoff --abort-reason "<reason>" --out
<path>` (below) is exactly for this: it writes a `decision: "abort"` report — same shape,
same exit code 3 — without needing a `status.json` at all.

1. Run the doctor (as in "0. Run the doctor" above). Exit 1 →

   ```bash
   "$PY" "$SKILLS/brain-maintenance/maintenance.py" handoff \
     --profile "$PROJECT/brain-maintenance.toml" \
     --abort-reason "doctor: <its missing items>" \
     --out "$PROJECT/ops/handoff/$(date +%Y-%m-%d).json"
   ```

   then stop; nothing applied.
2. Produce a fresh status report:

   ```bash
   "$PY" "$SKILLS/brain-maintenance/maintenance.py" status \
     --profile "$PROJECT/brain-maintenance.toml" \
     --out "$PROJECT/.brain-maintenance/runs/status.json"
   ```

   **Exit 2 here is expected and normal in hand-off mode** — `status` exits 2 whenever
   `ready` is false, which is also true every time there is a mere `remove_candidate` or
   an ambiguous move (both are `blockers`, and both are exactly what hand-off defers
   rather than stops for). Do **not** apply the interactive workflow's "stop if the
   report contains … ambiguous moves, or unreviewed removal candidates" rule here — that
   rule is for a human running the update by hand. In hand-off mode, continue to step 3
   regardless of `status`'s exit code, **as long as `.brain-maintenance/runs/status.json`
   was actually written** — check the file exists before moving on, since exit 2 alone
   does not distinguish "ready is false" from "the command crashed before writing
   anything" (a bad profile, an unreadable db, …). If the file was NOT written, that is a
   real failure: run `handoff --abort-reason "status: <the printed error>" --out
   "$PROJECT/ops/handoff/$(date +%Y-%m-%d).json"` and stop, the same as step 1; only the
   `handoff` classification below decides apply vs. abort.
3. Classify it:

   ```bash
   "$PY" "$SKILLS/brain-maintenance/maintenance.py" handoff \
     --profile "$PROJECT/brain-maintenance.toml" \
     --status "$PROJECT/.brain-maintenance/runs/status.json" \
     --out "$PROJECT/ops/handoff/$(date +%Y-%m-%d).json" \
     --apply-plan "$PROJECT/.brain-maintenance/runs/handoff-apply-plan.json"
   ```

   Exit 3 → `decision: "abort"`: stop here. Nothing was applied. `scribe:run`'s stale-Brain
   preflight reads this file next.
4. Exit 0 → apply only the actions listed in `handoff.json.apply`. `--apply-plan` above
   wrote a `source_registry`-shaped plan containing exactly those actions (never a
   deferred ambiguous-move or duplicate-content add, even though "add" is itself a
   structurally safe action type — see `classify_handoff`'s docstring); run:

   ```bash
   ./brain source apply --plan "$PROJECT/.brain-maintenance/runs/handoff-apply-plan.json"
   ```

   never `./brain source apply` against a freshly regenerated plan or the raw
   `status.json.source_plan` — either would re-include the deferred adds. Then
   render/visual-parse and parsed-store `update` for the same set (steps 3–4 above), then
   `./brain update parsed ... --out "$PROJECT"`, which is what writes `meta.built_at` via
   `brain_sync apply`.

   **If any step from here on fails** (source apply, parsing, `update parsed`,
   classification, marts), overwrite today's report so it no longer says `apply`, then stop:

   ```bash
   "$PY" "$SKILLS/brain-maintenance/maintenance.py" handoff \
     --profile "$PROJECT/brain-maintenance.toml" \
     --abort-reason "<step>: <the printed error>" \
     --out "$PROJECT/ops/handoff/$(date +%Y-%m-%d).json"
   ```

   Use the same dated path step 3 wrote. Left alone, an `apply` report with a failed apply
   behind it reads to `scribe:run` as a fresh Brain. (Scribe also cross-checks
   `meta.built_at` against an `apply` report's date, but the abort report is what names the
   failure.)
5. Classify the resulting new chunks against the **current** taxonomy only — no taxonomy
   changes in hand-off mode. Health items (untagged chunks, missing descriptions, …) stay
   queued for the human review app; do not run the review workbench unattended.
6. When `handoff.json.marts == "rebuild_strict"`, rebuild marts with `--strict`. On
   failure, keep the old marts and record the error in the report; do not fall back to a
   non-strict rebuild.
7. Never tombstone a source and never answer a question in this mode. Every item in
   `handoff.json.defer` (`remove_candidate`, `missing`, `ambiguous_move`,
   `duplicate_content`) stays exactly as classified, in the report, for a human to resolve
   later — it is not applied, not discarded, not re-decided.
8. Deploy only when `handoff.json.deploy == "auto"` (`[handoff].deploy` in the profile,
   default `"hold"`); `"hold"` never redeploys a hosted MCP revision on its own, even after
   a clean apply — follow "8. Plan and deploy through the project profile" above, still
   gated on local verification.

## Non-negotiable safety rules

- No source deletion without explicit human approval.
- No update when a configured root is unexpectedly unavailable.
- No consumption of incomplete agent batch output.
- No `classify_write --reset` during incremental updates.
- No deployment before local verification passes.
- No mutable image tags.
- No credentials in profiles, prompts, files generated for review, build args, or logs.
- Never update the deployed SQLite file in place; deploy an immutable image/revision.
