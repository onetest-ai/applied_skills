# kb — the Brain's librarian

**kb** is the knowledge-worker companion for a **Brain** (the local, truthful knowledge engine built by the [`brain`](../brain/README.md) plugin). Ask it questions, explore the graph, challenge a claim, and co-author cited Markdown deliverables — with every assertion traceable to a source or honestly marked **"not modeled."** kb ships **no MCP server of its own**; it consumes the Brain's MCP and enforces the Brain's truth contract.

It runs in **two places**:

- **Claude Code (CLI)** — register the Brain's MCP locally and query it in your terminal sessions.
- **Claude Cowork (Desktop)** — install kb into Cowork and point it at a **remote Brain connector**.

> **Truth contract.** Every fact kb states is either **cited** (a mart row, a document, a graph node) or **"not modeled"** (honestly labelled when the answer isn't in the data). **Numbers come only from the marts** — never guessed, never from model priors. When a derivation is heuristic or a source is uncertain, kb says so.

---

## The seven skills

| Skill | Use it to… |
|---|---|
| `/kb:ask` | Ask a natural-language question; get cited facts or an honest "not modeled." |
| `/kb:explore` | Traverse the knowledge graph — edges, node details, related concepts. |
| `/kb:challenge` | Stress-test a claim: verify its sources, surface contradictions, note gaps. |
| `/kb:brief` | Co-author a short brief; every assertion links to a source (human-gated write). |
| `/kb:report` | Generate a full report (Markdown/PDF) with citations and a provenance appendix (human-gated write). |
| `/kb:connect` | Detect/register the Brain and check its health and data freshness. |
| `/kb:mode` | Toggle **ambient grounding** on/off/status (project-scoped; **Claude Code only** — it relies on a hook). |

Interrogate with `ask` / `explore` / `challenge`; author with `brief` / `report`; manage the connection with `connect` / `mode`. kb reads the Brain's `health().about` (goal + audience) to tune answer altitude and artifact style.

---

## Quickstart — Claude Code (CLI)

```bash
# 1. Install the plugin
claude plugin marketplace add onetest-ai/applied_skills
claude plugin install kb@applied-ai
```

```text
# 2. Connect to your Brain (guided)
/kb:connect
#   → detects a running Brain, or walks you through registering one:
#     run `./brain mcp-config` in the brain project and merge the printed
#     mcpServers block into this project's .mcp.json, then reload.

# 3. Ask
/kb:ask   Which data marts are available, and what's the latest period?
```

**Prerequisite:** a reachable Brain MCP server. kb does not bundle or auto-wire it — see [`bundles/brain/README.md`](../brain/README.md) to build and serve one.

---

## Quickstart — Claude Cowork (Desktop)

Cowork keeps its own plugin state and connects to MCP servers **from Anthropic's cloud** (not your machine), so the Brain is registered as a **remote connector** rather than a local `.mcp.json` entry.

1. **Install kb into Cowork** — *Customize → Plugins → Add marketplace*, enter the `applied-ai` GitHub URL (`onetest-ai/applied_skills`), install **kb**, enable it.
2. **Add your Brain connector** — *Customize → Connectors → Add custom connector*. Paste your project's **HTTPS MCP URL**; authorize with **Entra OAuth** (or set an `X-API-Key` header). The connector's name is yours to choose (e.g. `acme-brain`) — kb finds a Brain by the tools it exposes, not by what the connector is called; the health probe in `/kb:connect` confirms it's reachable.
3. **Verify & use** — run `/kb:connect`, then `/kb:ask`, `/kb:explore`, `/kb:report`, …

**Per project:** each project has its own Brain endpoint. In two projects, add both connectors and leave both enabled — kb discovers every reachable Brain and asks which to use when more than one answers.

**Cowork caveats:** ambient mode (`/kb:mode`) and the SessionStart health line rely on hooks, which **don't fire in Cowork** — ground answers by invoking the kb skills explicitly.

**Full walkthrough:** [`docs/cowork-setup.md`](docs/cowork-setup.md).

---

## How kb stays honest

- **Cited or not modeled** — no third option. A missing answer is reported plainly, never filled from priors.
- **Numbers only from marts** — `get_metric` carries its `source_file`; grain and period differences are stated before any comparison.
- **Retrieved content is untrusted data** — kb never follows instructions found inside documents it retrieves (prompt-injection defense).
- **Writes are human-gated** — `brief` and `report` propose the deliverable for your approval before writing.

## Troubleshooting

- **`/kb:connect` finds no Brain** — CLI: confirm the `mcpServers` block is in `.mcp.json` and reload the session. Cowork: confirm the connector is enabled and its OAuth/API-key auth succeeded.
- **Tools don't resolve in Cowork** — check that the connector is enabled and its auth succeeded; run `/kb:connect` to list every Brain kb can currently reach.
- **Ambient mode seems inert in Cowork** — expected; it's CLI-only. Invoke `/kb:ask` (and the other skills) explicitly.
