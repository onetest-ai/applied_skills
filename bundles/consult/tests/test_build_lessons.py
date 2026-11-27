from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from build_lessons import coverage, render
from conftest import LESSONS_DIR
from register import Enforcement, Rule, Source

FIXTURE = Path(__file__).parent / "fixtures" / "register_valid.json"


def make_rule(
    rule_id: str = "SAMPLE-RULE",
    status: str = "approved",
    enforced_by: tuple[Enforcement, ...] = (),
    text: str = "Write one claim per sentence.",
    area: str = "language",
) -> Rule:
    return Rule(
        id=rule_id,
        version=1,
        status=status,  # type: ignore[arg-type]
        approved_on="2026-11-20" if status == "approved" else None,
        area=area,  # type: ignore[arg-type]
        scope="personal-style",
        rule=text,
        why="Readers skim.",
        sources=(Source("s-1", None, "2026-11-01T10:00:00Z"),),
        enforced_by=enforced_by,
        see_also=None,
    )


def touch(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def test_check_with_existing_ref_and_test_is_covered(tmp_path: Path) -> None:
    touch(tmp_path, "skills/a/check.py")
    touch(tmp_path, "tests/test_a.py")
    rule = make_rule(enforced_by=(Enforcement("check", "skills/a/check.py", "tests/test_a.py"),))
    [result] = coverage([rule], tmp_path)
    assert result.covered is True
    assert result.reasons == ()


def test_missing_test_file_is_uncovered(tmp_path: Path) -> None:
    touch(tmp_path, "skills/a/check.py")
    rule = make_rule(enforced_by=(Enforcement("check", "skills/a/check.py", "tests/test_a.py"),))
    [result] = coverage([rule], tmp_path)
    assert result.covered is False
    assert any("tests/test_a.py" in r for r in result.reasons)


def test_agent_ref_missing_is_uncovered(tmp_path: Path) -> None:
    rule = make_rule(enforced_by=(Enforcement("agent", "agents/reviewer.md", None),))
    [result] = coverage([rule], tmp_path)
    assert result.covered is False
    assert any("agents/reviewer.md" in r for r in result.reasons)


def test_no_enforcement_is_uncovered(tmp_path: Path) -> None:
    [result] = coverage([make_rule()], tmp_path)
    assert result.covered is False


def test_proposed_rules_never_gaps(tmp_path: Path) -> None:
    rule = make_rule(status="proposed")
    cov = coverage([rule], tmp_path)
    page = render([rule], cov, "2026-11-27")
    gaps = page.split("<h2>Gaps</h2>")[1].split("<h2>")[0]
    assert "SAMPLE-RULE" not in gaps
    assert "No gaps" in gaps


def test_render_lists_gaps_before_lessons(tmp_path: Path) -> None:
    rule = make_rule()
    page = render([rule], coverage([rule], tmp_path), "2026-11-27")
    assert page.index("Gaps") < page.index("<h2>Language</h2>")
    assert "SAMPLE-RULE" in page.split("<h2>Gaps</h2>")[1].split("<h2>")[0]


def test_render_escapes_html(tmp_path: Path) -> None:
    rule = make_rule(text="Never use <b> in a title.")
    page = render([rule], coverage([rule], tmp_path), "2026-11-27")
    assert "&lt;b&gt;" in page
    assert "<b>" not in page


def test_render_is_self_contained(tmp_path: Path) -> None:
    rule = make_rule()
    page = render([rule], coverage([rule], tmp_path), "2026-11-27")
    assert "prefers-color-scheme: dark" in page
    assert 'name="viewport"' in page
    assert "http://" not in page and "https://" not in page
    assert "<script" not in page and "<link" not in page


def run_cli(tmp_path: Path, rules: list[Rule]) -> subprocess.CompletedProcess[str]:
    register = tmp_path / "register.json"
    register.write_text(
        json.dumps(
            [
                {
                    "id": r.id, "version": r.version, "status": r.status,
                    "approved_on": r.approved_on, "area": r.area, "scope": r.scope,
                    "rule": r.rule, "why": r.why,
                    "sources": [{"session": "s-1", "uuid": None, "timestamp": "2026-11-01T10:00:00Z"}],
                    "enforced_by": [{"kind": e.kind, "ref": e.ref, "test": e.test} for e in r.enforced_by],
                    "see_also": None,
                }
                for r in rules
            ]
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(LESSONS_DIR / "build_lessons.py"), "--register", str(register),
         "--bundle", str(tmp_path), "--out", str(tmp_path / "out.html")],
        capture_output=True, text=True,
    )


def test_cli_exit_1_on_uncovered_approved(tmp_path: Path) -> None:
    result = run_cli(tmp_path, [make_rule()])
    assert result.returncode == 1
    assert (tmp_path / "out.html").is_file()


def test_cli_exit_0_when_all_covered(tmp_path: Path) -> None:
    touch(tmp_path, "skills/s/SKILL.md")
    rule = make_rule(enforced_by=(Enforcement("skill", "skills/s/SKILL.md", None),))
    assert run_cli(tmp_path, [rule]).returncode == 0


def test_cli_exit_0_with_only_proposed_uncovered(tmp_path: Path) -> None:
    assert run_cli(tmp_path, [replace(make_rule(), status="proposed", approved_on=None)]).returncode == 0


def test_mixed_rule_with_broken_check_is_uncovered(tmp_path: Path) -> None:
    touch(tmp_path, "skills/s/SKILL.md")
    touch(tmp_path, "skills/a/check.py")
    rule = make_rule(
        enforced_by=(
            Enforcement("skill", "skills/s/SKILL.md", None),
            Enforcement("check", "skills/a/check.py", "tests/test_a.py"),
        )
    )
    [result] = coverage([rule], tmp_path)
    assert result.covered is False
    assert any("tests/test_a.py" in r for r in result.reasons)


def test_matrix_cell_state_from_enforcements(tmp_path: Path) -> None:
    touch(tmp_path, "agents/reviewer.md")
    touch(tmp_path, "skills/a/check.py.bak")
    touch(tmp_path, "tests/test_a.py")
    rule = make_rule(
        enforced_by=(
            Enforcement("agent", "agents/reviewer.md", None),
            Enforcement("check", "skills/a/check.py", "tests/test_a.py"),
            Enforcement("check", "skills/a/check.py.bak", "tests/test_a.py"),
        )
    )
    page = render([rule], coverage([rule], tmp_path), "2026-11-27")
    row = page.split("<tbody>")[1].split("</tbody>")[0]
    assert '<td class="bad">broken</td><td class="ok">enforced</td><td class="none">none</td>' in row
    assert 'href="skills/a/check.py"' not in page
    assert "skills/a/check.py (missing)" in page
    assert 'href="skills/a/check.py.bak"' in page


def test_ref_outside_bundle_is_never_sound(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    touch(tmp_path, "outside.py")
    touch(bundle, "tests/test_a.py")
    for ref in ("/etc/hosts", "../outside.py"):
        rule = make_rule(enforced_by=(Enforcement("agent", ref, None),))
        [result] = coverage([rule], bundle)
        assert result.covered is False, ref
        assert any(ref in r and "outside" in r for r in result.reasons), ref
    check = make_rule(enforced_by=(Enforcement("check", "tests/test_a.py", "../outside.py"),))
    [result] = coverage([check], bundle)
    assert result.covered is False
    assert any("../outside.py" in r and "outside" in r for r in result.reasons)
