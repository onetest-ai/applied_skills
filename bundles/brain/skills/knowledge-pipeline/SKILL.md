---
name: knowledge-pipeline
description: Use when the user wants to "build the knowledge base", "index this corpus", "set up retrieval", "create a brain", "get started / onboard", or "answer questions over these docs+spreadsheets+transcripts" — the orchestrator that turns a mixed corpus (documents, transcripts, spreadsheets) into ONE local knowledge.sqlite (chunks+FTS+vector, numeric marts, taxonomy graph) and answers questions over it truthfully. Runs a guided onboarding wizard for first-time setup. Composes corpus-taxonomy-extraction, knowledge-index, tabular-semantic-layer, hybrid-retrieval. Local, portable, no server.
---

# Knowledge Pipeline (orchestrator)

One entry point that turns a mixed corpus (documents + transcripts + reporting spreadsheets) into a single portable **`knowledge.sqlite`** and answers questions over it. Composes the build + retrieval skills; the store is one file (no server), so it moves anywhere (.dsh / Claude / Copilot / Codex / CI).

**Core principle (unchanged across the toolchain):** *meaning is agentic, numbers are computed.* RAG never emits a figure; the marts never guess. Every answer is cited or an honest "not modeled."

## Guided onboarding (first-time setup)

When the user wants to **create a brain** / "get started" / doesn't yet have a project, run the wizard instead of dumping commands. Drive it conversationally — the questions are judgment (yours + the user's); the deterministic scaffold/preflight/verify are the shipped `onboard.py`.

**1. Ask, one at a time (skip any the user already answered):**
- **Goal** — the single analytical goal that scopes everything (the noise filter). *"What are you trying to get out of this corpus?"* (e.g. "optimize call-center operations and introduce an AI workforce"). Don't proceed without it — it drives taxonomy + demotion.
- **Name** — *"What should this Brain be called?"* (e.g. "ACME Contact Centre"). **Optional** — an anonymous Brain is fully functional. It's what a client shows when several Brains are connected (server identity, tool-description prefix), so worth asking for whenever the user will likely have more than one Brain around. Written to `name.txt`.
- **Audience** — *"Who will consume the KB — which roles/personas?"* (e.g. "call-center ops managers and workforce planners"). Optional but valuable: it's a secondary lens that refines taxonomy emphasis and drives how the `kb` plugin sets answer altitude/vocabulary and authored-artifact tone/depth. Distinct from the deployment target (that's distribution/infra). Recorded in `brain.toml` `[project].audience`.
- **Docs** — folder of narrative documents (PDF/PPTX/DOCX) and/or transcripts (VTT/SRT). VTT/SRT corpora require `--merge-cues N` at parse time (see Build step 1a).
- **Reporting** — folder of the numeric workbooks (XLSX/XLSM), if any. May be the same folder or none (then the numbers lane stays empty — that's fine).
- **Project dir** — where the brain + configs live (default: `./<name>-brain`).
- **Source-root semantics** — for each supplied folder decide with the user:
  - `import` (default/recommended): discover additions and content changes, but a missing file never removes it from the Brain;
  - `mirror`: an available folder is authoritative, so missing files become removal candidates (still human-confirmed);
  - `managed`: Brain-owned storage such as project-local `.incoming` for chat attachments.
  Use portable root keys (`docs`, `reporting`, `incoming`) and paths relative to the project whenever possible. Never put absolute paths into SQLite.
- **Deployment target** — *"Will this brain be consumed locally (an agent queries the local store), or served as a hosted MCP to remote clients like Copilot Studio?"* Ask this now: `hosted-mcp` needs auth/TLS, an immutable-image deployment profile, and server-shaped operator docs, so choosing up front avoids rewriting the operator guide later. Recorded in `brain.toml` `[deployment].target`; changeable later.

**2. Scaffold + preflight + scan** (deterministic):
```bash
python .../knowledge-pipeline/onboard.py scaffold \
  --project <proj> --goal "<goal>" [--name "<display name>"] [--audience "<roles/personas>"] --docs <docs> [--reporting <xlsx-dir>] \
  [--docs-mode import|mirror] [--reporting-mode import|mirror] \
  [--deploy-target local|hosted-mcp] [--corpus <name>]
```
This creates the project layout (`schema/ parsed/ taxonomy/ classify/ vision/ marts/ vault/ .incoming/`), copies `families.<corpus>.json` + `metrics.<corpus>.json` templates into `schema/`, and writes:

- `goal.txt`;
- `name.txt` (empty when no `--name` was given — the Brain stays anonymous and fully functional);
- portable `brain.toml` with a `[project]` section (canonical `audience`) followed by `incoming` (`managed`), `docs` (`import` by default), and `reporting` (`import` by default) roots; paths are relative to the project whenever the platform permits;
- **`BRAIN.md`** with exact ordered build and source-registry commands (plus a deployment section matching the chosen target).

**Canonical project artifacts** (what later maintenance relies on): `goal.txt` is the authoritative analytical goal, `brain.toml` the source registry, `BRAIN.md` the build plan. A host may add its own operator guide (e.g. an `AGENTS.md`), but that never replaces `goal.txt` — keep the goal in `goal.txt` so any agent/operator can recover it. If you find a project whose goal lives only inside a host doc, write it back to `goal.txt`.

Never overwrite an existing `brain.toml`. After scaffold, read it back, explain each root/mode to the user, and adjust modes/includes only with their agreement. Then report missing deps and narrative-vs-reporting counts.

**If deps are missing**, install them into the skills' **own isolated venv** (never the project's env) with `uv` via the installer:
```bash
./install.sh --bundle brain --deps          # per-project  <project>/.claude/venv
./install.sh --bundle brain --deps --user   # ONE shared   ~/.claude/venv  (shared across projects)
```
Deps are torch-free (docling retired) and modest (~200 MB); `.pptx/.docx` also need LibreOffice `soffice` (system dep). `BRAIN.md`'s `$PY` points at whichever venv exists; the zero-install path is `uv run --with-requirements bundles/brain/requirements.txt python <script>`.

