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

    def test_legacy_cli_flags_are_accepted(self) -> None:
        rc = main(
            [
                "--server-url",
                "https://example.org",
                "--token",
                "abc",
                "--worker-id",
                "worker-legacy-1",
                "--worker-name",
                "Legacy Worker",
                "--claim-wait",
                "30",
                "--heartbeat-interval",
                "10",
                "--lease-timeout",
                "120",
                "--gui",
                "--dry-run",
            ]
        )
        self.assertEqual(rc, 0)

    def test_timeout_flags_are_accepted(self) -> None:
        rc = main(
            [
                "--server-url",
                "https://example.org",
                "--token",
                "abc",
                "--worker-name",
                "Timeout Worker",
                "--http-timeout-seconds",
                "75",
                "--download-timeout-seconds",
                "240",
                "--upload-timeout-seconds",
                "900",
                "--no-gui",
                "--dry-run",
            ]
        )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
