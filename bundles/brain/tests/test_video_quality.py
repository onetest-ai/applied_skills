"""video_quality.py — deterministic quality metrics for an assembled video-lane doc."""
import json
import os
import tempfile
import unittest

import video_quality as Q

EMAIL = ("Launch documents email from Alex Doe to the release readiness team. "
         "Please find the launch documents attached: ExportOrdersStaging.csv, "
         "Setup_Guide.docx, CatalogVisibility_UserStories.xlsx, "
         "Load Test Approach.docx and the sample data generation deck.")
DIAGRAM = ("Systems overview diagram: web storefront, social lead forms, support desk "
           "customer care, bulk uploads, integration layer, product catalogue "
           "service, pricing engine, coupon service, customer data hub, reporting dashboards.")


def frame(t, n, title, body):
    return (f"## {t} · {title} (frame p{n:02d})\n\n<!-- image: rec--mp4/p{n:02d}.png -->\n"
            f"<!-- on-screen: {t}–{t} -->\n\n{body}\n")


def cue(t, n, text):
    return f"## {t} (cue {n})\n\n{text}\n"


def doc(*sections):
    return "# SOURCE: rec.mp4\n# method: video-lane (transcript: sidecar)\n# fidelity: full\n\n" + "\n".join(sections)


def pages(live, dropped=()):
    rows = [{"page": n, "image": f"rec--mp4/p{n:02d}.png"} for n in live]
    rows += [{"page": n, "image": f"rec--mp4/p{n:02d}.png", "dropped": why} for n, why in dropped]
    rows.sort(key=lambda r: r["page"])
    return {"medium": "video", "slug": "rec--mp4", "pages": rows}


class ParseTests(unittest.TestCase):
    def test_frame_and_cue_sections_are_split_by_their_heading_suffix(self):
        md = doc(cue("00:00:01", 1, "hello there"), frame("00:00:05", 2, "Mail", EMAIL),
                 cue("00:00:09", 2, "second turn"))
        self.assertEqual([f["page"] for f in Q.frame_sections(md)], [2])
        self.assertIn("Alex Doe", Q.frame_sections(md)[0]["body"])
        self.assertEqual(Q.cue_texts(md), ["hello there", "second turn"])

    def test_frame_body_excludes_marker_comments(self):
        f = Q.frame_sections(doc(frame("00:00:05", 3, "Mail", EMAIL)))[0]
        self.assertNotIn("<!--", f["body"])


class RedundancyTests(unittest.TestCase):
    def test_same_screen_transcribed_twice_is_a_redundant_pair(self):
        md = doc(frame("00:01:00", 1, "Mail", EMAIL), frame("00:02:00", 2, "Mail", EMAIL + " Regards."))
        pairs = Q.redundant_pairs(Q.frame_sections(md))
        self.assertEqual([(a, b) for a, b, _ in pairs], [(1, 2)])

    def test_different_screens_are_not_redundant(self):
        md = doc(frame("00:01:00", 1, "Mail", EMAIL), frame("00:02:00", 2, "Diagram", DIAGRAM))
        self.assertEqual(Q.redundant_pairs(Q.frame_sections(md)), [])

    def test_tiny_frames_are_never_counted(self):
        md = doc(frame("00:01:00", 1, "T", "Observations slide"), frame("00:02:00", 2, "T", "Observations slide"))
        self.assertEqual(Q.redundant_pairs(Q.frame_sections(md)), [])


class ReviewedRedundancyTests(unittest.TestCase):
    def test_pair_kept_on_a_blind_adds_verdict_is_reported_as_confirmed_distinct(self):
        md = doc(frame("00:01:00", 1, "Mail", EMAIL), frame("00:02:00", 2, "Mail", EMAIL + " Regards."),
                 frame("00:03:00", 3, "Mail", EMAIL + " Thanks."))
        pd = pages([1, 2, 3])
        pd["pages"][1]["review"] = [{"kind": "duplicate", "verdict": "adds", "md": "x"}]
        pairs = Q.redundant_pairs(Q.frame_sections(md))
        self.assertEqual([(a, b) for a, b, _ in Q.confirmed_distinct(pairs, pd)], [(1, 2), (2, 3)])


