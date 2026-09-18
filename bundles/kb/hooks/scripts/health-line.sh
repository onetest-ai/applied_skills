#!/bin/sh
# SessionStart hook: print one cheap status line about the local Brain store.
# Reads knowledge.sqlite directly (no MCP round-trip). Always exits 0.
set -u

find_db() {
  if [ -n "${BRAIN_DB:-}" ] && [ -f "${BRAIN_DB}" ]; then printf '%s\n' "$BRAIN_DB"; return 0; fi
  for d in \
    "${CLAUDE_PROJECT_DIR:-.}/schema" \
    "${CLAUDE_PROJECT_DIR:-.}/.claude" \
    "$(pwd)/schema" "$(pwd)/.claude" "$(pwd)"; do
    if [ -f "$d/knowledge.sqlite" ]; then printf '%s\n' "$d/knowledge.sqlite"; return 0; fi
  done
  return 1
}

DB="$(find_db)" || { echo "kb: no Brain detected — add a Brain MCP server (mcpServers entry in .mcp.json) to register one."; exit 0; }

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "kb: Brain store found ($DB) but sqlite3 is unavailable for a health line."
  exit 0
fi

CHUNKS=$(sqlite3 "$DB" "SELECT count(*) FROM chunks;" 2>/dev/null || echo "?")
FACTS=$(sqlite3 "$DB" "SELECT count(*) FROM facts;" 2>/dev/null || echo "?")
NODES=$(sqlite3 "$DB" "SELECT count(*) FROM graph_nodes;" 2>/dev/null || echo "?")
echo "kb: Brain ready — chunks=$CHUNKS facts=$FACTS graph_nodes=$NODES ($DB)"
exit 0
