"""tool_guard: a refused shell call says which taska command or tool to use instead, so the agent's next call is the
right one (Nov 3: 26 of 292 calls were pipes, redirects, wc or greps that the allow-list refused with no hint).
Commands below have the shapes of those calls; paths are invented."""
import json
import subprocess
import sys

import pytest

import tool_guard
from conftest import RUN_DIR

W = "/p/taska"   # an eval copy holds only `taska` and `taska.toml`; production uses <project>/bin/taska


@pytest.mark.parametrize("cmd, says", [
    (f"{W} context | python3 -c 'import json,sys; print(1)'", f"{W} context --ids"),
    (f"{W} context > /tmp/ctx.json", f"{W} context --ids"),
    (f"{W} leftovers --evidence E1 > /tmp/x", "--part"),
    ("cat > /p/.taska/work/E1_p.json <<'EOF'\n[]\nEOF", "Write tool"),
    ("echo '[]' > /p/.taska/work/E1_p.json", "Write tool"),
    ("python3 /s/skills/run/taska.py --project /p pending", f"{W} <subcommand>"),
    ("grep -c owner /u/.claude/projects/x/tool-results/b1.txt", "Read it in parts"),
    ("wc -w /p/.taska/evidence/{a,b}.md", "pending"),
    ("ls /p/.taska/evidence", "Glob"),
    (f"cd /p && {W} pending", "one command"),
    (f"{W} pending; {W} context", "one command"),
    (f"{W} context --ids $(cat ids.txt)", "one command"),
    (f"{W} pending &>/p/.taska/events.jsonl", "one command"),
    (f"{W} pending &>>x", "one command"),
    (f"{W} pending >|x", "one command"),
    (f"{W} pending <>x", "one command"),
    (f"{W} pending <<<x", "Write tool"),
])
def test_a_refusal_says_what_to_use(cmd, says):
    msg = tool_guard.guard(cmd, W)
    assert msg and says in msg


@pytest.mark.parametrize("cmd", [
    f"{W} pending",
    f"{W} context --ids S001,T002",
    f'{W} done --evidence E1 --ack "costs 5 | 6; maybe > 7" --quote "a && b"',
    f"{W} done --evidence E1 --no-decisions 'we only talked about the dock plan and the yard'",
])
def test_one_taska_command_passes(cmd):
    assert tool_guard.guard(cmd, W) is None


def test_another_projects_wrapper_is_refused():
    assert tool_guard.guard("/other/taska pending", W)


def _hook(cmd):
    return subprocess.run([sys.executable, str(RUN_DIR / "tool_guard.py"), W],
                          input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                          capture_output=True, text=True)


def test_hook_refuses_with_exit_2_and_one_line():
    p = _hook("ls /p")
    assert p.returncode == 2 and len(p.stderr.strip().splitlines()) == 1


def test_hook_allows_with_exit_0():
    p = _hook(f"{W} pending")
    assert p.returncode == 0 and not p.stderr
