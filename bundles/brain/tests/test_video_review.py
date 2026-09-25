"""Second round: BLIND review — readers never see the first-pass text, code decides.

review-prep lists the frames at risk and writes items that carry only images and a neutral
question (Chain-of-Verification: a checker that sees the draft tends to confirm it).
One isolated reader answers each item from the images alone:
  duplicate  -> {"same": true} | {"same": false, "md": <transcription of the later frame, on its own>}
  standalone -> {"md": <transcription of the frame on its own>}
assemble --review stores the verdicts on their pages in pages.json. Identifiers are not voted on:
the first pass's verbatim and [illegible] rules carry them.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import video_capture as V
import vision_assemble as VA

EMAIL = ("# Launch Documents — Email\n\nFrom Alex Doe to the release readiness team. "
         "Please find the launch documents attached: ExportOrdersStaging.csv, "
         "Setup_Guide.docx, CatalogVisibility_UserStories.xlsx, "
         "Load Test Approach.docx and the sample data generation deck.")
DIAGRAM = ("# Systems overview\n\nWeb storefront, social lead forms, support desk customer "
           "care, bulk uploads, integration layer, product catalogue service, pricing engine, "
           "coupon service, customer data hub, reporting dashboards.")
LEANING = "# Mail search\n\nSame search-results view as the previous frame, cursor on the From filter."
REWRITTEN = "# Mail search\n\nSearch results filtered by sender, cursor on the From filter."
FIRST = "id-a-0123abcd4567ef89"      # first pass misread (the screen shows the "ab" series)
OTHER = "id-c-9876fedc5432ba10"
IDS = f"# Sample Data\n\n| locale | id |\n|---|---|\n| en-GB | {FIRST} |\n| en-GB | {OTHER} |"


class ReviewFixture(unittest.TestCase):
    RESULTS = {"sha1": EMAIL, "sha2": DIAGRAM, "sha3": EMAIL, "sha4": LEANING}

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        self.slug = "rec__standup--mp4"
        self.rd = r / "assets" / self.slug; self.rd.mkdir(parents=True)
        pages = []
        for n, (t0, t1) in enumerate([(10, 20), (30, 40), (50, 60), (70, 80), (90, 95)], 1):
            (self.rd / f"p{n:02d}.png").write_bytes(b"png")
            pages.append({"page": n, "image": f"{self.slug}/p{n:02d}.png", "img_sha": f"sha{n}", "flagged": True,
                          "why": "video-frame", "t_start": t0, "t_end": t1, "shown_at": [[t0, t1]]})
        pages[4]["dropped"] = "no-content"
        (self.rd / "p05.png").unlink()
        self.pj = self.rd / "pages.json"
        self.pj.write_text(json.dumps({"doc": "standup.mp4", "slug": self.slug, "medium": "video",
                                       "duration": 100.0, "pages": pages}))
        self.probe = r / "probe.json"
        self.probe.write_text(json.dumps({"source": "rec/standup.mp4", "transcript": "none", "sidecar": None,
                                          "sidecar_source": None, "warnings": []}))
        self.results = r / "vision"; self.results.mkdir()
        self._set_results(self.RESULTS)
        self.review = r / "review"
        self.parsed = r / "parsed"; self.parsed.mkdir()
        self.db = r / "k.sqlite"; sqlite3.connect(self.db).close()

    def tearDown(self):
        self.t.cleanup()

    def _set_results(self, mapping):
        (self.results / "result_0.json").write_text(json.dumps(mapping))

    def _prep(self, out=None, *earlier):
        out = out or self.review
        args = ["review-prep", "--render-dir", str(self.rd), "--results", str(self.results), "--out", str(out)]
        for d in earlier:
            args += ["--review", str(d)]
        return V.main(args)

    def _items(self, d=None):
        return json.loads(((d or self.review) / "review_batch.json").read_text())

    def _answer(self, mapping, d=None):
        ((d or self.review) / "review_result.json").write_text(json.dumps(mapping))

    def _assemble(self, *dirs, review=True, extra=()):
        args = ["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd), "--results",
                str(self.results), "--parsed", str(self.parsed), "--db", str(self.db), *extra]
        for d in (dirs or ((self.review,) if review else ())):
            args += ["--review", str(d)]
        return V.main(args)

    def _md(self):
        return (self.parsed / "rec__standup.mp4.md").read_text()

    def _pages(self):
        return {p["page"]: p for p in json.loads(self.pj.read_text())["pages"]}


class BlindPrepTests(ReviewFixture):
    def test_lists_duplicates_and_leaning_frames(self):
        self.assertEqual(self._prep(), 0)
        items = {i["page"]: i for i in self._items()}
        self.assertEqual(sorted(items), [3, 4])
        self.assertEqual((items[3]["kind"], items[3]["kept_page"]), ("duplicate", 1))
        self.assertTrue(Path(items[3]["image"]).is_file() and Path(items[3]["kept_image"]).is_file())
        self.assertEqual(items[4]["kind"], "standalone")

    def test_items_carry_no_first_pass_text(self):
        # independence: a reader that sees the draft tends to confirm it
        self._set_results({**self.RESULTS, "sha2": IDS})
        self._prep()
        for it in self._items():
            for key in ("md", "kept_md", "ids"):
                self.assertNotIn(key, it)
            self.assertNotIn(FIRST, json.dumps(it))

    def test_instructions_ask_blind_questions_with_the_transcription_rules(self):
        self._prep()
        text = (self.review / "review_instructions.md").read_text()
        for phrase in ('"same"', '"md"', "review_result.json", "character for character",
                       "[illegible]", "never refer to other frames", "have not seen",
                       "Do not open any other file"):
            self.assertIn(phrase, text)

    def test_prep_does_not_touch_pages_json(self):
        before = self.pj.read_text()
        self._prep()
        self.assertEqual(self.pj.read_text(), before)


class DuplicateAndStandaloneTests(ReviewFixture):
    def test_confirmed_duplicate_stays_dropped(self):
        self._prep()
        self._answer({"sha3": {"same": True}, "sha4": {"md": REWRITTEN}})
        self.assertEqual(self._assemble(), 0)
        p = self._pages()
        self.assertEqual((p[3]["dropped"], p[3]["review"][-1]["verdict"]), ("duplicate", "same"))
        self.assertNotIn("(frame p03)", self._md())

    def test_duplicate_that_differs_is_kept_with_the_blind_transcription(self):
        self._prep()
        new = "# Launch Documents — Email\n\nSecond page of the attachment list: Rollback_Plan.pdf, Owners.xlsx."
        self._answer({"sha3": {"same": False, "md": new}, "sha4": {"md": REWRITTEN}})
        self.assertEqual(self._assemble(), 0)
        self.assertIn("(frame p03)", self._md())
        self.assertIn("Rollback_Plan.pdf", self._md())
        self.assertTrue(self._pages()[3]["keep"])

    def test_standalone_takes_the_blind_transcription_and_is_cached(self):
        self._prep()
        self._answer({"sha3": {"same": True}, "sha4": {"md": REWRITTEN}})
        self._assemble()
        self.assertNotIn("previous frame", self._md())
        self.assertIn("filtered by sender", self._md())
        self.assertEqual(VA.load_cache(str(self.db), "video")["sha4"], REWRITTEN)

    def test_refuses_unanswered_or_malformed_answers(self):
        self._prep()
        for bad in ({"sha3": {"same": True}},                               # sha4 unanswered
                    {"sha3": {"same": False}, "sha4": {"md": REWRITTEN}},    # differs but no md
                    {"sha3": {"strings": []}, "sha4": {"md": REWRITTEN}},    # wrong shape for kind
                    [], {"sha3": "same", "sha4": {"md": REWRITTEN}}):
            self._answer(bad)
            self.assertEqual(self._assemble(), 1, bad)
            self.assertFalse((self.parsed / "rec__standup.mp4.md").exists())


class PerItemReaderTests(ReviewFixture):
    """One isolated blind reader per item: each writes review_result_pNN.json for its item only
    (a reader holding many images in one context confused which image was which)."""

    def test_per_item_result_files_are_merged(self):
        self._prep()
        (self.review / "review_result_p03.json").write_text(json.dumps({"sha3": {"same": True}}))
        (self.review / "review_result_p04.json").write_text(json.dumps({"sha4": {"md": REWRITTEN}}))
        self.assertEqual(self._assemble(), 0)
        self.assertIn("filtered by sender", self._md())

    def test_two_files_answering_the_same_item_differently_are_refused(self):
        self._prep()
        (self.review / "review_result_p03.json").write_text(json.dumps({"sha3": {"same": True}, "sha4": {"md": REWRITTEN}}))
        (self.review / "review_result_p04.json").write_text(json.dumps({"sha4": {"md": "# Other\n\ntext"}}))
        self.assertEqual(self._assemble(), 1)

    def test_answer_for_an_item_not_in_the_batch_is_refused(self):
        self._prep()
        (self.review / "review_result_p03.json").write_text(json.dumps({"sha3": {"same": True}, "sha1": {"same": True}}))
        (self.review / "review_result_p04.json").write_text(json.dumps({"sha4": {"md": REWRITTEN}}))
        self.assertEqual(self._assemble(), 1)

    def test_each_reader_gets_a_file_holding_only_its_item(self):
        # a reader shown every item's img_sha once copied a neighbour's key in a real run
        self._prep()
        for it in self._items():
            own = json.loads((self.review / f"item_p{it['page']:02d}.json").read_text())
            self.assertEqual(own, it)
        text = (self.review / "review_instructions.md").read_text()
        self.assertIn("item_pNN.json", text)

    def test_stale_item_files_from_an_earlier_prep_are_removed(self):
        self.review.mkdir()
        (self.review / "item_p09.json").write_text("{}")
        self._prep()
        self.assertFalse((self.review / "item_p09.json").exists())

    def test_instructions_assign_one_item_per_reader(self):
        self._prep()
        text = (self.review / "review_instructions.md").read_text()
        self.assertIn("review_result_pNN.json", text)
        self.assertIn("only the item", text)


class PersistedVerdictTests(ReviewFixture):
    def _full_review(self):
        self._prep()
        self._answer({"sha3": {"same": True}, "sha4": {"md": REWRITTEN}})
        self.assertEqual(self._assemble(), 0)

    def test_rerun_with_first_pass_results_keeps_the_reviewed_text(self):
        self._full_review()
        self.assertEqual(self._assemble(review=False), 0)
        self.assertIn("filtered by sender", self._md())
        self.assertNotIn("previous frame", self._md())

    def test_maintenance_rerun_from_the_cache_alone_keeps_the_review(self):
        self._full_review()
        self.assertEqual(V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                                 "--parsed", str(self.parsed), "--db", str(self.db)]), 0)
        self.assertIn("filtered by sender", self._md())
        self.assertNotIn("(frame p03)", self._md())

    def test_rerun_after_a_full_review_needs_no_new_review(self):
        self._full_review()
        self._prep()
        self.assertEqual(self._items(), [])

    def test_a_changed_transcription_is_reviewed_again(self):
        self._full_review()
        self._set_results({**self.RESULTS, "sha4": LEANING + " New text."})
        self.assertEqual(self._assemble(review=False), 1)

    def test_empty_or_stale_review_dir_cannot_bypass_the_guard(self):
        self.review.mkdir()
        (self.review / "review_batch.json").write_text("[]")
        (self.review / "review_result.json").write_text("{}")
        self.assertEqual(self._assemble(), 1)

    def test_nothing_to_review_with_the_documented_review_flag_assembles(self):
        self._set_results({"sha1": EMAIL, "sha2": DIAGRAM, "sha3": "# Q3 plan\n\n- ship it", "sha4": "# Risks\n\n- none"})
        self._prep()
        self.assertEqual(self._assemble(), 0)

    def test_review_and_no_review_are_exclusive(self):
        self._prep()
        with self.assertRaises(SystemExit):
            V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd), "--parsed",
                    str(self.parsed), "--review", str(self.review), "--no-review"])


class FollowUpRoundTests(ReviewFixture):
    def test_blind_transcription_that_duplicates_another_frame_needs_a_follow_up_round(self):
        self._prep()
        self._answer({"sha3": {"same": True}, "sha4": {"md": EMAIL}})   # p4, re-read, is the email again
        self.assertEqual(self._assemble(), 1)
        r2 = Path(self.t.name) / "r2"
        self.assertEqual(self._prep(r2, self.review), 0)
        self.assertEqual([(i["kind"], i["page"], i["kept_page"]) for i in self._items(r2)], [("duplicate", 4, 1)])
        self._answer({"sha4": {"same": True}}, r2)
        self.assertEqual(self._assemble(self.review, r2), 0)
        self.assertNotIn("(frame p04)", self._md())


class NoIdentifierVoteTests(ReviewFixture):
    """Identifiers alone no longer make a frame a review item: the first pass's verbatim and
    [illegible] rules carry them (the string vote cost most of the review and most of its bugs)."""
    RESULTS = {"sha1": EMAIL, "sha2": IDS, "sha3": EMAIL, "sha4": REWRITTEN}

    def test_identifiers_alone_do_not_make_a_frame_a_review_item(self):
        self._prep()
        self.assertEqual([(i["kind"], i["page"]) for i in self._items()], [("duplicate", 3)])

    def test_instructions_ask_for_no_identifier_lists(self):
        self._prep()
        text = (self.review / "review_instructions.md").read_text()
        self.assertNotIn('"strings"', text)
        self.assertNotIn("verify", text)

    def test_frame_with_identifiers_keeps_its_first_pass_text(self):
        self._prep()
        self._answer({"sha3": {"same": True}})
        self.assertEqual(self._assemble(), 0)
        self.assertIn(FIRST, self._md())
        self.assertNotIn("[unverified", self._md())


class ComparativeAddsTests(ReviewFixture):
    """A duplicate reader sees A and B; its `adds` text must describe B alone."""
    COMPARED = "# Launch Documents — Email\n\nUnlike A, the list also shows Rollback_Plan.pdf and Owners.xlsx."

    def test_duplicate_instruction_says_to_describe_b_on_its_own(self):
        self._prep()
        text = (self.review / "review_instructions.md").read_text()
        self.assertIn("as if A did not exist", text)

    def test_adds_text_written_against_the_kept_frame_is_read_again_on_its_own(self):
        self._prep()
        self._answer({"sha3": {"same": False, "md": self.COMPARED}, "sha4": {"md": REWRITTEN}})
        self.assertEqual(self._assemble(), 1)
        r2 = Path(self.t.name) / "r2"
        self._prep(r2, self.review)
        self.assertEqual([(i["kind"], i["page"]) for i in self._items(r2)], [("standalone", 3)])
        clean = "# Launch Documents — Email\n\nAttachment list: Rollback_Plan.pdf, Owners.xlsx, Setup_Guide.docx."
        self._answer({"sha3": {"md": clean}}, r2)
        self.assertEqual(self._assemble(self.review, r2), 0)
        first = self._md()
        self.assertIn("Attachment list", first)
        self.assertNotIn("Unlike A", first)
        self.assertIn("(frame p03)", first)                    # still pinned by the `adds` verdict
        for args in ((), ("--review", str(self.review), "--review", str(r2))):   # cache-only reruns
            self.assertEqual(V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                                     "--parsed", str(self.parsed), "--db", str(self.db), *args]), 0)
            self.assertEqual(self._md(), first)


class CrossFrameRefTests(unittest.TestCase):
    def test_comparisons_with_image_a_or_b_are_cross_frame_references(self):
        for ln in ("Unlike A, there is no modal.", "a download icon (not present in A)",
                   "the modal shown in B is gone", "Same as image A but scrolled", "more rows than A."):
            self.assertTrue(V._XREF.search(ln), ln)

    def test_ordinary_text_with_a_capital_a_or_b_is_not(self):
        for ln in ("Opened in A/R Activity Detail", "Plan A: migrate", "Grade B", "stored in B2 bucket",
                   "A modal dialog is open", "Vitamin B in A-list", "Total value in B column",
                   "Copy from A to Z", "Cell in A."):
            self.assertFalse(V._XREF.search(ln), ln)


class ChainedVerdictTests(ReviewFixture):
    def test_re_read_that_duplicates_another_frame_but_adds_content_is_kept(self):
        self._prep()
        self._answer({"sha3": {"same": True}, "sha4": {"md": EMAIL}})   # p4 re-read == the email
        r2 = Path(self.t.name) / "r2"; self._prep(r2, self.review)
        more = EMAIL + " Plus a second page: Rollback_Plan.pdf, Owners.xlsx, Budget_2025.xlsx, Risks.docx."
        self._answer({"sha4": {"same": False, "md": more}}, r2)
        self.assertEqual(self._assemble(self.review, r2), 0)
        self.assertIn("(frame p04)", self._md())
        self.assertIn("Budget_2025.xlsx", self._md())
        first = self._md()
        self.assertEqual(V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                                 "--parsed", str(self.parsed), "--db", str(self.db)]), 0)
        self.assertEqual(self._md(), first)


class LegacyBatchTests(ReviewFixture):
    def test_batch_from_the_identifier_vote_asks_for_a_new_review_prep(self):
        import contextlib, io
        self.review.mkdir()
        (self.review / "review_batch.json").write_text(json.dumps([{"kind": "verify", "img_sha": "sha2", "page": 2}]))
        (self.review / "review_result.json").write_text(json.dumps({"sha2": {"strings": ["x"]}}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._assemble(), 1)
        self.assertIn("older review-prep", err.getvalue())
        self.assertNotIn("malformed", err.getvalue())

    def test_missing_answers_message_names_one_reader_per_item(self):
        import contextlib, io
        self.review.mkdir()                                     # no review_batch.json at all
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._assemble(), 1)
        self.assertIn("missing", err.getvalue())
        self.assertNotIn("the review agent", err.getvalue())


class MalformedResultTests(ReviewFixture):
    """A vision worker once wrote unquoted chat text into result_0.json; review-prep skipped the file and built
    an empty review batch from the remaining frames in a real run."""
    def _break(self):
        (self.results / "result_1.json").write_text('{"sha4": "# Chat\n\nsaid "hi" there"}')

    def test_review_prep_refuses_and_names_a_malformed_result_file(self):
        import contextlib, io
        self._break()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._prep(), 1)
        self.assertIn("result_1.json", err.getvalue())
        self.assertFalse((self.review / "review_batch.json").exists())

    def test_assemble_refuses_and_names_a_malformed_result_file(self):
        import contextlib, io
        self._break()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._assemble(review=False, extra=("--no-review",)), 1)
        self.assertIn("result_1.json", err.getvalue())


class SkipGuardTests(ReviewFixture):
    def test_assemble_refuses_when_frames_need_review_and_no_review_is_given(self):
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._assemble(review=False), 1)
        self.assertIn("review-prep", err.getvalue())
        self.assertIn("one blind reader per review item", err.getvalue())
        self.assertNotIn("one review agent", err.getvalue())
        self.assertFalse((self.parsed / "rec__standup.mp4.md").exists())

    def test_explicit_no_review_assembles_without_the_round(self):
        self.assertEqual(self._assemble(review=False, extra=("--no-review",)), 0)


class DedupKeepTests(unittest.TestCase):
    def test_frames_marked_keep_are_never_merged(self):
        p = lambda n: {"page": n, "img_sha": f"s{n}", "shown_at": [[n, n + 1]]}
        live, dups = V.dedup_content([(p(1), EMAIL), ({**p(2), "keep": True}, EMAIL)])
        self.assertEqual((len(live), dups), (2, []))


if __name__ == "__main__":
    unittest.main()
