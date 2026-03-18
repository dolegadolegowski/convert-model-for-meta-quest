from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from quest_model_optimizer.worker_security import compute_sha256, validate_download_file


class WorkerSecurityTests(unittest.TestCase):
    def test_sha256_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "mesh.obj"
            file_path.write_bytes(b"abc123")
            expected = hashlib.sha256(b"abc123").hexdigest()
            self.assertEqual(compute_sha256(file_path), expected)
            validate_download_file(file_path, expected_sha256=expected, max_bytes=100)

    def test_sha256_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "mesh.obj"
            file_path.write_bytes(b"abc123")
            with self.assertRaises(RuntimeError):
                validate_download_file(file_path, expected_sha256="deadbeef", max_bytes=100)

    def test_size_limit_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "mesh.obj"
            file_path.write_bytes(b"123456")
            with self.assertRaises(RuntimeError):
                validate_download_file(file_path, expected_sha256=None, max_bytes=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
