from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from unittest import mock

from quest_model_optimizer.desktop_worker import evaluate_startup_prerequisites


def _default_args(work_dir: str, blender_exec: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        server_url="",
        token="",
        worker_name="Local Worker",
        poll_wait=30,
        max_download_bytes=1024 * 1024 * 1024,
        work_dir=work_dir,
        show_window=False,
        blender_exec=blender_exec,
        face_limit=400000,
        blender_timeout_seconds=1800,
        log_level="INFO",
    )


class DesktopWorkerPreflightTests(unittest.TestCase):
    def test_required_prerequisites_pass_with_valid_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = _default_args(work_dir=tmp_dir)
            with mock.patch("quest_model_optimizer.desktop_worker.detect_blender_executable", return_value=sys.executable):
                results = evaluate_startup_prerequisites(args)

        by_key = {item.key: item for item in results}
        self.assertIn("python_runtime", by_key)
        self.assertIn("ssl_module", by_key)
        self.assertIn("blender_executable", by_key)
        self.assertIn("work_dir_writable", by_key)
        self.assertTrue(all(item.ok for item in results if item.required), "All required checks should pass.")

    def test_blender_missing_has_install_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = _default_args(work_dir=tmp_dir, blender_exec="/definitely/missing/blender")
            with mock.patch(
                "quest_model_optimizer.desktop_worker.detect_blender_executable",
                return_value="/definitely/missing/blender",
            ):
                results = evaluate_startup_prerequisites(args)

        by_key = {item.key: item for item in results}
        blender = by_key["blender_executable"]
        self.assertFalse(blender.ok)
        self.assertTrue(blender.required)
        self.assertIn("not found", blender.details.lower())
        self.assertIn("brew install --cask blender", blender.install_hint.lower())
        self.assertIn("blender.org/download", blender.install_hint.lower())

    def test_keyring_missing_is_reported_with_english_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = _default_args(work_dir=tmp_dir)
            with mock.patch("quest_model_optimizer.desktop_worker.detect_blender_executable", return_value=sys.executable):
                with mock.patch("quest_model_optimizer.desktop_worker.importlib.util.find_spec", return_value=None):
                    results = evaluate_startup_prerequisites(args)

        by_key = {item.key: item for item in results}
        keyring = by_key["keyring_package"]
        self.assertFalse(keyring.ok)
        self.assertFalse(keyring.required)
        self.assertIn("keyring", keyring.details.lower())
        self.assertIn("python3 -m pip install keyring", keyring.install_hint.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
