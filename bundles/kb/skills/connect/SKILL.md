---
description: Use when the user asks to connect, reconnect, or check the Brain connection for kb — detects whether a Brain MCP server is reachable, reports its status, or walks the user through registering one (a Cowork connector or CLI mcp-config).
---

Detect and (if needed) help the user register the Brain that `kb` grounds itself in.

1. **Detect the Brain.** Call `health` in each namespace kb knows about — `mcp__brain__health` and `mcp__plugin_brain_brain__health` — since the same server can be mounted under either name depending on how it was registered.

2. **If a Brain answers:** report which namespace responded and the lane
   counts / status `health` returns. Tell the user kb is ready to use
   `/kb:ask`, `/kb:explore`, `/kb:challenge`, and (CLI only) ambient mode.

3. **If no Brain answers, register one — the steps differ by surface.**
   First rule out a **misnamed connector**: kb can only call a Brain mounted as
   `mcp__brain__*` or `mcp__plugin_brain_brain__*` — the server-name segment must be
   literal (Claude Code allow-rules don't support a wildcard there), so a connector
   registered under any other name (e.g. `knowledge`, `primo-brain`) is unreachable to
   every kb skill even though it's healthy. If you see such a connector, **rename it to
   `brain`** rather than adding a new one, then re-run `/kb:connect`. Otherwise register
   one:

   **In Claude Cowork (Desktop):**
   - Open **Customize → Connectors → Add custom connector**.
   - Paste this project's Brain **HTTPS MCP URL** (Streamable HTTP).
   - Authorize with **Entra OAuth** in Advanced settings (or set an
     `X-API-Key` header if your endpoint uses static keys).
   - **Name the connector `brain`** so its tools should resolve as
     `mcp__brain__*`, which is what kb's skills expect; `/kb:connect`'s health
     probe will confirm the resolution (or reveal a mismatch). If you belong
     to two projects, keep each project's connector added, but two
     connectors cannot both be named `brain` at once — disable the other
     project's Brain connector and enable this project's (named `brain`) so
     exactly one `brain` connector is active for this task.
   - Enable the connector, then re-run `/kb:connect`.

   **In Claude Code (CLI):**
   - From the brain project, run `./brain mcp-config` to print the
     `mcpServers` JSON block for that store.
   - Add that block to this project's `.mcp.json` (merge; don't clobber other
     servers). Reload plugins / restart the session.
   - See `bundles/brain/README.md` for the full walkthrough.

4. **Re-check.** After registration, call `health` again to confirm the Brain
   is reachable, and report the result.
