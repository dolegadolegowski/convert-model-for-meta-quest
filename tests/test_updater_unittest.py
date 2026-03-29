from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib import error as urlerror

from quest_model_optimizer.updater import (
    UpdateInfo,
    check_for_updates,
    install_update,
    is_newer_version,
    normalize_version_text,
)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._buffer = io.BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, *_args, **_kwargs) -> bytes:
        if _args:
            return self._buffer.read(_args[0])
        return self._buffer.read()


class UpdaterTests(unittest.TestCase):
    def test_normalize_version_text(self) -> None:
        self.assertEqual(normalize_version_text("v0.44"), "0.44")
        self.assertEqual(normalize_version_text(" 1.2.3 "), "1.2.3")
        self.assertEqual(normalize_version_text("1.2rc1"), "1.2")
        self.assertIsNone(normalize_version_text("release-candidate"))

    def test_is_newer_version(self) -> None:
        self.assertTrue(is_newer_version("0.44", "0.43"))
        self.assertFalse(is_newer_version("0.43", "0.43"))
        self.assertFalse(is_newer_version("0.42", "0.43"))

    def test_check_for_updates_parses_latest_release(self) -> None:
        payload = b"""{
          "tag_name": "v0.44",
          "name": "v0.44",
          "html_url": "https://github.com/org/repo/releases/tag/v0.44",
          "assets": [
            {"name": "something.txt", "browser_download_url": "https://example.org/ignore.txt"},
            {"name": "Remote3Dworker-worker-v0.44.zip", "browser_download_url": "https://example.org/worker.zip"}
          ]
        }"""
        with mock.patch("quest_model_optimizer.updater.request.urlopen", return_value=_FakeResponse(payload)):
            info = check_for_updates(current_version="0.43", repo_full_name="org/repo")
        self.assertTrue(info.available)
        self.assertEqual(info.latest_version, "0.44")
        self.assertEqual(info.download_url, "https://example.org/worker.zip")

    def test_check_for_updates_handles_no_release_404_without_error(self) -> None:
        http_error = urlerror.HTTPError(
            url="https://api.github.com/repos/org/repo/releases/latest",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b"{}"),
        )
        with mock.patch("quest_model_optimizer.updater.request.urlopen", side_effect=http_error):
            info = check_for_updates(current_version="0.45", repo_full_name="org/repo")
        self.assertFalse(info.available)
        self.assertIsNone(info.error)
        self.assertIn("No published GitHub release", str(info.status_message))

    def test_install_update_zip_mode_preserves_local_runtime_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_root = root / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            (project_root / "VERSION").write_text("0.43", encoding="utf-8")
            (project_root / ".venv").mkdir(parents=True, exist_ok=True)
            (project_root / ".venv" / "keep.txt").write_text("keep", encoding="utf-8")
            (project_root / "worker_runtime").mkdir(parents=True, exist_ok=True)
            (project_root / "worker_runtime" / "data.txt").write_text("runtime", encoding="utf-8")

            archive_buffer = io.BytesIO()
            with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("repo/VERSION", "0.44")
                zf.writestr("repo/README.md", "updated")
                zf.writestr("repo/.venv/override.txt", "must-not-overwrite")
                zf.writestr("repo/worker_runtime/override.txt", "must-not-overwrite")
            archive_buffer.seek(0)

            info = UpdateInfo(
                current_version="0.43",
                latest_version="0.44",
                available=True,
                html_url="https://github.com/example/release",
                download_url="https://example.org/release.zip",
                release_name="v0.44",
                error=None,
            )

            with mock.patch("quest_model_optimizer.updater.request.urlopen", return_value=_FakeResponse(archive_buffer.read())):
                result = install_update(
                    project_root=project_root,
                    update_info=info,
                    read_version_fn=lambda: (project_root / "VERSION").read_text(encoding="utf-8").strip(),
                )

            self.assertTrue(result.ok)
            self.assertEqual((project_root / "VERSION").read_text(encoding="utf-8").strip(), "0.44")
            self.assertEqual((project_root / ".venv" / "keep.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual((project_root / "worker_runtime" / "data.txt").read_text(encoding="utf-8"), "runtime")

    def test_install_update_uses_git_mode_when_repo_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / ".git").mkdir(parents=True, exist_ok=True)

            version_state = {"value": "0.43"}

            def read_version_fn() -> str:
                return version_state["value"]

            def fake_git_install(_project_root: Path) -> None:
                version_state["value"] = "0.44"

            info = UpdateInfo(
                current_version="0.43",
                latest_version="0.44",
                available=True,
                html_url=None,
                download_url=None,
                release_name="v0.44",
                error=None,
            )
            with mock.patch("quest_model_optimizer.updater.shutil.which", return_value="/usr/bin/git"):
                with mock.patch("quest_model_optimizer.updater._install_via_git", side_effect=fake_git_install) as git_mock:
                    result = install_update(
                        project_root=project_root,
                        update_info=info,
                        read_version_fn=read_version_fn,
                    )

            self.assertTrue(result.ok)
            self.assertEqual(result.mode, "git")
            self.assertEqual(result.previous_version, "0.43")
            self.assertEqual(result.installed_version, "0.44")
            git_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
