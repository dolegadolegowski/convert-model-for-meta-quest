from __future__ import annotations

import unittest
from unittest import mock

from quest_model_optimizer.worker_app import _auto_worker_id, _auto_worker_name, main


class WorkerAppTests(unittest.TestCase):
    def test_auto_worker_identity_generation(self) -> None:
        with mock.patch("quest_model_optimizer.worker_app.platform.node", return_value="MacBook-Pro"):
            worker_name = _auto_worker_name()
        self.assertEqual(worker_name, "MacBook-Pro")

        with mock.patch("quest_model_optimizer.worker_app.uuid.uuid4") as uuid_mock:
            uuid_mock.return_value.hex = "abc12345deadbeef"
            worker_id = _auto_worker_id(worker_name)
        self.assertEqual(worker_id, "worker-macbook-pro-abc12345")

    def test_no_gui_dry_run(self) -> None:
        rc = main(["--no-gui", "--dry-run", "--worker-name", "test-worker"])
        self.assertEqual(rc, 0)

    def test_minimal_cli_with_server_url_and_token_only(self) -> None:
        rc = main(
            [
                "--server-url",
                "https://example.org",
                "--token",
                "abc",
                "--no-gui",
                "--dry-run",
            ]
        )
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
