"""The fact-check deliverables hold the user's document text and Brain evidence. They are delivered as files only: never as an
Artifact (a hosted page), a shared link, or an upload to another service."""
import re
from pathlib import Path

KB = Path(__file__).resolve().parents[1]
RULE = re.compile(r"never create or publish an Artifact", re.I)


def deliver_line(skill):
    text = (KB / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    return next(l for l in text.split("\n") if l.startswith("- Deliver the annotated"))


def test_both_fact_check_skills_forbid_publishing_the_deliverables():
    for skill in ("doc-fact-check", "deck-fact-check"):
        assert RULE.search(deliver_line(skill)), f"{skill}: the delivery step must forbid Artifacts and hosted links"


def test_no_skill_or_agent_mentions_artifacts_except_to_forbid_them():
    for path in [*(KB / "skills").rglob("SKILL.md"), *(KB / "agents").rglob("*.md")]:
        for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if re.search(r"\bArtifact", line):          # the product feature; a lowercase "artifact" just means a deliverable
                assert re.search(r"\bnever\b", line, re.I), f"{path.relative_to(KB)}:{n} mentions an Artifact without forbidding it"
