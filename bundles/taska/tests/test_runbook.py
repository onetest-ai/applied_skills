"""docs/taska-runbook.md is pinned to the CLI, its images and the public-repo rules, so it cannot drift from the code."""
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs"
RUNBOOK = DOCS / "taska-runbook.md"
TASKA = ROOT / "bundles" / "taska" / "skills" / "run" / "taska.py"
IMG_DIR = DOCS / "img" / "taska-runbook"

TITLES = ["About this runbook", "Before you start", "Old owner: freeze and pack", "New owner: install and take over",
          "Schedules", "Routine", "Verify the handover", "Roll back", "Troubleshooting", "What never goes in a handover"]


def _text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _help(*args: str) -> str:
    r = subprocess.run([sys.executable, str(TASKA), *args, "-h"], capture_output=True, text=True, check=True)
    return r.stdout


@lru_cache(maxsize=None)
def _subcommands() -> frozenset[str]:
    m = re.search(r"\{([a-z0-9,-]+)\}", _help())
    assert m, "no subcommand list in taska.py -h"
    return frozenset(m.group(1).split(","))


@lru_cache(maxsize=None)
def _options(sub: str) -> frozenset[str]:
    lines = [l for l in _help(sub).splitlines() if l.startswith("usage:") or re.match(r"\s+-", l)]
    return frozenset(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", "\n".join(lines)))


def _lines(text: str) -> list[tuple[int, str, bool]]:
    """(line number, line, inside a fence) for every line outside ```example / ~~~example blocks."""
    out: list[tuple[int, str, bool]] = []
    fence = ""
    skip = False
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"\s*(```|~~~)(\S*)", line)
        if m and (not fence or m.group(1) == fence):
            if not fence:
                fence, skip = m.group(1), m.group(2) == "example"
            else:
                fence, skip = "", False
            continue
        if not skip:
            out.append((n, line, bool(fence)))
    return out


def _outside_examples(text: str) -> list[tuple[int, str]]:
    return [(n, l) for n, l, _ in _lines(text)]


_INLINE = re.compile(r"(`taska |\$TASKA |taska\.py\b(?:\s+--project\s+\S+)?\s+)([a-z][a-z-]*)([^`\n]*)")
_START = re.compile(r"^\s*(?:\$ )?taska\s+([a-z][a-z-]*)(.*)$")


def _command_problems(text: str) -> list[str]:
    subs = _subcommands()
    bad: list[str] = []
    for n, line, fenced in _lines(text):
        found = [(m.group(2), m.group(3), m.group(1) != "`taska ") for m in _INLINE.finditer(line)]
        st = _START.match(line)
        if st:
            found.append((st.group(1), st.group(2), True))
        for sub, rest, command_context in found:
            if sub not in subs:
                if fenced or command_context:
                    bad.append(f"{n}: unknown subcommand {sub!r}")
                continue
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", rest):
                if flag != "--project" and flag not in _options(sub):
                    bad.append(f"{n}: {sub} has no {flag}")
    return bad


def _sections() -> dict[int, list[str]]:
    secs: dict[int, list[str]] = {}
    cur = -1
    for line in _text().splitlines():
        m = re.match(r"## (\d+)\. ", line)
        if m:
            cur = int(m.group(1))
            secs[cur] = []
        elif cur >= 0:
            secs[cur].append(line)
    return secs


def test_runbook_sections():
    heads = re.findall(r"^## (\d+)\. (.+?)\s*$", _text(), re.M)
    assert [(int(n), t) for n, t in heads] == list(enumerate(TITLES))


def test_runbook_header_box():
    box = "\n".join(l for l in _text().splitlines() if l.startswith(">"))
    for field in ("Purpose", "When to use", "Who", "Time needed"):
        assert re.search(rf"\*\*{field}\*\*|{field}:", box), f"header box lacks {field}"
    assert re.search(r"Last verified: \S+ · taska \d+\.\d+\.\d+", box)


def test_runbook_commands_exist():
    bad = _command_problems(_text())
    assert not bad, "\n".join(bad)


def test_runbook_no_hardcoded_labels():
    pat = re.compile(r"\bcom\.[a-z0-9-]+\.[a-z0-9.-]+|(?:127\.0\.0\.1|localhost):\d+", re.I)
    bad = [f"{n}: {m.group(0)}" for n, line in _outside_examples(_text()) for m in pat.finditer(line)]
    assert not bad, "look labels and ports up instead of writing them:\n" + "\n".join(bad)


def test_runbook_steps_shape():
    secs = _sections()
    missing: list[str] = []
    checked = 0
    for sec in (2, 3, 7):
        steps: list[list[str]] = []
        for line in secs.get(sec, []):
            if re.match(r"\d+\. ", line):
                steps.append([line])
            elif steps and (line.startswith((" ", "\t")) or not line.strip()):
                steps[-1].append(line)
        for s in steps:
            checked += 1
            body = "\n".join(s)
            for word in ("Expected:", "If not:"):
                if word not in body:
                    missing.append(f"§{sec} {s[0][:50]!r} lacks {word!r}")
    assert checked, "no numbered steps in §2, §3 and §7"
    assert not missing, "\n".join(missing)


def test_runbook_images():
    refs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", _text())
    bad = [r for r in refs if not re.fullmatch(r"img/taska-runbook/[A-Za-z0-9._-]+\.png", r)]
    assert not bad, f"image links must be img/taska-runbook/<file>.png: {bad}"
    missing = [r for r in refs if not (DOCS / r).is_file()]
    assert not missing, f"missing images: {missing}"
    on_disk = {p.name for p in IMG_DIR.glob("*.png")} if IMG_DIR.is_dir() else set()
    unused = on_disk - {Path(r).name for r in refs}
    assert not unused, f"images never referenced: {sorted(unused)}"


def test_checker_rules_catch_what_they_guard():
    assert [n for n, _ in _outside_examples("a\n```example\nb\n```\nc")] == [1, 5]
    assert "--register" in _options("schedules")
    assert "--nope" not in _options("schedules")
    assert [n for n, _ in _outside_examples("a\n~~~example\nb\n~~~\nc")] == [1, 5]
    assert _command_problems("```\ntaska handover --nope\n```")
    assert _command_problems("$ taska bogus")
    assert _command_problems("`taska schedules --nope`")
    assert not _command_problems("Use `taska keeps` the tree. `taska schedules --register`")
    assert not _command_problems("```example\ntaska bogus\n```")
    assert not _command_problems("~~~example\ntaska bogus\n~~~")


def test_runbook_troubleshooting_rows():
    rows = [l for l in _sections().get(8, []) if l.startswith("|")]
    body = [r for r in rows[2:] if not re.fullmatch(r"\|[-:| ]+\|", r)]
    assert rows and re.match(r"\|\s*You see\s*\|\s*Cause\s*\|\s*Do\s*\|", rows[0]), "§8 is a You see | Cause | Do table"
    assert len(body) >= 12, f"§8 has {len(body)} rows; at least 12"
    seen = [r.split("|")[1].lower() for r in body]
    for want in ("no email arrived", "a stop command names a job that does not exist", "doctor says would_reread: true"):
        assert any(want in s for s in seen), f"§8 lacks the row {want!r}"
