---
description: Use when the user asks to connect, reconnect, check the Brain connection for kb, or see which Brains are reachable — detects every reachable Brain, reports it, or walks the user through registering one (a Cowork connector or CLI mcp-config).
arguments: [brain]
---

Report the Brains `kb` can ground itself in, and help register one if none is reachable.

1. **Discover.** Follow `../_shared/doctrine.md` → **Brain Discovery**: find every MCP
   server in your available tools carrying the Brain tool surface, and call `health` on
   each. If **$brain** is given, limit the report to the Brain it matches.

2. **Report every Brain that answers** — not just the first. For each, give its server
   name, its `about.goal` (and `about.name` when the Brain advertises one), and the lane
   counts / status from `health`. Then tell the user kb is ready for `/kb:ask`,
   `/kb:explore`, `/kb:challenge`, `/kb:brief`, `/kb:report`, and (CLI only) ambient mode.

   If several answered, say so plainly and note that the skills will ask which to use, or
   that the user can name one in the request ("ask the acme brain about X"). **Do not tell
   the user to rename or disable a connector — any name works.**

3. **If no Brain answers, register one.** The steps differ by surface.

   **In Claude Cowork (Desktop):**
   - Open **Customize → Connectors → Add custom connector**.
   - Paste the project's Brain **HTTPS MCP URL** (Streamable HTTP).
   - Authorize with **Entra OAuth** in Advanced settings (or set an `X-API-Key` header if
     the endpoint uses static keys).
   - Give it a name that identifies the project (`acme-brain`, `primo-brain`). The name is
     free — kb finds a Brain by its tools, not its name — so a meaningful one just makes
     the choice readable when several are connected.
   - Enable the connector, then re-run `/kb:connect`.

   **In Claude Code (CLI):**
   - From the brain project, run `./brain mcp-config` to print the `mcpServers` JSON block
     for that store.
   - Add that block to this project's `.mcp.json` (merge; don't clobber other servers).
     Reload plugins / restart the session.
   - See `bundles/brain/README.md` for the full walkthrough.

4. **Re-check.** After registration, discover again and report the result.

**On approvals.** kb never writes permission rules or settings files — that is the user's
own file to edit. Brain tool calls prompt for approval unless the user has allowed them.
If the prompts are unwelcome, *tell the user* they can silence them by adding the Brain's
server to `permissions.allow` in their own `.claude/settings.json`, e.g.
`"mcp__acme-brain"`, using the exact name they registered the connector under. Report this
as guidance; do not edit the file.
