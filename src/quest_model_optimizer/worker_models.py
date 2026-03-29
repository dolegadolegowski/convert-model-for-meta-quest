"""Data models used by remote worker components."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WorkerSession:
    worker_id: str
    heartbeat_interval: int = 15
    runtime_config: dict[str, int] | None = None


@dataclass
class JobClaim:
    job_id: str
    input_filename: str
    download_url: str | None
    payload: dict[str, Any]
    lease_token: str | None = None


@dataclass(frozen=True)
class JobTask:
    task_type: str = "convert"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessingOutcome:
    success: bool
    output_path: Path
    report_path: Path
    report: dict[str, Any]
    returncode: int
    error: str | None = None
