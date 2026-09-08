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

**4. Walk the build.** Create a todo per step from `BRAIN.md` and run them in order, pausing at the two 🤖 agent steps (taxonomy induction, per-section classification) to dispatch low-tier subagents per `corpus-taxonomy-extraction`. Checkpoint on the long ones. Don't silently continue past a failed step — surface it.

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
Chunk ids are content-addressed (`f(source, section-ordinal)`), so an unchanged document keeps its ids across rebuilds and its tags/graph edges survive — this is what makes incremental updates safe.
Result: one `knowledge.sqlite` — `chunks`/`chunks_fts`/`chunks_vec` (a chunk = a section = an Obsidian note), `chunk_topics` (real per-section taxonomy tags via low-tier agents), `facts` (marts), `graph_nodes`/`graph_edges` (taxonomy vertices + `subclass_of` + `about` edges to chunks). Check the `build_marts` audit (`--strict` in CI). The vault is generated from the store, so notes, retrieval chunks, tags, and graph all reference the same ids.

## Updating the brain (documents add / change / delete)

The store records a **content hash per document** (`documents` table), so updates are **incremental** — only the delta is re-embedded and re-classified, and unchanged docs (with their tags + graph edges) are left untouched. Cost scales with the change, not the corpus.

```bash
# 1. re-parse the corpus (or just the changed sources) into <project>/parsed
python .../corpus-taxonomy-extraction/parse_corpus.py --corpus <docs> --out <project>/parsed --formats pptx,docx,pdf
# 2. see the delta
python .../knowledge-pipeline/brain_sync.py plan  --db "$DB" --parsed <project>/parsed
# 3. apply it (snapshots the .sqlite first; (re)embeds only added/changed, deletes removed)
python .../knowledge-pipeline/brain_sync.py apply --db "$DB" --parsed <project>/parsed
#    → writes sync_plan.json naming the chunk ids that must be RE-CLASSIFIED
# 4. 🤖 reclassify ONLY those chunks (agentic), then refresh graph + vault:
python .../corpus-taxonomy-extraction/classify_prep.py  --db "$DB" --taxonomy <tax> --out <cls> --chunks <ids from sync_plan.json>
#    → Haiku subagents → classify/result_k.json
python .../corpus-taxonomy-extraction/classify_write.py --db "$DB" --results <cls>          # incremental (only these chunks)
python .../corpus-taxonomy-extraction/build_graph.py    --db "$DB" --taxonomy <tax>          # rebuilds subclass_of; preserves 'about'
python .../corpus-taxonomy-extraction/to_obsidian.py    --db "$DB" --out <project>/vault --clean
# marts: re-run build_marts only if the reporting workbooks changed (it's a full idempotent recompute)
# rollback if needed:  brain_sync.py rollback --db "$DB"
```
What each lane does on change: **RAG** — per-doc delete+reindex, stable ids; **tags/graph** — only changed chunks re-tagged, `about` edges preserved, vanished taxonomy nodes pruned; **marts** — full idempotent recompute (self-healing); **vault** — `--clean` reconciles (drops notes for removed docs). **Taxonomy** is *not* auto-re-induced — many new/changed docs may warrant re-running induction (additively: add L1/L2, never rename — node ids = slug(label) must stay stable, or existing tags break); that stays a deliberate, human-gated step.

> Migrating an OLD store (built before content-addressed ids): do one full rebuild (steps 3–8 above with `index --reset`) once, so chunk ids become stable; then incremental updates apply.

Shortcut via the launcher: `./brain plan <parsed>` · `./brain update <parsed>` · `./brain rollback`.

## Answer (per question)
Follow **`hybrid-retrieval`**: decompose → classify each sub-claim (computable→marts / narrative→RAG / relation→graph / both→reconcile) → retrieve against the one `$DB` → compose one cited answer. Tag facts `[MART]` / `[RAG]` / `[GRAPH]`; state unmodeled sub-parts plainly.

**Retrieve on the semantic layer; answer from the content.** RAG finds a section by its (possibly VLM-transcribed, lossy) text. When a hit is a **visual/table page** (`chunks.image` set), pull the FULL content before answering — the `page` MCP tool returns the rendered image + verbatim text + **deterministically-extracted table grids**. Cite a table's grid for any figure, never the prose paraphrase — so detail dropped in transcription is never dropped in the answer.

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
