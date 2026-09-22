# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A **marketplace of two Claude Code plugins**, not an application. The product is the skills themselves — `SKILL.md` files plus the deterministic scripts they drive — and they ship to other people's projects.

- **`bundles/brain`** — builds, maintains and deploys a *Brain*: one portable `knowledge.sqlite` over a mixed corpus. Heavy, interactive, Claude Code only (needs local scripts, a venv, source credentials).
- **`bundles/kb`** — the librarian that queries an existing Brain over MCP. Works in Claude Code **and** Claude Cowork.
- **`mcp/brain`** — the FastMCP server that exposes a built `knowledge.sqlite` as governed read-only tools. This is what `kb` talks to.

`.claude-plugin/marketplace.json` lists both plugins; each bundle has its own `.claude-plugin/plugin.json`.

**The rule everything obeys:** *meaning is agentic, numbers are computed.* RAG and the taxonomy graph explain what things mean; deterministic marts compute every figure. Every answer is cited or an honest "not modeled". Gaps beat fabrication — this is why so much of the code below refuses rather than guesses.

**Nothing project-specific lives here.** No client data, corpus paths or credentials — those stay in the consuming project. A change that hardcodes one is wrong regardless of whether tests pass.

## Commands

```bash
# The three suites (from the repo root)
uv run --with-requirements bundles/brain/requirements.txt --with pytest python -m pytest bundles/brain/tests
uv run --with pytest python -m pytest bundles/kb/tests
uv run --with-requirements bundles/brain/requirements.txt --with pytest python -m pytest mcp/brain/test_semantic_mcp.py

# One file, one test
uv run --with-requirements bundles/brain/requirements.txt --with pytest python -m pytest bundles/brain/tests/test_parse_corpus_markdown.py -v
uv run --with-requirements bundles/brain/requirements.txt --with pytest python -m pytest "bundles/brain/tests/test_parse_corpus_markdown.py::MarkdownPassthroughTests::test_markdown_is_returned_verbatim" -v

# Install the skills into a host (claude | dsh | copilot | codex | all)
./install.sh --target claude            # project-local .claude/skills
./install.sh --target all --user        # ~/.claude/skills etc.
./install.sh --deps                     # create the venv the brain scripts need

# What system tools does this Brain need? (report only; exit 1 = something required is missing)
uv run --with-requirements bundles/brain/requirements.txt python bundles/brain/skills/knowledge-pipeline/brain_doctor.py --config <project>/brain.toml
```

There is no build step and no linter config. `uv` resolves dependencies per invocation; nothing is installed globally.

**`.pptx`/`.docx` parsing needs LibreOffice (`soffice`) on PATH.** PDFs need only PyMuPDF. Scripts that require `soffice` exit with an instruction rather than degrading.

## Architecture

**Build (brain), in order:** source registry → parse (`corpus-taxonomy-extraction/parse_corpus.py`, plus `visual-parse` for slide decks and HTML) → taxonomy induction → the user ratifies the draft in the local review app (`taxonomy_review.py` → `taxonomy_merge.py`, writing `taxonomy/current.json`) → `knowledge-index` (FTS5 + sqlite-vec chunks) and `tabular-semantic-layer` (numeric marts) → `brain_sync` applies the delta into `knowledge.sqlite`. `knowledge-pipeline` orchestrates; `brain-maintenance` re-runs it. Every later taxonomy change is a review in the same app (health, refine, browse); agents only add, humans decide renames/merges/removals.

**Serve:** `mcp/brain/fastmcp_server.py` wraps `semantic_core.py` and exposes read-only tools (`search_knowledge`, `get_metric`, `get_taxonomy`, `get_evidence`, …). It derives its own identity from the store's `meta` table, so a deployed Brain describes itself.

**Consume (kb):** skills identify a Brain by its **tool surface**, never by MCP server name — users register Brains under any name, and Claude Code allow-rules cannot glob the server segment.

Tool-dependent tests (needing `ffmpeg`/`ffprobe`/`whisper-cli`) skip rather than fail, and the pytest run ends with a `MISSING SYSTEM TOOLS` warning section listing what was skipped.

### Cross-file invariants

These are the things that take several files to see, and that tests pass while violating:

**The visual-lane artifact layout is a contract, not an implementation detail.** `render_pages.py` writes, per document slug, `pNN.png`, `pNN.txt` (verbatim text sidecar), optional `pNN.tables.md` (deterministic table grids) and `pages.json`; `vision_prep.py` reads exactly that and hands batches to a vision model; `vision_assemble.py` turns the results into the parsed Markdown that gets indexed. There are three producers — `render_pages.py`, `html_capture.py` and `video_capture.py` — and any further one must match the layout in **field values, not just key names** — `image` is slug-relative (`<slug>/pNN.png`) because `vision_prep` joins it onto the *parent* of the render dir, so a schema check passes while a wrong value breaks every image path.

