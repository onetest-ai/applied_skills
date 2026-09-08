# 🧠 Brain — a local, truthful knowledge engine

**One portable `knowledge.sqlite` + an Obsidian vault over a messy corpus of documents *and* spreadsheets.**
No server, no cloud, no lock-in. Copy one file and the whole brain moves with it.

> **The one rule everything obeys:**
> **Meaning is agentic. Numbers are computed.**
> RAG and the graph explain *what things mean and how they relate*; they never assert a figure.
> The marts compute *every number* deterministically from source tables; they never guess.
> Every answer is either **cited** or an honest **"not modeled."** Gaps beat fabrication.

---

## What's in the bundle

`brain` collects five skills (+ one optional) and all their scripts into one installable set:

| Skill | Role | Ships |
|---|---|---|
| **knowledge-pipeline** | 🎛️ orchestrator — the entrypoint | `SKILL.md` (build & answer sequence) |
| **corpus-taxonomy-extraction** | 🏷️ meaning: parse, induce taxonomy, build graph, tag sections, emit vault | `parse_corpus.py`, `consolidate.py`, `emit_taxonomy.py`, `emit_ontology.py`, `chunking.py`, `build_graph.py`, `classify_prep.py`, `classify_write.py`, `to_obsidian.py` |
| **knowledge-index** | 🔎 narrative: heading-aware chunks → FTS5 + vectors | `knowledge_index.py`, `chunking.py` (shared) |
| **tabular-semantic-layer** | 🔢 numbers: Excel → deterministic `facts` | `build_marts.py`, `profile_workbooks.py`, `families.example.json`, `metrics.example.json` |
| **hybrid-retrieval** | 🧭 answer: route each sub-claim to the right lane, fuse, cite | `query.py` |
| _cognee_ (optional) | 🌐 external graph service (only if you run one) | `cognee_client.py`, `api-reference.md` |

Plus the repo's top-level **`mcp/brain/`** — the MCP **tool layer** (a dependency-free stdio server, `brain_mcp.py`) that fronts these skills as tools. It lives in `mcp/`, not `skills/` (see below).

---

## Install

```bash
# the whole brain, into every host you use (claude / dsh / copilot / codex)
npx github:onetest-ai/applied_skills init --bundle brain

# or with the shell installer from a checkout
./install.sh --bundle brain

# add the optional cognee skill; scope to one host; or symlink for live edits
npx github:onetest-ai/applied_skills init --bundle brain --optional --target claude --symlink
```

The installer reads `bundles/brain/factory.json`, resolves the ordered skill list, and copies (or symlinks) each into the host's native `skills/` dir. All hosts read the same `SKILL.md` format — no translation.

---

## Getting started — guided onboarding

Don't hand-run the pipeline on your first brain. Ask the orchestrator to **create a brain** / **get started** and it runs a wizard: it asks for your **goal** (the single analytical goal that scopes everything), your **docs** folder, your **reporting spreadsheets** (if any), and a **project dir** — then scaffolds the layout, drops in config templates, preflights the deps, scans your corpus into narrative-vs-reporting, and writes a `BRAIN.md` with the exact ordered build commands. It walks you through the build (pausing at the two agent steps), verifies every lane, and answers your first question.

```bash
# the deterministic core of the wizard (the orchestrator drives the questions):
python .../knowledge-pipeline/onboard.py scaffold --project ./acme-brain \
  --goal "optimize call-center ops and introduce an AI workforce" --docs ./docs --reporting ./xlsx
python .../knowledge-pipeline/onboard.py verify --db ./acme-brain/schema/knowledge.sqlite
```

See **knowledge-pipeline → Guided onboarding** for the full flow.

## The tool layer: an MCP server (who does what)

The brain is served to an agent through the **`brain` MCP server** — a dependency-free
stdio JSON-RPC server (stdlib only, ~100 lines; no SDK, no web framework — it *hides the
scripts behind tools*). This draws a hard line between the two jobs:

