# Applied AI Skills

A distributable collection of **generic, corpus-agnostic** agent skills for building truthful data pipelines over documents, knowledge graphs, and tabular reporting. Each skill is self-contained code + docs; **no project-specific data, paths, or credentials live here** — those stay in the consuming project.

## The pipeline

**Meaning is agentic (RAG/graph); numbers are computed (deterministic SQL).** RAG never produces figures; the mart lane never guesses. Everything lands in **one portable `knowledge.sqlite`** (no server) — narrative chunks (FTS5+vector), numeric marts, and the taxonomy graph — with an **Obsidian vault** as the human-readable canon.

```
docs ─▶ corpus-taxonomy-extraction ─▶ Markdown + taxonomy_v0 + graph  ─▶ Obsidian vault (human canon)
                                          │                    │
          Markdown ─▶ knowledge-index ────┤ chunks+FTS5+vector │
   reporting xlsx ─▶ tabular-semantic-layer│ facts (marts)     ├─▶  ONE knowledge.sqlite
                     corpus…/build_graph.py │ graph_nodes/edges │        │
                                                                └─▶ hybrid-retrieval ─▶ cited answer
                                            (all orchestrated by knowledge-pipeline)
```

Retrieval is **hybrid**: BM25 (FTS5) + vector (sqlite-vec) fused by **Reciprocal Rank Fusion** — pattern from [arozumenko/wikis](https://github.com/arozumenko/wikis). One file, moves anywhere. The RAG embedder (fastembed/onnx) needs no PyTorch; note the `docling` parser does pull torch (see Dependencies).

## Skills

| Skill | Role | Key idea |
|---|---|---|
| **corpus-taxonomy-extraction** | build | Goal-directed taxonomy induction (intent classes + entities + metric inventory) from a mixed corpus (PDF/PPTX/XLSX via Docling/pypdf). Also emits the taxonomy **graph** (`build_graph.py`) and an **Obsidian vault** (`to_obsidian.py`). |
| **knowledge-index** | build | Local hybrid RAG over Markdown → SQLite **FTS5 + sqlite-vec, RRF-fused**. Torch-free embeddings (fastembed/onnx). The narrative lane. |
| **tabular-semantic-layer** | build | Config-driven ETL of large/heterogeneous Excel → normalized `facts` in the same SQLite + a governed metric catalog. Four layouts, weighted rollups, build audit (`--strict`). |
| **hybrid-retrieval** | answer | Routes each sub-question — numbers→marts SQL, narrative→RRF RAG, relations→graph JOINs — over the one SQLite; reconciles `both`; composes one cited answer. |
| **knowledge-pipeline** | orchestrate | One entry point: build the store (index+marts+graph) then answer, with disk-first workspace/checkpoint discipline for long research. |
| **cognee** | optional | Connection-agnostic REST/MCP access to a Cognee server — an *alternative* remote knowledge backend. Not part of the default local stack. |

## Why this exists

Vector RAG cannot return correct numbers; raw text-to-SQL returns *confident wrong* numbers. A governed semantic layer over deterministic SQL benchmarks far higher and fails by honest refusal. These skills implement that split, plus the operational guardrails (provenance, entity conformance, silent-gap auditing) that make it hold up in production.

## Install

Every host reads the standard `SKILL.md` format, so install is a copy (or symlink) — no translation.

**Targets**

| Host | Project dir | User dir (`--user`) |
|---|---|---|
| `claude` | `.claude/skills/` | `~/.claude/skills/` |
| `dsh` (DeepSeek Harness) | `.dsh/skills/` | `~/.dsh/skills/` |
| `copilot` (GitHub Copilot) | `.github/skills/` | `~/.copilot/skills/` |
| `codex` | `.codex/skills/` | `~/.codex/skills/` |

### Bundles (curated sets)

Install a whole curated toolchain in one shot instead of listing skills. The **`brain`**
bundle is the full local knowledge engine (the pipeline above) — see
[`bundles/brain/README.md`](bundles/brain/README.md) for the human-facing creation/update flow,
[`bundles/brain/AGENT_README.md`](bundles/brain/AGENT_README.md) for the build agent's exact
orchestration runbook, and [`bundles/SPEC.md`](bundles/SPEC.md) for the bundle format.

```bash
npx github:onetest-ai/applied_skills init --bundle brain              # the 7 pipeline skills
npx github:onetest-ai/applied_skills init --bundle brain --optional   # + optional cognee
./install.sh --bundle brain                                           # same, from a checkout
```

### npx one-liner (no clone)

```bash
npx github:onetest-ai/applied_skills init                      # all skills → all 4 hosts (this project)
npx github:onetest-ai/applied_skills init --target claude,dsh  # pick hosts
npx github:onetest-ai/applied_skills init --user               # install under $HOME
npx github:onetest-ai/applied_skills init --symlink            # link instead of copy
npx github:onetest-ai/applied_skills init --dry-run            # preview
```
(Private repo → needs git access, e.g. `npx git+ssh://git@github.com/onetest-ai/applied_skills.git init`.)

### shell installer (no Node)

```bash
git clone git@github.com:onetest-ai/applied_skills.git && cd applied_skills
./install.sh --target all            # or claude|dsh|copilot|codex
./install.sh --target codex --user   # → ~/.codex/skills
./install.sh --symlink --dry-run
```

Restart the host session after installing so it loads the skills.

**Claude plugin (alternative):** this repo is also a plugin marketplace —
```bash
claude plugin marketplace add onetest-ai/applied_skills
claude plugin install applied-skills@onetest-ai
```
(Don't combine the plugin and the copy/symlink install — pick one, or the skills load twice.)

Corpus-specific configuration (family definitions, metric catalogs, Cognee connection + dataset ids) belongs in the **consuming project's** repo, not here. Each skill's `*.example.*` templates show the shape to copy.

## Dependencies

Python 3.10+, stdlib `sqlite3` (with `enable_load_extension`). Per-skill: `pymupdf` (PDF text + page render + table extraction); `sqlite-vec`, `fastembed` (knowledge-index RAG); `openpyxl`, `pandas`, optional `pyarrow` (tabular); `fastmcp` + `uvicorn` (Brain MCP stdio/HTTP). All are pip-installable and **torch-free** (docling retired). `.pptx/.docx` also need LibreOffice `soffice` (a system dep); PDFs need only pymupdf.

Install them into an **isolated venv that belongs to the skills, not your project** — `install.sh --bundle brain --deps` (uses `uv`) builds `<project>/.claude/venv`. Add `--user` to build **one shared** `~/.claude/venv` (or `~/.dsh/venv`) reused across all projects instead of a venv per project. Zero-install alternative: `uv run --with-requirements bundles/brain/requirements.txt python <script>`. The store remains local and portable; the optional Brain MCP HTTP transport is disabled by default. `cognee` remains an optional remote backend.