class LeakageTests(unittest.TestCase):
    def test_line_repeating_six_transcript_words_is_a_caption_leak(self):
        said = "so we will extrapolate this data as part of our next round of testing"
        md = doc(cue("00:00:01", 1, said),
                 frame("00:00:05", 2, "Mail", EMAIL + "\n\nextrapolate this data as part of our"))
        leaks = Q.caption_leaks(Q.frame_sections(md), Q.cue_entries(md))
        self.assertEqual([p for p, _ in leaks], [2])

    def test_slide_read_aloud_long_after_it_was_shown_is_not_a_leak(self):
        said = "so we will extrapolate this data as part of our next round of testing"
        md = doc(frame("00:00:05", 2, "Plan", EMAIL + "\n\nextrapolate this data as part of our"),
                 cue("00:10:00", 1, said))
        self.assertEqual(Q.caption_leaks(Q.frame_sections(md), Q.cue_entries(md)), [])

    def test_described_caption_overlay_is_a_leak_but_a_caption_block_on_a_slide_is_not(self):
        md = doc(frame("00:00:05", 2, "A", EMAIL + '\n\nA live-caption bar at the bottom reads "see this one."'),
                 frame("00:00:09", 3, "B", EMAIL + '\n\nLogo top left, with a small caption block reading "Data Model v2"'))
        self.assertEqual([p for p, _ in Q.caption_leaks(Q.frame_sections(md), [])], [2])

    def test_explicit_caption_label_is_a_leak_even_without_transcript(self):
        md = doc(frame("00:00:05", 2, "Mail", EMAIL + '\n\nCaption spoken: "hello"'))
        self.assertEqual(len(Q.caption_leaks(Q.frame_sections(md), [])), 1)

    def test_slide_subtitle_label_is_not_a_caption_leak(self):
        md = doc(frame("00:00:05", 2, "Title", EMAIL + "\n\n**Subtitle / context:** Launch plan"))
        self.assertEqual(Q.caption_leaks(Q.frame_sections(md), []), [])

    def test_on_screen_text_not_in_transcript_is_not_a_leak(self):
        md = doc(cue("00:00:01", 1, "let us look at the email"), frame("00:00:05", 2, "Mail", EMAIL))
        self.assertEqual(Q.caption_leaks(Q.frame_sections(md), Q.cue_entries(md)), [])

    def test_reference_to_another_frame_is_flagged(self):
        md = doc(frame("00:00:05", 2, "ER", "Same ER diagram view as previous frame."),
                 frame("00:00:09", 3, "Mail", EMAIL + " (same email as prior frame)"))
        self.assertEqual([p for p, _ in Q.cross_frame_refs(Q.frame_sections(md))], [2, 3])

    def test_comparison_with_image_a_is_flagged(self):
        md = doc(frame("00:00:05", 2, "Case", "Unlike A, there is no modal; a download icon (not present in A)."),
                 frame("00:00:09", 3, "AR", "Opened in A/R Activity Detail."))
        self.assertEqual([p for p, _ in Q.cross_frame_refs(Q.frame_sections(md))], [2])

    def test_ui_tokens_are_matched_case_sensitively(self):
        md = doc(frame("00:00:05", 2, "Mail", EMAIL + "\n\nParticipants panel open on the right"))
        self.assertEqual(len(Q.ui_leaks(Q.frame_sections(md), ["Participants panel"])), 1)
        self.assertEqual(Q.ui_leaks(Q.frame_sections(md), ["participants PANEL"]), [])


class GoldenTests(unittest.TestCase):
    def test_missing_and_forbidden_strings_are_reported_by_id(self):
        md = doc(frame("00:00:05", 2, "Sample Data", "acmetestb-0123abcd4567ef"))
        golden = {"must_contain": [{"id": "id-1", "text": "acmetestab-0123abcd4567ef"}],
                  "must_not_contain": [{"id": "id-1-wrong", "text": "acmetestb-0123abcd4567ef"}]}
        r = Q.golden_check(md, golden)
        self.assertEqual(r["missing"], ["id-1"])
        self.assertEqual(r["forbidden_present"], ["id-1-wrong"])
        self.assertEqual(r["contain_hits"], 0)

    def test_frame_scoped_golden_string_must_be_in_frame_text_not_the_transcript(self):
        md = doc(cue("00:00:01", 1, "the id is acmetestab-0123"), frame("00:00:05", 2, "T", "acmetestb-0123"))
        r = Q.golden_check(md, {"must_contain": [{"id": "x", "frame": "p02", "text": "acmetestab-0123"},
                                                 {"id": "y", "text": "acmetestab-0123"}]})
        self.assertEqual(r["missing"], ["x"])

    def test_golden_match_is_exact_case(self):
        r = Q.golden_check(doc(frame("00:00:05", 2, "T", "acmetestAB-ab12")),
                           {"must_contain": [{"id": "x", "text": "acmetestab-ab12"}]})
        self.assertEqual(r["missing"], ["x"])


