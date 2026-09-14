#!/usr/bin/env bash
# E2E pipeline: VTT corpus → parsed MD → knowledge.sqlite → taxonomy → brain → evals
# Usage: bash skills/evals/run_e2e.sh \
#   --corpus   /path/to/vtt/dir \
#   --work     /tmp/primo_e2e \
#   --brain-port 8003 \
#   --taxonomy /path/to/taxonomy.json \
#   [--extractions /path/to/extraction_jsons]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VENV="$REPO/.claude/venv/bin/python3"
SKILL_TAXO="$REPO/skills/corpus-taxonomy-extraction"
SKILL_KI="$REPO/skills/knowledge-index"
SKILL_EVALS="$REPO/skills/evals"
MCP_BRAIN="$REPO/mcp/brain"

CORPUS=""; WORK=""; PORT=8003; TAXONOMY=""; EXTRACTIONS=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --corpus)      CORPUS="$2";      shift 2 ;;
    --work)        WORK="$2";        shift 2 ;;
    --brain-port)  PORT="$2";        shift 2 ;;
    --taxonomy)    TAXONOMY="$2";    shift 2 ;;
    --extractions) EXTRACTIONS="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

[[ -z "$CORPUS" ]]   && { echo "ERROR: --corpus required"; exit 1; }
[[ -z "$WORK" ]]     && { echo "ERROR: --work required"; exit 1; }
[[ -z "$TAXONOMY" ]] && { echo "ERROR: --taxonomy required"; exit 1; }

DB="$WORK/knowledge.sqlite"
PARSED="$WORK/parsed"
CLASSIFY_DIR="$WORK/classify"
EVAL_CSV="$WORK/evals.csv"
EVAL_YAML="$WORK/promptfooconfig.yaml"
EVAL_RESULTS="$WORK/results.json"
JS="$SKILL_EVALS/load_brain_context.js"

mkdir -p "$PARSED" "$CLASSIFY_DIR"

echo "=== Stage 1: Parse VTT corpus ==="
t0=$SECONDS
"$VENV" "$SKILL_TAXO/parse_corpus.py" \
  --corpus "$CORPUS" \
  --out    "$PARSED" \
  --formats vtt,srt
echo "  done in $((SECONDS - t0))s — $(ls "$PARSED"/*.md 2>/dev/null | wc -l) markdown files"

echo "=== Stage 2: Index into knowledge.sqlite ==="
t0=$SECONDS
"$VENV" "$SKILL_KI/knowledge_index.py" index \
  --db     "$DB" \
  --corpus "$PARSED" \
  --reset
echo "  done in $((SECONDS - t0))s"

echo "=== Stage 3: Taxonomy — classify_prep ==="
t0=$SECONDS
"$VENV" "$SKILL_TAXO/classify_prep.py" \
  --db       "$DB" \
  --taxonomy "$TAXONOMY" \
  --out      "$CLASSIFY_DIR"
echo "  done in $((SECONDS - t0))s — $(ls "$CLASSIFY_DIR"/batch_*.json 2>/dev/null | wc -l) batches"

echo "=== Stage 3b: Taxonomy — classify agents (run manually or via Claude) ==="
echo "  Batches at: $CLASSIFY_DIR"
echo "  Run each batch agent then continue with Stage 3c."
echo "  Expected output: $CLASSIFY_DIR/result_*.json"
echo ""
echo "  Press ENTER when result_*.json files are ready, or Ctrl-C to stop."
read -r

echo "=== Stage 3c: Taxonomy — classify_write + build_graph ==="
t0=$SECONDS
"$VENV" "$SKILL_TAXO/classify_write.py" \
  --db      "$DB" \
  --results "$CLASSIFY_DIR"
"$VENV" "$SKILL_TAXO/build_graph.py" \
  --taxonomy "$TAXONOMY" \
  --db       "$DB"
echo "  done in $((SECONDS - t0))s"

echo "=== Stage 4: Start brain server ==="
BRAIN_PID=""
cleanup() { [[ -n "$BRAIN_PID" ]] && kill "$BRAIN_PID" 2>/dev/null || true; }
trap cleanup EXIT

BRAIN_DB="$DB" PORT="$PORT" \
  "$VENV" "$MCP_BRAIN/fastmcp_server.py" --transport http &
BRAIN_PID=$!

# Wait for health
for i in $(seq 1 20); do
  if curl -sf "http://localhost:$PORT/healthz" > /dev/null 2>&1; then break; fi
  sleep 1
done
curl -sf "http://localhost:$PORT/healthz" > /dev/null || { echo "Brain failed to start"; exit 1; }
echo "  brain running at http://localhost:$PORT (PID $BRAIN_PID)"

echo "=== Stage 5: Generate evals ==="
EXT_DIR="${EXTRACTIONS:-$PARSED}"
"$VENV" "$SKILL_EVALS/generate_evals.py" \
  --extractions "$EXT_DIR" \
  --out         "$EVAL_CSV"

BRAIN_URL="http://localhost:$PORT" \
"$VENV" "$SKILL_EVALS/generate_promptfoo.py" \
  --csv        "$EVAL_CSV" \
  --out        "$EVAL_YAML" \
  --brain-url  "http://localhost:$PORT" \
  --context-js "$JS"

echo "=== Stage 6: Run evals ==="
t0=$SECONDS
(cd "$(dirname "$EVAL_YAML")" && \
  AWS_REGION=us-east-1 \
  npx promptfoo@0.123.0 eval \
    --config "$(basename "$EVAL_YAML")" \
    --no-cache \
    --max-concurrency 3 \
    --output "$EVAL_RESULTS"
)
echo "  done in $((SECONDS - t0))s"

echo ""
echo "=== Results ==="
if [[ -f "$EVAL_RESULTS" ]]; then
  python3 -c "
import json
d = json.load(open('$EVAL_RESULTS'))
r = d['results']['results']
total = len(r); passed = sum(1 for x in r if x.get('success'))
print(f'PASS: {passed}/{total} ({round(100*passed/total)}%)')
"
fi
echo "Full results: $EVAL_RESULTS"
