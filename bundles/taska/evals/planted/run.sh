#!/usr/bin/env bash
# Planted suite end-to-end runner.
#
# Usage: run.sh [--out DIR] SEED...
#
# For each seed:
#   1. Create a scratch project in a temp directory.
#   2. Generate the VTT + gold.json via generate.py.
#   3. taska init, append [[people]], [[teams]], [sources.meetings] to taska.toml.
#   4. taska sync  (writes the evidence item).
#   5. Get the evidence ID from taska pending.
#   6. Patch gold.json with the evidence ID.
#   7. Run headless extraction via claude -p "..." --due mode.
#   8. Compute per-form rates via generate.per_form.
#
# Writes: $OUT/planted-<ts>.json  {"seeds": {seed: per_form_result}, "total": {form: {n,ok,rate}}}
#
# Requires: python3 (stdlib only for taska), claude on PATH for step 7.
# If claude is not on PATH, writes {"incomplete": true} for every seed and exits 0.
# Never touches any real project.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
PLANTED="$REPO/bundles/taska/evals/planted"
SKILL="$REPO/bundles/taska/skills/run"

# Delegate to generate.py's __main__ which handles the full workflow
exec python3 "$PLANTED/generate.py" "$@"
