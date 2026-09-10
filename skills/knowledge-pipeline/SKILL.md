---
name: knowledge-pipeline
description: Orchestrator — turn a document + data corpus into ONE local knowledge.sqlite (chunks+FTS+vector, numeric marts, taxonomy graph) and answer questions over it truthfully. Use when the user wants to "build the knowledge base", "index this corpus", "set up retrieval", "create a brain", "get started / onboard", or "answer questions over these docs+spreadsheets". Runs a guided onboarding wizard for first-time setup. Composes corpus-taxonomy-extraction, knowledge-index, tabular-semantic-layer, hybrid-retrieval. Local, portable, no server.
---

# Knowledge Pipeline (orchestrator)

One entry point that turns a mixed corpus (documents + reporting spreadsheets) into a single portable **`knowledge.sqlite`** and answers questions over it. Composes the build + retrieval skills; the store is one file (no server), so it moves anywhere (.dsh / Claude / Copilot / Codex / CI).

**Core principle (unchanged across the toolchain):** *meaning is agentic, numbers are computed.* RAG never emits a figure; the marts never guess. Every answer is cited or an honest "not modeled."

## Guided onboarding (first-time setup)

When the user wants to **create a brain** / "get started" / doesn't yet have a project, run the wizard instead of dumping commands. Drive it conversationally — the questions are judgment (yours + the user's); the deterministic scaffold/preflight/verify are the shipped `onboard.py`.

**1. Ask, one at a time (skip any the user already answered):**
- **Goal** — the single analytical goal that scopes everything (the noise filter). *"What are you trying to get out of this corpus?"* (e.g. "optimize call-center operations and introduce an AI workforce"). Don't proceed without it — it drives taxonomy + demotion.
- **Docs** — folder of narrative documents (PDF/PPTX/DOCX).
- **Reporting** — folder of the numeric workbooks (XLSX/XLSM), if any. May be the same folder or none (then the numbers lane stays empty — that's fine).
- **Project dir** — where the brain + configs live (default: `./<name>-brain`).

**2. Scaffold + preflight + scan** (deterministic):
```bash
python .../knowledge-pipeline/onboard.py scaffold \
  --project <proj> --goal "<goal>" --docs <docs> [--reporting <xlsx-dir>] [--corpus <name>]
```
This creates the project layout (`schema/ parsed/ taxonomy/ classify/ marts/ vault/`), copies `families.<corpus>.json` + `metrics.<corpus>.json` templates into `schema/`, writes `goal.txt` and a **`BRAIN.md`** with the exact ordered build commands (real paths filled in), then reports missing deps and splits the corpus into narrative-vs-reporting counts.

**If deps are missing**, install them into the skills' **own isolated venv** (never the project's env) with `uv` via the installer:
```bash
./install.sh --bundle brain --deps          # per-project  <project>/.claude/venv
./install.sh --bundle brain --deps --user   # ONE shared   ~/.claude/venv  (shared across projects)
```
Deps are torch-free (docling retired) and modest (~200 MB); `.pptx/.docx` also need LibreOffice `soffice` (system dep). `BRAIN.md`'s `$PY` points at whichever venv exists; the zero-install path is `uv run --with-requirements bundles/brain/requirements.txt python <script>`.

**3. Configure the numbers lane (only if there are workbooks).** The narrative/graph lanes need no config, but the marts do: walk the user through editing `schema/families.<corpus>.json` to describe their workbooks (glob, layout, sheets, measures). Use `tabular-semantic-layer` (its `profile_workbooks.py` inspects real files) — this is the one step that genuinely needs their input. If they have no workbooks, skip and note the numbers lane will be empty.

**4. Walk the build.** For the complete orchestration contract, read the installed bundle's `AGENT_README.md` when available (source checkout: `bundles/brain/AGENT_README.md`). Create a todo per phase and run in order. Visual corpora have **three** agentic stages: VLM transcription of flagged pages, taxonomy induction, and per-section classification. The top-level coding agent launches those subagents; no script or MCP server launches them automatically. Assemble VLM-enriched Markdown before taxonomy/index/classification, checkpoint long phases, validate every batch result, and never silently continue past a failed step.