**3. Initialize and register sources.** The scaffold creates the config, not registry rows. Once the store file exists, run:

```bash
./brain source init
./brain source plan --out source_plan.json
# Review proposed additions/content changes with the user, then:
./brain source apply --plan source_plan.json
```

**Dedupe by content before applying.** Folders synced from SharePoint/OneDrive/Drive routinely expose the *same file* at several relative paths (old flat layout + a nested "from Client…" hierarchy). Each distinct path becomes its own `source_id` and would be rendered and embedded again. The plan's **`duplicate_content`** array groups any SHA-256 that appears at more than one live path — review it with the user and keep a single canonical path (prefer the authoritative one) before `apply`; it is advisory and never auto-collapsed.

For a deliberately selected single file use `source adopt --root <key> <relative-path>`. For a chat attachment use `source import <temporary-path> --root incoming --provenance '{...}'`. During the first full build, link final parsed documents to registry sources using `brain_sync.py seed --root-key <key> --manifest <parsed>/manifest.json --strict-sources`; for a visual pipeline that emits its own manifest, require the same `{source, md}` mapping. If multiple narrative roots feed one parsed corpus, generate one unambiguous combined manifest or seed them separately without overwriting prior links.

**4. Configure the numbers lane (only if there are workbooks).** The narrative/graph lanes need no config, but the marts do: walk the user through editing `schema/families.<corpus>.json` to describe their workbooks (glob, layout, sheets, measures). Use `tabular-semantic-layer` (its `profile_workbooks.py` inspects real files) — this is the one step that genuinely needs their input. If they have no workbooks, skip and note the numbers lane will be empty.

**5. Walk the build.** For the complete orchestration contract, read the installed bundle's `AGENT_README.md` when available (source checkout: `bundles/brain/AGENT_README.md`). Create a todo per phase and run in order. Visual corpora have **three** agentic stages: VLM transcription of flagged pages, taxonomy induction, and per-section classification. The top-level coding agent launches those subagents; no script or MCP server launches them automatically. Between induction and the graph build sits the user's gate: the draft taxonomy review in the local app (`corpus-taxonomy-extraction` → "A. Draft review"). You run `serve` in the background and end your turn; the user reviews and submits in the browser; you apply it and continue. Assemble VLM-enriched Markdown before taxonomy/index/classification, checkpoint long phases, validate every batch result, and never silently continue past a failed step.

**6. Verify + first answer.**
```bash
"$PY" .../knowledge-pipeline/onboard.py verify --db <proj>/schema/knowledge.sqlite
```
Report per-lane row counts (it flags any empty lane) and the smoke-query result. Then answer the user's first real question via **hybrid-retrieval** to prove all lanes fire, and point them at the `vault/` to browse.

Re-runnable: `onboard.py scan --docs <dir>` is a standalone preflight; re-running `scaffold` never clobbers an existing config or `goal.txt`.

