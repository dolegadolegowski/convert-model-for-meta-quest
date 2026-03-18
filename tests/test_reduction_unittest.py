"""Stdlib unittest coverage for reduction helpers."""

from __future__ import annotations

import unittest

from quest_model_optimizer.reduction import (
    compute_correction_ratio,
    compute_initial_ratio,
    compute_object_ratio_map,
    should_decimate,
)


class ReductionMathTests(unittest.TestCase):
    def test_should_decimate(self) -> None:
        self.assertFalse(should_decimate(300000, 300000))
        self.assertTrue(should_decimate(300001, 300000))

    def test_compute_initial_ratio(self) -> None:
        ratio = compute_initial_ratio(600000, 300000)
        self.assertGreater(ratio, 0.4)
        self.assertLess(ratio, 0.6)

    def test_compute_correction_ratio(self) -> None:
        ratio = compute_correction_ratio(330000, 300000)
        self.assertGreater(ratio, 0.8)
        self.assertLess(ratio, 1.0)

    def test_object_ratio_map_preserves_small_objects(self) -> None:
        ratio_map = compute_object_ratio_map(
            face_counts={"small": 100, "large": 10000},
            target_total_faces=5000,
            min_object_faces_for_decimate=1000,
        )
        self.assertEqual(ratio_map["small"], 1.0)
        self.assertGreater(ratio_map["large"], 0.1)
        self.assertLess(ratio_map["large"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
