"""One frame = one chunk: VLM headings inside a frame must not open new retrieval sections."""
from __future__ import annotations

import unittest

import chunking
import video_capture as V

VLM = ("# Nightly Import Load Test Results\n\n**Environment:** staging\n\n"
       "## Results table\n\n| Job | 100K rows | 1M rows |\n|---|---|---|\n| Nightly import | 12min | 2hr 5min |\n\n"
       "### Notes\n\n- run once with the full feed")


def _doc(md):
    p = {"page": 15, "t_start": 755, "t_end": 831, "shown_at": [[755, 831]]}
    return V.render_doc("rec.mp4", "video-lane (transcript: none)", "rec--mp4", [], [(p, md)])


class FrameChunkingTests(unittest.TestCase):
    def test_vlm_headings_become_bold_lines(self):
        body = _doc(VLM).split("-->\n\n", 2)[-1]
        self.assertNotRegex(body, r"(?m)^#")
        self.assertIn("**Results table**", body)
        self.assertIn("**Notes**", body)

    def test_frame_section_title_still_comes_from_the_vlm_title(self):
        self.assertIn("## 00:12:35 · Nightly Import Load Test Results (frame p15)", _doc(VLM))

    def test_a_short_frame_is_exactly_one_chunk_that_carries_its_content(self):
        recs = chunking.section_records(chunking.strip_preamble(_doc(VLM)), 1200)
        self.assertEqual(len(recs), 1)
        self.assertIn("12min", recs[0]["body"])
        self.assertIn("(frame p15)", recs[0]["title"])

    def test_trailing_hash_in_a_heading_is_content(self):
        self.assertEqual(V.flatten_headings("## Learn C#"), "**Learn C#**")

    def test_code_comment_in_a_fence_never_becomes_a_heading(self):
        # chunking is not fence-aware: a column-0 "# comment" would open a live H1 and re-root
        # the breadcrumb of every later section in the doc
        for fence in ("```", "~~~"):
            md = f"# Script on screen\n\n{fence}bash\n# load the feed\nrun --all\n{fence}"
            p = {"page": 3, "t_start": 5, "t_end": 9, "shown_at": [[5, 9]]}
            doc = V.render_doc("rec.mp4", "video-lane (transcript: none)", "rec--mp4",
                               [{"start": 20.0, "speaker": "Bob", "text": "next point"}], [(p, md)])
            recs = chunking.section_records(chunking.strip_preamble(doc), 1200)
            self.assertEqual([r["title"] for r in recs], ["00:00:05 · Script on screen (frame p03)",
                                                           "00:00:20 — Bob (cue 1)"], fence)
            self.assertIn("load the feed", recs[0]["body"])

    def test_unclosed_fence_cannot_leave_live_headings(self):
        md = "# Script\n\n```\n# step one\n## step two"
        self.assertNotRegex(V.flatten_headings(md), r"(?m)^#{1,6}\s")

    def test_code_in_a_fence_is_kept_apart_from_a_one_space_indent_on_hash_lines(self):
        md = "# Script on screen\n\n```python\n# load the feed\nrun()\n```"
        self.assertIn("```python\n # load the feed\nrun()\n```", _doc(md))


if __name__ == "__main__":
    unittest.main()
