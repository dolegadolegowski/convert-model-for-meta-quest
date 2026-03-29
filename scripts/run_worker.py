#!/usr/bin/env python3
"""Executable wrapper for remote worker app."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quest_model_optimizer.env_loader import load_local_env_file  # noqa: E402


_loaded_env_path = load_local_env_file(PROJECT_ROOT)
if _loaded_env_path is not None:
    print(f"[setup] Loaded local env from {_loaded_env_path}")

from quest_model_optimizer.worker_app import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
