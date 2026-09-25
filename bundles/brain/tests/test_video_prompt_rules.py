"""VIDEO_GATE fidelity rules: what a meeting-recording frame must and must not transcribe.

These pin the rules, not the prose: each assertion names the failure it prevents.
"""
import unittest

import vision_prep as VP


class VideoGateRulesTests(unittest.TestCase):
    gate = VP.VIDEO_GATE

    def test_rules_are_scoped_to_video_items(self):
        self.assertIn("ONLY to items whose `medium` is `video`", self.gate)
        self.assertNotIn("character for character", VP.INSTRUCTIONS)  # decks keep their prompt (version 1)

    def test_identifiers_are_copied_verbatim_not_normalised(self):
        # an "ab" ID series was "corrected" to the "b" series printed next to it
        self.assertIn("character for character", self.gate)
        self.assertIn("never normalise", self.gate.lower())

    def test_verbatim_names_are_the_ones_in_the_shared_content(self):
        # not the participant names the same gate tells the model to ignore
        self.assertIn("names shown in the shared content", self.gate)

    def test_document_rule_is_its_own_paragraph_not_a_video_bullet(self):
        self.assertIn("\n\nNever answer `<!-- no-content -->` for an item whose `medium` is `document`", self.gate)

    def test_unreadable_text_is_marked_not_guessed(self):
        # a ~7px recipient name was guessed as a different name
        self.assertIn("[illegible]", self.gate)

    def test_burned_in_captions_and_meeting_ui_are_ignored(self):
        g = self.gate.lower()
        for phrase in ("caption", "participant", "toolbar", "taskbar"):
            self.assertIn(phrase, g)

    def test_ignored_content_is_not_described_or_quoted(self):
        # a note like '(burned-in caption "the next step is…" ignored)' still indexes the caption
        self.assertIn("do not mention, describe or quote anything you ignore", self.gate.lower())

    def test_each_frame_stands_alone(self):
        # "Same ER diagram view as previous frame" is meaningless inside a retrieval chunk
        self.assertIn("never refer to other frames", self.gate.lower())

    def test_application_screens_get_a_descriptive_title(self):
        self.assertIn("<app> — <window or document title>", self.gate)

    def test_video_prompt_version_was_bumped_for_these_rules(self):
        self.assertEqual(VP.PROMPT_VERSION["video"], 2)


if __name__ == "__main__":
    unittest.main()