## The store — one SQLite, three lanes
| Lane | Tables | Built by |
|---|---|---|
| narrative (RAG) | `chunks`, `chunks_fts`, `chunks_vec` | `knowledge-index` |
| semantic neighbors | `related` (chunk↔chunk cosine kNN) | `knowledge-index` (`related`) |
| numbers | `facts` (+ `metrics.<corpus>.json` catalog) | `tabular-semantic-layer` |
| taxonomy graph | `graph_nodes`, `graph_edges` | `corpus-taxonomy-extraction` (`build_graph.py`) |

## Build (run once per corpus; re-run to refresh)
```bash
DB=<project>/schema/knowledge.sqlite
PY=<BRAIN.md's $PY>   # the skills' venv (install.sh --deps); or: uv run --with-requirements <bundle>/requirements.txt python
# 1a. parse transcripts → Markdown (VTT/SRT corpora — use --merge-cues to join same-speaker cues into speaker turns):
#     WARNING: omitting --merge-cues produces one chunk per cue (~50-100 chars each), which agents
#     classify as empty [] and retrieval quality degrades severely. Always pass --merge-cues N > 1 for VTT/SRT.
"$PY" .../corpus-taxonomy-extraction/parse_corpus.py --corpus <docs> --out <project>/parsed --formats vtt,srt --merge-cues 10
# 1b. parse narrative docs → Markdown. TEXT pages via pymupdf (torch-free):
"$PY" .../corpus-taxonomy-extraction/parse_corpus.py --corpus <docs> --out <project>/parsed --formats pptx,docx,pdf,md,markdown,txt,html,htm
#     HTML with no browser degrades to DOM text (fidelity: degraded) via the same command; an
#     HTML deck needs a browser to capture — see the visual-parse capture step below.
# 1v. VISUAL/diagram/table pages (slide decks, flows, timelines) — the visual-parse skill:
"$PY" .../visual-parse/render_pages.py --doc <deck.pdf> --out <project>/assets     # PNG + text + table grids; flag visual pages
#     HTML deck with a browser (full fidelity, images + VLM transcription): run the
#     visual-parse skill's capture step (html_segments.js → html_capture.py plan → screenshots
#     → html_capture.py assemble) to produce this same <project>/assets/<slug>/pages.json first.
#     Do NOT run both 1b and 1v for the same HTML deck as if they were independent: write the
#     capture path's assembled output to the SAME parsed-doc path 1b would have produced for
#     that source, so it OVERWRITES the degraded parse rather than creating a duplicate parsed
#     document for one source. When 1b skipped the deck as `skipped-js-rendered` (no text, so
#     no manifest row was written for it), there is nothing to overwrite — the capture path's
#     output is the ONLY parsed document that will ever exist for that source.
"$PY" .../visual-parse/vision_prep.py --render-dir <project>/assets/<slug> --out <project>/vision --db "$DB"
#    → 🤖 dispatch VISION subagents (cheap) → vision/result_k.json {img_sha: faithful markdown}
"$PY" .../visual-parse/vision_assemble.py --render-dir <project>/assets/<slug> --out <project>/parsed/<doc>.md --results <project>/vision --db "$DB"
# 2. (optional) induce taxonomy → taxonomy/taxonomy_v0.json  [map→reduce→judge→emit; see that skill]
#    👤 then the user ratifies it in the review app: corpus-taxonomy-extraction → "A. Draft review"
#    (plan --mode draft → serve in the background, end your turn → on submit, taxonomy_merge.py --review … --apply
#    writes taxonomy/current.json, which steps 4–5 read)
# 3. narrative index — heading-aware sections (shared chunker) → chunks+FTS+vector
"$PY" .../knowledge-index/knowledge_index.py index --db "$DB" --corpus <project>/parsed --reset
# 4. taxonomy graph (vertices = L1/L2) into the SAME db
"$PY" .../corpus-taxonomy-extraction/build_graph.py --taxonomy <project>/taxonomy/current.json --db "$DB"
# 5. per-section taxonomy tags — LOW-TIER AGENTS (meaning is agentic), not a script:
"$PY" .../corpus-taxonomy-extraction/classify_prep.py --db "$DB" --taxonomy <project>/taxonomy/current.json --out <project>/classify --batches 25
#    --batches controls chunks-per-agent: too few batches → agent hits context limit and writes nothing.
#    Rule of thumb: ceil(total_chunks / 1000) batches. Default 25 handles corpora up to ~25k chunks safely.
#    Agents write result_k.json into the SAME <project>/classify/ dir as the batch files (not a subdir).
#    → dispatch N Haiku subagents: each reads classify/{instructions,vocab,batch_k}.md/json → writes classify/result_k.json
"$PY" .../corpus-taxonomy-extraction/classify_write.py --db "$DB" --results <project>/classify   # -> chunk_topics + graph 'about' edges
# 5b. semantic 'related' layer — cosine kNN over the vectors we already store (no re-embed, no API)
"$PY" .../knowledge-index/knowledge_index.py related --db "$DB"                                   # -> related(chunk_id, related_id, score)
# 6. numeric marts (Excel → facts) into the SAME db
"$PY" .../tabular-semantic-layer/build_marts.py --root <reporting> --config <project>/schema/families.<corpus>.json --out-dir <project>/marts --db "$DB"
# 7. (OPTIONAL) Obsidian vault — a DISPOSABLE view of the store, regenerable anytime.
#    Skip it in the default build; export on demand (debugging / a human wants to browse):
"$PY" .../corpus-taxonomy-extraction/to_obsidian.py --db "$DB" --out <project>/vault --clean --assets <project>/assets   # or: ./brain vault
# 8. record document hashes so future updates can diff (see "Updating" below)
#    seed also UPSERTs the durable meta table (goal from goal.txt, audience from
#    brain.toml [project].audience) that health() exposes as `about`. Re-run seed
#    (no rebuild needed) whenever the goal or audience changes to refresh meta.
#    GOVERNANCE: pass --require-goal to seed/apply to REFUSE (exit 3) an ungoverned
#    store (empty meta.goal); a changed goal is flagged as GOAL DRIFT (it reshapes the
#    whole taxonomy). `apply` (publish path) also refreshes meta, so a build can't ship
#    ungoverned. Inspect the recorded goal/audience anytime with `./brain about`.
"$PY" .../knowledge-pipeline/brain_sync.py seed --db "$DB" --parsed <project>/parsed --require-goal
```
Chunk ids are deterministic (`f(source, section-ordinal)`), so an unchanged document with unchanged section boundaries keeps its ids across rebuilds. During an update, changed documents are delete-then-reindexed and their new chunk ids are explicitly reclassified; unchanged documents keep their tags/graph edges.
Result: one `knowledge.sqlite` — `chunks`/`chunks_fts`/`chunks_vec` (a chunk = a section = an Obsidian note), `chunk_topics` (real per-section taxonomy tags via low-tier agents), `facts` (marts), `graph_nodes`/`graph_edges` (taxonomy vertices + `subclass_of` + `about` edges to chunks). Check the `build_marts` audit (`--strict` in CI). The vault is generated from the store, so notes, retrieval chunks, tags, and graph all reference the same ids.

