# Security and data handling

- The plugin ZIP contains instructions and skill definitions only — no credentials.
- `brain.config.json` and `.env` are gitignored and never packaged in the ZIP.
- The localhost bridge receives `mcpEndpoint` and `apiKeyEnvVar` from the plist
  (stamped by the installer) — it never reads `brain.config.json` at runtime.
- The bridge sends `X-Api-Key` only to the fixed HTTPS endpoint from config.
  Claude Desktop connects only to `http://127.0.0.1:<port>/mcp` and never
  receives the API key.
- The installer backs up the Gateway config before modifying it.
- The workflow is read-only. Seven tools, no write surface:
  `health`, `list_metrics`, `get_metric`, `search_knowledge`, `get_taxonomy`,
  `find_related_content`, `get_evidence`.
- Retrieved content is treated as untrusted data to reduce prompt-injection risk.
- A `not_modeled` result is an honest gap — stated plainly, never filled from priors.

Production owners must:
- Rotate the API key on a defined schedule.
- Use per-user credentials or central secret management in production.
- Review MCP logging and retention policies.
- Restrict egress to the declared brain endpoint.
- Register the connector in CodeMie's managed catalog for governed rollout.
