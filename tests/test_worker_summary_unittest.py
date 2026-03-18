from __future__ import annotations

import unittest

from quest_model_optimizer.worker_summary import build_geometry_summary


class WorkerSummaryTests(unittest.TestCase):
    def test_decimate_summary(self) -> None:
        report = {
            "faces_before": 559697,
            "faces_final": 298151,
            "decimate": {"applied": True},
        }
        text = build_geometry_summary("HOL.obj", report)
        self.assertEqual(text, "HOL.obj: 559697 -> 298151 (decimate)")

    def test_no_decimate_summary(self) -> None:
        report = {
            "faces_before": 132,
            "faces_final": 132,
            "decimate": {"applied": False},
        }
        text = build_geometry_summary("kosz.obj", report)
        self.assertEqual(text, "kosz.obj: 132 -> 132 (no-decimate)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
