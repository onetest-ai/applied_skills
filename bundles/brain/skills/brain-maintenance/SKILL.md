---
name: brain-maintenance
description: Safely update and optionally deploy an existing Brain knowledge product. Use when the user asks to refresh, synchronize, maintain, rebuild changed sources, update the knowledge base, publish a new Brain image, deploy a Brain MCP revision, or roll back an update. Orchestrates source registry, visual parsing, parsed-store delta, selective classification, conditional marts, verification, and project-profile-driven deployment. The agent owns judgment and gates; scripts perform deterministic operations.
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

An update-ready project has the following. Start by copying `profile.example.toml` to project-local `brain-maintenance.toml`, then edit only project-relative paths and policy values; validate it before status:

```bash
cp <skills>/brain-maintenance/profile.example.toml "$PROJECT/brain-maintenance.toml"
python <skills>/brain-maintenance/maintenance.py validate-profile \
  --profile "$PROJECT/brain-maintenance.toml"
```

```text
brain.toml                         # portable source roots and modes
brain-maintenance.toml             # required maintenance planner profile
brain.deploy.toml                  # optional external-adapter profile; no secrets
parsed/manifest.json               # {source, md} mapping for final parsed documents
schema/knowledge.sqlite OR configured DB path
schema/taxonomy*.json              # approved taxonomy
./brain                            # launcher
```

The database may use another path inside the project, such as `.dsh/knowledge.sqlite`; set it in `[paths].db` in `brain-maintenance.toml`. The maintenance planner deliberately does not accept an ad-hoc `--db`, so the reviewed profile remains the single operational contract.

Source modes:

- `import`: additions/content changes are discovered; absence never implies deletion;
- `mirror`: absence becomes a removal candidate only while the root is available;
- `managed`: Brain-owned files; absence is corruption.

## Update workflow

### 1. Produce a read-only status report

```bash
python <skills>/brain-maintenance/maintenance.py status \
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

Before applying, check the plan's `duplicate_content` groups: a synced source tree (SharePoint/OneDrive/Drive) commonly presents the same SHA-256 at multiple live paths, which would register and re-embed the same document several times. Resolve duplicates with the human (keep one canonical path) before `apply`; `missing` at an old path after a re-sync is a warning under `import`, never an inferred deletion.

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

For a genuinely text-only source, deterministic parsing is acceptable. Do not downgrade an existing visually enriched document to a text-only parse.

**VTT/SRT sources require two separate parse passes** — `--merge-cues` only applies to transcripts and must not be passed for PDF/PPTX/DOCX:

```bash
# Pass 1 — transcripts only
python <skills>/corpus-taxonomy-extraction/parse_corpus.py --corpus <root> --out parsed/ --formats vtt,srt --merge-cues 10
# Pass 2 — narrative docs
python <skills>/corpus-taxonomy-extraction/parse_corpus.py --corpus <root> --out parsed/ --formats pptx,docx,pdf,md,markdown,txt
```

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

If `sync_plan.json.reclassify_chunk_ids` is non-empty:

1. create a fresh classification run directory;
2. run `classify_prep --chunks <ids> --batches <classification.batches from brain-maintenance.toml>` with the approved taxonomy (default 5 overflows context on VTT corpora — always read the profile value);
3. dispatch low-cost text subagents;
4. verify exact chunk-id coverage and valid labels;
5. run incremental `classify_write` without `--reset`;
6. rebuild the taxonomy graph.

Do not silently change taxonomy. Vocabulary changes are additive, human-approved work.

### 6. Rebuild numbers only when needed

If reporting sources or governed metric/family configuration changed, run the configured marts build with `--strict`. Otherwise preserve the current facts lane.

Never derive numeric authority from parsed narrative documents.

### 7. Verify locally

Run the project verification, MCP tests, and representative narrative/numeric smoke checks. Compare lane counts with the pre-update status. Do not deploy with empty required lanes, missing citations, incomplete classification, or a failed MCP contract.

- **Check the Brain is identifiable.** Read `meta.name`. If it is empty, warn the operator:
  a client with several Brains connected will show this one only by its connector name and
  goal. Offer to set it (`INSERT OR REPLACE INTO meta VALUES('name', '<name>')`). This is a
  warning, never a gate — an anonymous Brain is fully functional.

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

## Non-negotiable safety rules

- No source deletion without explicit human approval.
- No update when a configured root is unexpectedly unavailable.
- No consumption of incomplete agent batch output.
- No `classify_write --reset` during incremental updates.
- No deployment before local verification passes.
- No mutable image tags.
- No credentials in profiles, prompts, files generated for review, build args, or logs.
- Never update the deployed SQLite file in place; deploy an immutable image/revision.
