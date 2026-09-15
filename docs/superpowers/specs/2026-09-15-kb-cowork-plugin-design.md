# `kb` — a knowledge-worker plugin over the Brain

**Status:** Design (approved for spec review)
**Date:** 2026-09-15
**Author:** Artem Rozumenko (with Claude)

## 1. Summary

`kb` is a new, lightweight Claude Code plugin that lets a person **cowork with the
Brain** — interrogate it and co-author cited deliverables from it — without ever
undoing the Brain's core truth guarantee (*meaning is agentic, numbers are
computed; every claim is cited or honestly "not modeled"*).

It is the **consumer/knowledge-worker** counterpart to the existing
**producer/maintainer** tooling in the `applied-skills` umbrella plugin. The two
ship as separate plugins in the one `onetest-ai` marketplace:

| | `applied-skills` (umbrella; ships the brain bundle) | `kb` (new) |
|---|---|---|
| Role | build / maintain / deploy the Brain (via the `brain` bundle) | interrogate + author over it |
| Skills | the pipeline & maintenance skills | ask, explore, challenge, brief, report, mode, connect |
| MCP | bundles the pipeline; the Brain MCP is **registered separately** (`./brain mcp-config`, stdio) | **consumes** it (health-detected) |
| Support | — | shared doctrine, `verifier` subagent, ambient hooks |

`kb` reads and composes only. It never writes `knowledge.sqlite`, never runs the
Brain's build/venv scripts, and never asserts a number the Brain did not compute.

## 2. Goals & non-goals

### Goals
- **Interrogate** the Brain interactively: one-shot cited answers, iterative
  deep-dives, and adversarial gap-finding.
- **Co-author** cited Markdown deliverables (memos, reports) grounded in the
  Brain, where every claim is traceable and every number carries its
  `source_file`.
- Enforce the Brain's truth contract **at the composition/authoring layer**, via a
  shared doctrine and an independent verifier gate — with the human as the final
  gate.
- Offer an **opt-in ambient mode** that makes an entire session behave with the
  same grounded, cited discipline, at effectively zero idle overhead.
- Stay **lightweight and composable**: no bundled MCP server, no heavy deps; robust
  across the Brain's install styles.

### Non-goals
- **No deck / presentation engine.** `kb` produces cited Markdown and is an
  excellent *source*; anything that builds HTML decks lives elsewhere and can pull
  from the Brain via its MCP.
- **No brain-building or maintenance.** Creating, updating, and deploying the Brain
  stays with the `applied-skills` plugin's own skills (the `brain` bundle).
- **No writes to the store** and no raw-SQL surface. `kb` uses only the governed
  FastMCP tools.
- **No orchestration-at-scale** (large multi-agent fan-out). `kb` is an interactive
  companion, not a research farm.

## 3. Background: what `kb` builds on

The Brain exposes a governed FastMCP server (`mcp/brain/fastmcp_server.py`,
implementation in `semantic_core.py`) with **seven tools**. Every tool returns a
`status` of `ok | not_modeled | error`; errors come back as structured results
(never HTTP 500); every `limit` argument is an integer 1..100.

| Tool | Purpose (lane) |
|---|---|
| `list_metrics()` | discover governed metrics + availability (numbers) |
| `get_metric(name, grain?, entity?, entity_contains?, month?, start_month?, end_month?, limit=50)` | authoritative numbers, each row with `source_file` |
| `search_knowledge(query, limit=5)` | narrative RAG (BM25+vector, RRF-fused), cited; never a figure |
| `get_taxonomy(label?, relation?, kind?, limit=50)` | taxonomy graph: nodes, subclasses, edges, tagged sections |
| `find_related_content(chunk_id?, query?, limit=6)` | precomputed cross-document neighbors |
| `get_evidence(chunk_id, include_page_text=True)` | one section's text + page-image path + verbatim page text + extracted table grids |
| `health()` | lane counts, vector-extension check, `knowledge_version`, empty-lane flags |

The doctrine for an answering agent already exists at
`bundles/brain/BUILDING-AGENTS.md` — `kb` distills it rather than reinventing it.

