#!/usr/bin/env python3
"""Desktop launcher for Remote3Dworker."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quest_model_optimizer.env_loader import load_local_env_file  # noqa: E402


_loaded_env_path = load_local_env_file(PROJECT_ROOT)
if _loaded_env_path is not None:
    print(f"[setup] Loaded local env from {_loaded_env_path}")


def _prepare_qt_platform_plugins() -> None:
    """Work around Qt cocoa plugin discovery issues on macOS iCloud paths.

    Some macOS environments fail to load `libqcocoa.dylib` when PySide6 lives under
    iCloud-managed paths containing spaces/special segments (for example
    `Mobile Documents/com~apple~CloudDocs/...`).

    We mirror platform plugins into a stable temp directory and point Qt there.
    """

    if platform.system() != "Darwin":
        return
    if str(os.getenv("QT_QPA_PLATFORM_PLUGIN_PATH", "")).strip():
        return

    try:
        import PySide6  # type: ignore
    except Exception:
        return

    source_dir = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins" / "platforms"
    if not source_dir.exists() or not source_dir.is_dir():
        return

    _ = hashlib.sha256(str(source_dir).encode("utf-8", errors="ignore")).hexdigest()[:12]
    target_root = Path("/tmp") / "cmq-qt-plugins"
    target_dir = target_root / "platforms"
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        for stale in target_dir.iterdir():
            if stale.is_file():
                stale.unlink(missing_ok=True)
        for item in source_dir.iterdir():
            if not item.is_file():
                continue
            destination = target_dir / item.name
            shutil.copyfile(item, destination)
            destination.chmod(0o755)
    except Exception:
        return

    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(target_dir)


_prepare_qt_platform_plugins()

from quest_model_optimizer.desktop_worker import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
