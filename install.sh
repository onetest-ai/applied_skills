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
#                [--dry-run] [--root <dir>] [--skills a,b,c] [--bundle <name> [--optional]]
#                [--deps [--venv <dir>]]
#
# Examples:
#   ./install.sh                       # all skills -> ./.claude/skills + ./.dsh/skills
#   ./install.sh --bundle brain        # just the 'brain' bundle's skills (from factory.json)
#   ./install.sh --bundle brain --deps # + a DEDICATED venv (uv) for the skills' Python deps
#   ./install.sh --target dsh --user   # -> ~/.dsh/skills
#   ./install.sh --symlink             # link instead of copy (dev: edits reflect live)
#
# --deps builds an ISOLATED venv holding ONLY the skills' dependencies, next to
# the skills inside the host dir (.claude/venv, .dsh/venv, …). It's a separate
# env from the project's own — never mixed. Scope follows the skills:
#   default        -> <project>/.claude/venv   (per-project)
#   with --user    -> ~/.claude/venv           (ONE shared env for all projects;
#                     use this when the ~1.3 GB deps are too big to copy per project)
# Override the exact path with --venv or BRAIN_VENV. Requires uv
# (https://astral.sh/uv). Needs --bundle.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="all"; SCOPE_HOME=""; MODE="copy"; DRYRUN=""; ROOT="$PWD"; ONLY=""; BUNDLE=""; OPTIONAL=""
DEPS=""; VENV=""; MCP=""

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2;;
    --user)   SCOPE_HOME=1; shift;;
    --symlink) MODE="symlink"; shift;;
    --dry-run) DRYRUN=1; shift;;
    --root)   ROOT="$2"; shift 2;;
    --skills) ONLY=",$2,"; shift 2;;
    --bundle|--factory) BUNDLE="$2"; shift 2;;
    --optional) OPTIONAL=1; shift;;
    --deps)   DEPS=1; shift;;
    --venv)   VENV="$2"; shift 2;;
    --mcp)    MCP=1; shift;;
    -h|--help) sed -n '2,32p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

# A bundle resolves an ordered skill list from bundles/<name>/factory.json.
if [ -n "$BUNDLE" ]; then
  MANIFEST="$HERE/bundles/$BUNDLE/factory.json"
  [ -f "$MANIFEST" ] || { echo "error: bundle '$BUNDLE' not found ($MANIFEST)"; exit 2; }
  LIST="$(python3 - "$MANIFEST" "${OPTIONAL:-0}" <<'PY'
import json, sys
f = json.load(open(sys.argv[1])); opt = sys.argv[2] == "1"
names = list(f.get("skills", [])) + (list(f.get("optionalSkills", [])) if opt else [])
print(",".join(names))
PY
)"
  echo "bundle: $BUNDLE -> $LIST"
  ONLY=",$LIST,"
fi

# Skills live inside their bundle: bundles/<name>/skills/ (default bundle: brain).
SRC="$HERE/bundles/${BUNDLE:-brain}/skills"
[ -d "$SRC" ] || { echo "error: bundle skills dir not found ($SRC) — run from a repo checkout"; exit 1; }
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
case " $TARGETS " in *" claude "*) echo "claude: or install the plugin — claude plugin marketplace add onetest-ai/applied_skills && claude plugin install brain@applied-ai";; esac

# --deps: an isolated venv for the skills' Python deps, PROJECT-LOCAL and living
# inside each host dir (<root>/.claude/venv, …) — never global, never the project's own env.
if [ -n "$DEPS" ]; then
  [ -n "$BUNDLE" ] || { echo "error: --deps needs --bundle <name> (to know which requirements)"; exit 2; }
  REQ="$HERE/bundles/$BUNDLE/requirements.txt"
  [ -f "$REQ" ] || { echo "error: no requirements.txt for bundle '$BUNDLE' ($REQ)"; exit 2; }
  command -v uv >/dev/null 2>&1 || { echo "error: uv not found — install it: https://astral.sh/uv"; exit 1; }
  # one venv per selected host, next to its skills/ (or a single --venv override)
  if [ -n "$VENV" ]; then VENVS="$VENV"; else
    VENVS=""; for tgt in $TARGETS; do VENVS="$VENVS $(dirname "$(dest_for "$tgt")")/venv"; done
  fi
  scope_label="per-project"; [ -n "$SCOPE_HOME" ] && scope_label="shared (--user)"
  for venv in $VENVS; do
    echo "→ deps venv (isolated, $scope_label): $venv"
    if [ -n "$DRYRUN" ]; then
      echo "   [dry-run] uv venv \"$venv\" && uv pip install --python \"$venv\" -r \"$REQ\""; continue
    fi
    uv venv --allow-existing "$venv"
    uv pip install --python "$venv" -r "$REQ"
    echo "   ✓ installed $(basename "$REQ") into $venv"
    if [ "$BUNDLE" = "brain" ]; then
      echo "   dependency report (nothing is installed by this step):"
      "$venv/bin/python" "$HERE/bundles/brain/skills/knowledge-pipeline/brain_doctor.py" | sed 's/^/   /' || true
    fi
    echo "   run brain scripts with:  \"$venv/bin/python\" <script>   (BRAIN_PY)"
  done
  [ -z "$DRYRUN" ] && echo "(zero-install alt, no venv: uv run --with-requirements \"$REQ\" python <script>)"
