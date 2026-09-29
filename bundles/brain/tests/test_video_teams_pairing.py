"""Teams names a recording `<Meeting>-YYYYMMDD_HHMM[SS][UTC]-Meeting Recording.mp4` and its
transcript `<Meeting>.vtt` (or .srt/.docx). Neither the same-stem rule nor the docx-title rule
pairs them, so probe fell back to ASR and the doctor reported every such video as
transcript-less. find_sidecar's third rule strips that suffix; brain_doctor mirrors it."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _tools
import brain_doctor as D
import video_capture as V

REC = "Weekly Ops Sync-20260925_1429UTC-Meeting Recording"


class MeetingStemTests(unittest.TestCase):
    def test_strips_the_teams_recording_suffix(self):
        self.assertEqual(V.meeting_stem(REC), "Weekly Ops Sync")
        self.assertEqual(V.meeting_stem("Plan-20260105_150400-Meeting Recording"), "Plan")
        self.assertEqual(V.meeting_stem("Plan-20260105_1504UTC-meeting recording"), "Plan")

    def test_non_teams_names_have_no_meeting_stem(self):
        for s in ("standup", "Plan-Meeting Recording", "Plan-2026-01-05-Meeting Recording",
                  "-20260105_1504UTC-Meeting Recording"):
            self.assertIsNone(V.meeting_stem(s), s)

    def test_doctor_copy_of_the_pattern_is_identical(self):
        self.assertEqual(D._TEAMS_REC.pattern, V._TEAMS_REC.pattern)
        self.assertEqual(D._TEAMS_REC.flags, V._TEAMS_REC.flags)


class FindSidecarTeamsTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.d = Path(self.t.name)
        self.video = self.d / f"{REC}.mp4"; self.video.write_bytes(b"v")

    def tearDown(self):
        self.t.cleanup()

    def _both(self):
        try:
            vc = V.find_sidecar(str(self.video))
            vc = Path(vc).name if vc else None
        except ValueError:
            vc = "ambiguous"
        doc = D.sidecar_of(self.video)
        self.assertEqual(doc.name if doc else None, None if vc == "ambiguous" else vc)
        return vc

    def test_meeting_named_vtt_is_paired(self):
        (self.d / "Weekly Ops Sync.vtt").write_text("WEBVTT\n")
        self.assertEqual(self._both(), "Weekly Ops Sync.vtt")

    def test_priority_vtt_then_srt_then_teams_docx(self):
        _tools.make_teams_docx(self.d / "Weekly Ops Sync.docx", "Weekly Ops Sync", "5m",
                               [("Dana Rivers", "0:05", "hello")])
        self.assertEqual(self._both(), "Weekly Ops Sync.docx")
        (self.d / "Weekly Ops Sync.SRT").write_text("")
        self.assertEqual(self._both(), "Weekly Ops Sync.SRT")

    def test_same_name_docx_that_is_not_a_transcript_is_skipped(self):
        _tools.make_teams_docx(self.d / "Weekly Ops Sync.docx", "Agenda", "x", [])
        self.assertIsNone(self._both())

    def test_exact_stem_still_wins(self):
        (self.d / "Weekly Ops Sync.vtt").write_text("WEBVTT\n")
        (self.d / f"{REC}.vtt").write_text("WEBVTT\n")
        self.assertEqual(self._both(), f"{REC}.vtt")

    def test_two_recordings_of_one_meeting_refuse_instead_of_sharing_a_transcript(self):
        (self.d / "Weekly Ops Sync.vtt").write_text("WEBVTT\n")
        (self.d / "Weekly Ops Sync-20261002_1430UTC-Meeting Recording.mp4").write_bytes(b"v")
        self.assertEqual(self._both(), "ambiguous")
        self.assertEqual(D.meeting_claims(self.video),
                         [f"{REC}.mp4", "Weekly Ops Sync-20261002_1430UTC-Meeting Recording.mp4"])

    def test_doctor_names_the_shared_transcript_conflict(self):
        from unittest.mock import patch
        (self.d / "Weekly Ops Sync.vtt").write_text("WEBVTT\n")
        other = self.d / "Weekly Ops Sync-20261002_1430UTC-Meeting Recording.mp4"; other.write_bytes(b"v")
        with patch.object(D, "check_python_deps", return_value=(True, "ok")), \
             patch.object(D, "check_sqlite_vec", return_value=(True, "ok")):
            checks = D.run_checks({"files": [self.video, other]})
        w = next(c for c in checks if c["name"] == "whisper-cli")
        self.assertIn("share one meeting transcript", w["detail"])
        self.assertIn("2 of 2 video(s) have no transcript", w["why"])

    def test_no_meeting_transcript_is_none(self):
        (self.d / "Other meeting.vtt").write_text("WEBVTT\n")
        self.assertIsNone(self._both())


class RealTeamsShapesTests(unittest.TestCase):
    """Real naming shapes from one Teams tenant (file names only; bodies are synthetic)."""

    TURNS = [("Dana Rivers", "0:05", "hello")]

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.d = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def _pair(self, video, *others):
        v = self.d / video; v.write_bytes(b"v")
        for o in others:
            if o.endswith(".docx"):
                _tools.make_teams_docx(self.d / o, "Some meeting title", "5m", self.TURNS)
            else:
                (self.d / o).write_text("WEBVTT\n")
        try:
            vc = V.find_sidecar(str(v))
            vc = Path(vc).name if vc else None
        except ValueError:
            vc = "ambiguous"
        doc = D.sidecar_of(v)
        self.assertEqual(doc.name if doc else None, None if vc == "ambiguous" else vc)
        return vc

    def test_one_to_one_call_keeps_the_date_in_the_transcript_name(self):
        self.assertEqual(self._pair("Meeting with Alex Rivera-20260916_143505-Meeting Recording.mp4",
                                    "Meeting with Alex Rivera-20260916.vtt"),
                         "Meeting with Alex Rivera-20260916.vtt")

    def test_other_days_call_is_not_paired(self):
        self.assertIsNone(self._pair("Meeting with Sam Lee-20260916_143659-Meeting Recording.mp4",
                                     "Meeting with Alex Rivera-20260916.vtt"))

    def test_punctuation_sanitised_differently(self):
        cases = [
            ("Platform Session HandheldsMobileApp-20260928_135522UTC-Meeting Recording.mp4",
             "Platform Session_ Handhelds_MobileApp.vtt"),
            ("Acme   Ride a long-20260917_102257UTC-Meeting Recording.mp4", "Acme __  Ride a long.vtt"),
            ("Sep 25 Acme Pathfinder\u200b - Weekly status report meeting-20260925_142930UTC-Meeting Recording.mp4",
             "Sep 25 Acme Pathfinder_ - Weekly status report meeting.vtt"),
            ("Discovery Session wVendor  Delivery-20260914_162508UTC-Meeting Recording.mp4",
             "Discovery Session w_Vendor _ Delivery .docx"),
        ]
        for video, transcript in cases:
            with self.subTest(video=video):
                for f in self.d.iterdir():
                    f.unlink()
                self.assertEqual(self._pair(video, transcript), transcript)

    def test_a_date_decorated_neighbour_does_not_steal_or_block_the_pairing(self):
        # the date prefix is not stripped: "Sep 18 …" is another week's meeting
        self.assertEqual(self._pair("Sep 25 Acme Pathfinder - Weekly status report meeting-20260925_142930UTC"
                                    "-Meeting Recording.mp4",
                                    "Sep 18 Acme Pathfinder_ - Weekly status report meeting.vtt",
                                    "Sep 25 Acme Pathfinder_ - Weekly status report meeting.vtt"),
                         "Sep 25 Acme Pathfinder_ - Weekly status report meeting.vtt")

    def test_two_normalised_matches_of_one_kind_refuse(self):
        self.assertEqual(self._pair("Acme   Ride a long-20260917_102257UTC-Meeting Recording.mp4",
                                    "Acme __  Ride a long.vtt", "Acme _ Ride a long.vtt"), "ambiguous")

    def test_duplicate_title_claims_are_settled_by_the_matching_name(self):
        rec = "Discovery Session wVendor  Billing-20260914_150212-Meeting Recording"
        v = self.d / f"{rec}.mp4"; v.write_bytes(b"v")
        for n in ("Discovery Session w_Vendor _ Billing.docx", "Sep 14 Discovery Session w_Vendor _ Billing.docx"):
            _tools.make_teams_docx(self.d / n, rec, "5m", self.TURNS)
        self.assertEqual(Path(V.find_sidecar(str(v))).name, "Discovery Session w_Vendor _ Billing.docx")
        self.assertEqual(D.sidecar_of(v).name, "Discovery Session w_Vendor _ Billing.docx")

    def test_duplicate_title_claims_with_no_name_match_still_refuse(self):
        rec = "Overview  Service Desk-20260924_090526-Meeting Recording"
        v = self.d / f"{rec}.mp4"; v.write_bytes(b"v")
        for n in ("Overview _ Service Desk Sept 24.docx", "Overview copy.docx"):
            _tools.make_teams_docx(self.d / n, rec, "5m", self.TURNS)
        with self.assertRaises(ValueError):
            V.find_sidecar(str(v))
        self.assertIsNone(D.sidecar_of(v))

    def test_name_key_is_identical_in_both_copies(self):
        for s in ("Acme __  Ride a long", "Pathfinder\u200b - Weekly", "Ünïcode Straße", "w_Vendor _ Delivery "):
            self.assertEqual(V.name_key(s), D.name_key(s), s)
        self.assertEqual(V.name_key("Acme __  Ride a long"), V.name_key("Acme   Ride a long"))
        self.assertEqual(D._TEAMS_TIME.pattern, V._TEAMS_TIME.pattern)


if __name__ == "__main__":
    unittest.main()
