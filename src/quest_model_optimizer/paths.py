"""Path and naming helpers for outputs and reports."""

from __future__ import annotations

from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_filename_for(input_path: Path, suffix: str = "_optimized", ext: str = ".glb") -> str:
    stem = input_path.stem
    return f"{stem}{suffix}{ext}"
