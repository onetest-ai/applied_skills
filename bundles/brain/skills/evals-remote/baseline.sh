#!/usr/bin/env bash
#
# baseline.sh — trigger a Brain BASELINE (scope gate + coverage). No LLM/Bedrock needed.
#
# SENSITIVE-DATA POLICY: every artifact this produces (source lists, coverage, corpus paths)
# is client data and MUST NOT be committed. Outputs go to $EVALS_OUT, which defaults OUTSIDE the
# repo (/tmp) and is HARD-REFUSED if it resolves inside the git working tree. The API key comes
# from the environment and is never written to disk. The per-brain config (endpoint, corpus,
# policy) also stays out of the repo — pass its path via EVALS_CONFIG.
#
# Usage:
#   EVALS_CONFIG=/path/evals.config.json BRAIN_API_KEY=… \
#     bash bundles/brain/skills/evals-remote/baseline.sh
#
# Optional env:
#   BRAIN_API_KEY  key for a remote brain (env name per config brain.key_env); omit for a local keyless one
#   EVALS_OUT      output dir (default /tmp/brain_eval-<config-stem>)   — must be outside the repo
#   EVALS_CSV      gold set CSV (default evals.example.csv in the skill dir)
#   EVALS_PY       python with fastmcp (default <repo>/.claude/venv/bin/python)
#   PATIENCE       superseded-source sweep early-stop threshold (default 25)
#
# Exit codes: 0 gate PASS · 1 gate FAIL · 2 refused / usage error · 3 a stage failed (no verdict).
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"

: "${EVALS_CONFIG:?set EVALS_CONFIG to your per-brain config JSON (kept out of git)}"
PY="${EVALS_PY:-$REPO/.claude/venv/bin/python}"
CSV="${EVALS_CSV:-$HERE/evals.example.csv}"
PATIENCE="${PATIENCE:-25}"

[ -x "$PY" ] || { echo "python not found: $PY (run ./install.sh --bundle brain --deps)" >&2; exit 2; }
[ -f "$EVALS_CONFIG" ] || { echo "config not found: $EVALS_CONFIG" >&2; exit 2; }

# shellcheck source-path=SCRIPTDIR source=out_dir.sh
. "$HERE/out_dir.sh"
abs_out="$(evals_out_dir "$EVALS_CONFIG")" || exit 2
# A report left by an earlier run must never be read as this run's verdict.
rm -f "$abs_out"/brain.json "$abs_out"/metrics.json "$abs_out"/baseline_report.* "$abs_out"/coverage_report.*
export EVALS_CONFIG
trap 'echo "baseline.sh: stage failed at line $LINENO — no gate verdict" >&2; exit 3' ERR
RUN() { "$PY" "$HERE/$1" "${@:2}"; }

echo "== Brain baseline =="
VER="$(RUN brain_mcp_client.py call health --json '{}' --config "$EVALS_CONFIG" \
        | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("knowledge_version","unknown"))')"
echo "brain version : $VER"
echo "config        : $EVALS_CONFIG"
echo "output dir    : $abs_out  (outside repo; do not commit)"
echo

# 1. The brain's sources: the list_sources catalog plus superseded ones (latest_only=false sweep).
RUN brain_inventory.py --config "$EVALS_CONFIG" --out "$abs_out/brain.json" --patience "$PATIENCE"

# 2. Metric catalog (for coverage's numeric lens). A brain without marts has none: skip the lens.
METRICS_ARG=()
if RUN brain_mcp_client.py call list_metrics --json '{}' --config "$EVALS_CONFIG" > "$abs_out/metrics.json"; then
  METRICS_ARG=(--metrics-json "$abs_out/metrics.json")
else
  rm -f "$abs_out/metrics.json"
  echo "note: list_metrics unavailable — metric lens skipped" >&2
fi

# 3. Scope gate — exit 1 means out-of-scope / prior-engagement sources are in the brain. Any other
#    failure (no inventory, a crash) is a stage error, never a verdict.
GATE_RC=0
RUN baseline_reconcile.py --config "$EVALS_CONFIG" --brain-json "$abs_out/brain.json" \
    --brain-version "$VER" --out "$abs_out/baseline_report.md" >/dev/null || GATE_RC=$?
if [ "$GATE_RC" -gt 1 ] || [ ! -f "$abs_out/baseline_report.json" ]; then
  echo "baseline.sh: baseline_reconcile failed (exit $GATE_RC) — no gate verdict" >&2
  exit 3
fi

# 4. Coverage report (authoritative narrative-corpus + metric lenses; the untested work queue).
RUN coverage_report.py --config "$EVALS_CONFIG" --evals "$CSV" --brain-json "$abs_out/brain.json" \
    ${METRICS_ARG[@]+"${METRICS_ARG[@]}"} --brain-version "$VER" --out "$abs_out/coverage_report.md" >/dev/null

echo
echo "== Summary =="
"$PY" - "$abs_out/baseline_report.json" "$abs_out/coverage_report.json" "$abs_out/brain.json" <<'PY'
import json, sys
base = json.load(open(sys.argv[1])); cov = json.load(open(sys.argv[2])); brain = json.load(open(sys.argv[3]))
print(f"  scope gate        : {base['gate']}  (FAIL = remove out-of-scope sources)")
print(f"  fail / review     : {base['counts']['fail']} fail, {base['counts']['review']} review")
print(f"  brain sources     : {len(brain['sources'])} (catalog: {brain.get('catalog', 'sweep')}, "
      f"{brain.get('superseded_found', 0)} superseded)")
if brain.get("superseded_sweep") == "not_run":
    print("  WARNING           : superseded documents were NOT checked (no sweep ran)")
cc = cov.get("narrative_corpus_coverage") or {}
if cc:
    print(f"  narrative coverage: {cc['tested']}/{cc['corpus_total']} ({cc['pct']}%)  "
          f"[{cc['present_untested']} present-untested, {cc['not_retrievable']} not-retrieved]")
mc = cov.get("metric_coverage") or {}
if mc:
    print(f"  metric coverage   : {len(mc['tested'])}/{len(mc['tested'])+len(mc['untested'])} ({mc['pct']}%)")
PY
echo
echo "reports (SENSITIVE — do not commit):"
echo "  $abs_out/baseline_report.md"
echo "  $abs_out/coverage_report.md"
echo
echo "gate exit code: $GATE_RC (0=PASS, 1=FAIL)"
exit "$GATE_RC"
