#!/bin/sh
# UserPromptSubmit hook: when ambient mode is on, append a short grounding
# reminder. Plain local file read only — no MCP call, no LLM. Always exits 0.
set -u

STATE="${CLAUDE_PROJECT_DIR:-.}/.claude/kb/state.json"
[ -f "$STATE" ] || exit 0

# grep-based check to avoid a jq dependency
if grep -Eq '"ambient"[[:space:]]*:[[:space:]]*true' "$STATE"; then
  cat <<'EOF'
kb ambient mode: ground factual and numeric claims in the Brain (search_knowledge / get_metric / get_taxonomy) and cite them; prefer "not modeled" over a guess. Numbers come only from get_metric with their source_file.
EOF
fi
exit 0
