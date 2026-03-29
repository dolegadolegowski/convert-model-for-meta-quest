"""Self-update helpers for desktop worker using GitHub releases."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib import error
from urllib import request


DEFAULT_GITHUB_REPO = "dolegadolegowski/Remote3Dworker"
DEFAULT_TIMEOUT_SECONDS = 20


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str | None
    available: bool
    html_url: str | None
    download_url: str | None
    release_name: str | None
    error: str | None = None
    status_message: str | None = None


@dataclass
class UpdateInstallResult:
    ok: bool
    mode: str
    previous_version: str
    installed_version: str
    message: str


def normalize_version_text(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    if text.lower().startswith("v"):
        text = text[1:].strip()
    parts = text.split(".")
    if not parts:
        return None
    normalized_parts: list[str] = []
    for part in parts:
        if part.isdigit():
            normalized_parts.append(str(int(part)))
            continue
        prefix_digits = ""
        for char in part:
            if char.isdigit():
                prefix_digits += char
            else:
                break
        if not prefix_digits:
            return None
        normalized_parts.append(str(int(prefix_digits)))
        break
    return ".".join(normalized_parts) if normalized_parts else None


def _version_key(version_text: str | None) -> tuple[int, ...]:
    normalized = normalize_version_text(version_text)
    if not normalized:
        return tuple()
    return tuple(int(part) for part in normalized.split("."))


def is_newer_version(latest_version: str | None, current_version: str | None) -> bool:
    latest_key = _version_key(latest_version)
    current_key = _version_key(current_version)
    if not latest_key:
        return False
    return latest_key > current_key


def _select_download_url(release_payload: dict) -> str | None:
    assets = release_payload.get("assets")
    if isinstance(assets, list):
        preferred = None
        fallback = None
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name", "")).lower()
            url = str(asset.get("browser_download_url", "")).strip()
            if not url:
                continue
            if name.endswith(".zip"):
                if fallback is None:
                    fallback = url
                if "worker" in name:
                    preferred = url
                    break
        if preferred:
            return preferred
        if fallback:
            return fallback
    zipball = str(release_payload.get("zipball_url", "")).strip()
    return zipball or None


def check_for_updates(
    current_version: str,
    repo_full_name: str = DEFAULT_GITHUB_REPO,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> UpdateInfo:
    api_url = f"https://api.github.com/repos/{repo_full_name}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Remote3Dworker-Updater",
    }
    req = request.Request(api_url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=max(3, int(timeout_seconds))) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        if int(getattr(exc, "code", 0)) == 404:
            return UpdateInfo(
                current_version=current_version,
                latest_version=None,
                available=False,
                html_url=None,
                download_url=None,
                release_name=None,
                error=None,
                status_message="No published GitHub release found for this repository yet.",
            )
        return UpdateInfo(
            current_version=current_version,
            latest_version=None,
            available=False,
            html_url=None,
            download_url=None,
            release_name=None,
            error=f"HTTP {exc.code}: {exc.reason}",
            status_message=None,
        )
    except Exception as exc:
        return UpdateInfo(
            current_version=current_version,
            latest_version=None,
            available=False,
            html_url=None,
            download_url=None,
            release_name=None,
            error=str(exc),
            status_message=None,
        )

    latest_version = normalize_version_text(payload.get("tag_name") or payload.get("name"))
    html_url = str(payload.get("html_url", "")).strip() or None
    release_name = str(payload.get("name", "")).strip() or latest_version
    download_url = _select_download_url(payload)
    if not latest_version:
        return UpdateInfo(
            current_version=current_version,
            latest_version=None,
            available=False,
            html_url=html_url,
            download_url=download_url,
            release_name=release_name,
            error=None,
            status_message="Latest release exists but has an unrecognized version tag format.",
        )
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        available=is_newer_version(latest_version, current_version),
        html_url=html_url,
        download_url=download_url,
        release_name=release_name,
        error=None,
        status_message=None,
    )


def _find_extracted_root(extract_dir: Path) -> Path:
    entries = [entry for entry in extract_dir.iterdir() if entry.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extract_dir


def _sync_project_tree(source_root: Path, project_root: Path) -> None:
    exclude_names = {
        ".git",
        ".venv",
        "worker_runtime",
        "dist",
        "__pycache__",
        ".pytest_cache",
    }
    for item in source_root.iterdir():
        if item.name in exclude_names:
            continue
        target = project_root / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _install_via_git(project_root: Path) -> None:
    subprocess.run(
        ["git", "-C", str(project_root), "pull", "--ff-only"],
        check=True,
        capture_output=True,
        text=True,
    )


def _install_via_zip(project_root: Path, download_url: str, timeout_seconds: int) -> None:
    req = request.Request(
        download_url,
        headers={"User-Agent": "Remote3Dworker-Updater"},
        method="GET",
    )
    with tempfile.TemporaryDirectory(prefix="cmq-update-") as tmp:
        tmp_dir = Path(tmp)
        archive_path = tmp_dir / "update.zip"
        with request.urlopen(req, timeout=max(5, int(timeout_seconds))) as resp, archive_path.open("wb") as handle:
            shutil.copyfileobj(resp, handle)
        extract_dir = tmp_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(extract_dir)
        source_root = _find_extracted_root(extract_dir)
        _sync_project_tree(source_root=source_root, project_root=project_root)


def install_update(
    project_root: Path,
    update_info: UpdateInfo,
    read_version_fn: Callable[[], str],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> UpdateInstallResult:
    previous_version = str(read_version_fn()).strip()
    git_repo = (project_root / ".git").exists() and shutil.which("git")
    mode = "git" if git_repo else "zip"

    try:
        if mode == "git":
            _install_via_git(project_root)
        else:
            if not update_info.download_url:
                return UpdateInstallResult(
                    ok=False,
                    mode=mode,
                    previous_version=previous_version,
                    installed_version=previous_version,
                    message="No download URL found for latest release.",
                )
            _install_via_zip(project_root=project_root, download_url=update_info.download_url, timeout_seconds=timeout_seconds)
    except Exception as exc:
        return UpdateInstallResult(
            ok=False,
            mode=mode,
            previous_version=previous_version,
            installed_version=previous_version,
            message=str(exc),
        )

    installed_version = str(read_version_fn()).strip()
    if installed_version == previous_version:
        return UpdateInstallResult(
            ok=False,
            mode=mode,
            previous_version=previous_version,
            installed_version=installed_version,
            message="Update did not change local version.",
        )
    return UpdateInstallResult(
        ok=True,
        mode=mode,
        previous_version=previous_version,
        installed_version=installed_version,
        message=f"Updated from {previous_version} to {installed_version} via {mode}.",
    )
