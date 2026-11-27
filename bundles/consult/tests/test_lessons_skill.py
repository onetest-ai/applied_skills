from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from conftest import LESSONS_DIR

SKILL: Path = LESSONS_DIR / "SKILL.md"
SCRIPT_CALL = re.compile(r"python3 \$LESSONS/(\w+)\.py(?: (check|add|approve))?([^\n]*)")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")


def _body() -> str:
    return SKILL.read_text(encoding="utf-8")


def _flags_by_command(text: str) -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for script, sub, rest in SCRIPT_CALL.findall(text):
        found.setdefault((script, sub), set()).update(FLAG.findall(rest))
    return found


def _help(script: str, sub: str) -> str:
    cmd = [sys.executable, str(LESSONS_DIR / f"{script}.py")]
    if sub:
        cmd.append(sub)
    cmd.append("--help")
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _missing_flags(text: str) -> list[str]:
    missing: list[str] = []
    for (script, sub), flags in _flags_by_command(text).items():
        known = set(FLAG.findall(_help(script, sub)))
        missing += [f"{script} {sub} {f}".replace("  ", " ") for f in flags if f not in known]
    return missing


def test_skill_names_every_cli_and_the_private_inputs() -> None:
    body = _body()
    for command in ("mine_sessions.py", "register.py check", "register.py add", "register.py approve", "build_lessons.py"):
        assert command in body, command
    assert "private" in body
    assert "terms" in body
    assert "../" not in body


def test_every_documented_flag_exists_in_cli_help() -> None:
    body = _body()
    assert len(_flags_by_command(body)) == 5
    assert _missing_flags(body) == []


def test_flag_check_bites_on_a_wrong_flag() -> None:
    mutated = _body().replace("--only-cued", "--only-cue", 1).replace("--proposals", "--proposal", 1)
    assert len(_missing_flags(mutated)) >= 2


def test_skill_forbids_agent_approval() -> None:
    body = _body()
    marker = "wait for the user to name the ids to approve; never choose them"
    assert marker in body
    assert "never approve on your own" in body
    assert body.index(marker) < body.index("register.py approve")
    assert body.count("python3 $LESSONS/register.py approve") == 1


def test_add_runs_before_check() -> None:
    body = _body()
    assert body.index("register.py add") < body.index("register.py check")


def test_proposal_format_line_present() -> None:
    body = _body()
    assert "^[A-Z][A-Z0-9-]{2,40}$" in body
    assert "`version` is `1`" in body
    assert "`see_also` is `null`" in body
    assert "mkdir -p <project>/lessons" in body
    assert "rev-parse --is-inside-work-tree" in body


def test_step_one_asks_for_project_and_refuses_a_repo_inside_the_bundle() -> None:
    body = _body()
    guard = 'git -C "<project>" rev-parse --show-toplevel 2>/dev/null'
    assert guard in body
    assert 'git -C "$BUNDLE" rev-parse --show-toplevel' in body
    assert "private project folder" in body
    assert "raw session text" in body
    assert body.index("private project folder") < body.index(guard) < body.index("python3 $LESSONS/mine_sessions.py")


def test_step_six_has_a_command_for_missing_enforcement() -> None:
    body = _body()
    start = body.index("6. ")
    step = body[start:body.index("7. ", start)]
    assert "python3 $LESSONS/build_lessons.py" in step
    assert "--out <project>/lessons/lessons.html" in step