**`chunking.py` exists twice and the copies must stay byte-identical** (`knowledge-index/` and `corpus-taxonomy-extraction/`), so a retrieval chunk is exactly the vault note a human sees. Change one, change both.

**`strip_preamble` in `chunking.py` is coupled to the header `parse_corpus.py` writes** (`# SOURCE:` then `# method:`). Adding a preamble line without teaching both copies to strip it turns that line into a live H1, which then seeds `parent_heading`/`breadcrumb_path` — indexed columns returned in search results — for every document of *every* format. Every parser emits level-2 headings for its own sections precisely so the preamble is the only H1 candidate.

**kb skills carry their rules inline, not by reference.** The Agent Skills format documents only same-directory references from a `SKILL.md`; `../_shared/…` is unspecified and does not resolve in Cowork. So the non-negotiable contract lives between `<!-- BRAIN-CONTRACT:START/END -->` in `_shared/doctrine.md` and is inlined **verbatim** into every answering skill, pinned byte-for-byte by a drift test. Depth may stay in `_shared/`; rules may not.

**`brain_sync` links parsed docs to registered sources.** A registered source with no parsed output lands in `blocked_missing_parsed` and aborts `apply` — which is why a parser that cannot handle a format must skip it *with a reason in the manifest* rather than emit nothing silently.

**`taxonomy_merge.py` is the only code that creates a taxonomy version.** It writes `taxonomy_vN.json` (refusing if it exists: versions are immutable) and then `current.json` with **identical bytes** (`taxo_io.write_version_and_current`); `adopt` only copies an existing version. Byte identity is load-bearing: a review's `base.sha256`, `build_graph`'s drift guard and `adopt` all compare raw bytes, so re-serialising `current.json` (different indent, key order) makes every planned review refuse to apply and the graph build refuse or prune.

**`build_graph` moves tags only through `history[].migrations`.** `taxonomy_merge` records the migrations `taxo_ops.apply_ops` returns; `build_graph` runs those newer than `meta.taxonomy_version`, then prunes every node no longer in the taxonomy, with its `chunk_topics` and `about` edges. A new op type that changes node ids without emitting a migration passes its own tests and deletes those tags on the next build, with only a stderr warning.

**`decisions.jsonl` is append-only, and "agents only add" is enforced by surface, not by the app.** Records are only appended (`decisions.append`/`append_many`, one `O_APPEND` write); `requests.jsonl`/`responses.jsonl` follow the same rule, a later line superseding an earlier one by id. `authorship_errors` limits non-human surfaces (`record`'s `terminal`/`script`) to `AGENT_ALLOWED` ops; the `browser` and `markdown` surfaces, stamped by `review_server`/`import-md` rather than sent by the client, may enter any op. Do not add skill rules to the human surfaces, and do not let an agent path claim a human surface.

**Review item ids are frozen in the review file.** `plan` derives each id from the review id, item position and fingerprint, then writes the review once (`taxonomy/reviews/review_<id>.json`, never overwritten). Decisions, redo requests, `respond` and `redo-prep` all look items up by that id in the file; re-deriving items (re-running `health_items`, re-planning) produces different ids that match nothing in the log.

**Health plan items carry a public `fallback: true`.** `health.py` marks a safe default (no usable agent fix) with an internal `_fallback`; `build_plan` counts it for the context line, then replaces it with `fallback: true` (present only when true). `review_ui.html` relies on that field to keep fallbacks out of **Accept all remaining** and its counts; dropping or renaming it in the plan makes the app batch-accept defaults as if they were recommendations.

**`meta` carries `goal`, `audience` and optional `name`.** `write_meta` UPSERTs `goal`/`audience` unconditionally from their source files, but `name` **only when `name.txt` is non-empty** — no existing project has one, and an unconditional write would clear a `meta.name` set by hand. Do not "tidy" that asymmetry into consistency.

**`taxonomy/PROVISIONAL` gates deployment, and only two scripts touch it.** `taxonomy_review.py adopt --provisional` creates it (copying the draft to `current.json` and marking it unreviewed); `taxonomy_merge.py` deletes it on two paths only: `apply_review` (every branch that logs `applied`, including a no-change review and `_recover_applied`; the marker is cleared *before* `applied` is appended, so a crash never leaves an applied review with the marker standing) and the legacy `--without-review` apply once it has written the new version (the user explicitly authorised skipping review). Nothing else creates or clears it. `onboard.py verify` fails while it exists, so a build/classify/graph pass over a provisional taxonomy can never be mistaken for a deployable one — the marker, not the presence of `current.json`, is what "reviewed" means.

**The `__no_topic__` sentinel is a verdict, not a missing tag.** The classifier writes it (alone, never mixed with real labels) for a chunk that carries no topic at all — filler, boilerplate, off-goal; `classify_write.py` stores it in `chunk_verdicts` keyed by `chunk_id`/`taxonomy_version`, separately from `chunk_topics`. `health.detect`, the review app's totals and plan stats (all via `graph_migrate.chunk_coverage`), `onboard.py verify`, `maintenance.py` and `taxonomy_refine_prep` all exclude chunks with a `no_topic` verdict from their "untagged" counts. Verdict rows die with their chunk (`knowledge_index` deletes them wherever it deletes chunks; a non-merge `classify_write` clears them with the chunk's old tags). Any new path that counts untagged chunks must join against `chunk_verdicts` and exclude `no_topic` too, or it will report real coverage gaps that are actually filler.

