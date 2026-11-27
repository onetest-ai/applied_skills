from __future__ import annotations

import re
from pathlib import Path

from conftest import CONSULT_ROOT


def _description(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return ""
    for line in match.group(1).splitlines():
        if line.startswith("description:"):
            return line.partition(":")[2].strip()
    return ""


def test_every_skill_description_opens_with_use_when() -> None:
    skills = sorted((CONSULT_ROOT / "skills").glob("*/SKILL.md"))
    assert skills, "the bundle must ship at least one skill"
    offenders = [
        f"{path.parent.name}: {_description(path)[:50]!r}"
        for path in skills
        if not _description(path).startswith("Use when ")
    ]
    assert offenders == []