## Updating the brain (documents add / change / delete)

Read `bundles/brain/AGENT_README.md` for the authoritative source-to-store update runbook. The top-level coding agent orchestrates two deltas:

1. **source/page delta:** identify changed source documents, rerun `render_pages.py`, use `page_render.img_sha` so `vision_prep.py` sends only flagged uncached pages to vision subagents, then assemble final enriched Markdown;
2. **parsed/store delta:** `brain_sync` compares SHA-256 of final `parsed/*.md` against the `documents` table, re-embeds only added/changed docs, deletes removed docs, and writes `sync_plan.json` for incremental classification.

`./brain update <parsed>` covers only the second boundary. It does **not** parse source files, run visual routing, or launch agents.

```bash
# After changed sources have gone through render → vision_prep → agents → vision_assemble:
"$PY" .../knowledge-pipeline/brain_sync.py plan  --db "$DB" --parsed <project>/parsed
# show/approve the delta; plan ensures `documents` exists, so validate the DB path first
"$PY" .../knowledge-pipeline/brain_sync.py apply --db "$DB" --parsed <project>/parsed --out <project>
# apply snapshots first and writes sync_plan.json; preserve the snapshot for rollback

# Use a fresh result directory; scripts do not clean stale result_*.json:
"$PY" .../corpus-taxonomy-extraction/classify_prep.py \
  --db "$DB" --taxonomy <tax> --out <fresh-cls-run> --chunks <ids from sync_plan.json>
# → low-tier text subagents → <fresh-cls-run>/result_k.json; validate complete ID coverage
"$PY" .../corpus-taxonomy-extraction/classify_write.py --db "$DB" --results <fresh-cls-run>
"$PY" .../corpus-taxonomy-extraction/build_graph.py --db "$DB" --taxonomy <tax>
"$PY" .../knowledge-index/knowledge_index.py related --db "$DB"
"$PY" .../corpus-taxonomy-extraction/to_obsidian.py \
  --db "$DB" --out <project>/vault --clean --assets <project>/assets
# Re-run build_marts --strict only when reporting inputs/config changed.
# On failure: brain_sync.py rollback --db "$DB" (then reconcile parsed/assets/vault).
```

