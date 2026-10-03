# shellcheck shell=bash
# Sourced by baseline.sh and run_eval.sh: resolve the output dir and refuse one inside the repo.
#
# Every eval artifact (source lists, coverage, answers, retrieved context) is client data and
# must never be committable. The check runs on the resolved path BEFORE anything is created, so
# a refused run leaves nothing behind. Needs $REPO and $PY set by the caller.
evals_out_dir() {
  local out abs top
  out="${EVALS_OUT:-/tmp/brain_eval-$(basename "$1" .json)}"
  abs="$("$PY" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$out")"
  top="$("$PY" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$REPO")"
  case "$abs/" in
    "$top"/*)
      echo "REFUSING: EVALS_OUT ($abs) is inside the git repo — eval outputs are sensitive and" >&2
      echo "must not be committable. Set EVALS_OUT to a path outside $top (e.g. /tmp)." >&2
      return 2 ;;
  esac
  mkdir -p "$abs"
  echo "$abs"
}