The Brain MCP server is registered out-of-band, not declared in any plugin
manifest: `./brain mcp-config` writes a stdio config (venv interpreter +
resolved `BRAIN_*` env) for the local store. Depending on how that config is
merged, the Brain answers as a standalone `brain` MCP server (tools
`mcp__brain__*`) or, if registered under the `applied-skills` umbrella plugin,
under that plugin's scoped namespace (`mcp__plugin_applied-skills_brain__*`).
`kb` detects which namespace answers via `health` and documents both.

## 4. Architecture

Four component layers, all in a single plugin directory:

1. **Skills** — the `/kb:*` suite (interrogate + co-author + utility).
2. **Shared doctrine** — one reference file every skill and the ambient hook point
   at, so answers *and* artifacts obey one truth rule.
3. **`verifier` subagent** — read-only; independently re-checks numbers and
   citations against the Brain.
4. **Ambient mode** — a project-scoped toggle read by two hooks.

### Boundary rules (invariants)
- `kb` calls **only** the seven governed FastMCP tools; no writes, no raw SQL, no
  build scripts.
- **No number** appears in any `kb` output that the Brain did not compute via
  `get_metric`; every number renders with its `source_file`.
- **Gaps beat fabrication:** a `not_modeled`/empty result is stated plainly, never
  filled from model priors.
- The Brain is a **hard dependency, gracefully degraded**: with no reachable Brain,
  `kb` says so and stops (points to `/kb:connect`); it never fabricates.
- Every artifact passes the **verifier** and then a **human gate** before it is
  written or published. File-writes are never auto-approved.

## 5. Component design

### 5.1 Skills

All skills are namespaced `/kb:` (plugin name `kb`). Each opens by calling
`health` to confirm a reachable Brain and discover the answering namespace, then
follows the shared doctrine (§5.2).

**Interrogate — work *with* the Brain:**

- **`/kb:ask "<question>"`** — one-shot, fully cited answer. Decomposes the question
  into sub-claims, routes each to the right lane (`search_knowledge` narrative,
  `get_metric` numbers, `get_taxonomy` relations, `get_evidence` page/table), then
  composes one answer where every claim is tagged and cited, or marked
  "not modeled." The everyday driver.
- **`/kb:explore "<topic>"`** — iterative, multi-turn deep-dive. Anchors on a
  taxonomy node or top search hit, then walks `find_related_content` and
  `get_taxonomy` subclasses to map a topic across documents. Ends with a navigable
  summary (chunk ids / vault paths). May run its gather phase in a forked context
  to keep tool-output out of the main thread.
- **`/kb:challenge "<claim or prior answer>"`** — adversarial. Tries to *break* a
  claim: contradicting sections, whether cited numbers exist at the stated grain,
  silent gaps ("asserted but not modeled"). The skeptic.

**Co-author — produce deliverables *from* the Brain (Markdown, in-project):**

- **`/kb:brief "<subject>"`** — a short cited memo.
- **`/kb:report "<subject>"`** — long-form structured document (adds a table of
  contents and section structure).

Both run the shared authoring pipeline (§5.3). Default output path is a
per-project-configurable directory, default `docs/kb/`, with pointing at the
Brain's Obsidian vault as a documented option. Each document is accompanied by a
machine-readable `<slug>.sources.json` sidecar.

**Utility:**

- **`/kb:mode [on|off|status]`** — toggle ambient mode (§5.4).
- **`/kb:connect`** — detect/register the Brain MCP when `health` finds none;
  walks the user through `./brain mcp-config`.

**Permission posture:**
- Read-only interrogation skills **pre-approve** the Brain MCP tools via
  `allowed-tools` (both namespaces documented) so interrogation does not nag.
- Skills that **write files** do **not** auto-approve the write — the human stays in
  the loop on anything that leaves a trace.

### 5.2 Shared doctrine

One file, `skills/_shared/doctrine.md`, distilled from
`bundles/brain/BUILDING-AGENTS.md`. Every SKILL.md links to it; the ambient hook
loads it. It codifies:

