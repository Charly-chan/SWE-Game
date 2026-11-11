


from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evalsys.render.imagediff import (
    DEFAULT_THRESHOLD,
    MIN_DIFFERING_PIXELS,
    content_bbox,
    diff_fraction,
    distinguishable,
    fingerprint,
    load,
    null_control_delta,
)

W, H = 1152, 648


def background(seed: int = 0) -> np.ndarray:


    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:, :] = (18, 20, 28)
    img[H // 2 :, :] = (24, 26, 36)
    for _ in range(40):
        y = int(rng.integers(0, H - 8))
        x = int(rng.integers(0, W - 8))
        img[y : y + 6, x : x + 6] = (60, 70, 90)
    return img


def stamp_text(img: np.ndarray, top: int, left: int, word: str) -> np.ndarray:


    out = img.copy()
    for i, ch in enumerate(word):
        col = left + i * 7
        bits = (ord(ch) * 2654435761) & 0xFFFFFFFF
        for r in range(7):
            for c in range(5):
                if (bits >> ((r * 5 + c) % 31)) & 1:
                    out[top + r, col + c] = (240, 240, 240)
    return out


def hud_panel(img: np.ndarray, value: int) -> np.ndarray:

    out = img.copy()
    out[24:144, 24:324] = (40, 44, 60)
    out[26:142, 26:322] = (12, 14, 20)
    return stamp_text(out, 60, 48, f"SCORE {value:05d}")


class TestTextSizedDifference(unittest.TestCase):


    def setUp(self) -> None:
        base = background(1)
        self.a = stamp_text(base, 300, 480, "DEFEATED STAGE 1")
        self.b = stamp_text(base, 300, 480, "DEFEATED STAGE 3")

    def test_diff_fraction_is_nonzero(self) -> None:
        d = diff_fraction(self.a, self.b)
        self.assertTrue(d.comparable)
        self.assertGreater(d.differing_pixels, 0)
        self.assertGreater(d.frame_fraction, 0.0)

    def test_they_are_distinguishable(self) -> None:
        for metric in ("bbox", "frame"):
            with self.subTest(metric=metric):
                r = distinguishable(
                    self.a, self.b, threshold=DEFAULT_THRESHOLD, metric=metric
                )
                self.assertTrue(
                    r.distinguishable,
                    f"two frames differing only in text read as the same frame: "
                    f"{r.detail}",
                )

    def test_the_difference_is_genuinely_small(self) -> None:


        d = diff_fraction(self.a, self.b)
        self.assertLess(d.frame_fraction, 0.001)
        self.assertLess(d.differing_pixels, 100)
        self.assertGreaterEqual(d.differing_pixels, MIN_DIFFERING_PIXELS)

    def test_a_downsampled_fingerprint_would_have_missed_it(self) -> None:


        fa = fingerprint(self.a, size=32)
        fb = fingerprint(self.b, size=32)
        coarse = float(np.abs(fa - fb).mean())
        self.assertLess(coarse, 1.0)
        self.assertTrue(distinguishable(self.a, self.b).distinguishable)


class TestPixelFloor(unittest.TestCase):
    def test_a_handful_of_pixels_is_not_a_difference(self) -> None:
        a = background(11)
        b = a.copy()
        b[10:12, 10:12] = (255, 255, 255)
        r = distinguishable(a, b)
        self.assertFalse(r.distinguishable)
        self.assertIn("below the pixel floor", r.detail)

    def test_the_floor_is_the_only_thing_stopping_it(self) -> None:


        a = background(11)
        b = a.copy()
        b[10:12, 10:12] = (255, 255, 255)
        self.assertTrue(distinguishable(a, b, min_pixels=1).distinguishable)


class TestNullControl(unittest.TestCase):
    def test_identical_images_are_zero(self) -> None:
        a = background(2)
        d = diff_fraction(a, a.copy())
        self.assertEqual(d.differing_pixels, 0)
        self.assertEqual(d.frame_fraction, 0.0)
        self.assertEqual(d.bbox_fraction, 0.0)
        self.assertFalse(distinguishable(a, a.copy()).distinguishable)

    def test_null_control_over_same_class_frames(self) -> None:

        base = stamp_text(background(3), 300, 480, "DEFEATED STAGE 1")
        frames = []
        for k in range(3):
            f = base.copy()
            f[500 + k : 504 + k, 700:704] = (200, 120, 60)
            frames.append(f)
        nc = null_control_delta(frames)
        self.assertTrue(nc.usable)
        self.assertEqual(nc.n_pairs, 3)
        self.assertGreater(nc.max_delta, 0.0)
        self.assertLess(nc.max_delta, 0.01)

    def test_a_difference_below_the_null_control_is_not_a_difference(self) -> None:

        base = stamp_text(background(4), 300, 480, "DEFEATED STAGE 1")
        jitter_a = base.copy()
        jitter_a[500:520, 700:720] = (200, 120, 60)
        jitter_b = base.copy()
        jitter_b[501:521, 701:721] = (200, 120, 60)

        nc = null_control_delta([jitter_a, jitter_b])


        r = distinguishable(jitter_a, jitter_b, null_control=nc.max_delta)
        self.assertGreater(r.value, 0.0)
        self.assertFalse(r.distinguishable, r.detail)

        bare = distinguishable(jitter_a, jitter_b)
        self.assertIn("NO NULL CONTROL", bare.detail)


class TestBBoxVersusFrame(unittest.TestCase):
    def test_content_bbox_finds_the_panel(self) -> None:
        img = np.zeros((H, W, 3), dtype=np.uint8)
        img[:, :] = (10, 10, 10)
        img[100:220, 50:350] = (200, 200, 200)
        box = content_bbox(img)
        self.assertEqual((box.top, box.left, box.bottom, box.right), (100, 50, 220, 350))

    def test_concentrated_content_scores_higher_in_its_own_box(self) -> None:


        flat = np.full((H, W, 3), (10, 10, 10), dtype=np.uint8)
        a = hud_panel(flat, 100)
        b = hud_panel(flat, 999)
        d = diff_fraction(a, b)
        self.assertTrue(d.comparable)
        self.assertGreater(d.bbox_fraction, d.frame_fraction * 5)
        self.assertIsNotNone(d.bbox)
        assert d.bbox is not None
        self.assertLess(d.bbox.area, W * H)

    def test_full_frame_change_scores_the_same_both_ways(self) -> None:

        a = background(5)
        b = (a.astype(np.int16) + 60).clip(0, 255).astype(np.uint8)
        d = diff_fraction(a, b)
        self.assertAlmostEqual(d.frame_fraction, 1.0, places=6)
        self.assertAlmostEqual(d.bbox_fraction, 1.0, places=6)


class TestIncomparable(unittest.TestCase):
    def test_mismatched_sizes_do_not_crash(self) -> None:
        a = background(6)
        b = background(6)[:400, :800]
        d = diff_fraction(a, b)
        self.assertFalse(d.comparable)
        self.assertIn("incomparable", d.detail)
        self.assertEqual(d.size_a, (W, H))
        self.assertEqual(d.size_b, (800, 400))

    def test_distinguishable_reports_incomparable_rather_than_true(self) -> None:
        a = background(7)
        b = background(7)[:400, :800]
        r = distinguishable(a, b)
        self.assertFalse(r.comparable)
        self.assertFalse(r.distinguishable)

    def test_null_control_with_no_comparable_pair_is_unusable(self) -> None:
        a = background(8)
        b = background(8)[:400, :800]
        nc = null_control_delta([a, b])
        self.assertFalse(nc.usable)
        self.assertEqual(nc.incomparable, 1)


class TestOnDisk(unittest.TestCase):
    def test_round_trip_through_png(self) -> None:

        base = background(9)
        a = stamp_text(base, 300, 480, "DEFEATED STAGE 1")
        b = stamp_text(base, 300, 480, "DEFEATED STAGE 3")
        with tempfile.TemporaryDirectory() as td:
            pa = os.path.join(td, "a.png")
            pb = os.path.join(td, "b.png")
            Image.fromarray(a).save(pa)
            Image.fromarray(b).save(pb)
            on_disk = diff_fraction(pa, pb)
            in_memory = diff_fraction(a, b)
            self.assertEqual(on_disk.differing_pixels, in_memory.differing_pixels)
            self.assertEqual(load(pa).shape, (H, W, 3))
            self.assertTrue(distinguishable(pa, pb).distinguishable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
