from __future__ import annotations

import unittest

from quest_model_optimizer.worker_app import main


class WorkerAppTests(unittest.TestCase):
    def test_no_gui_dry_run(self) -> None:
        rc = main(["--no-gui", "--dry-run", "--worker-name", "test-worker"])
        self.assertEqual(rc, 0)

    def test_http_requires_override(self) -> None:
        rc = main(
            [
                "--no-gui",
                "--server-url",
                "http://localhost:9999",
                "--token",
                "abc",
                "--once",
            ]
        )
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