**`video_capture.py` is the third producer of the visual-lane layout** (after `render_pages.py` and `html_capture.py`). Its `pages.json` matches `render_pages.py`'s shape (slug-relative `image`, `flagged: true`) plus `medium: "video"`, `t_start`/`t_end`/`shown_at`, and `dropped` once `assemble` has deleted a no-content frame. Three things only hold across files:
- **Slug namespace.** A recording's slug is `video_slug` = `doc_slug(rel)` + `--<ext>` (`m/standup.mp4` → `m__standup--mp4`), because `doc_slug` drops the extension and a same-stem deck (`m/standup.pptx` → `m__standup`) would otherwise share — and lose — its asset dir. `frames` refuses and `forget` skips any dir whose `pages.json` is not `medium: video`. Do not "unify" the video slug back onto `doc_slug`.
- **Per-item medium.** Every `vision_prep` batch item carries `medium` (`video` or `document`), and the no-content gate applies only to `video` items. A global gate lets a deck slide of team photos be answered `<!-- no-content -->`, which `page_render` caches forever and `vision_assemble` emits empty.
- **Manifest-gated sidecar consumption.** `parse_corpus` skips a file (as `consumed-by-video`) only when it is listed in the `inputs` of a `method: "video-lane"` entry in the out dir's manifest whose `md` exists — keyed on `inputs`, never on file names, because a Teams `.docx` transcript is named after the meeting, not the recording (it is paired by its first paragraph), and a video assembled with `--transcript asr` has no sidecar in `inputs` and consumes nothing. There is no flag, so a corpus that never ran the video lane parses byte-identically. `assemble` writes that consumed entry and deletes the sidecar's stale parsed doc in the same step. The entry is what lets `brain_sync` retire the old transcript doc — and only while the video doc exists.

## Conventions

- **Every `SKILL.md` description opens with the literal `Use when `.** The description is all a model sees when deciding to invoke a skill, so the trigger leads and the capability follows. Enforced by tests in both bundles.
- **No kb `SKILL.md` declares `allowed-tools`.** It is pre-approval, never capability, and it cannot name an MCP server whose name varies per user. Brain calls prompt; that is accepted.
- **Tests live at the bundle root** (`bundles/*/tests/`), never inside a skill directory — per the Agent Skills standard a skill ships only `SKILL.md` and its runtime resources. `conftest.py` puts every skill dir on `sys.path` so a test can `import parse_corpus` directly.
- **Design specs and implementation plans are untracked**, under the gitignored `docs/superpowers/`. Tracked `docs/` is for user-facing guides.
- **Branch from `main` and open a PR** (`.github/PULL_REQUEST_TEMPLATE.md`); the repo has no direct-to-main workflow.
- **Adding a Python dependency is a real decision.** The parse stack is deliberately torch-free — PyMuPDF for PDF text, page rendering and table grids; `fastembed`/onnx for embeddings; `sqlite-vec` for vectors. Browser automation is driven through MCP by the agent rather than spawned by scripts, specifically so no browser enters `requirements.txt` or the Docker image.

## In flight

PR #26 (HTML deck ingestion) has merged, so the invariants above about visual-lane field values and the preamble are load-bearing for `html_capture.py` and the `# fidelity:` line. PR #28 (taxonomy review workbench: review app, decisions log, versioned taxonomy, health review) has also merged. `feat/count-grounded-first-review` builds on it: the first build now adopts the draft taxonomy as provisional, classifies against it, and runs the first human review after classification instead of on the bare draft — see the `PROVISIONAL` and `__no_topic__` invariants above. Check `git log` before assuming this section is current.

## Working on skills

A `SKILL.md` is a prompt that an agent executes, so treat its prose as code: an ambiguous imperative is a bug. Two failure modes recur here — a subjectless instruction that an agent reads as "do this yourself" when it should tell the user, and a documented sequence whose steps have no runnable command behind them. Scripts in `visual-parse/`, `knowledge-pipeline/` and `corpus-taxonomy-extraction/` all expose argparse CLIs; a new script that a skill documents should too.
