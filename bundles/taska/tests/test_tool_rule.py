"""One tool rule for every agent prompt, and every documented command runs as written: it parses against the CLI and
passes tool_guard (Nov 3: two definitions of $TASKA, and agents improvising shell the allow-list refused)."""
import re
import shlex
from pathlib import Path

import pytest

import taska
import tool_guard

RUN = Path(__file__).resolve().parents[1] / "skills" / "run"
FILES = ["SKILL.md", "EXTRACT.md", "DEPS.md", "VERIFY.md"]
BLOCK = re.compile(r"<!-- TOOL-RULE:START -->.*?<!-- TOOL-RULE:END -->", re.S)
W = "/p/taska"


def test_the_tool_rule_is_one_block_identical_in_every_prompt():
    blocks = {f: BLOCK.findall((RUN / f).read_text()) for f in FILES}
    assert all(len(b) == 1 for b in blocks.values()), {f: len(b) for f, b in blocks.items()}
    assert len({b[0] for b in blocks.values()}) == 1


def test_taska_is_defined_once_as_the_command_given():
    for f in FILES:
        text = (RUN / f).read_text()
        assert "python3 /…/skills/run/taska.py" not in text, f
        assert f == "SKILL.md" or "python3 <skill-dir>/taska.py" not in text, f


def _documented():
    for f in FILES:
        for cmd in re.findall(r"`\$TASKA ([a-z][^`]*)`", (RUN / f).read_text()):
            yield f, cmd


def _concrete(cmd):
    """A documented call as an agent would type it: a code span's line break is a space, `[…]` is optional (taken),
    a trailing `…` means "repeat" (dropped), `<x>` and a quoted `…` are values."""
    cmd = re.sub(r"\s+", " ", cmd).strip()
    cmd = re.sub(r"\[([^\]]*)\]", r"\1", re.sub(r" …$", "", cmd)).replace("…", "words from the meeting")
    return re.sub(r"<[^>]+>", "x1", cmd)


@pytest.mark.parametrize("f, cmd", list(_documented()))
def test_every_documented_command_parses_and_passes_the_guard(f, cmd):
    c = _concrete(cmd)
    assert tool_guard.guard(f"{W} {c}", W) is None, (f, cmd)
    if " " not in c:   # a command named in prose (`$TASKA backfill`): it must exist
        assert c in taska.build_parser()._subparsers._group_actions[0].choices, (f, cmd)
    else:
        taska.build_parser().parse_args(["--project", "/p", *shlex.split(c)])
