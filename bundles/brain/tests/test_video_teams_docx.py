"""Teams .docx transcripts in the video lane (synthetic files, invented names only)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _tools
import parse_corpus
import video_capture as V

STEM = "Planning Sync wAcme  Roadmap-20260105_150400-Meeting Recording"
TURNS = [
    ("Dana Rivers (Acme Corp)", "0:13", ["Welcome everyone.", "Let us start with the roadmap."]),
    ("Lee Park1", "1:05", "Budget is on slide four."),
    ("Dana Rivers (Acme Corp)", "1:00:11", "Wrapping up now."),
]


class ReadTeamsDocxTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.d = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def _docx(self, name="t.docx", **kw):
        args = dict(title=STEM, duration_line="1h 2m 3s", turns=TURNS)
        args.update(kw)
        return _tools.make_teams_docx(self.d / name, **args)

    def test_turns_speakers_times_and_text(self):
        cues = V.read_teams_docx(self._docx(events=("started", "stopped")))
        self.assertEqual([(c["speaker"], c["start"], c["end"], c["text"]) for c in cues], [
            ("Dana Rivers (Acme Corp)", 13.0, 65.0, "Welcome everyone. Let us start with the roadmap."),
            ("Lee Park1", 65.0, 3611.0, "Budget is on slide four."),
            ("Dana Rivers (Acme Corp)", 3611.0, 3723.0, "Wrapping up now."),
        ])

    def test_avatar_offsets_and_header_never_reach_the_text(self):
        cues = V.read_teams_docx(self._docx())
        blob = json.dumps(cues)
        for noise in ("576072", "914400", "transcription", "January", "1h 2m 3s", "Meeting Recording"):
            self.assertNotIn(noise, blob)

    def test_last_end_is_the_duration_else_its_own_start(self):
        for line, end in (("45m 3s", 2703.0), ("58s", 58.0), ("1h 26m 30s", 5190.0)):
            cues = V.read_teams_docx(self._docx(duration_line=line, turns=TURNS[:1]))
            self.assertEqual(cues[-1]["end"], end, line)
        cues = V.read_teams_docx(self._docx(duration_line="5s", turns=TURNS[:1]))
        self.assertEqual(cues[-1]["end"], 13.0)  # a duration before the last start is not an end
        cues = V.read_teams_docx(self._docx(duration_line="no duration here", turns=TURNS[:2]))
        self.assertEqual(cues[-1]["end"], 65.0)

    def test_fallback_when_runs_are_not_split(self):
        cues = V.read_teams_docx(self._docx(split_runs=False))
        self.assertEqual([(c["speaker"], c["start"], c["text"]) for c in cues][1],
                         ("Lee Park1", 65.0, "Budget is on slide four."))
        self.assertEqual(cues[2]["start"], 3611.0)

    def test_speaker_split_across_runs(self):
        turns = [(("Lee Park", "1 "), "0:13", ["Budget first.", "Then hiring."]), ("Dana Rivers", "0:40", "Agreed.")]
        cues = V.read_teams_docx(self._docx(turns=turns))
        self.assertEqual([(c["speaker"], c["start"], c["text"]) for c in cues], [
            ("Lee Park1", 13.0, "Budget first. Then hiring."), ("Dana Rivers", 40.0, "Agreed.")])

    def test_read_cues_dispatches_docx(self):
        self.assertEqual(V.read_cues(self._docx()), V.read_teams_docx(self._docx()))

    def test_zero_turns_is_an_empty_list(self):
        self.assertEqual(V.read_teams_docx(self._docx(turns=[])), [])

    def test_truncated_docx_raises_a_clean_value_error(self):
        p = _tools.truncate_file(self._docx())
        with self.assertRaises(ValueError) as cm:
            V.read_teams_docx(p)
        self.assertEqual(str(cm.exception),
                         f"cannot read transcript {p}: not a readable Word file (truncated download?)")
        self.assertIsNone(V.docx_title(p))

    def test_zip_without_document_xml_and_bad_xml_raise_value_error(self):
        import zipfile
        a = self.d / "a.docx"
        with zipfile.ZipFile(a, "w") as z:
            z.writestr("other.xml", "<x/>")
        b = self.d / "b.docx"
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("word/document.xml", "<w:document")
        for p in (a, b):
            with self.assertRaises(ValueError):
                V.read_teams_docx(p)

    def test_docx_title_is_the_first_paragraph_verbatim(self):
        self.assertEqual(V.docx_title(self._docx()), STEM)


class FindSidecarDocxTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.d = Path(self.t.name)
        self.video = self.d / f"{STEM}.mp4"; self.video.write_bytes(b"v")

    def tearDown(self):
        self.t.cleanup()

    def test_pairs_a_differently_named_docx_by_its_title(self):
        _tools.make_teams_docx(self.d / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        _tools.make_teams_docx(self.d / "Other meeting.docx", "Some other title", "5m", TURNS)
        self.assertEqual(Path(V.find_sidecar(str(self.video))).name, "Acme_ Roadmap.docx")

    def test_same_stem_wins_over_title(self):
        _tools.make_teams_docx(self.d / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        (self.d / f"{STEM}.VTT").write_text("WEBVTT\n")
        self.assertEqual(Path(V.find_sidecar(str(self.video))).name, f"{STEM}.VTT")

    def test_same_stem_docx_is_found_after_vtt_and_srt(self):
        _tools.make_teams_docx(self.d / f"{STEM}.DOCX", "whatever", "1m", TURNS)
        self.assertEqual(Path(V.find_sidecar(str(self.video))).name, f"{STEM}.DOCX")
        (self.d / f"{STEM}.srt").write_text("")
        self.assertEqual(Path(V.find_sidecar(str(self.video))).name, f"{STEM}.srt")

    def test_two_titles_claiming_the_video_is_an_error(self):
        _tools.make_teams_docx(self.d / "A.docx", STEM, "1m", TURNS)
        _tools.make_teams_docx(self.d / "B.docx", STEM, "1m", TURNS)
        with self.assertRaises(ValueError) as cm:
            V.find_sidecar(str(self.video))
        self.assertIn("A.docx", str(cm.exception)); self.assertIn("B.docx", str(cm.exception))

    def test_same_stem_docx_without_turns_is_skipped(self):
        _tools.make_teams_docx(self.d / f"{STEM}.docx", "Agenda", "x", [])
        self.assertIsNone(V.find_sidecar(str(self.video)))
        _tools.make_teams_docx(self.d / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        self.assertEqual(Path(V.find_sidecar(str(self.video))).name, "Acme_ Roadmap.docx")

    def test_unreadable_same_stem_docx_is_skipped(self):
        _tools.truncate_file(_tools.make_teams_docx(self.d / f"{STEM}.docx", STEM, "1m", TURNS))
        self.assertIsNone(V.find_sidecar(str(self.video)))

    def test_unreadable_docx_is_not_a_candidate(self):
        _tools.truncate_file(_tools.make_teams_docx(self.d / "A.docx", STEM, "1m", TURNS))
        self.assertIsNone(V.find_sidecar(str(self.video)))


class ProbeDocxTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        self.corpus = r / "c"; self.rec = self.corpus / "rec"; self.rec.mkdir(parents=True)
        self.video = self.rec / f"{STEM}.mp4"; self.video.write_bytes(b"v")
        self.work = r / "video"
        self.ff = patch.object(V, "ffprobe", return_value=(3800.0, True)); self.ff.start()

    def tearDown(self):
        self.ff.stop(); self.t.cleanup()

    def _probe(self, *extra):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = V.main(["probe", "--video", str(self.video), "--rel-to", str(self.corpus),
                           "--work", str(self.work), *extra])
        self.err = err.getvalue()
        return code

    def _json(self):
        return json.loads((self.work / V.video_slug(f"rec/{STEM}.mp4") / "probe.json").read_text())

    def test_title_paired_docx_becomes_the_sidecar(self):
        _tools.make_teams_docx(self.rec / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        self.assertEqual(self._probe(), 0)
        p = self._json()
        self.assertEqual((p["transcript"], p["sidecar_source"]), ("sidecar", "rec/Acme_ Roadmap.docx"))
        self.assertEqual(p["warnings"], [])

    def test_truncated_same_stem_docx_is_skipped_with_a_warning(self):
        _tools.truncate_file(_tools.make_teams_docx(self.rec / f"{STEM}.docx", STEM, "1m", TURNS))
        self.assertEqual(self._probe(), 0)
        p = self._json()
        self.assertEqual((p["transcript"], p["sidecar"]), ("asr", None))
        self.assertEqual(p["warnings"], [f"could not read {STEM}.docx (truncated download?) — "
                                         "it was not considered as a transcript"])

    def test_truncated_transcript_file_fails_cleanly(self):
        bad = _tools.truncate_file(_tools.make_teams_docx(self.corpus / "x.docx", STEM, "1m", TURNS))
        self.assertEqual(self._probe("--transcript-file", str(bad)), 1)
        self.assertIn("not a readable Word file (truncated download?)", self.err)
        self.assertNotIn("Traceback", self.err)

    def test_same_stem_notes_docx_is_not_a_transcript(self):
        _tools.make_teams_docx(self.rec / f"{STEM}.docx", "Agenda", "not a duration", [])
        self.assertEqual(self._probe(), 0)
        p = self._json()
        self.assertEqual((p["transcript"], p["sidecar"], p["warnings"]), ("asr", None, []))

    def test_same_stem_notes_docx_gives_way_to_the_title_paired_transcript(self):
        _tools.make_teams_docx(self.rec / f"{STEM}.docx", "Agenda", "not a duration", [])
        _tools.make_teams_docx(self.rec / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        self.assertEqual(self._probe(), 0)
        self.assertEqual(self._json()["sidecar_source"], "rec/Acme_ Roadmap.docx")

    def test_empty_same_stem_vtt_still_dies_sidecar_empty(self):
        (self.rec / f"{STEM}.vtt").write_text("WEBVTT\n\n")
        _tools.make_teams_docx(self.rec / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        self.assertEqual(self._probe(), 1)
        self.assertIn("sidecar-empty", self.err)

    def test_truncated_neighbour_docx_is_a_warning(self):
        _tools.truncate_file(_tools.make_teams_docx(self.rec / "Acme_ Roadmap.docx", STEM, "1m", TURNS))
        self.assertEqual(self._probe(), 0)
        p = self._json()
        self.assertEqual(p["transcript"], "asr")
        self.assertEqual(p["warnings"], ["could not read Acme_ Roadmap.docx (truncated download?) — "
                                         "it was not considered as a transcript"])

    def test_two_claiming_titles_exit_1_and_suggest_transcript_file(self):
        _tools.make_teams_docx(self.rec / "A.docx", STEM, "1m", TURNS)
        _tools.make_teams_docx(self.rec / "B.docx", STEM, "1m", TURNS)
        self.assertEqual(self._probe(), 1)
        self.assertIn("--transcript-file", self.err)

    def test_docx_with_zero_turns_is_sidecar_empty(self):
        _tools.make_teams_docx(self.rec / "Acme_ Roadmap.docx", STEM, "1m", [])
        self.assertEqual(self._probe(), 1)
        self.assertIn("sidecar-empty", self.err)

    def test_transcript_file_is_used_verbatim(self):
        other = self.corpus / "elsewhere" / "notes.docx"
        _tools.make_teams_docx(other, "Unrelated title", "1h 2m 3s", TURNS)
        self.assertEqual(self._probe("--transcript-file", str(other)), 0)
        p = self._json()
        self.assertEqual((p["transcript"], p["sidecar"], p["sidecar_source"]),
                         ("sidecar", str(other), "elsewhere/notes.docx"))

    def test_transcript_file_outside_the_source_root_is_used_but_not_consumable(self):
        outside = Path(self.t.name) / "elsewhere" / "notes.docx"
        _tools.make_teams_docx(outside, "Unrelated title", "1h 2m 3s", TURNS)
        self.assertEqual(self._probe("--transcript-file", str(outside)), 0)
        p = self._json()
        self.assertEqual((p["transcript"], p["sidecar"], p["sidecar_source"]),
                         ("sidecar", str(outside), None))
        self.assertIn("transcript file is outside the source root — it will be used but not retired "
                      "from the index", p["warnings"])

    def test_transcript_file_without_rel_to_is_not_consumable(self):
        other = self.rec / "notes.docx"
        _tools.make_teams_docx(other, "Unrelated title", "1h 2m 3s", TURNS)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = V.main(["probe", "--video", str(self.video), "--work", str(self.work),
                           "--transcript-file", str(other)])
        self.assertEqual(code, 0)
        p = json.loads((self.work / V.video_slug(f"{STEM}.mp4") / "probe.json").read_text())
        self.assertIsNone(p["sidecar_source"])
        self.assertTrue(any("outside the source root" in w for w in p["warnings"]))

    def test_auto_sidecar_without_rel_to_stays_consumable_beside_the_video(self):
        # No --rel-to: the video's own source is its basename, so a sibling sidecar's is too.
        _tools.make_teams_docx(self.rec / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(V.main(["probe", "--video", str(self.video), "--work", str(self.work)]), 0)
        p = json.loads((self.work / V.video_slug(f"{STEM}.mp4") / "probe.json").read_text())
        self.assertEqual((p["source"], p["sidecar_source"]), (f"{STEM}.mp4", "Acme_ Roadmap.docx"))

    def test_unreadable_neighbour_is_reported_even_when_a_sidecar_is_found(self):
        _tools.make_teams_docx(self.rec / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        _tools.truncate_file(_tools.make_teams_docx(self.rec / "Broken.docx", "x", "1m", TURNS))
        self.assertEqual(self._probe(), 0)
        p = self._json()
        self.assertEqual(p["sidecar_source"], "rec/Acme_ Roadmap.docx")
        self.assertEqual(p["warnings"], ["could not read Broken.docx (truncated download?) — "
                                         "it was not considered as a transcript"])

    def test_transcript_file_must_be_vtt_srt_or_docx(self):
        bad = self.corpus / "notes.txt"; bad.write_text("hello")
        self.assertEqual(self._probe("--transcript-file", str(bad)), 1)
        self.assertIn(".vtt, .srt or .docx", self.err)

    def test_unreadable_permissions_die_without_traceback(self):
        import os
        vtt = self.corpus / "x.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A>hi</v>\n")
        vtt.chmod(0)
        try:
            if os.access(vtt, os.R_OK):
                self.skipTest("running as a user that ignores file permissions")
            self.assertEqual(self._probe("--transcript-file", str(vtt)), 1)
            self.assertIn("cannot read", self.err)
            self.assertNotIn("Traceback", self.err)
        finally:
            vtt.chmod(0o644)

    def test_transcript_file_vtt_and_missing_and_empty(self):
        vtt = self.corpus / "x.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A>hi</v>\n")
        self.assertEqual(self._probe("--transcript-file", str(vtt)), 0)
        self.assertEqual(self._json()["sidecar_source"], "x.vtt")
        self.assertEqual(self._probe("--transcript-file", str(self.corpus / "nope.vtt")), 1)
        self.assertIn("not found", self.err)
        empty = self.corpus / "e.vtt"; empty.write_text("WEBVTT\n\n")
        self.assertEqual(self._probe("--transcript-file", str(empty)), 1)
        self.assertIn("sidecar-empty", self.err)

    def test_transcript_file_with_asr_or_none_is_refused(self):
        vtt = self.corpus / "x.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A>hi</v>\n")
        for mode in ("asr", "none"):
            self.assertEqual(self._probe("--transcript-file", str(vtt), "--transcript", mode), 1)
            self.assertIn("--transcript-file", self.err)

    def test_transcript_file_bypasses_an_ambiguous_title_pairing(self):
        _tools.make_teams_docx(self.rec / "A.docx", STEM, "1m", TURNS)
        _tools.make_teams_docx(self.rec / "B.docx", STEM, "1m", TURNS)
        self.assertEqual(self._probe("--transcript-file", str(self.rec / "B.docx")), 0)
        self.assertEqual(self._json()["sidecar_source"], "rec/B.docx")

    def test_asr_override_ignores_an_ambiguous_title_pairing(self):
        _tools.make_teams_docx(self.rec / "A.docx", STEM, "1m", TURNS)
        _tools.make_teams_docx(self.rec / "B.docx", STEM, "1m", TURNS)
        self.assertEqual(self._probe("--transcript", "asr"), 0)
        self.assertEqual(self._json()["transcript"], "asr")


class AssembleDocxTests(unittest.TestCase):
    """End to end over a docx sidecar: probe (ffprobe stubbed) -> assemble."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        self.corpus = r / "c"; self.rec = self.corpus / "rec"; self.rec.mkdir(parents=True)
        self.video = self.rec / f"{STEM}.mp4"; self.video.write_bytes(b"v")
        _tools.make_teams_docx(self.rec / "Acme_ Roadmap.docx", STEM, "1h 2m 3s", TURNS)
        self.work = r / "video"; self.parsed = r / "parsed"; self.parsed.mkdir()
        self.rel = f"rec/{STEM}.mp4"; self.slug = V.video_slug(self.rel)
        with patch.object(V, "ffprobe", return_value=(3800.0, True)):
            assert V.main(["probe", "--video", str(self.video), "--rel-to", str(self.corpus),
                           "--work", str(self.work)]) == 0
        self.rd = r / "assets" / self.slug; self.rd.mkdir(parents=True)
        (self.rd / "p01.png").write_bytes(b"png"); (self.rd / "p01.txt").write_text("")
        (self.rd / "pages.json").write_text(json.dumps({
            "doc": self.video.name, "slug": self.slug, "medium": "video", "duration": 3800.0,
            "frames_capped": False, "pages": [{"page": 1, "image": f"{self.slug}/p01.png", "img_sha": "sha1",
                                               "flagged": True, "why": "video-frame", "t_start": 30,
                                               "t_end": 60, "shown_at": [[30, 60]]}]}))
        self.results = r / "vision"; self.results.mkdir()
        (self.results / "result_0.json").write_text(json.dumps({"sha1": "# Roadmap\n\n- Q1"}))

    def tearDown(self):
        self.t.cleanup()

    def test_outside_transcript_file_consumes_and_deletes_nothing(self):
        outside = Path(self.t.name) / "elsewhere" / "notes.docx"
        _tools.make_teams_docx(outside, "Unrelated title", "1h 2m 3s", TURNS)
        with patch.object(V, "ffprobe", return_value=(3800.0, True)), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(V.main(["probe", "--video", str(self.video), "--rel-to", str(self.corpus),
                                     "--work", str(self.work), "--transcript-file", str(outside)]), 0)
        root_doc = self.parsed / "notes.docx.md"; root_doc.write_text("# SOURCE: notes.docx\n")
        self.assertEqual(V.main(["assemble", "--probe", str(self.work / self.slug / "probe.json"),
                                 "--render-dir", str(self.rd), "--results", str(self.results),
                                 "--parsed", str(self.parsed)]), 0)
        self.assertTrue(root_doc.exists())
        man = json.loads((self.parsed / "manifest.json").read_text())
        self.assertEqual([e["source"] for e in man], [self.rel])
        self.assertEqual(man[0]["inputs"], [self.rel])
        self.assertIn("Wrapping up now.", (self.parsed / V.doc_name(self.rel)).read_text())

    def test_docx_doc_name_matches_parse_corpus(self):
        rel = "rec/Acme_ Roadmap.docx"
        self.assertEqual(V.doc_name(rel), rel.replace("/", "__") + ".md")
        self.assertEqual(V.doc_name(rel), "rec__Acme_ Roadmap.docx.md")

    def test_assemble_consumes_the_docx(self):
        stale = self.parsed / "rec__Acme_ Roadmap.docx.md"
        stale.write_text("# SOURCE: rec/Acme_ Roadmap.docx\n# method: soffice+pymupdf\n")
        self.assertEqual(V.main(["assemble", "--probe", str(self.work / self.slug / "probe.json"),
                                 "--render-dir", str(self.rd), "--results", str(self.results),
                                 "--parsed", str(self.parsed)]), 0)
        md = (self.parsed / V.doc_name(self.rel)).read_text()
        self.assertIn("# method: video-lane (transcript: sidecar)\n", md)
        heads = [l for l in md.splitlines() if l.startswith("## ")]
        self.assertEqual(heads, ["## 00:00:13 — Dana Rivers (Acme Corp) (cue 1)",
                                 "## 00:00:30 · Roadmap (frame p01)",
                                 "## 00:01:05 — Lee Park1 (cue 2)",
                                 "## 01:00:11 — Dana Rivers (Acme Corp) (cue 3)"])
        self.assertNotIn("576072", md)
        self.assertFalse(stale.exists())
        man = {e["source"]: e for e in json.loads((self.parsed / "manifest.json").read_text())}
        self.assertEqual(man[self.rel]["inputs"], [self.rel, "rec/Acme_ Roadmap.docx"])
        self.assertEqual(man["rec/Acme_ Roadmap.docx"], {"source": "rec/Acme_ Roadmap.docx", "skipped": True,
                                                         "method": "consumed-by-video", "consumed_by": self.rel})
        # and a later parse_corpus pass keeps it consumed (never re-parses it via soffice)
        parse_corpus.main(["--corpus", str(self.corpus), "--out", str(self.parsed), "--formats", "docx"])
        man = {e["source"]: e for e in json.loads((self.parsed / "manifest.json").read_text())}
        self.assertEqual(man["rec/Acme_ Roadmap.docx"]["method"], "consumed-by-video")
        self.assertFalse(stale.exists())
