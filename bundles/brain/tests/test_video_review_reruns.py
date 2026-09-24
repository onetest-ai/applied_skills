"""Edge cases of the blind review round and of reruns (found by two independent code reviews)."""
from __future__ import annotations

import contextlib
import io
import json

import video_capture as V
from test_video_review import DIAGRAM, EMAIL, REWRITTEN, ReviewFixture


class RerunTests(ReviewFixture):
    def _reviewed(self):
        self._prep()
        self._answer({"sha3": {"same": True}, "sha4": {"md": REWRITTEN}})
        self.assertEqual(self._assemble(), 0)

    def _cache_only(self, *extra):
        return V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd), "--parsed",
                       str(self.parsed), "--db", str(self.db), *extra])

    def test_cache_only_rerun_with_the_review_dir_is_idempotent(self):
        self._reviewed()
        first = self._md()
        self.assertEqual(self._cache_only("--review", str(self.review)), 0)
        self.assertEqual(self._md(), first)

    def test_cache_only_rerun_does_not_warn_about_missing_frame_echo(self):
        self._reviewed()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._cache_only()
        self.assertNotIn("frame echo", err.getvalue())

    def test_legacy_identifier_verdicts_are_ignored(self):
        self._reviewed()
        pj = json.loads(self.pj.read_text())
        pj["pages"][1]["review"] = [{"kind": "verify", "verdict": "rewrite", "judged_sha": V._text_sha(DIAGRAM), "md": "# Old\n\nvoted"}]
        self.pj.write_text(json.dumps(pj))
        self.assertEqual(self._assemble(review=False), 0)
        self.assertNotIn("voted", self._md())


class DuplicatePairingTests(ReviewFixture):
    def test_same_verdict_counts_only_for_the_frame_it_was_compared_with(self):
        self._prep()
        self._answer({"sha3": {"same": True}, "sha4": {"md": REWRITTEN}})
        self.assertEqual(self._assemble(), 0)                  # p3 confirmed same as p1
        self._set_results({"sha1": "# Other screen\n\n" + " ".join(f"word{i}" for i in range(30)),
                           "sha2": EMAIL, "sha3": EMAIL, "sha4": REWRITTEN})
        self.assertEqual(self._assemble(review=False), 1)      # p3 now duplicates p2: never compared


class OlderBatchTests(ReviewFixture):
    def test_batch_without_kept_img_sha_is_tied_by_kept_page(self):
        self._prep()
        items = self._items()
        for i in items:
            i.pop("kept_img_sha", None)
        (self.review / "review_batch.json").write_text(json.dumps(items))
        self._answer({"sha3": {"same": True}, "sha4": {"md": REWRITTEN}})
        self.assertEqual(self._assemble(), 0)
        self.assertEqual(self._pages()[3]["review"][-1]["kept_img_sha"], "sha1")


class ReviewerTextTests(ReviewFixture):
    def test_frame_echo_in_a_reviewers_text_is_stripped(self):
        self._prep()
        self._answer({"sha3": {"same": True}, "sha4": {"md": "<!-- frame: p04 -->\n" + REWRITTEN}})
        self.assertEqual(self._assemble(), 0)
        self.assertNotIn("<!-- frame:", self._md())
        self.assertEqual(self._assemble(review=False), 0)      # record still matches on rerun

    def test_per_item_file_must_answer_its_own_page(self):
        self._prep()
        (self.review / "review_result_p04.json").write_text(json.dumps({"sha3": {"same": True}}))
        (self.review / "review_result_p03.json").write_text(json.dumps({"sha4": {"md": REWRITTEN}}))
        self.assertEqual(self._assemble(), 1)
