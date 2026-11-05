"""PreToolUse hook for Bash in taska runs: allows exactly one `<wrapper> <subcommand> …` and refuses anything else with
one line saying which taska command or tool to use instead. It never allows what the allow-list refuses.

    python3 tool_guard.py <wrapper>      # reads the hook's JSON on stdin; exit 0 allows, exit 2 refuses
"""
import json
import re
import shlex
import sys

PUNCT = set("();<>|&")   # shlex keeps a run of these as one token: `&>`, `>|`, `<<<` … are all operators
RULE = "Only `{w} <subcommand>` runs in Bash, one per call. Files: Read/Write/Edit; search: Grep/Glob."
HINTS = [  # (pattern on the command, what to use instead); the first match wins
    (r"\bcontext\b.*([|>]|python)", "Use: {w} context --ids S001,T002 or --compact (no pipes)."),
    (r"\bleftovers\b.*[|>]", "Use: {w} leftovers --evidence E --part N."),
    (r"<<|\b(cat|echo|printf|tee)\b.*>", "Write files with the Write tool."),
    (r"taska\.py", "Use the wrapper: {w} <subcommand>."),
    (r"tool-results|\.claude/projects", "Saved output: Read it in parts (offset/limit), or re-run with the filter its `next` names."),
    (r"^\s*(wc|ls|cat|head|tail|grep|rg|find|sed|awk)\b", "Use Read (offset/limit), Grep or Glob; {w} pending gives meeting sizes."),
]


def _tokens(command: str) -> list[str]:
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return []


def guard(command: str, wrapper: str) -> str | None:
    """None when `command` is one call of `wrapper`, else the line that says what to use instead."""
    toks = _tokens(command)
    shell = any(t and set(t) <= PUNCT for t in toks) or "$(" in command or "`" in command or "\n" in command.strip()
    if toks[:1] == [wrapper] and len(toks) > 1 and not shell:
        return None
    for pat, msg in HINTS:
        if re.search(pat, command):
            return msg.format(w=wrapper)
    return ("Run one command, no shell operators. " if shell else "") + RULE.format(w=wrapper)


if __name__ == "__main__":
    msg = guard((json.load(sys.stdin).get("tool_input") or {}).get("command") or "", sys.argv[1])
    if msg:
        print(msg, file=sys.stderr)
        sys.exit(2)
