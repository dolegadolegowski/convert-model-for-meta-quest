"""Security checks for downloaded worker input files."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 64)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def validate_download_file(path: Path, expected_sha256: str | None, max_bytes: int) -> None:
    size = path.stat().st_size
    if size > max_bytes:
        raise RuntimeError(f"downloaded file exceeds limit: {size} > {max_bytes} bytes")

    if expected_sha256:
        actual = compute_sha256(path)
        if actual.lower() != expected_sha256.lower():
            raise RuntimeError(
                "downloaded file sha256 mismatch "
                f"expected={expected_sha256.lower()} actual={actual.lower()}"
            )
