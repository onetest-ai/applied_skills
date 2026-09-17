# kb: Knowledge-Base Companion

**kb** is a Claude Code plugin that lets you cowork with the Brain's knowledge base. Interrogate structured facts and cited sources, co-author Markdown deliverables with claims traceable to data or honestly marked "not modeled," and explore what's actually in your marts and marts-of-marts.

## Prerequisites

kb requires a reachable Brain MCP server providing fact queries, graph traversal, and data access. The `kb` plugin does **not** bundle or auto-wire the Brain MCP server — it must be registered separately (run `./brain mcp-config` from the brain project, or use `/kb:connect` for guided setup). See [bundles/brain/README.md](../bundles/brain/README.md) for setup.

**Using kb in Cowork (Claude Desktop):** see [`docs/cowork-setup.md`](docs/cowork-setup.md).

## Skills

- `/kb:ask` — Query the knowledge base with a natural-language question; get cited facts or "not modeled."
- `/kb:explore` — Explore the knowledge graph: traverse edges, inspect node details, find related concepts.
- `/kb:challenge` — Challenge a claim: verify its sources, check for contradictions, note data gaps.
- `/kb:brief` — Co-author a brief or summary: every assertion links to a source or is marked "not modeled."
- `/kb:report` — Generate a report (PDF or Markdown) with full citations and a data provenance appendix.
- `/kb:mode` — Turn ambient grounding mode on/off or check its status (project-scoped; CLI-only, since it relies on a hook).
- `/kb:connect` — Connect or reconnect to the Brain MCP; check its health and data freshness.

## Truth Contract

Every fact in kb-authored content is either:
- **Cited**: linked to a source in the Brain (a table row, a document, a node in the graph).
- **Not modeled**: honestly labeled when the answer isn't in the data—no guessing, no confabulation.

Numbers come from marts; ambiguities are noted. When a source is uncertain or a derivation is heuristic, we say so.

## Installation

```bash
claude plugin marketplace add onetest-ai/applied_skills
claude plugin install kb@onetest-ai
```

Then connect to your Brain:

```
/kb:connect
```

Verify the connection:

```
/kb:ask What data marts are available?
```
