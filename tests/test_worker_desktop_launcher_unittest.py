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

        self.assertIn("detect_supported_python()", content)
        self.assertIn("python_version_ok()", content)
        self.assertIn("\"$VENV_PYTHON\" scripts/worker_desktop_app.py", content)
        self.assertIn("--no-compile PySide6 keyring", content)
        self.assertIn("source \".venv/bin/activate\"", content)
        self.assertIn("sleep 0.7", content)
        self.assertIn("tell application \"Terminal\"", content)

    def test_package_worker_zip_script_exists_and_is_executable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts" / "package_worker_zip.sh"
        self.assertTrue(script.exists())

        mode = script.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR))

        content = script.read_text(encoding="utf-8")
        self.assertIn("zip -r", content)
        self.assertIn("worker_runtime/*", content)
        self.assertIn(".venv/*", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
