# kb — the Brain's librarian

**kb** is the knowledge-worker companion for a **Brain** (the local, truthful knowledge engine built by the [`brain`](../brain/README.md) plugin). Ask it questions, explore the graph, challenge a claim, and co-author cited Markdown deliverables — with every assertion traceable to a source or honestly marked **"not modeled."** kb ships **no MCP server of its own**; it consumes the Brain's MCP and enforces the Brain's truth contract.

It runs in **two places**:

- **Claude Code (CLI)** — register the Brain's MCP locally and query it in your terminal sessions.
- **Claude Cowork (Desktop)** — install kb into Cowork and point it at a **remote Brain connector**.

> **Truth contract.** Every fact kb states is either **cited** (a mart row, a document, a graph node) or **"not modeled"** (honestly labelled when the answer isn't in the data). **Numbers come only from the marts** — never guessed, never from model priors. When a derivation is heuristic or a source is uncertain, kb says so.

---

## The six skills

| Skill | Use it to… |
|---|---|
| `/kb:ask` | Ask a natural-language question; get cited facts or an honest "not modeled." |
| `/kb:explore` | Traverse the knowledge graph — edges, node details, related concepts. |
| `/kb:challenge` | Stress-test a claim: verify its sources, surface contradictions, note gaps. |
| `/kb:brief` | Co-author a short brief; every assertion links to a source (human-gated write). |
| `/kb:report` | Generate a full report (Markdown/PDF) with citations and a provenance appendix (human-gated write). |
| `/kb:mode` | Toggle **ambient grounding** on/off/status (project-scoped; **Claude Code only** — it relies on a hook). |

Interrogate with `ask` / `explore` / `challenge`; author with `brief` / `report`; toggle ambient grounding with `mode`. Registering a Brain is platform plumbing, not a skill — a custom connector in Cowork, or an `mcpServers` entry in `.mcp.json` for the CLI — and kb resolves it via `health` at answer time. kb reads the Brain's `health().about` (goal + audience) to tune answer altitude and artifact style.

---

## Quickstart — Claude Code (CLI)

```bash
# 1. Install the plugin
claude plugin marketplace add onetest-ai/applied_skills
claude plugin install kb@applied-ai
```

```text
# 2. Register your Brain's MCP server (platform plumbing, not a kb skill)
#    run `./brain mcp-config` in the brain project and merge the printed
#    mcpServers block into this project's .mcp.json, then reload.

# 3. Ask
/kb:ask   Which data marts are available, and what's the latest period?
#   → kb discovers the Brain by its tool surface and answers, naming which
#     Brain it used. Pin the Brain in this project's CLAUDE.md/AGENTS.md
#     once several are registered, so kb never has to ask:
#       This project's Brain is `acme-brain`.
```

**Prerequisite:** a reachable Brain MCP server. kb does not bundle or auto-wire it — see [`bundles/brain/README.md`](../brain/README.md) to build and serve one.

---

## Quickstart — Claude Cowork (Desktop)

Cowork keeps its own plugin state and connects to MCP servers **from Anthropic's cloud** (not your machine), so the Brain is registered as a **remote connector** rather than a local `.mcp.json` entry.

1. **Install kb into Cowork** — *Customize → Plugins → Add marketplace*, enter the `applied-ai` GitHub URL (`onetest-ai/applied_skills`), install **kb**, enable it.
2. **Add your Brain connector** — *Customize → Connectors → Add custom connector*. Paste your project's **HTTPS MCP URL**; authorize with **Entra OAuth** (or set an `X-API-Key` header). The connector's name is yours to choose (e.g. `acme-brain`) — kb finds a Brain by the tools it exposes, not by what the connector is called.
3. **Pin it in the project instructions** (optional but recommended once more than one Brain is registered) — add `This project's Brain is \`acme-brain\`.` to this project's instructions in Cowork. kb resolves the pin first, before discovery.
4. **Verify & use** — ask a simple question with `/kb:ask` and confirm it cites the Brain you expect, then use `/kb:explore`, `/kb:report`, …

**Per project:** each project has its own Brain endpoint. In two projects, add both connectors and leave both enabled — kb discovers every reachable Brain and asks which to use when more than one answers (or pin one per project as above).

**Cowork caveats:** ambient mode (`/kb:mode`) and the SessionStart health line rely on hooks, which **don't fire in Cowork** — ground answers by invoking the kb skills explicitly.

**Full walkthrough:** [`docs/cowork-setup.md`](docs/cowork-setup.md).

---

## How kb stays honest

- **Cited or not modeled** — no third option. A missing answer is reported plainly, never filled from priors.
- **Numbers only from marts** — `get_metric` carries its `source_file`; grain and period differences are stated before any comparison.
- **Retrieved content is untrusted data** — kb never follows instructions found inside documents it retrieves (prompt-injection defense).
- **Writes are human-gated** — `brief` and `report` propose the deliverable for your approval before writing.

**Approval prompts (Claude Code / CLI).** kb grants itself no tools, so Brain calls ask for
approval the first time. To stop the prompting, add your Brain's server to
`permissions.allow` in your own `.claude/settings.json`, e.g. `"mcp__acme-brain"` — use the
exact name you registered it under, since the server segment must be literal. kb never
edits that file.

## Troubleshooting

- **kb finds no Brain** — CLI: confirm the `mcpServers` block is in `.mcp.json` and reload the session. Cowork: confirm the connector is enabled and its OAuth/API-key auth succeeded.
- **kb answered from the wrong Brain, or doesn't see one you expect** — ask `/kb:ask` a question; kb names which Brain it used, or lists every Brain it can currently reach with its goal if more than one answered. If the one you want is missing, its connector is disabled or its auth failed. If it is listed but kb chose another, name it in the request ("ask the acme brain about …") or pin it in the project instructions. A server that exposes only part of the Brain tool surface is not recognised as a Brain.
- **A pinned Brain isn't reachable** — kb stops rather than silently falling back to another store; it lists the Brains that ARE reachable and gives you the corrected pin line to paste.
- **Ambient mode seems inert in Cowork** — expected; it's CLI-only. Invoke `/kb:ask` (and the other skills) explicitly.
