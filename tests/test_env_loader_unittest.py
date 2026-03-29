from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from quest_model_optimizer.env_loader import load_local_env_file


class EnvLoaderTests(unittest.TestCase):
    def test_loads_env_file_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".cmq_worker.env"
            env_file.write_text(
                "# comment\nCMQ_CONNECTION_CODE_SECRET=abc123\nWORKER_NAME='Desk Worker'\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                loaded_path = load_local_env_file(root)
                self.assertEqual(loaded_path, env_file.resolve())
                self.assertEqual(os.environ.get("CMQ_CONNECTION_CODE_SECRET"), "abc123")
                self.assertEqual(os.environ.get("WORKER_NAME"), "Desk Worker")

    def test_does_not_override_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".cmq_worker.env"
            env_file.write_text("CMQ_CONNECTION_CODE_SECRET=from-file\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CMQ_CONNECTION_CODE_SECRET": "from-system"}, clear=True):
                _ = load_local_env_file(root)
                self.assertEqual(os.environ.get("CMQ_CONNECTION_CODE_SECRET"), "from-system")

    def test_returns_none_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {}, clear=True):
                loaded_path = load_local_env_file(Path(tmp))
                self.assertIsNone(loaded_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
