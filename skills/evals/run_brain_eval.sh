#!/usr/bin/env bash
# run_brain_eval.sh — start brain on PORT and run a promptfoo eval against it.
#
# Usage:
#   ./run_brain_eval.sh --db <path/to/knowledge.sqlite> \
#                       --config <path/to/promptfooconfig.yaml> \
#                       [--port 9100] \
#                       [--env <path/to/.env>]        # default: kt-docs/brain/.env
#                       [--max-concurrency 4]
#
# What it does:
#   1. Sources .env (default: ~/projects/kt-docs/brain/.env) for AWS creds
#   2. Starts the brain FastMCP server on --port
#   3. Runs: BRAIN_URL=http://localhost:<port> npx promptfoo@0.123.0 eval
#   4. Kills the brain server on exit
#
# The .env sourcing is required because AWS_SESSION_TOKEN must NOT be present
# (IAM user key, no session token) — sourcing .env sets only the needed vars.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BRAIN_PYTHON="$REPO_ROOT/.claude/venv/bin/python"
BRAIN_SCRIPT="$REPO_ROOT/.claude/mcp/brain/fastmcp_server.py"
BRAIN_SKILLS="$REPO_ROOT/.claude/skills"
DEFAULT_ENV="$HOME/projects/kt-docs/brain/.env"

# Defaults
PORT=9100
MAX_CONCURRENCY=4
ENV_FILE="$DEFAULT_ENV"
DB=""
CONFIG=""

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)             DB="$2";               shift 2 ;;
    --config)         CONFIG="$2";           shift 2 ;;
    --port)           PORT="$2";             shift 2 ;;
    --env)            ENV_FILE="$2";         shift 2 ;;
    --max-concurrency) MAX_CONCURRENCY="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

[[ -z "$DB" ]]     && { echo "ERROR: --db is required"; exit 1; }
[[ -z "$CONFIG" ]] && { echo "ERROR: --config is required"; exit 1; }
[[ ! -f "$DB" ]]   && { echo "ERROR: DB not found: $DB"; exit 1; }
[[ ! -f "$CONFIG" ]] && { echo "ERROR: config not found: $CONFIG"; exit 1; }

# Source AWS credentials
if [[ -f "$ENV_FILE" ]]; then
  echo "Sourcing credentials from $ENV_FILE"
  set -a && source "$ENV_FILE" && set +a
else
  echo "WARNING: .env not found at $ENV_FILE — using existing env vars"
fi

# Verify AWS creds
CALLER=$(aws sts get-caller-identity 2>&1) || true
if echo "$CALLER" | grep -q "UserId"; then
  echo "AWS identity: $(echo "$CALLER" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["Arn"])')"
else
  echo "WARNING: AWS credential check failed: $CALLER"
fi

# Kill any leftover brain
pkill -f "fastmcp_server" 2>/dev/null || true
sleep 1

# Start brain
BRAIN_LOG="/tmp/brain-port${PORT}.log"
echo "Starting brain on port $PORT with DB: $DB"
nohup env \
  AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}" \
  BRAIN_DB="$DB" \
  BRAIN_SKILLS="$BRAIN_SKILLS" \
  PORT="$PORT" \
  "$BRAIN_PYTHON" "$BRAIN_SCRIPT" --transport http \
  > "$BRAIN_LOG" 2>&1 &
BRAIN_PID=$!
echo "Brain PID: $BRAIN_PID (log: $BRAIN_LOG)"

# Wait for brain to be ready
for i in {1..15}; do
  sleep 1
  STATUS=$(curl -s "http://localhost:${PORT}/healthz" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || true)
  if [[ "$STATUS" == "ready" || "$STATUS" == "degraded" ]]; then
    echo "Brain ready (status=$STATUS)"
    break
  fi
  if [[ $i -eq 15 ]]; then
    echo "ERROR: Brain did not start in 15s. Log:"
    tail -20 "$BRAIN_LOG"
    kill $BRAIN_PID 2>/dev/null || true
    exit 1
  fi
done

# Ensure brain is killed on script exit
trap 'echo "Stopping brain..."; kill $BRAIN_PID 2>/dev/null || true' EXIT

# Run promptfoo eval
CONFIG_DIR="$(dirname "$CONFIG")"
cd "$CONFIG_DIR"

echo ""
echo "Running: npx promptfoo@0.123.0 eval --config $(basename $CONFIG)"
echo ""

BRAIN_URL="http://localhost:${PORT}" \
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}" \
npx promptfoo@0.123.0 eval \
  --config "$(basename $CONFIG)" \
  --no-cache \
  --max-concurrency "$MAX_CONCURRENCY"

echo ""
echo "Done. View results: promptfoo view"
