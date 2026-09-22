from __future__ import annotations

import unittest

import numpy as np

import video_capture as V


def slide(bars, bg=0.9):
    f = np.full((54, 96), bg, np.float32)
    for r0, r1, c0, c1 in bars:
        f[r0:r1, c0:c1] = 0.1
    return f


A = slide([(4, 9, 8, 80), (14, 18, 8, 60)])
B = slide([(4, 9, 8, 80), (14, 18, 8, 60), (24, 28, 8, 70), (34, 38, 8, 50)])  # A + 2 bullets
C = slide([(4, 50, 10, 20), (4, 50, 40, 50), (4, 50, 70, 80)])                   # different layout


def spans_t(spans):
    return [(s["t_start"], s["t_end"], s["key"]) for s in spans]


class SpanTests(unittest.TestCase):
    def test_hard_cut_splits(self):
        self.assertEqual(spans_t(V.find_spans([A] * 10 + [C] * 10)), [(0, 10, 9), (10, 20, 19)])

    def test_slow_drift_still_cuts_against_anchor(self):
        frames = [np.full((54, 96), 0.2 + 0.01 * i, np.float32) for i in range(20)]
        self.assertGreater(len(V.find_spans(frames)), 1)

    def test_noise_is_not_a_stable_span(self):
        rng = np.random.default_rng(0)
        frames = [(0.5 + 0.03 * rng.standard_normal((54, 96))).astype(np.float32) for _ in range(10)]
        self.assertEqual(V.find_spans(frames), [])

    def test_min_hold(self):
        self.assertEqual(spans_t(V.find_spans([A] * 2 + [C] * 5)), [(2, 7, 6)])

    def test_small_build_never_cuts_and_keyframe_is_the_finished_slide(self):
        spans = V.find_spans([A] * 5 + [B] * 5)
        self.assertEqual(spans_t(spans), [(0, 10, 9)])  # key 9 is B, the complete slide


class CollapseAndDedupTests(unittest.TestCase):
    def test_build_that_cuts_collapses_to_later_frame(self):
        frames = [A] * 5 + [B] * 5 + [C] * 5
        spans = V.find_spans(frames, diff=0.05)
        self.assertEqual(len(spans), 3)
        merged = V.collapse_builds(spans, frames)
        self.assertEqual(spans_t(merged), [(0, 10, 9), (10, 15, 14)])

    def test_low_texture_slides_are_not_collapsed(self):
        dark, light = np.full((54, 96), 0.3, np.float32), np.full((54, 96), 0.7, np.float32)
        frames = [dark] * 5 + [light] * 5  # dHash 0 for both, MAD 0.4
        self.assertEqual(len(V.collapse_builds(V.find_spans(frames), frames)), 2)

    def test_revisited_slide_is_stored_once_with_every_showing(self):
        frames = [A] * 5 + [C] * 5 + [A] * 5
        kept = V.dedup(V.find_spans(frames), frames)
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[0]["shown_at"], [[0, 5], [10, 15]])

    def test_build_is_not_deduped_into_its_earlier_state(self):
        frames = [A] * 5 + [C] * 5 + [B] * 5
        self.assertEqual(len(V.dedup(V.find_spans(frames), frames)), 3)

    def test_different_solid_colours_are_not_the_same_frame(self):
        red, blue = np.full((54, 96), 0.3, np.float32), np.full((54, 96), 0.7, np.float32)
        frames = [red] * 5 + [blue] * 5
        self.assertEqual(len(V.dedup(V.find_spans(frames), frames)), 2)


class CapTests(unittest.TestCase):
    def test_rate_cap_keeps_longest_holds_in_a_minute(self):
        kept = [{"t_start": s, "t_end": s + h, "key": s + h - 1, "shown_at": [[s, s + h]]}
                for s, h in [(0, 3), (3, 9), (12, 4), (16, 20)]]
        out, dropped, capped = V.cap(kept, max_per_min=2, max_frames=300)
        self.assertEqual([q["t_start"] for q in out], [3, 16])
        self.assertEqual((dropped, capped), (2, False))

    def test_max_frames_sets_capped(self):
        kept = [{"t_start": 60 * i, "t_end": 60 * i + 5 + i, "key": 0, "shown_at": [[60 * i, 60 * i + 5 + i]]}
                for i in range(5)]
        out, dropped, capped = V.cap(kept, max_per_min=6, max_frames=2)
        self.assertEqual([q["t_start"] for q in out], [180, 240])
        self.assertTrue(capped)
