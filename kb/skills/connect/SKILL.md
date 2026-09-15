---
description: Detect whether a Brain MCP server is reachable, report its status, or walk the user through registering one via mcp-config. Use when the user asks to connect, reconnect, or check the Brain connection for kb.
allowed-tools: mcp__brain__health mcp__plugin_brain_brain__health Bash Read
---

Detect and (if needed) help the user register the Brain that `kb` grounds itself in.

1. **Detect the Brain.** Call `health` in each namespace kb knows about — `mcp__brain__health` and `mcp__plugin_brain_brain__health` — since the same server can be mounted under either name depending on how it was registered.

2. **If a Brain answers:** report which namespace responded and the lane counts / status it returns (chunks, facts, graph nodes, whatever `health` exposes). Tell the user kb is ready to use `/kb:ask`, `/kb:explore`, `/kb:challenge`, and ambient mode (`/kb:mode on`).

3. **If no Brain answers:** walk the user through registering one:
   - From the brain project, run `./brain mcp-config` to print the `mcpServers` JSON block for that store.
   - Add that block to this project's `.mcp.json` (merging with any existing `mcpServers` entries — don't clobber other servers).
   - Reload plugins / restart the session so Claude Code picks up the new MCP server.
   - Point to `bundles/brain/README.md` for the full setup walkthrough (building the store, `brain mcp-config` details, troubleshooting).

4. **Re-check.** After registration, call `health` again to confirm the Brain is now reachable, and report the result.
