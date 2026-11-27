from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import register
from conftest import CONSULT_ROOT

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def rules() -> list[register.Rule]:
    return register.load(FIXTURES / "register_valid.json")


@pytest.fixture
def terms() -> list[str]:
    return register.read_terms(FIXTURES / "terms.txt")


def messages(problems: list[register.Problem]) -> str:
    return " | ".join(f"{p.rule_id}: {p.message}" for p in problems)


def raw(rule: register.Rule) -> dict:
    data = json.loads(json.dumps(register._to_dict(rule)))
    return data


def write_proposals(path: Path, items: list[register.Rule]) -> None:
    path.write_text("\n".join(json.dumps(register._to_dict(r)) for r in items) + "\n", encoding="utf-8")


def test_read_terms_ignores_blank_and_comments(terms: list[str]) -> None:
    assert terms == ["Acme", "Jane Roe"]


def test_valid_fixture_has_no_problems(rules, terms) -> None:
    assert register.validate(rules, CONSULT_ROOT, terms) == []


def test_duplicate_id_refused(rules, terms) -> None:
    problems = register.validate([rules[0], rules[0]], CONSULT_ROOT, terms)
    assert "duplicate" in messages(problems)


def test_client_specific_scope_refused(rules, terms) -> None:
    bad = replace(rules[0], scope="client-specific")
    problems = register.validate([bad], CONSULT_ROOT, terms)
    assert "client-specific" in messages(problems)


def test_approved_needs_date(rules, terms) -> None:
    bad = replace(rules[0], approved_on=None)
    assert "approved_on" in messages(register.validate([bad], CONSULT_ROOT, terms))


def test_rule_must_be_one_sentence(rules, terms) -> None:
    for text in ("Do this. Then that.", "Do this; then that.", "A. B."):
        bad = replace(rules[0], rule=text)
        assert "one sentence" in messages(register.validate([bad], CONSULT_ROOT, terms)), text


def test_abbreviations_are_not_sentence_breaks(rules, terms) -> None:
    ok = replace(rules[0], rule="Price like for like, e.g. customer tier.")
    assert register.validate([ok], CONSULT_ROOT, terms) == []


def test_sentence_breaks_cannot_be_gamed(rules, terms) -> None:
    for text in ("I said no. Then left.", "Is it right? Yes.", "Stop! Now.", "One.\nTwo."):
        bad = replace(rules[0], rule=text)
        assert "one sentence" in messages(register.validate([bad], CONSULT_ROOT, terms)), text
    for text in ("Compare vs. the previous bill.", "Answer no.", "Use approx. 5 items, etc., always."):
        ok = replace(rules[0], rule=text)
        assert register.validate([ok], CONSULT_ROOT, terms) == [], text


def test_private_terms_spacing_hyphen_plural(rules, terms) -> None:
    for text in ("jane  roe", "Jane\nRoe", "Jane\u00a0Roe", "Jane-Roe", "ACMEs", "Acme\u2019s", "Acmes", "Jane Roe's"):
        bad = replace(rules[0], why=f"Note {text} here")
        assert "private term" in messages(register.validate([bad], CONSULT_ROOT, terms)), text
    ok = replace(rules[0], why="Acmeville is fine")
    assert register.validate([ok], CONSULT_ROOT, terms) == []


def test_sources_required(rules, terms) -> None:
    bad = replace(rules[0], sources=())
    assert "sources" in messages(register.validate([bad], CONSULT_ROOT, terms))


def test_private_terms_case_and_possessive(rules, terms) -> None:
    bad = replace(rules[0], why="acme's prices drive this")
    problems = register.validate([bad], CONSULT_ROOT, terms)
    assert problems and "why" in problems[0].message
    assert "acme" not in problems[0].message.lower()
    also = replace(rules[0], see_also="notes about JANE ROE")
    assert "see_also" in messages(register.validate([also], CONSULT_ROOT, terms))
    fine = replace(rules[0], why="Acmetech is not a match")
    assert register.validate([fine], CONSULT_ROOT, terms) == []


def test_approved_check_needs_existing_ref_and_test(rules, terms, tmp_path: Path) -> None:
    check = register.Enforcement("check", "skills/x/check.py", "tests/test_x.py")
    approved = replace(rules[0], enforced_by=(check,))
    assert "skills/x/check.py" in messages(register.validate([approved], tmp_path, terms))
    (tmp_path / "skills/x").mkdir(parents=True)
    (tmp_path / "skills/x/check.py").write_text("")
    assert "tests/test_x.py" in messages(register.validate([approved], tmp_path, terms))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text("")
    assert register.validate([approved], tmp_path, terms) == []
    proposed = replace(approved, status="proposed", approved_on=None)
    missing = replace(proposed, enforced_by=(replace(check, ref="nope.py"),))
    assert register.validate([missing], tmp_path, terms) == []


