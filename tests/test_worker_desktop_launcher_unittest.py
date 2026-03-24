from __future__ import annotations

from pathlib import Path
import stat
import unittest


class WorkerDesktopLauncherTests(unittest.TestCase):
    def test_run_worker_command_exists_and_is_executable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = root / "Run Worker.command"
        self.assertTrue(launcher.exists())

        mode = launcher.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR))

    def test_run_worker_command_calls_desktop_worker_script(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = root / "Run Worker.command"
        content = launcher.read_text(encoding="utf-8")

        self.assertIn("python3 scripts/worker_desktop_app.py", content)
        self.assertIn("python3 -m pip install PySide6 keyring", content)
        self.assertIn("source \".venv/bin/activate\"", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
