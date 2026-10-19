"""Round 3 rules that live in prose (spec §10): pinned so an edit cannot drop them silently."""
from __future__ import annotations

import unittest

from test_plugin_structure import KB_ROOT

DECK = KB_ROOT / "skills" / "deck-fact-check"
read = lambda name: (DECK / name).read_text(encoding="utf-8")


class Briefs(unittest.TestCase):
    def test_attribution_is_part_of_the_claim(self):
        self.assertIn("the attribution is part of that claim, never a claim of its own", read("extract.md"))
        v = read("verify.md")
        self.assertIn("A time that differs from the source's by up to a minute is not a defect", v)
        self.assertIn("Found only in another source: Misleading (misattributed)", v)
        self.assertIn("never a finding on the attribution alone", v)

    def test_metric_names_only_from_the_catalogue(self):
        v = read("verify.md")
        self.assertIn("Call `get_metric` only with a name listed in `brain_context.json`'s `metric_names`", v)
        self.assertNotIn("even when the catalogue does not list an exact match", v)


class SkillMd(unittest.TestCase):
    def test_figure_claims_name_their_element_and_skip_slide_text(self):
        s = read("SKILL.md")
        self.assertIn("`p_id` names the element the claim reads, `s<N>-sh<id>`", s)
        self.assertIn("Never transcribe text the slide already holds as text", s)

    def test_comment_is_quote_and_fix_with_fields_on_request(self):
        s = read("SKILL.md")
        self.assertIn('Re: "<the statement, first 12 words…>"\nFix: <one sentence, or "needs owner input">\n```', s)
        self.assertIn("add `--fields quote,brain,fix,source`", s)

    def test_redispatch_is_the_same_text(self):
        s = read("SKILL.md")
        self.assertIn("A re-dispatch sends exactly the same text again. Nothing is added to it", s)
        self.assertIn('`Stage V. Brief: "<skill dir>/verify.md". Chunk: <work dir>/chunk_<j>.json.', s)


if __name__ == "__main__":
    unittest.main()
