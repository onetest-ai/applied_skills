#!/usr/bin/env bash
# applied_skills installer — installs the skills into Claude Code and/or DeepSeek
# Harness (dsh). Both hosts read the same SKILL.md format, so this is a copy (or
# symlink), no translation.
#
#   Claude Code : <root>/.claude/skills/<name>/
#   dsh         : <root>/.dsh/skills/<name>/    (dsh rank-100 project source)
#
# <root> is the current project (default) or $HOME with --user.
#
# Usage:
#   ./install.sh [--target claude|dsh|all] [--user] [--symlink] [--dry-run]
#                [--root <dir>] [--skills a,b,c]
#
# Examples:
#   ./install.sh                       # all skills -> ./.claude/skills + ./.dsh/skills
#   ./install.sh --target dsh --user   # -> ~/.dsh/skills
#   ./install.sh --symlink             # link instead of copy (dev: edits reflect live)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills"
TARGET="all"; SCOPE_HOME=""; MODE="copy"; DRYRUN=""; ROOT="$PWD"; ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2;;
    --user)   SCOPE_HOME=1; shift;;
    --symlink) MODE="symlink"; shift;;
    --dry-run) DRYRUN=1; shift;;
    --root)   ROOT="$2"; shift 2;;
    --skills) ONLY=",$2,"; shift 2;;
    -h|--help) sed -n '2,25p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[ -d "$SRC" ] || { echo "error: skills/ not found next to install.sh (run from a repo checkout)"; exit 1; }
[ -n "$SCOPE_HOME" ] && ROOT="$HOME"

case "$TARGET" in
  claude) DIRS=".claude/skills";;
  dsh)    DIRS=".dsh/skills";;
  all)    DIRS=".claude/skills .dsh/skills";;
  *) echo "error: --target must be claude|dsh|all" >&2; exit 2;;
esac

count=0
for rel in $DIRS; do
  dest_base="$ROOT/$rel"
  echo "→ $dest_base"
  for skill in "$SRC"/*/; do
    name="$(basename "$skill")"
    [ -n "$ONLY" ] && case "$ONLY" in *",$name,"*) ;; *) continue;; esac
    dest="$dest_base/$name"
    if [ -n "$DRYRUN" ]; then echo "   [dry-run] $MODE $name"; continue; fi
    mkdir -p "$dest_base"; rm -rf "$dest"
    if [ "$MODE" = "symlink" ]; then ln -s "${skill%/}" "$dest"
    else cp -R "${skill%/}" "$dest"; fi
    echo "   ✓ $name"; count=$((count+1))
  done
done
echo "done: $count skill install(s) ($MODE)."
[ "$TARGET" != claude ] && echo "dsh: skills are discovered from .dsh/skills (rank 100) automatically; restart the dsh session to load."
[ "$TARGET" != dsh ] && echo "claude: project skills load from .claude/skills; or install the plugin: claude plugin marketplace add onetest-ai/applied_skills && claude plugin install applied-skills@onetest-ai"