```mermaid
flowchart LR
    A["🧠 Agent<br/>(reasoning layer)<br/>decompose · route · compose<br/>ONE cited answer"] -->|MCP tool call| S
    subgraph S["🔌 brain MCP server (tool layer)"]
        direction TB
        T1["search — narrative (cited)"]
        T2["sql / metric — numbers (computed)"]
        T3["graph — taxonomy (cited)"]
        T4["verify · which"]
    end
    S --> DB[("🗄️ knowledge.sqlite")]
    S -. owns .-> V["🐍 venv (its own)"]
    S -. owns .-> K["📦 skills' code"]

    classDef ag fill:#fff3e0,stroke:#e65100,color:#bf360c;
    classDef srv fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    class A ag
    class S srv
```

- The **server owns the venv + skills + store** and returns cited text / computed numbers — it **never reasons**. The truthfulness rule lives right here: `sql`/`metric` return figures with `source_file`; `search`/`graph` return cited text, never a number.
- The **agent never runs Python or guesses a path** — it calls tools. Because the server is registered with the venv's interpreter, "where do I run?" simply doesn't arise.

The server lives at the repo's top-level **`mcp/brain/`** (installed to `<host>/mcp/brain/`, a sibling of `<host>/skills/`). Register it during install (writes `.mcp.json` for Claude Code: `command=<venv>/bin/python`, `args=[…/mcp/brain/brain_mcp.py]`):

```bash
./install.sh --bundle brain --deps --mcp          # copy skills, build venv, register the server
./brain mcp-config                                # or print the JSON block for another host
```

Tools: `which` · `search(query,k)` · `sql(query)` · `metric(name,grain?,entity?,…)` · `graph(label?,relation?,kind?)` · `related(chunk_id?,query?)` · `page(chunk_id?,query?)` · `verify`.

**Building the answering agent on top?** See [`BUILDING-AGENTS.md`](BUILDING-AGENTS.md) — what to put in *your* agent's role instructions so it disambiguates scope/grain/population, surfaces caveats, and answers truthfully (this is agent-design guidance, separate from a deployment's own `AGENTS.md`).

## The core idea: two lanes, one truth

Most "chat with your docs" tools blur narrative and numbers into one embedding soup, then let the model *narrate a number*. That is exactly where they lie. Brain keeps the two apart by **what makes each trustworthy**, and only rejoins them at answer time.

```mermaid
flowchart LR
    Q["❓ Question"] --> R{"🧭 Router<br/>(hybrid-retrieval)<br/>decompose into sub-claims"}
    R -->|"meaning / narrative"| A["🟢 AGENTIC LANE<br/>RAG + graph + tags"]
    R -->|"any actual number"| N["🔵 DETERMINISTIC LANE<br/>SQL over facts"]
    A --> C["🧩 Compose<br/>one cited answer"]
    N --> C
    C --> OUT["✅ Answer<br/>every claim cited,<br/>or 'not modeled'"]

    classDef ag fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20;
    classDef de fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    class A ag
    class N de
```

- 🟢 **Agentic lane** — narrative retrieval (BM25 + vectors, fused), a taxonomy graph, and per-section tags. Answers *what things mean and how they relate*. **Never emits a figure.**
- 🔵 **Deterministic lane** — SQL over the `facts` table, each row tracing to a source workbook. Answers *every actual number*. **Never guesses.**

---

## One store, three lanes, one file

Everything lands in a single `knowledge.sqlite`. A **chunk = a section = an Obsidian note = a retrieval unit = a graph-linked entity** — one identity across all lanes, so retrieval hits, tags, graph vertices, and human-readable notes all reference the same ids.

```mermaid
erDiagram
    chunks       ||--|| chunks_fts   : "BM25 mirror"
    chunks       ||--|| chunks_vec   : "384-d embedding"
    chunks       ||--o{ chunk_topics : "per-section tags"
    graph_nodes  ||--o{ chunk_topics : "about (chunk→vertex)"
    graph_nodes  ||--o{ graph_edges  : "subclass_of / about"

    chunks {
        int id PK
        text source
        int ord
        text title
        text text
    }
    chunk_topics {
        int chunk_id FK
        text category_label
    }
    graph_nodes {
        text id PK
        text label
        text kind
    }
    graph_edges {
        text source
        text rel
        text target
    }
    facts {
        text metric
        text dimension
        real value
        text source_file
    }
```

