"""The fact-check skills give the model ONE documented way to wait (wait_for.py) and say what not to improvise. Improvised waiting is what
Cowork blocks (`sleep 150; echo waiting`) or asks the user to approve (`until` loops, `ls`, `timeout`, `&&` chains), and it contradicted the
skills' own rule of running only documented commands."""
import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"


def paragraph(skill, start):
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    return next(p for p in text.split("\n") if p.startswith(start))


def test_stage_e_and_stage_v_wait_with_the_documented_script():
    for skill in ("doc-fact-check", "deck-fact-check"):
        for start in ("1. **Stage E", "3. **Stage V"):
            p = paragraph(skill, start)
            assert 'wait_for.py" "<work dir>"' in p, f"{skill}: {start} must say how to wait with wait_for.py"
            assert re.search(r"Never wait with `sleep`", p), f"{skill}: {start} must forbid improvised waiting"


def test_the_independent_verifier_is_not_polled():
    for skill in ("doc-fact-check", "deck-fact-check"):
        assert "end your turn" in paragraph(skill, "`verifier` judges"), f"{skill}: verifiers report as messages; do not poll"


def test_the_deck_slide_review_waits_with_the_script():
    assert 'wait_for.py" "<work dir>" --glob "slide_review_*.json"' in paragraph("deck-fact-check", "A finding is verified one statement at a time")


def test_the_documented_commands_rule_names_what_not_to_run():
    for skill in ("doc-fact-check", "deck-fact-check"):
        p = paragraph(skill, "- Run only the documented commands")
        assert "never copy it" in p and "`mkdir`" in p and "wait_for.py" in p, f"{skill}: the rule must list the commands the model improvises"


def test_no_skill_still_recommends_a_shell_loop_for_waiting():
    for skill in ("doc-fact-check", "deck-fact-check"):
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "wait with one `until" not in text and "list the work folder once per turn" not in text
