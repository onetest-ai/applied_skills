# Team onboarding

Prerequisites: Claude Desktop with Cowork, EPAM CodeMie access, an approved
CodeMie profile, network access to your Brain MCP endpoint, and an authorized
API key for that endpoint.

1. Install the supported CodeMie CLI and authenticate with EPAM SSO.
2. Run `codemie proxy connect --claude-desktop` against your CodeMie instance.
3. Clone `https://github.com/onetest-ai/applied_skills` (or pull latest `main`).
4. Copy `cowork/brain-cowork/brain.config.example.json` to
   `cowork/brain-cowork/brain.config.json` and fill in your values:
   - `brainName` — kebab-case identifier for your Brain (e.g. `acme-brain`)
   - `displayName` — human-readable name (e.g. `Acme Brain`)
   - `mcpEndpoint` — the HTTPS MCP URL of your Brain
   - `apiKeyEnvVar` — name of the env var holding your key (e.g. `ACME_BRAIN_API_KEY`)
   - `codemie.gatewayUrl` — your CodeMie instance URL
5. Create `cowork/brain-cowork/.env` containing `<apiKeyEnvVar>=<your-key>`.
6. From `cowork/brain-cowork/`, run `node scripts/install.mjs`.
   The installer verifies the bridge and restarts Claude Desktop automatically.
7. Upload `dist/<brainName>-1.0.0.zip` in **Customize → Plugins → Add** and enable it.
8. Run the acceptance test in `docs/ACCEPTANCE_TEST.md`.

Never commit `.env` or `brain.config.json`. Never send a key with the ZIP.
Owners should maintain credential rotation, a support channel, and offboarding
that revokes both Brain and CodeMie access.
