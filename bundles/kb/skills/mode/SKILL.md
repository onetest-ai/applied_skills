---
description: Use when the user asks to enable, disable, or check "ambient mode" for kb — turns ambient grounding mode on or off (or checks its status) for this project. When on, every prompt gets a short reminder to ground factual/numeric claims in the Brain via a UserPromptSubmit hook.
arguments: [action]
---

Manage ambient mode for **$action** (`on`, `off`, or `status`), stored project-scoped at `.claude/kb/state.json`.

1. **Resolve the state file.** It lives at `${CLAUDE_PROJECT_DIR:-.}/.claude/kb/state.json`. If the `.claude/kb/` directory does not exist yet, create it with `mkdir -p`.

2. **Act on $action:**
   - `on` → write `.claude/kb/state.json` as `{"ambient": true, "since": "<iso8601 timestamp>"}`, creating `.claude/kb/` first if needed. Confirm to the user that ambient mode is now **on**.
   - `off` → write `.claude/kb/state.json` as `{"ambient": false}`. Confirm ambient mode is now **off**.
   - `status` → read `.claude/kb/state.json` (if present) and report the current `ambient` value (on/off), or "off" (default) if the file is missing. Also mention that the SessionStart health line reflects the current Brain connection, independent of this toggle.

3. **Explain the effect.** Ambient mode is **project-scoped** (state lives under this project's `.claude/kb/`, not globally). Toggling `ambient` here does not retroactively change anything already said:
   - The `UserPromptSubmit` hook reads this file on the *next* prompt you submit — so `on`/`off` takes effect starting with your next message.
   - The `SessionStart` health line reflects Brain reachability at the *next* session start, not this setting.

4. **Cowork limitation.** Ambient mode relies on the `UserPromptSubmit` hook,
   and hooks do not fire in Claude Cowork (Desktop). If the user is in Cowork,
   say so plainly: ambient auto-grounding is a Claude Code (CLI) feature. In
   Cowork, ground each answer by invoking `/kb:ask` (and the other kb skills)
   explicitly — the toggle state still writes to `state.json`, but nothing
   reads it there.

5. **Report** the state file's final contents (or current value for `status`) back to the user in one line.
