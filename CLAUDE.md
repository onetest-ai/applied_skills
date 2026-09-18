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
```

There is no build step and no linter config. `uv` resolves dependencies per invocation; nothing is installed globally.

**`.pptx`/`.docx` parsing needs LibreOffice (`soffice`) on PATH.** PDFs need only PyMuPDF. Scripts that require `soffice` exit with an instruction rather than degrading.

## Architecture

**Build (brain), in order:** source registry → parse (`corpus-taxonomy-extraction/parse_corpus.py`, plus `visual-parse` for slide decks and HTML) → taxonomy induction → `knowledge-index` (FTS5 + sqlite-vec chunks) and `tabular-semantic-layer` (numeric marts) → `brain_sync` applies the delta into `knowledge.sqlite`. `knowledge-pipeline` orchestrates; `brain-maintenance` re-runs it.

**Serve:** `mcp/brain/fastmcp_server.py` wraps `semantic_core.py` and exposes read-only tools (`search_knowledge`, `get_metric`, `get_taxonomy`, `get_evidence`, …). It derives its own identity from the store's `meta` table, so a deployed Brain describes itself.

**Consume (kb):** skills identify a Brain by its **tool surface**, never by MCP server name — users register Brains under any name, and Claude Code allow-rules cannot glob the server segment.

### Cross-file invariants

These are the things that take several files to see, and that tests pass while violating:

**The visual-lane artifact layout is a contract, not an implementation detail.** `render_pages.py` writes, per document slug, `pNN.png`, `pNN.txt` (verbatim text sidecar), optional `pNN.tables.md` (deterministic table grids) and `pages.json`; `vision_prep.py` reads exactly that and hands batches to a vision model; `vision_assemble.py` turns the results into the parsed Markdown that gets indexed. Anything that becomes a second producer of that layout must match it in **field values, not just key names** — `image` is slug-relative (`<slug>/pNN.png`) because `vision_prep` joins it onto the *parent* of the render dir, so a schema check passes while a wrong value breaks every image path.

**`chunking.py` exists twice and the copies must stay byte-identical** (`knowledge-index/` and `corpus-taxonomy-extraction/`), so a retrieval chunk is exactly the vault note a human sees. Change one, change both.

**`strip_preamble` in `chunking.py` is coupled to the header `parse_corpus.py` writes** (`# SOURCE:` then `# method:`). Adding a preamble line without teaching both copies to strip it turns that line into a live H1, which then seeds `parent_heading`/`breadcrumb_path` — indexed columns returned in search results — for every document of *every* format. Every parser emits level-2 headings for its own sections precisely so the preamble is the only H1 candidate.

**kb skills carry their rules inline, not by reference.** The Agent Skills format documents only same-directory references from a `SKILL.md`; `../_shared/…` is unspecified and does not resolve in Cowork. So the non-negotiable contract lives between `<!-- BRAIN-CONTRACT:START/END -->` in `_shared/doctrine.md` and is inlined **verbatim** into every answering skill, pinned byte-for-byte by a drift test. Depth may stay in `_shared/`; rules may not.

**`brain_sync` links parsed docs to registered sources.** A registered source with no parsed output lands in `blocked_missing_parsed` and aborts `apply` — which is why a parser that cannot handle a format must skip it *with a reason in the manifest* rather than emit nothing silently.

**`meta` carries `goal`, `audience` and optional `name`.** `write_meta` UPSERTs `goal`/`audience` unconditionally from their source files, but `name` **only when `name.txt` is non-empty** — no existing project has one, and an unconditional write would clear a `meta.name` set by hand. Do not "tidy" that asymmetry into consistency.

## Conventions

- **Every `SKILL.md` description opens with the literal `Use when `.** The description is all a model sees when deciding to invoke a skill, so the trigger leads and the capability follows. Enforced by tests in both bundles.
- **No kb `SKILL.md` declares `allowed-tools`.** It is pre-approval, never capability, and it cannot name an MCP server whose name varies per user. Brain calls prompt; that is accepted.
- **Tests live at the bundle root** (`bundles/*/tests/`), never inside a skill directory — per the Agent Skills standard a skill ships only `SKILL.md` and its runtime resources. `conftest.py` puts every skill dir on `sys.path` so a test can `import parse_corpus` directly.
- **Design specs and implementation plans are untracked**, under the gitignored `docs/superpowers/`. Tracked `docs/` is for user-facing guides.
- **Branch from `main` and open a PR** (`.github/PULL_REQUEST_TEMPLATE.md`); the repo has no direct-to-main workflow.
- **Adding a Python dependency is a real decision.** The parse stack is deliberately torch-free — PyMuPDF for PDF text, page rendering and table grids; `fastembed`/onnx for embeddings; `sqlite-vec` for vectors. Browser automation is driven through MCP by the agent rather than spawned by scripts, specifically so no browser enters `requirements.txt` or the Docker image.

## In flight

PR #26 (`feat/html-ingestion`) adds HTML deck ingestion: DOM-based segmentation, a second producer of the visual-lane artifact layout (`html_capture.py`), a `# fidelity:` preamble line, and a PyMuPDF text fallback. If it has merged, the two invariants above about field values and the preamble are load-bearing for that code — check `git log` before assuming this section is current.

## Working on skills

A `SKILL.md` is a prompt that an agent executes, so treat its prose as code: an ambiguous imperative is a bug. Two failure modes recur here — a subjectless instruction that an agent reads as "do this yourself" when it should tell the user, and a documented sequence whose steps have no runnable command behind them. Scripts in `visual-parse/`, `knowledge-pipeline/` and `corpus-taxonomy-extraction/` all expose argparse CLIs; a new script that a skill documents should too.