class StructureTests(unittest.TestCase):
    def test_frame_whose_content_sits_under_sub_headings_leaves_a_marker_only_chunk(self):
        md = doc(frame("00:00:05", 2, "Results", "### Results\n\n| a | b |\n|---|---|\n| 1 | 2 |"))
        self.assertEqual(Q.marker_only_chunks(md), 1)
        self.assertEqual(Q.marker_only_chunks(doc(frame("00:00:05", 2, "Mail", EMAIL))), 0)

    def test_frame_parity_compares_doc_frames_with_live_pages(self):
        md = doc(frame("00:00:05", 2, "Mail", EMAIL), frame("00:00:09", 4, "D", DIAGRAM))
        self.assertTrue(Q.frame_parity(md, pages([2, 4], dropped=[(1, "no-content"), (3, "duplicate")])))
        self.assertFalse(Q.frame_parity(md, pages([2, 3, 4])))

    def test_cue_coverage_counts_transcript_cues_found_in_the_doc(self):
        md = doc(cue("00:00:01", 1, "hello there general kenobi"))
        cues = [{"text": "hello there"}, {"text": "general  kenobi"}, {"text": "never said"}]
        self.assertEqual(Q.cue_coverage(md, cues), (2, 3))


class RetentionTests(unittest.TestCase):
    def test_dropped_duplicate_is_retained_when_its_words_are_in_the_survivor(self):
        pj = pages([2], dropped=[(5, "duplicate")])
        pj["pages"][1]["duplicate_of"] = 2
        pj["pages"][0]["img_sha"], pj["pages"][1]["img_sha"] = "s2", "s5"
        vlm = {"s2": EMAIL, "s5": EMAIL}
        self.assertEqual(Q.retention(pj, vlm), [{"page": 5, "duplicate_of": 2, "containment": 1.0}])

    def test_retention_is_unknown_when_the_duplicates_transcription_is_missing(self):
        pj = pages([2], dropped=[(5, "duplicate")])
        pj["pages"][1]["duplicate_of"] = 2
        pj["pages"][0]["img_sha"], pj["pages"][1]["img_sha"] = "s2", "s5"
        self.assertEqual(Q.retention(pj, {"s2": EMAIL})[0]["containment"], None)


class CliTests(unittest.TestCase):
    def test_cli_writes_metrics_and_fails_on_golden_miss(self):
        with tempfile.TemporaryDirectory() as d:
            p = lambda n: os.path.join(d, n)
            open(p("doc.md"), "w").write(doc(frame("00:00:05", 2, "Mail", EMAIL)))
            json.dump(pages([2]), open(p("pages.json"), "w"))
            json.dump({"must_contain": [{"id": "absent", "text": "not on screen"}]}, open(p("g.json"), "w"))
            rc = Q.main(["--doc", p("doc.md"), "--pages", p("pages.json"), "--golden", p("g.json"),
                         "--out", p("m.json")])
            m = json.load(open(p("m.json")))
        self.assertEqual(rc, 1)
        self.assertEqual(m["frames"], 1)
        self.assertEqual(m["golden"]["missing"], ["absent"])
        for key in ("redundant_pairs", "caption_leaks", "cross_frame_refs", "ui_leaks",
                    "marker_only_chunks", "frame_parity"):
            self.assertIn(key, m)

    def test_cli_passes_when_clean(self):
        with tempfile.TemporaryDirectory() as d:
            p = lambda n: os.path.join(d, n)
            open(p("doc.md"), "w").write(doc(frame("00:00:05", 2, "Mail", EMAIL)))
            json.dump(pages([2]), open(p("pages.json"), "w"))
            self.assertEqual(Q.main(["--doc", p("doc.md"), "--pages", p("pages.json"), "--out", p("m.json")]), 0)


if __name__ == "__main__":
    unittest.main()