fi

# --mcp: install the bundle's MCP servers from mcp/<name>/ into <host>/mcp/<name>/,
# and register each for local stdio (command = the bundle venv python) so the agent
# calls tools instead of running Python itself. HTTP remains an explicit deployment mode.
if [ -n "$MCP" ]; then
  [ -n "$BUNDLE" ] || { echo "error: --mcp needs --bundle"; exit 2; }
  SERVERS="$(python3 -c "import json;print(' '.join(json.load(open('$HERE/bundles/$BUNDLE/factory.json')).get('mcp',{}).get('servers',[])))" 2>/dev/null)"
  [ -n "$SERVERS" ] || echo "  (bundle '$BUNDLE' declares no mcp servers)"
  for tgt in $TARGETS; do
    skills_dir="$(dest_for "$tgt")"
    host_dir="$(dirname "$skills_dir")"          # e.g. <root>/.claude or <root>/.dsh
    py="${VENV:-$host_dir/venv}/bin/python"
    # discover the store + assets: prefer inside the host dir, then the project root
    db=""; for c in "$host_dir/knowledge.sqlite" "$ROOT/knowledge.sqlite" "$ROOT/schema/knowledge.sqlite"; do [ -f "$c" ] && { db="$c"; break; }; done
    assets=""; for c in "$host_dir/assets" "$ROOT/assets"; do [ -d "$c" ] && { assets="$c"; break; }; done
    catalog=""; for c in "$ROOT"/schema/metrics.*.json "$host_dir"/schema/metrics.*.json; do [ -f "$c" ] && { catalog="$c"; break; }; done
    # config location: Claude Code reads <root>/.mcp.json; other hosts read <host>/mcp.json (registered from inside)
    if [ "$tgt" = "claude" ]; then conf="$ROOT/.mcp.json"; else conf="$host_dir/mcp.json"; fi
    for name in $SERVERS; do
      srcdir="$HERE/mcp/$name"
      [ -d "$srcdir" ] || { echo "  ! mcp/$name not found in repo"; continue; }
      entry="$(python3 -c "import json;print(json.load(open('$srcdir/server.json')).get('entry','server.py'))" 2>/dev/null)"
      dest="$host_dir/mcp/$name"
      echo "→ MCP '$name' -> $dest  (python: $py)  config: $conf"
      if [ -n "$DRYRUN" ]; then echo "   [dry-run] $MODE mcp/$name + write $conf"; continue; fi
      mkdir -p "$host_dir/mcp"; rm -rf "$dest"
      if [ "$MODE" = "symlink" ]; then ln -s "$srcdir" "$dest"; else cp -R "$srcdir" "$dest"; fi
      python3 - "$conf" "$name" "$py" "$dest/$entry" "$skills_dir" "$db" "$assets" "$catalog" <<'PY'
import json, os, sys
conf, name, py, server, skills, db, assets, catalog = sys.argv[1:9]
data = {}
if os.path.exists(conf):
    try: data = json.load(open(conf))
    except Exception: data = {}
env = {"BRAIN_SKILLS": skills}
if db: env["BRAIN_DB"] = db
if assets: env["BRAIN_ASSETS"] = assets
if catalog: env["BRAIN_CATALOG"] = catalog
data.setdefault("mcpServers", {})[name] = {"command": py, "args": [server, "--transport", "stdio"], "env": env}
json.dump(data, open(conf, "w"), indent=2)
print(f"   ✓ wrote {conf} (mcpServers.{name})")
PY
    done
  done
fi