| Lane | Tables | Built by | Answers |
|---|---|---|---|
| 🟢 narrative (RAG) | `chunks`, `chunks_fts`, `chunks_vec` | knowledge-index | "what does the corpus say about…" |
| 🟢 taxonomy graph | `graph_nodes`, `graph_edges`, `chunk_topics` | corpus-taxonomy-extraction | "how do these relate / classify" |
| 🔵 numbers | `facts` (+ governed `metrics.<corpus>.json`) | tabular-semantic-layer | "what is the value / trend / comparison" |

The **Obsidian vault** is a *view* of this same store: one note per chunk, real per-section tags from `chunk_topics`, and `[[topic]]` links that are 1:1 with the graph vertices. So a human browsing the vault sees exactly what retrieval sees.

---

## Build pipeline (run once per corpus; re-run to refresh)

```mermaid
flowchart TD
    subgraph IN["📥 Corpus"]
        D["📄 Docs<br/>PDF · PPTX · DOCX"]
        X["📊 Spreadsheets<br/>XLSX reporting"]
    end

    D --> P["1 · parse + visual-parse<br/>→ uniform Markdown<br/><i>pymupdf text · VLM for visual pages</i>"]
    P --> TX["2 · induce taxonomy<br/>map → reduce → judge → emit<br/><b>low-tier agents</b> (Haiku)<br/>→ taxonomy_v0.json"]

    P --> IDX["3 · knowledge_index.py<br/>heading-aware sections (shared chunker)<br/>→ chunks + FTS5 + sqlite-vec"]
    TX --> G["4 · build_graph.py<br/>taxonomy → graph_nodes / edges<br/>(L1/L2 · subclass_of)"]
    IDX --> CLS["5 · classify_prep → <b>Haiku agents</b> → classify_write<br/>real per-section tags (empty when nothing fits)<br/>→ chunk_topics + 'about' edges"]
    G --> CLS

    X --> M["6 · build_marts.py<br/>Excel → weighted rollups<br/>→ facts (+ audit, --strict)"]

    CLS --> DB[("🗄️ knowledge.sqlite<br/>ONE portable file")]
    IDX --> DB
    G --> DB
    M --> DB
    DB --> OBS["7 · to_obsidian.py<br/>vault = a VIEW of the store<br/>notes · tags · [[topic]] links"]

    classDef agent fill:#fff3e0,stroke:#e65100,color:#bf360c;
    classDef det fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    classDef store fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c;
    class TX,CLS agent
    class P,IDX,G,M det
    class DB,OBS store
```

**Steps 2 and 5 are agents on cheap models, not scripts** — deciding what a term *means*, what merges, and which section is *really* about a category is judgment, and a low-tier model does it well and cheaply. Everything numeric (steps 3, 4, 6) is deterministic code. The document bulk lives and dies inside each subagent; the orchestrator only ever sees compact JSON.

```bash
DB=<project>/schema/knowledge.sqlite
python .../corpus-taxonomy-extraction/parse_corpus.py --corpus <docs> --out <project>/parsed --formats pptx,docx,pdf
# 2. induce taxonomy (map→reduce→judge→emit; Haiku subagents) → taxonomy_v0.json
python .../knowledge-index/knowledge_index.py index --db "$DB" --corpus <project>/parsed --reset
python .../corpus-taxonomy-extraction/build_graph.py --taxonomy <project>/taxonomy/taxonomy_v0.json --db "$DB"
python .../corpus-taxonomy-extraction/classify_prep.py --db "$DB" --taxonomy <project>/taxonomy/taxonomy_v0.json --out <project>/classify --batches 5
#    → dispatch N Haiku subagents → classify/result_k.json
python .../corpus-taxonomy-extraction/classify_write.py --db "$DB" --results <project>/classify
python .../tabular-semantic-layer/build_marts.py --root <reporting> --config <project>/schema/families.<corpus>.json --out-dir <project>/marts --db "$DB"
python .../corpus-taxonomy-extraction/to_obsidian.py --db "$DB" --out <project>/vault
```

---

## The taxonomy — when it's made, where it lives, and how it changes

The **taxonomy is the seed the whole store keys off**: the intent hierarchy (L1/L2) that becomes the graph vertices and the closed vocabulary the classifier tags sections against. Understanding *when* it's produced and *how it evolves* is the difference between a store that stays coherent and one whose tags rot.

### Where it sits in the pipeline
Taxonomy induction is the **first agentic step, right after parsing** — and everything relational depends on it:

```mermaid
flowchart TD
    P["1 · parse (+ visual-parse)<br/>docs → parsed/*.md"] --> TX["2 · 🤖 TAXONOMY induction<br/>parsed/ + goal → taxonomy_v0.json<br/><i>map → reduce → judge → emit · human-gated</i>"]
    P --> IDX["3 · index<br/>chunks + FTS + vector"]
    TX --> G["4 · build_graph<br/>taxonomy → graph_nodes / edges (L1/L2)"]
    TX --> CL["5 · 🤖 classify<br/>chunk × taxonomy-vocab → chunk_topics + about-edges"]
    IDX --> CL
    G --> CL
    CL --> R["5b · related · 6 · marts · 7 · vault(opt) · 8 · seed"]

    classDef ag fill:#fff3e0,stroke:#e65100,color:#bf360c;
    class TX,CL ag
```

- **`index` (3) does NOT depend on the taxonomy** — it can run in parallel; but **`build_graph` (4) and `classify` (5) do**: the graph *is* the taxonomy as vertices, and the classifier tags each chunk *against the taxonomy vocabulary*. So the taxonomy must exist before them.
- Induction reads the **document text** (`parsed/`), not the chunks/store.

### How it's made (`map → reduce → judge → emit`)
1. **map** — low-tier (Haiku) subagents, per document, extract candidate terms (intent classes, entities, metrics) each with an evidence quote, source, and confidence → one JSON per doc. The bulk context lives and dies inside each subagent.
2. **reduce** — `consolidate.py` deterministically clusters near-duplicates (stdlib difflib); a low-tier agent adjudicates **only the ambiguous** merges ("Chicago" vs "CHI").
3. **judge** — an LLM-as-judge scores coverage/coherence and flags low-confidence/unmapped terms.
4. **emit** — `taxonomy_v0.json` (+ `.md`): human-reviewable, **versioned**, with a *demoted* list.

The **goal string is a noise filter** — extraction is scoped to the analytical goal. Prefer **seed-guided over schema-free**: anchor on any existing taxonomy doc (a "Taxonomy Compendium") and extend it.

### Where it lives
`taxonomy_v0.json` is a **project artifact** — it lives in the consuming project (with `families.<corpus>.json`, the goal, the built store), **never in the store or the skills**. It is *loaded into* the store as `graph_nodes` by `build_graph`, but the source-of-truth JSON stays a file so it can be reviewed, versioned, and hand-edited.

### How the visual/VLM parse affects it
The parse stack now transcribes visual pages (flows, timelines, diagrams) via `visual-parse` instead of dropping them to fragments. Since induction reads `parsed/`, **its input is now richer** — concepts that previously lived only on slides ("North Star Vision & Service Design Blueprint", phase/framework/capability names) become visible to the map step, so a freshly-induced taxonomy covers **more**. Consequence:

- A **from-scratch build** captures visual concepts automatically (induction reads the VLM-enriched `parsed/`).
- An **existing brain whose taxonomy predates the visual parse under-covers** those concepts — visible as visual-page chunks that classify leaves **untagged** (no matching L1). That's the signal it's time to refresh the taxonomy.

### How it behaves during an update (the key rule)
The pipeline deliberately separates **two different deltas**:

| What changes | Mechanism | Automatic? |
|---|---|---|
| **Content** (docs added/changed/deleted) | `brain_sync` by content hash → re-embed + reclassify only the delta | ✅ automatic (`./brain update`) |
| **Vocabulary** (the taxonomy itself) | re-induce + **additive** merge | ❌ deliberate, human-gated |

So on `./brain update`: the taxonomy is **reused as-is**; changed docs are **reclassified against the existing vocabulary**; a genuinely new concept in a new doc **does not get a new L1** until someone re-induces and extends the taxonomy.

**Why vocabulary change is gated and additive:** chunk ids are content-addressed and graph node ids are `slug(label)`, so **adding** L1/L2 is safe (`build_graph` is non-destructive — it rebuilds `subclass_of`, preserves `about` edges, and only prunes nodes that vanished). But **renaming or removing** an L1 changes its node id and orphans every `chunk_topics` tag and `about` edge that pointed at it. Therefore taxonomy evolution during updates is **add-only, never rename**, and passes a human gate.