def test_add_proposals_forces_proposed_and_refuses_bad(rules, terms, tmp_path: Path) -> None:
    reg = tmp_path / "register.json"
    reg.write_text("[]", encoding="utf-8")
    good = replace(rules[0], id="GOOD-ONE")
    bad = replace(rules[0], id="BAD-ONE", why="Acme pays")
    props = tmp_path / "p.jsonl"
    write_proposals(props, [good, bad])
    problems = register.add_proposals(reg, props, CONSULT_ROOT, terms)
    assert [p.rule_id for p in problems] == ["BAD-ONE"]
    saved = register.load(reg)
    assert [r.id for r in saved] == ["GOOD-ONE"]
    assert saved[0].status == "proposed" and saved[0].approved_on is None


def test_proposal_defaults_status_and_date(rules, terms, tmp_path: Path) -> None:
    reg = tmp_path / "register.json"
    reg.write_text("[]", encoding="utf-8")
    data = raw(replace(rules[0], id="NO-STATUS"))
    del data["status"], data["approved_on"]
    props = tmp_path / "p.jsonl"
    props.write_text(json.dumps(data) + "\n", encoding="utf-8")
    assert register.add_proposals(reg, props, CONSULT_ROOT, terms) == []
    assert register.load(reg)[0].status == "proposed"


def test_approve_sets_status_and_date_and_keeps_version(rules, terms, tmp_path: Path) -> None:
    reg = tmp_path / "register.json"
    check_free = replace(rules[1], enforced_by=())
    register.save(reg, [rules[0], check_free])
    assert register.approve(reg, ["ONE-BASE"], "2026-11-27") == []
    saved = {r.id: r for r in register.load(reg)}
    assert saved["ONE-BASE"].status == "approved"
    assert saved["ONE-BASE"].approved_on == "2026-11-27"
    assert saved["ONE-BASE"].version == 2


def test_approve_unknown_id_is_problem_and_saves_nothing(rules, tmp_path: Path) -> None:
    reg = tmp_path / "register.json"
    register.save(reg, [replace(rules[1], enforced_by=())])
    before = reg.read_bytes()
    problems = register.approve(reg, ["ONE-BASE", "NOPE-ID"], "2026-11-27")
    assert "NOPE-ID" in messages(problems)
    assert reg.read_bytes() == before


def test_changed_rule_text_needs_new_version(rules, terms, tmp_path: Path) -> None:
    reg = tmp_path / "register.json"
    register.save(reg, [rules[0]])
    props = tmp_path / "p.jsonl"
    write_proposals(props, [replace(rules[0], rule="Write something else entirely.")])
    problems = register.add_proposals(reg, props, CONSULT_ROOT, terms)
    assert "version" in messages(problems)
    assert register.load(reg)[0].rule == rules[0].rule
    write_proposals(props, [replace(rules[0], rule="Write something else entirely.", version=2)])
    assert register.add_proposals(reg, props, CONSULT_ROOT, terms) == []
    saved = register.load(reg)
    assert len(saved) == 1 and saved[0].version == 2 and saved[0].status == "proposed"


def test_save_format(rules, tmp_path: Path) -> None:
    reg = tmp_path / "r.json"
    register.save(reg, [rules[1], rules[0]])
    text = reg.read_text(encoding="utf-8")
    assert text.endswith("\n") and text.startswith('[\n {')
    assert [r.id for r in register.load(reg)] == ["ONE-BASE", "PLAIN-WORDS"]


def test_approved_ref_outside_bundle_is_a_problem(rules, terms, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "tests").mkdir(parents=True)
    (tmp_path / "outside.py").write_text("")
    (bundle / "tests/test_x.py").write_text("")
    for ref, test in (("/etc/hosts", "tests/test_x.py"), ("../outside.py", "tests/test_x.py"), ("tests/test_x.py", "../outside.py")):
        bad = replace(rules[0], enforced_by=(register.Enforcement("check", ref, test),))
        assert "outside" in messages(register.validate([bad], bundle, terms)), (ref, test)


def test_private_term_in_id_ref_and_test(rules, terms) -> None:
    bad = replace(rules[0], id="ACME-RULE")
    assert "private term in field id" in messages(register.validate([bad], CONSULT_ROOT, terms))
    for kind_field in ("ref", "test"):
        enforcement = register.Enforcement("agent", "agents/acme-check.md" if kind_field == "ref" else "a.md", "tests/test_acme.py" if kind_field == "test" else None)
        bad = replace(rules[0], enforced_by=(enforcement,))
        assert f"private term in field {kind_field}" in messages(register.validate([bad], CONSULT_ROOT, terms)), kind_field


def test_shipped_register_is_valid(tmp_path: Path) -> None:
    shipped = CONSULT_ROOT / "rules" / "register.json"
    assert register.validate(register.load(shipped), CONSULT_ROOT, []) == []
    result = subprocess.run(
        [sys.executable, str(CONSULT_ROOT / "skills" / "lessons" / "build_lessons.py"),
         "--register", str(shipped), "--bundle", str(CONSULT_ROOT), "--out", str(tmp_path / "lessons.html")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "lessons.html").is_file()
