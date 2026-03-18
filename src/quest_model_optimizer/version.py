"""Project version helpers."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_version() -> str:
    version_file = project_root() / "VERSION"
    if not version_file.exists():
        return "0.0"
    return version_file.read_text(encoding="utf-8").strip()