- **Two lanes, one truth:** meaning from `search_knowledge`/`get_taxonomy` (never a
  figure); every number from `get_metric` with its `source_file`.
- **Citation tags (draft form):** each claim carries `[RAG:chunk_id]`,
  `[GRAPH:node]`, or `[MART:metric@grain]`; visual/table facts cite `get_evidence`.
- **Commit, cite, then qualify:** give the retrievable value and cite it;
  disambiguate scope/grain/population *after*; never refuse a figure that exists.
- **Gaps beat fabrication:** on `not_modeled`/empty, say so plainly.
- **Source precedence:** authoritative scorecard over lossy transcription; flag
  discrepancies rather than silently choosing.

### 5.3 The authoring pipeline & citation model

Used by `/kb:brief` and `/kb:report`: **gather → draft → verify → human gate →
emit.**

**Two representations of the same truth:**
- **Working draft:** inline machine tags — `[RAG:1423]`, `[MART:aht@branch/2025-06]`,
  `[GRAPH:ai-workforce]`. These are what the verifier parses and re-checks.
- **Emitted artifact:** tags render as human-readable references with a **Sources**
  section (numbered footnotes in Markdown), backed by a machine-readable
  `<slug>.sources.json` sidecar (chunk_ids, metric+grain, `source_file`) so any
  claim is re-traceable. Numbers always show `source_file`. Unmodeled areas render
  as an explicit *"Not modeled: …"* callout — never omitted silently.

### 5.4 The `verifier` subagent

`agents/verifier.md` — read-only (`tools:` Read, Grep + the Brain MCP tools only;
`model: sonnet`; `permissionMode: auto`). Given a draft answer or artifact it
independently:

1. Extracts every citation tag and every numeric claim.
2. Re-queries the Brain: does `chunk_id` exist and support the sentence? Does
   `get_metric` return that value at that grain?
3. Returns a structured verdict per claim: `verified | unsupported |
   grain-mismatch | uncited-number`.

**Gate policy:** the verifier is an automated *pre-check* whose verdict is surfaced
to the human; **the human is always the final gate.**
- Co-author skills **always** run the verifier before emit; a flagged claim blocks
  emit or is demoted to "not modeled" — subject to human sign-off on the write.
- Interrogation skills may invoke the verifier **on request**.

Why a subagent (not inline): independence — a fresh context re-derives instead of
rationalizing its own draft — and it keeps heavy re-query output off the main
thread.

### 5.5 Ambient mode

Off by default; explicit `/kb:*` skills always work regardless.

- **State:** `/kb:mode on|off|status` writes `.claude/kb/state.json` (project-scoped,
  gitignorable), e.g. `{ "ambient": true, "since": "<iso8601>" }`.