**5. Verify + first answer.**
```bash
python .../knowledge-pipeline/onboard.py verify --db <proj>/schema/knowledge.sqlite
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
# 1. parse docs → Markdown. TEXT pages via pymupdf (torch-free):
python .../corpus-taxonomy-extraction/parse_corpus.py --corpus <docs> --out <project>/parsed --formats pptx,docx,pdf
# 1v. VISUAL/diagram/table pages (slide decks, flows, timelines) — the visual-parse skill:
python .../visual-parse/render_pages.py --doc <deck.pdf> --out <project>/assets     # PNG + text + table grids; flag visual pages
python .../visual-parse/vision_prep.py --render-dir <project>/assets/<slug> --out <project>/vision --db "$DB"
#    → 🤖 dispatch VISION subagents (cheap) → vision/result_k.json {img_sha: faithful markdown}
python .../visual-parse/vision_assemble.py --render-dir <project>/assets/<slug> --out <project>/parsed/<doc>.md --results <project>/vision --db "$DB"
# 2. (optional) induce taxonomy → taxonomy_v0.json  [map→reduce→judge→emit; see that skill]
# 3. narrative index — heading-aware sections (shared chunker) → chunks+FTS+vector
python .../knowledge-index/knowledge_index.py index --db "$DB" --corpus <project>/parsed --reset
# 4. taxonomy graph (vertices = L1/L2) into the SAME db
python .../corpus-taxonomy-extraction/build_graph.py --taxonomy <project>/taxonomy/taxonomy_v0.json --db "$DB"
# 5. per-section taxonomy tags — LOW-TIER AGENTS (meaning is agentic), not a script:
python .../corpus-taxonomy-extraction/classify_prep.py --db "$DB" --taxonomy <project>/taxonomy/taxonomy_v0.json --out <project>/classify --batches 5
#    → dispatch N Haiku subagents: each reads classify/{instructions,vocab,batch_k}.md/json → writes classify/result_k.json
python .../corpus-taxonomy-extraction/classify_write.py --db "$DB" --results <project>/classify   # -> chunk_topics + graph 'about' edges
# 5b. semantic 'related' layer — cosine kNN over the vectors we already store (no re-embed, no API)
python .../knowledge-index/knowledge_index.py related --db "$DB"                                   # -> related(chunk_id, related_id, score)
# 6. numeric marts (Excel → facts) into the SAME db
python .../tabular-semantic-layer/build_marts.py --root <reporting> --config <project>/schema/families.<corpus>.json --out-dir <project>/marts --db "$DB"
# 7. (OPTIONAL) Obsidian vault — a DISPOSABLE view of the store, regenerable anytime.
#    Skip it in the default build; export on demand (debugging / a human wants to browse):
python .../corpus-taxonomy-extraction/to_obsidian.py --db "$DB" --out <project>/vault --clean --assets <project>/assets   # or: ./brain vault
# 8. record document hashes so future updates can diff (see "Updating" below)
python .../knowledge-pipeline/brain_sync.py seed --db "$DB" --parsed <project>/parsed
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
python .../knowledge-pipeline/brain_sync.py plan  --db "$DB" --parsed <project>/parsed
# show/approve the delta; plan ensures `documents` exists, so validate the DB path first
python .../knowledge-pipeline/brain_sync.py apply --db "$DB" --parsed <project>/parsed --out <project>
# apply snapshots first and writes sync_plan.json; preserve the snapshot for rollback

# Use a fresh result directory; scripts do not clean stale result_*.json:
python .../corpus-taxonomy-extraction/classify_prep.py \
  --db "$DB" --taxonomy <tax> --out <fresh-cls-run> --chunks <ids from sync_plan.json>
# → low-tier text subagents → <fresh-cls-run>/result_k.json; validate complete ID coverage
python .../corpus-taxonomy-extraction/classify_write.py --db "$DB" --results <fresh-cls-run>
python .../corpus-taxonomy-extraction/build_graph.py --db "$DB" --taxonomy <tax>
python .../knowledge-index/knowledge_index.py related --db "$DB"
python .../corpus-taxonomy-extraction/to_obsidian.py \
  --db "$DB" --out <project>/vault --clean --assets <project>/assets
# Re-run build_marts --strict only when reporting inputs/config changed.
# On failure: brain_sync.py rollback --db "$DB" (then reconcile parsed/assets/vault).
```

What each lane does on change: **RAG** — per-doc delete+reindex with deterministic source+ordinal ids; **tags/graph** — only changed chunks are re-tagged; **marts** — full idempotent recompute when reporting changes; **vault** — `--clean` reconciles removed notes. Taxonomy is reused by default and changed only through a human-approved additive merge.

> Migrating an OLD store (built before deterministic source+ordinal ids): do one full rebuild (steps 3–8 above with `index --reset`) once; then incremental updates apply.

Launcher shortcuts for the parsed→store stage: `./brain plan <parsed>` · `./brain update <parsed>` · `./brain rollback`.

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
Generic (these skills): all the code. Project-specific (the consuming repo): `schema/families.<corpus>.json`, `schema/metrics.<corpus>.json`, the goal string, `taxonomy_v0.json`, and the built `knowledge.sqlite`. Keep project data in the project, never in the skills.

## Deps
`sqlite3` (stdlib) + `sqlite-vec`, `fastembed` (RAG, onnx — no torch), `pymupdf`/`openpyxl` (parse/marts), `pandas`/`pyarrow` (marts). **Torch-free** (docling retired). `.pptx/.docx` also need LibreOffice `soffice` (system dep). Install into the skills' own isolated venv (`install.sh --deps`), or `uv run --with-requirements`.
