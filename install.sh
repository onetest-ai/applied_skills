#!/usr/bin/env bash
# applied_skills installer — installs the skills into Claude Code and/or DeepSeek
# Harness (dsh). Both hosts read the same SKILL.md format, so this is a copy (or
# symlink), no translation.
#
#   claude  : <root>/.claude/skills/<name>/   (user: ~/.claude/skills)
#   dsh     : <root>/.dsh/skills/<name>/       (user: ~/.dsh/skills)   [rank-100 project source]
#   copilot : <root>/.github/skills/<name>/    (user: ~/.copilot/skills)
#   codex   : <root>/.codex/skills/<name>/     (user: ~/.codex/skills)
#
# <root> is the current project (default) or $HOME with --user.
# (There is also an npx one-liner: npx github:onetest-ai/applied_skills init …)
#
# Usage:
#   ./install.sh [--target claude|dsh|copilot|codex|all] [--user] [--symlink]
#                [--dry-run] [--root <dir>] [--skills a,b,c]
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

# resolve one target -> its dest base, honoring project vs --user scope
dest_for() {
  case "$1" in
    claude)  [ -n "$SCOPE_HOME" ] && echo "$HOME/.claude/skills"  || echo "$ROOT/.claude/skills";;
    dsh)     [ -n "$SCOPE_HOME" ] && echo "$HOME/.dsh/skills"     || echo "$ROOT/.dsh/skills";;
    copilot) [ -n "$SCOPE_HOME" ] && echo "$HOME/.copilot/skills" || echo "$ROOT/.github/skills";;
    codex)   [ -n "$SCOPE_HOME" ] && echo "$HOME/.codex/skills"   || echo "$ROOT/.codex/skills";;
    *) echo "" ;;
  esac
}
case "$TARGET" in
  claude|dsh|copilot|codex) TARGETS="$TARGET";;
  all) TARGETS="claude dsh copilot codex";;
  *) echo "error: --target must be claude|dsh|copilot|codex|all" >&2; exit 2;;
esac

count=0
for tgt in $TARGETS; do
  dest_base="$(dest_for "$tgt")"
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
echo "done: $count skill install(s) ($MODE). Restart the host session to load the skills."
case " $TARGETS " in *" claude "*) echo "claude: or install the plugin — claude plugin marketplace add onetest-ai/applied_skills && claude plugin install applied-skills@onetest-ai";; esac