- **Hooks (`hooks/hooks.json`):**
  1. **`SessionStart`** (always fires) — runs `scripts/health-line.sh`: calls the
     Brain `health` tool **once per session** and prints a one-line status (brain
     reachable, lane counts, `knowledge_version`) or a quiet "no brain detected;
     run `/kb:connect`". If `state.json` says ambient is on, it also injects the
     doctrine summary.
  2. **`UserPromptSubmit`** (acts only when ambient is on) — runs
     `scripts/ambient-reminder.sh`: a **plain local `state.json` read** plus a
     few-line standing reminder ("route factual/numeric claims through the Brain
     MCP and cite; prefer 'not modeled' over a guess"). **No brain call, no LLM.**

**Performance invariant:** the hook layer makes **at most one** Brain round-trip per
session (the `health` call at SessionStart, whose payload is small). No hook calls
a Brain MCP tool on a per-tool-use or per-prompt cadence. Ambient mode is
effectively free when idle.

**Deliberately omitted from v1:** a `PreToolUse` matcher on `get_metric` that nags
for downstream citations — rejected because matching per tool call adds overhead
for little gain; the verifier gate already covers the real risk.

## 6. Repository layout & packaging

New plugin directory in this repo, registered as a second plugin in
`.claude-plugin/marketplace.json`.

```
kb/                                  # the new plugin root
├── .claude-plugin/plugin.json       # name: "kb"; declares no MCP server of its own
├── skills/
│   ├── ask/SKILL.md
│   ├── explore/SKILL.md
│   ├── challenge/SKILL.md
│   ├── brief/SKILL.md
│   ├── report/SKILL.md
│   ├── mode/SKILL.md
│   ├── connect/SKILL.md
│   └── _shared/doctrine.md          # distilled BUILDING-AGENTS.md
├── agents/verifier.md               # read-only truth-check subagent
├── hooks/
│   ├── hooks.json                   # SessionStart + UserPromptSubmit
│   └── scripts/{health-line.sh, ambient-reminder.sh}
└── README.md
```

- `marketplace.json` gains a second `plugins[]` entry: `name: "kb"`,
  `source: "./kb"`.
- `kb/.claude-plugin/plugin.json` declares **no** MCP server (it consumes the
  Brain's). README documents the Brain as a prerequisite.
- **Brain-namespace resolution:** support both install styles (`mcp__brain__*` and
  `mcp__plugin_<plugin>_brain__*`); the doctrine and `/kb:connect` document both,
  and skills detect the answering namespace via `health`.

## 7. Companion change considered and rejected: rename `applied-skills` → `brain`

An earlier draft of this spec proposed renaming the umbrella plugin
`applied-skills` → `brain` to line up its identity with the Brain it builds.
That rename was **considered and rejected**: the bundle at `bundles/brain/`
already owns the name "brain", and the umbrella plugin is a collection (it
ships the `brain` bundle *and* the new `kb` plugin), so a plugin named `brain`
would collide with — and be confused for — the bundle. The umbrella plugin
stays `applied-skills` in both `.claude-plugin/marketplace.json` and
`.claude-plugin/plugin.json`.

There is therefore **no manifest rename** as part of this work — the only
manifest change is adding the `kb` plugin as a second `marketplace.json`
entry (see §6).

## 8. Testing strategy

Reuses existing repo patterns (e.g. `mcp/brain/test_semantic_mcp.py`).

- **Hook scripts (unit, fast, no Brain):** `health-line.sh` and
  `ambient-reminder.sh` across state on/off and Brain present/absent.
- **Doctrine/citation contract:** the verifier's parser recognizes
  `[RAG:*]`/`[MART:*]`/`[GRAPH:*]` tags and flags an uncited number and a
  grain-mismatch.
- **Integration (optional, gated):** against a tiny fixture `knowledge.sqlite`, run
  `/kb:ask` end-to-end and assert every emitted claim carries a resolvable
  citation; reuse the Brain's existing fixtures where present.
- **Plugin validity:** `claude plugin` validation / eval so the manifest, skills,
  and hooks load cleanly; verify `/kb:*` skills appear and namespace correctly.

## 9. Risks & open questions

- **Namespace drift:** if the Brain's MCP tool names or namespace change, `kb`'s
  pre-approvals and doctrine references must track them. Mitigated by
  `health`-based detection and documenting both styles.
- **Verifier cost:** re-querying every claim adds latency to authoring. Acceptable
  because it is the truth guarantee and runs only on emit; can be scoped to
  numbers-only if it proves heavy.
- **Plugin naming** (§7) — rename considered and rejected; umbrella stays `applied-skills`.
- **Markdown output location** default (`docs/kb/`) vs. vault — confirmed
  configurable; confirm the default at review.

## 10. Milestones (for the implementation plan)

1. Scaffold `kb` plugin (manifest, marketplace entry, README).
2. Shared doctrine + `/kb:ask` (the core interrogation loop) + `verifier`.
3. `/kb:explore`, `/kb:challenge`.
4. Authoring pipeline + `/kb:brief`, `/kb:report` + sources sidecar.
5. Ambient mode: `/kb:mode`, `/kb:connect`, hooks + scripts.
6. Tests (hooks, citation contract, plugin validity; optional integration).
7. Companion: rename considered and rejected (§7) — `applied-skills` stays as the umbrella plugin.
