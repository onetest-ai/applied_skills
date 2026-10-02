#!/usr/bin/env bash
# Full loop for ANY brain: baseline gate -> coverage -> (existing generator) promptfoo eval -> consistency diff.
# Brain-agnostic: all project specifics come from the config JSON (arg 1 or $EVALS_CONFIG).
#
# Every output (reports, generated config, answers, snapshots) is client data: it goes to
# $EVALS_OUT (default /tmp/brain_eval-<config-stem>), refused if it resolves inside the repo.
# The API key stays in the environment: it is withheld from the shared generator, which would
# otherwise copy it into every test's vars (and so into the config, results and promptfoo's DB).
#
# Env: EVALS_CSV (required — your gold set, kept out of git) · BRAIN_API_KEY (omit for a keyless
# local brain) · EVALS_OUT · EVALS_PY · REPEAT (default 3) · PATIENCE (default 25).
# Exit codes: 0 done (the gate verdict is reported, not fatal) · 2 refused / usage error · 3 a stage failed.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
EXIST="$REPO/bundles/brain/skills/evals"        # reused generator, unchanged

CONFIG="${1:-${EVALS_CONFIG:?set EVALS_CONFIG or pass a config path as arg 1}}"
EVALS_CSV="${EVALS_CSV:?set EVALS_CSV to your gold set CSV (project data, kept out of git)}"
PY="${EVALS_PY:-$REPO/.claude/venv/bin/python}"
REPEAT="${REPEAT:-3}"
PATIENCE="${PATIENCE:-25}"
[ -x "$PY" ] || { echo "python not found: $PY (run ./install.sh --bundle brain --deps)" >&2; exit 2; }
[ -f "$CONFIG" ] || { echo "config not found: $CONFIG" >&2; exit 2; }
[ -f "$EVALS_CSV" ] || { echo "gold set not found: $EVALS_CSV" >&2; exit 2; }

# shellcheck source-path=SCRIPTDIR source=out_dir.sh
. "$HERE/out_dir.sh"
OUT="$(evals_out_dir "$CONFIG")" || exit 2
export EVALS_CONFIG="$CONFIG" EVALS_PY="$PY"
trap 'echo "run_eval.sh: stage failed at line $LINENO" >&2; exit 3' ERR
RUN() { "$PY" "$HERE/$1" "${@:2}"; }

# Resolve the endpoint exactly as the python scripts do (config brain.url, else its url_env).
BRAIN_URL="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1])
from evals_config import load_config, resolve_brain
print(resolve_brain(load_config(sys.argv[2]))[0])' "$HERE" "$CONFIG")"
export BRAIN_MCP_URL="$BRAIN_URL"
VER="$(RUN brain_mcp_client.py call health --json '{}' --config "$CONFIG" \
        | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("knowledge_version","unknown"))')"
echo "brain version: $VER"
echo "output dir   : $OUT  (outside repo; do not commit)"

# 1. inventory + baseline scope gate (non-fatal here; records the finding)
RUN brain_inventory.py --config "$CONFIG" --out "$OUT/brain.json" --patience "$PATIENCE"
GATE_RC=0
RUN baseline_reconcile.py --config "$CONFIG" --brain-json "$OUT/brain.json" --brain-version "$VER" \
    --out "$OUT/baseline_report.md" >/dev/null || GATE_RC=$?
echo "baseline gate exit=$GATE_RC (0 = PASS, 1 = out-of-scope leakage, other = could not run)"

# 2. coverage report (the work queue)
METRICS_ARG=()
if RUN brain_mcp_client.py call list_metrics --json '{}' --config "$CONFIG" > "$OUT/metrics.json"; then
  METRICS_ARG=(--metrics-json "$OUT/metrics.json")
else
  rm -f "$OUT/metrics.json"
  echo "note: list_metrics unavailable — metric lens skipped" >&2
fi
RUN coverage_report.py --config "$CONFIG" --evals "$EVALS_CSV" --brain-json "$OUT/brain.json" \
    ${METRICS_ARG[@]+"${METRICS_ARG[@]}"} --brain-version "$VER" --out "$OUT/coverage_report.md" >/dev/null

# 3. generate config with the EXISTING generator — without the key — then eval with repeat
env -u BRAIN_API_KEY "$PY" "$EXIST/generate_promptfoo.py" --csv "$EVALS_CSV" --out "$OUT/promptfooconfig.yaml" \
    --brain-url "$BRAIN_URL" --context-js "$HERE/load_brain_context.js"
if [ -n "${BRAIN_API_KEY:-}" ] && grep -qF -- "$BRAIN_API_KEY" "$OUT/promptfooconfig.yaml"; then
  echo "REFUSING: the API key reached promptfooconfig.yaml — not running the eval" >&2
  rm -f "$OUT/promptfooconfig.yaml"
  exit 3
fi
# promptfoo exits 100 when some test fails: that is a result to report, not a crash.
PF_RC=0
( cd "$OUT" && npx promptfoo@0.123.0 eval --config promptfooconfig.yaml --no-cache \
    --repeat "$REPEAT" --max-concurrency 4 --output results.json ) || PF_RC=$?
if [ "$PF_RC" -ne 0 ] && [ "$PF_RC" -ne 100 ]; then
  echo "run_eval.sh: promptfoo failed (exit $PF_RC)" >&2
  exit 3
fi
"$PY" - "$OUT/results.json" "$VER" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d["knowledge_version"] = sys.argv[2]
json.dump(d, open(sys.argv[1], "w"))
PY

# 4. snapshot (one per run, never overwritten) + consistency diff vs the previous run
SNAPS="$OUT/snapshots"; mkdir -p "$SNAPS"
# Snapshot names are ours (no odd characters), so ls is safe here.
# shellcheck disable=SC2012
PREV="$(ls -1t "$SNAPS"/results_*.json 2>/dev/null | head -1 || true)"
SNAP="$SNAPS/results_$(printf '%s' "$VER" | tr -c 'A-Za-z0-9._-' '_')_$(date -u +%Y%m%dT%H%M%SZ)_$$.json"
cp "$OUT/results.json" "$SNAP"
if [ -n "$PREV" ]; then
  RUN consistency_diff.py --current "$SNAP" --baseline "$PREV" --out "$OUT/eval_report.md"
else
  RUN consistency_diff.py --current "$SNAP" --out "$OUT/eval_report.md"
fi
echo "reports in $OUT: baseline_report.md, coverage_report.md, eval_report.md (SENSITIVE — do not commit)"