### Refreshing the taxonomy (when the corpus or parse shifts)
1. **Signal** — a rising share of **untagged** chunks after classification, or new docs / newly-transcribed visual pages carrying concepts with no home L1.
2. **Re-induce on the delta** — run `map → reduce → judge` over the parsed text of the new/changed docs → candidate new terms.
3. **Human review → additive merge** into `taxonomy_v0.json` (bump the version; **add** L1/L2, never rename; deprecate rather than delete).
4. **Rebuild + reclassify** — `build_graph` (adds the new vertices, keeps existing edges) → reclassify the affected chunks (now the new L1s are available) → refresh `related` and (optionally) the vault.

> Not yet automated: the induce-delta-and-merge step is currently a manual run of the taxonomy scripts plus a hand-merge of the JSON. `build_graph` (additive) and `classify` (incremental, per-chunk) already support the downstream half.

---

## Answer pipeline (per question)

```mermaid
sequenceDiagram
    participant U as ❓ User
    participant H as 🧭 hybrid-retrieval
    participant R as 🟢 RAG (FTS+vec RRF)
    participant Gr as 🟢 Graph (JOINs)
    participant Ma as 🔵 Marts (SQL)
    U->>H: question
    H->>H: decompose into sub-claims
    Note over H: classify each sub-claim by lane
    par narrative
        H->>R: search chunks → cited sections (= notes)
    and relation / classification
        H->>Gr: graph_nodes / edges / chunk_topics JOINs
    and any number
        H->>Ma: deterministic SQL over facts
    end
    R-->>H: quotes + citations
    Gr-->>H: vertices + tags
    Ma-->>H: figures + source_file
    H->>U: one composed answer — every claim [RAG]/[GRAPH]/[MART] cited,<br/>unmodeled parts stated plainly
```

**Retrieval fusion (the RAG lane):** BM25 (SQLite FTS5) and vector similarity (sqlite-vec, `bge-small-en-v1.5`, 384-d) are run independently and merged by **Reciprocal Rank Fusion** (`k=60`, weights FTS `0.4` / VEC `0.6`, pool `30`) — the pattern borrowed from the `wikis` retriever. Numbers never come from here; they come from `facts`.

---

## Why local SQLite + Obsidian (and not a server)

- **Portable** — one file. Moves to `.dsh` / Claude / Copilot / Codex / CI with a copy; no service to stand up, no auth to manage.
- **Hybrid search is ours** — FTS5 + sqlite-vec + RRF need no external engine.
- **Taxonomy & node-sets live fine in the graph tables + the vault** — a UI/server was more friction than value for a portable toolkit.
- **RAG lives in the same SQLite** as the numbers and the graph, so one identity ties chunk ↔ note ↔ tag ↔ vertex.
- **Cognee stays optional** — if you *do* run a graph service, the `cognee` skill talks to it (MCP-preferred), but nothing in the core depends on it.

---

## Dependencies & the skills' venv

Python ≥ 3.9, all pip-installable: `sqlite-vec`, `fastembed`, `pymupdf`, `openpyxl`, `pandas`, `pyarrow` (`sqlite3` is stdlib). **Torch-free** (docling retired) — the installed set is small (~200 MB, mostly onnxruntime). `.pptx/.docx` also need LibreOffice `soffice` (a system dep); PDFs need only pymupdf.

These deps live in a venv that **belongs to the skills, not your project** — kept separate so they never mix with your project's own Python env. The installer (via `uv`) builds it next to the skills inside the host dir:

```bash
./install.sh --bundle brain --deps          # per-project:  <project>/.claude/venv
./install.sh --bundle brain --deps --user   # SHARED:       ~/.claude/venv  (or ~/.dsh/venv)
```

Use `--user` to build **one shared venv reused by every project** instead of a venv per project. The generated `BRAIN.md` and scripts then run under that interpreter (`BRAIN_PY`). Zero-install alternative (no venv, uv caches the deps): `uv run --with-requirements requirements.txt python <script>`.

The low-tier map/classify steps assume a subagent mechanism with a model override (e.g. Haiku); on another harness, substitute any cheap model that can read a file and emit JSON.

## Generic vs project-specific

The bundle ships **only generic code**. Everything corpus-specific — `families.<corpus>.json`, `metrics.<corpus>.json`, the goal string, `taxonomy_v0.json`, and the built `knowledge.sqlite` — stays in the consuming project, never in the skills.