What each lane does on change: **RAG** — per-doc delete+reindex with deterministic source+ordinal ids; **tags/graph** — only changed chunks are re-tagged; **marts** — full idempotent recompute when reporting changes; **vault** — `--clean` reconciles removed notes. Taxonomy is reused by default; agents only propose additions, and every change goes through the taxonomy review app. After the update, if many changed chunks come back untagged or the user asks to refresh or clean up the taxonomy, offer the health review (`corpus-taxonomy-extraction` → "B. Health review"); if the user wants to change categories themselves, use "D. Browse and edit". `brain-maintenance` covers the whole update, including this offer.

> Migrating an OLD store (built before deterministic source+ordinal ids): do one full rebuild (steps 3–8 above with `index --reset`) once; then incremental updates apply.

Launcher shortcuts for the parsed→store stage: `./brain plan <parsed>` · `./brain update <parsed>` · `./brain rollback`.

## Portable source registry

A Brain may track its mother sources without storing original document bytes. `brain.toml` maps portable root keys to paths resolved relative to the project; SQLite stores only stable `source_id`, `root_key`, normalized `relative_path`, original SHA-256, description, and provenance JSON.

```bash
./brain source init
./brain source adopt --root docs "path/inside/root.pdf" --description "..."
./brain source import /temporary/chat-attachment.pdf --root incoming \
  --provenance '{"attachment_id":"…","conversation_id":"…"}'
./brain source list --json
./brain source get <source-id>
./brain source plan --out source_plan.json
./brain source apply --plan source_plan.json       # adds/content changes/moves only
./brain source remove <source-id> --yes            # explicit tombstone; then brain_sync removes derived doc
```

Root modes: `import` never infers deletion from absence; `mirror` reports `remove_candidate` only while the root is available; `managed` is for Brain-owned files such as `.incoming` and missing files are corruption. A missing whole root is `root_unavailable`, never “delete everything.” Attachments are atomically copied into the managed root; their bytes are not stored in SQLite. `documents.source_id` links parsed documents to the registry while `documents.doc_id == chunks.source` remains the parsed-relative identity for compatibility.

## Answer (per question)
Follow **`hybrid-retrieval`**: decompose → classify each sub-claim (computable→marts / narrative→RAG / relation→graph / both→reconcile) → retrieve against the one `$DB` → compose one cited answer. Tag facts `[MART]` / `[RAG]` / `[GRAPH]`; state unmodeled sub-parts plainly.

**Retrieve on the semantic layer; answer from the content.** RAG finds a section by its (possibly VLM-transcribed, lossy) text. When a hit is a **visual/table page** (`chunks.image` set), call `get_evidence` for the source text and **deterministically-extracted table grids**. It also returns the private `page_asset` path for clients that can open the rendered image. Cite a table's grid for any figure, never the prose paraphrase.

## Workspace & checkpointing (for long / multi-step research)
Borrowed from a disk-first research discipline — use it when a question needs many retrieval steps:
```
<project>/.research/<YYYY-MM-DD>/<session>/
  00_plan.md         # sub-questions + which lane answers each — before retrieving
  notes.md           # rolling findings with source citations
  checkpoint_NNN.md  # batch results; drop detail from context after writing
  report.md          # final answer, assembled from disk (never from memory)
```
Rules: plan first; disk is truth, memory is scratch; checkpoint every ~10 steps or ~150K tokens; assemble the final report from checkpoints; on interruption `ls` the workspace and resume from the last checkpoint. For a single-shot question, skip the workspace — just route via `hybrid-retrieval`.

## Corpus-specific vs generic
Generic (these skills): all the code. Project-specific (the consuming repo): `schema/families.<corpus>.json`, `schema/metrics.<corpus>.json`, the goal string, `taxonomy/` (versions, `current.json`, `decisions.jsonl`), and the built `knowledge.sqlite`. Keep project data in the project, never in the skills.

## Deps
`sqlite3` (stdlib) + `sqlite-vec`, `fastembed` (RAG, onnx — no torch), `pymupdf`/`openpyxl` (parse/marts), `pandas`/`pyarrow` (marts). **Torch-free** (docling retired). `.pptx/.docx` also need LibreOffice `soffice` (system dep). Install into the skills' own isolated venv (`install.sh --deps`), or `uv run --with-requirements`.
