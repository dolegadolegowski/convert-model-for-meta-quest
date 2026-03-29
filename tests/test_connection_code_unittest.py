from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from quest_model_optimizer.connection_code import (
    ConnectionCodeError,
    connect_button_state,
    decode_connection_code,
    encode_connection_code,
)


class ConnectionCodeTests(unittest.TestCase):
    def test_valid_code_roundtrip(self) -> None:
        shared_secret = "unit-test-secret"
        payload = {
            "server_url": "https://medical.example.com",
            "worker_token": "token-123",
            "worker_name": "Local Worker",
            "runtime_config": {"poll_wait_seconds": 30},
        }
        code = encode_connection_code(payload, shared_secret=shared_secret)
        decoded = decode_connection_code(code, shared_secret=shared_secret)
        self.assertEqual(decoded["server_url"], payload["server_url"])
        self.assertEqual(decoded["worker_token"], payload["worker_token"])
        self.assertEqual(decoded["worker_name"], payload["worker_name"])

    def test_invalid_code_missing_required_field(self) -> None:
        shared_secret = "unit-test-secret"
        payload = {
            "server_url": "https://medical.example.com",
            "worker_token": "token-123",
        }
        code = encode_connection_code(payload, shared_secret=shared_secret)
        with self.assertRaises(ConnectionCodeError):
            decode_connection_code(code, shared_secret=shared_secret)

    def test_decode_uses_compat_secret_when_env_missing(self) -> None:
        shared_secret = "medical3d-worker-code-v1"
        payload = {
            "server_url": "https://medical.example.com",
            "worker_token": "token-123",
            "worker_name": "Local Worker",
        }
        code = encode_connection_code(payload, shared_secret=shared_secret)
        with mock.patch.dict("os.environ", {"CMQ_CONNECTION_CODE_SECRET": "", "WORKER_CONNECTION_CODE_SHARED_SECRET": ""}):
            decoded = decode_connection_code(code, shared_secret=None)
            self.assertEqual(decoded["server_url"], payload["server_url"])

    def test_decode_custom_secret_still_requires_matching_configuration(self) -> None:
        shared_secret = "unit-test-secret"
        payload = {
            "server_url": "https://medical.example.com",
            "worker_token": "token-123",
            "worker_name": "Local Worker",
        }
        code = encode_connection_code(payload, shared_secret=shared_secret)
        with mock.patch.dict("os.environ", {"CMQ_CONNECTION_CODE_SECRET": "", "WORKER_CONNECTION_CODE_SHARED_SECRET": ""}):
            with self.assertRaises(ConnectionCodeError):
                decode_connection_code(code, shared_secret=None)

    def test_connect_button_state_transitions(self) -> None:
        enabled, label = connect_button_state(connected=False, config_valid=False)
        self.assertFalse(enabled)
        self.assertEqual(label, "Connect")

        enabled, label = connect_button_state(connected=False, config_valid=True)
        self.assertTrue(enabled)
        self.assertEqual(label, "Connect")

        enabled, label = connect_button_state(connected=True, config_valid=False)
        self.assertTrue(enabled)
        self.assertEqual(label, "Disconnect")

    def test_desktop_worker_includes_code_and_manual_config_flow(self) -> None:
        root = Path(__file__).resolve().parents[1]
        content = (root / "src" / "quest_model_optimizer" / "desktop_worker.py").read_text(encoding="utf-8")
        self.assertIn("Connection Code", content)
        self.assertIn("Manual Config", content)
        self.assertIn("connection_code", content)
        self.assertIn("self.connect_btn.setText(label)", content)
        self.assertIn("Check Updates", content)
        self.assertIn("Install Update", content)
        self.assertIn("hasattr(self, \"action_install_update\")", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
