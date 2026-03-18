"""Data models used by remote worker components."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class WorkerSession:
    worker_id: str
    heartbeat_interval: int = 15


@dataclass
class JobClaim:
    job_id: str
    input_filename: str
    download_url: str | None
    payload: dict[str, Any]
    lease_token: str | None = None


@dataclass
class ProcessingOutcome:
    success: bool
    output_path: Path
    report_path: Path
    report: dict[str, Any]
    returncode: int
    error: str | None = None
