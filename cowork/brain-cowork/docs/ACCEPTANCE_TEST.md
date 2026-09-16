# Acceptance test

In a new Cowork session, invoke `/<brainName>` (the slash command matching
`brainName` in your `brain.config.json`) and ask:

> Call Brain health. List available metrics. Search approved sources for one
> known topic in this knowledge base. Cite source identifiers and tell me
> exactly which MCP tools you called. Do not use web search.

Pass criteria:

- Cowork displays Gateway and uses an approved CodeMie model.
- `health` succeeds; all seven Brain tools appear in the tool trace.
- `knowledge_version` is present in the `health` response.
- The answer cites only sources returned by the Brain.
- No credential appears in transcript, trace, screenshot, or output.
- A missing result is reported as `not_modeled` — never invented.
