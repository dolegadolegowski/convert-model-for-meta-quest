"""Main worker loop with retry/backoff and event notifications."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .remote_client import RemoteWorkerClient
from .worker_models import JobClaim
from .worker_processor import PipelineProcessor
from .worker_summary import build_geometry_summary


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class WorkerObserver(Protocol):
    def set_connection_status(self, connected: bool) -> None:
        ...

    def set_last_download(self, message: str) -> None:
        ...

    def set_geometry_summary(self, message: str) -> None:
        ...

    def set_upload_status(self, message: str) -> None:
        ...


class NullWorkerObserver:
    def set_connection_status(self, connected: bool) -> None:
        return None

    def set_last_download(self, message: str) -> None:
        return None

    def set_geometry_summary(self, message: str) -> None:
        return None

    def set_upload_status(self, message: str) -> None:
        return None


@dataclass
class LoopConfig:
    poll_wait_seconds: int = 30
    max_backoff_seconds: int = 60
    once: bool = False
    heartbeat_interval_fallback: int = 15


class ExponentialBackoff:
    def __init__(self, max_seconds: int = 60) -> None:
        self.max_seconds = max(1, int(max_seconds))
        self.current = 1

    def reset(self) -> None:
        self.current = 1

    def next_delay(self) -> int:
        delay = self.current
        self.current = min(self.max_seconds, self.current * 2)
        return delay


class WorkerLoop:
    """Coordinates remote API calls and local processing pipeline."""

    def __init__(
        self,
        client: RemoteWorkerClient,
        processor: PipelineProcessor,
        work_root: Path,
        logger: logging.Logger,
        observer: WorkerObserver | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        self.client = client
        self.processor = processor
        self.work_root = work_root
        self.logger = logger
        self.observer = observer or NullWorkerObserver()
        self.config = config or LoopConfig()

        self.stop_event = threading.Event()
        self.worker_id: str | None = None
        self.current_job_id: str | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_interval = self.config.heartbeat_interval_fallback

        self.download_dir = self.work_root / "downloads"
        self.output_dir = self.work_root / "output"
        self.report_dir = self.work_root / "reports"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def run_forever(self) -> int:
        backoff = ExponentialBackoff(max_seconds=self.config.max_backoff_seconds)

        while not self.stop_event.is_set():
            try:
                if not self.worker_id:
                    self._register_worker()
                claim = self.client.claim_job(
                    worker_id=self.worker_id,
                    wait_seconds=self.config.poll_wait_seconds,
                )
                self.observer.set_connection_status(True)
                backoff.reset()

                if claim is None:
                    if self.config.once:
                        return 0
                    continue

                self._handle_job(claim)
                if self.config.once:
                    return 0
            except Exception as exc:
                self.observer.set_connection_status(False)
                delay = backoff.next_delay()
                self.logger.error("Worker loop error: %s", exc)
                if self.stop_event.is_set() or self.config.once:
                    return 1
                time.sleep(delay)

        return 0

    def stop(self) -> None:
        self.stop_event.set()

    def _register_worker(self) -> None:
        session = self.client.register_worker()
        self.worker_id = session.worker_id
        self._heartbeat_interval = max(5, int(session.heartbeat_interval or self.config.heartbeat_interval_fallback))
        self.logger.info("Registered worker_id=%s", self.worker_id)
        self.observer.set_connection_status(True)
        self._start_heartbeat_thread()

    def _start_heartbeat_thread(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            if self.worker_id:
                try:
                    self.client.heartbeat(self.worker_id)
                    self.observer.set_connection_status(True)
                    self.logger.debug("Heartbeat sent for worker_id=%s", self.worker_id)
                except Exception as exc:
                    self.observer.set_connection_status(False)
                    self.logger.warning("Heartbeat failed: %s", exc)
            time.sleep(self._heartbeat_interval)

    def _retry(self, fn, operation_name: str, attempts: int = 5):
        backoff = ExponentialBackoff(max_seconds=self.config.max_backoff_seconds)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except Exception as exc:  # pragma: no cover - retry branch depends on runtime failures
                last_exc = exc
                delay = backoff.next_delay()
                self.logger.warning(
                    "%s failed (attempt %s/%s): %s",
                    operation_name,
                    attempt,
                    attempts,
                    exc,
                )
                if attempt < attempts:
                    time.sleep(delay)
        raise RuntimeError(f"{operation_name} failed after {attempts} attempts: {last_exc}")

    def _handle_job(self, claim: JobClaim) -> None:
        self.current_job_id = claim.job_id
        self.logger.info("Claimed job_id=%s input=%s", claim.job_id, claim.input_filename)

        download_path = self.download_dir / f"{claim.job_id}_{Path(claim.input_filename).name}"
        output_path = self.output_dir / f"{claim.job_id}_{Path(claim.input_filename).stem}_optimized.glb"
        report_path = self.report_dir / f"{claim.job_id}_{Path(claim.input_filename).stem}_report.json"

        self._retry(
            lambda: self.client.download_job_file(claim=claim, destination=download_path),
            operation_name="download",
            attempts=5,
        )

        download_msg = f"{utc_timestamp()} | Download complete: {download_path.name}"
        self.observer.set_last_download(download_msg)
        self.logger.info(download_msg)

        outcome = self.processor.process(download_path, output_path, report_path)
        if not outcome.success:
            error_message = outcome.error or "unknown processing error"
            self.logger.error("Processing failed for job_id=%s: %s", claim.job_id, error_message)
            self.observer.set_upload_status(f"{utc_timestamp()} | Upload status: FAILED (processing)")
            self._retry(
                lambda: self.client.report_failure(
                    worker_id=self.worker_id or "",
                    claim=claim,
                    error_message=error_message,
                ),
                operation_name="report_failure",
                attempts=5,
            )
            self.current_job_id = None
            return

        summary = build_geometry_summary(claim.input_filename, outcome.report)
        summary_msg = f"{utc_timestamp()} | {summary}"
        self.observer.set_geometry_summary(summary_msg)
        self.logger.info(summary_msg)

        self._retry(
            lambda: self.client.upload_result(
                worker_id=self.worker_id or "",
                claim=claim,
                optimized_file=outcome.output_path,
                report_file=outcome.report_path,
                summary=summary,
            ),
            operation_name="upload_result",
            attempts=5,
        )

        upload_msg = f"{utc_timestamp()} | Upload status: SUCCESS"
        self.observer.set_upload_status(upload_msg)
        self.logger.info("Job completed job_id=%s", claim.job_id)
        self.current_job_id = None
