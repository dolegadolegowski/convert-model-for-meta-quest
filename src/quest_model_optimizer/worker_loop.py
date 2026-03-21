"""Main worker loop with retry/backoff and event notifications."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib import parse

from .remote_client import ApiRequestError, RemoteWorkerClient
from .worker_models import JobClaim
from .worker_processor import PipelineProcessor
from .worker_security import compute_sha256, validate_download_file
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
    max_download_bytes: int = 1024 * 1024 * 1024
    reconnect_after_failures: int = 3
    download_retries: int = 5
    upload_retries: int = 5
    transient_log_window_seconds: int = 30


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
        self._consecutive_failures = 0
        self._heartbeat_consecutive_failures = 0
        self._heartbeat_missing_config_logged = False
        self._claim_inflight = False
        self._runtime_config_snapshot: dict[str, int] = {}
        self._throttled_warnings: dict[str, tuple[float, int]] = {}

        self.download_dir = self.work_root / "downloads"
        self.output_dir = self.work_root / "output"
        self.report_dir = self.work_root / "reports"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _format_size(num_bytes: int | None) -> str:
        if num_bytes is None:
            return "?"
        value = float(num_bytes)
        units = ("B", "KB", "MB", "GB", "TB")
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                return f"{value:.1f}{unit}"
            value /= 1024.0
        return f"{num_bytes}B"

    @staticmethod
    def _progress_bar(percent: int, width: int = 24) -> str:
        bounded = max(0, min(100, int(percent)))
        filled = int((bounded / 100.0) * width)
        return f"[{'#' * filled}{'.' * (width - filled)}] {bounded:3d}%"

    def _make_transfer_progress_callback(
        self,
        operation: str,
        filename: str,
        step_percent: int = 5,
        unknown_step_bytes: int = 5 * 1024 * 1024,
    ) -> Callable[[int, int | None], None]:
        last_percent_bucket = -1
        last_unknown_bucket = -1

        def _callback(transferred: int, total: int | None) -> None:
            nonlocal last_percent_bucket, last_unknown_bucket
            transferred = max(0, int(transferred))
            if total is not None and total > 0:
                percent = min(100, int((transferred * 100) / total))
                bucket = min(100, (percent // max(1, step_percent)) * max(1, step_percent))
                is_complete = transferred >= total
                should_log = is_complete or bucket > last_percent_bucket or last_percent_bucket < 0
                if should_log:
                    last_percent_bucket = bucket
                    self.logger.info(
                        "%s %s %s (%s / %s)",
                        operation,
                        filename,
                        self._progress_bar(percent),
                        self._format_size(transferred),
                        self._format_size(total),
                    )
            else:
                bucket = transferred // max(1, unknown_step_bytes)
                if bucket > last_unknown_bucket or (transferred == 0 and last_unknown_bucket < 0):
                    last_unknown_bucket = bucket
                    self.logger.info(
                        "%s %s [stream] (%s transferred)",
                        operation,
                        filename,
                        self._format_size(transferred),
                    )

        return _callback

    def _apply_runtime_config(self, runtime_config: dict[str, int], source: str, log_applied: bool = True) -> None:
        if not runtime_config:
            return

        normalized = {str(key): int(value) for key, value in runtime_config.items()}
        if "poll_wait_seconds" in runtime_config:
            self.config.poll_wait_seconds = max(1, int(runtime_config["poll_wait_seconds"]))
        if "heartbeat_interval" in runtime_config:
            self._heartbeat_interval = max(5, int(runtime_config["heartbeat_interval"]))
        if "reconnect_after_failures" in runtime_config:
            self.config.reconnect_after_failures = max(1, int(runtime_config["reconnect_after_failures"]))
        if "max_backoff_seconds" in runtime_config:
            self.config.max_backoff_seconds = max(1, int(runtime_config["max_backoff_seconds"]))
        if "download_retries" in runtime_config:
            self.config.download_retries = max(1, int(runtime_config["download_retries"]))
        if "upload_retries" in runtime_config:
            self.config.upload_retries = max(1, int(runtime_config["upload_retries"]))

        self.client.apply_runtime_config(runtime_config)
        changed_values = {
            key: value
            for key, value in normalized.items()
            if self._runtime_config_snapshot.get(key) != value
        }
        self._runtime_config_snapshot.update(normalized)
        if log_applied and changed_values:
            ordered = ", ".join(f"{key}={changed_values[key]}" for key in sorted(changed_values))
            self.logger.info("Applied server runtime config from %s: %s", source, ordered)

    def run_forever(self) -> int:
        backoff = ExponentialBackoff(max_seconds=self.config.max_backoff_seconds)

        while not self.stop_event.is_set():
            try:
                if not self.worker_id:
                    self._register_worker()
                self._claim_inflight = True
                try:
                    claim = self.client.claim_job(
                        worker_id=self.worker_id,
                        wait_seconds=self.config.poll_wait_seconds,
                    )
                finally:
                    self._claim_inflight = False
                self.observer.set_connection_status(True)
                backoff.reset()
                self._consecutive_failures = 0

                if claim is None:
                    if self.config.once:
                        return 0
                    continue

                self._handle_job(claim)
                if self.config.once:
                    return 0
            except Exception as exc:
                self.observer.set_connection_status(False)
                self._consecutive_failures += 1
                transient_error = self._is_transient_network_error(exc)
                if self._should_reconnect(exc):
                    self._reset_worker_session(reason=f"server/session connectivity error: {exc}")
                elif (
                    self.worker_id
                    and not transient_error
                    and self._consecutive_failures >= max(1, int(self.config.reconnect_after_failures))
                ):
                    reason = (
                        f"transient connectivity failures reached {self._consecutive_failures}"
                        if transient_error
                        else f"consecutive failures reached {self._consecutive_failures}"
                    )
                    self._reset_worker_session(
                        reason=reason
                    )
                delay = backoff.next_delay()
                retry_after_seconds = self._retry_after_seconds(exc)
                if retry_after_seconds is not None:
                    delay = max(delay, retry_after_seconds)
                if transient_error:
                    self._log_throttled_warning(
                        key=f"loop:{self._error_signature(exc)}",
                        message=f"Transient worker loop network error: {exc}",
                    )
                else:
                    self.logger.error("Worker loop error: %s", exc)
                if self.stop_event.is_set() or self.config.once:
                    return 1
                backoff.max_seconds = max(1, int(self.config.max_backoff_seconds))
                jittered_delay = max(0.1, delay * random.uniform(0.9, 1.1))
                time.sleep(jittered_delay)

        return 0

    def stop(self) -> None:
        self.stop_event.set()

    def _register_worker(self) -> None:
        session = self.client.register_worker()
        self.worker_id = session.worker_id
        self._heartbeat_interval = max(5, int(session.heartbeat_interval or self.config.heartbeat_interval_fallback))
        self._heartbeat_consecutive_failures = 0
        if session.runtime_config:
            self._apply_runtime_config(session.runtime_config, source="register")
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
                if self._claim_inflight and self.current_job_id is None:
                    time.sleep(self._heartbeat_interval)
                    continue
                try:
                    heartbeat_runtime = self.client.heartbeat(self.worker_id)
                    if heartbeat_runtime:
                        self._apply_runtime_config(heartbeat_runtime, source="heartbeat", log_applied=False)
                        self._heartbeat_missing_config_logged = False
                    elif not self._heartbeat_missing_config_logged:
                        self.logger.warning(
                            "Heartbeat response missing runtime config; keeping current worker settings."
                        )
                        self._heartbeat_missing_config_logged = True
                    self._heartbeat_consecutive_failures = 0
                    self.observer.set_connection_status(True)
                    self.logger.debug("Heartbeat sent for worker_id=%s", self.worker_id)
                except Exception as exc:
                    self.observer.set_connection_status(False)
                    if self._is_transient_network_error(exc):
                        self._log_throttled_warning(
                            key=f"heartbeat:{self._error_signature(exc)}",
                            message=f"Heartbeat failed: {exc}",
                        )
                    else:
                        self.logger.warning("Heartbeat failed: %s", exc)
                    self._heartbeat_missing_config_logged = False
                    self._heartbeat_consecutive_failures += 1
                    if self._should_reconnect(exc):
                        self._reset_worker_session(reason=f"heartbeat rejected: {exc}")
            time.sleep(self._heartbeat_interval)

    def _reset_worker_session(self, reason: str) -> None:
        if not self.worker_id:
            return
        self.logger.warning("Resetting worker session worker_id=%s (%s)", self.worker_id, reason)
        self.worker_id = None
        self._heartbeat_consecutive_failures = 0

    @staticmethod
    def _should_reconnect(exc: Exception) -> bool:
        if isinstance(exc, ApiRequestError):
            if exc.status_code in {401, 403, 404, 410, 412}:
                return True
            if exc.status_code == 409:
                body = (exc.body or "").lower()
                reconnect_tokens = ("worker", "lease", "session", "not found", "expired", "invalid")
                return any(token in body for token in reconnect_tokens)
            return False
        return False

    @staticmethod
    def _is_transient_network_error(exc: Exception) -> bool:
        if isinstance(exc, ApiRequestError):
            return exc.status_code in {408, 425, 429, 500, 502, 503, 504}

        if isinstance(exc, OSError) and exc.errno in {8, 32, 54, 60, 104, 110}:
            return True

        error_text = str(exc).lower()
        transient_tokens = (
            "timed out",
            "timeout",
            "connection reset",
            "broken pipe",
            "temporarily unavailable",
            "connection aborted",
            "connection refused",
            "network is unreachable",
            "nodename nor servname provided",
            "name or service not known",
            "temporary failure in name resolution",
        )
        return any(token in error_text for token in transient_tokens)

    @staticmethod
    def _error_signature(exc: Exception) -> str:
        if isinstance(exc, ApiRequestError):
            path = parse.urlparse(exc.url).path or exc.url
            return f"api:{exc.status_code}:{exc.method}:{path}"
        if isinstance(exc, OSError):
            return f"oserror:{exc.errno}:{type(exc).__name__}"
        return f"{type(exc).__name__}:{str(exc).lower().split(':')[0][:80]}"

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> int | None:
        if isinstance(exc, ApiRequestError):
            return exc.retry_after_seconds
        return None

    def _log_throttled_warning(self, key: str, message: str) -> None:
        window = max(1, int(self.config.transient_log_window_seconds))
        now = time.monotonic()
        last_logged_at, suppressed_count = self._throttled_warnings.get(key, (0.0, 0))
        if last_logged_at <= 0 or now - last_logged_at >= window:
            if suppressed_count > 0:
                self.logger.warning("%s (suppressed %s similar events)", message, suppressed_count)
            else:
                self.logger.warning(message)
            self._throttled_warnings[key] = (now, 0)
            return
        self._throttled_warnings[key] = (last_logged_at, suppressed_count + 1)

    def _retry(self, fn, operation_name: str, attempts: int = 5):
        backoff = ExponentialBackoff(max_seconds=self.config.max_backoff_seconds)
        last_exc: Exception | None = None
        requested_attempts = max(1, int(attempts))
        effective_attempts = requested_attempts
        attempt = 0
        while attempt < effective_attempts:
            attempt += 1
            try:
                return fn()
            except Exception as exc:  # pragma: no cover - retry branch depends on runtime failures
                last_exc = exc
                if self._is_transient_network_error(exc):
                    effective_attempts = max(effective_attempts, 4)
                delay = backoff.next_delay()
                retry_after_seconds = self._retry_after_seconds(exc)
                if retry_after_seconds is not None:
                    delay = max(delay, retry_after_seconds)
                self.logger.warning(
                    "%s failed (attempt %s/%s): %s",
                    operation_name,
                    attempt,
                    effective_attempts,
                    exc,
                )
                if attempt < effective_attempts:
                    time.sleep(delay)
        raise RuntimeError(f"{operation_name} failed after {effective_attempts} attempts: {last_exc}")

    def _handle_job(self, claim: JobClaim) -> None:
        self.current_job_id = claim.job_id
        self.logger.info("Claimed job_id=%s input=%s", claim.job_id, claim.input_filename)

        download_path = self.download_dir / f"{claim.job_id}_{Path(claim.input_filename).name}"
        output_path = self.output_dir / f"{claim.job_id}_{Path(claim.input_filename).stem}_optimized.glb"
        report_path = self.report_dir / f"{claim.job_id}_{Path(claim.input_filename).stem}_report.json"

        self._retry(
            lambda: self.client.download_job_file(
                claim=claim,
                destination=download_path,
                worker_id=self.worker_id,
                progress_callback=self._make_transfer_progress_callback("Download", download_path.name),
            ),
            operation_name="download",
            attempts=max(1, int(self.config.download_retries)),
        )

        download_msg = f"{utc_timestamp()} | Download complete: {download_path.name}"
        self.observer.set_last_download(download_msg)
        self.logger.info(download_msg)

        expected_sha256 = claim.payload.get("sha256") or claim.payload.get("input_sha256")
        try:
            validate_download_file(
                path=download_path,
                expected_sha256=str(expected_sha256) if expected_sha256 else None,
                max_bytes=int(self.config.max_download_bytes),
            )
            source_sha256 = compute_sha256(download_path).lower()
            claim.payload.setdefault("source_checksum", source_sha256)
            claim.payload.setdefault("source_sha256", source_sha256)
            claim.payload.setdefault("input_sha256", source_sha256)
        except Exception as exc:
            error_message = f"download validation failed: {exc}"
            self.logger.error("Job %s failed security validation: %s", claim.job_id, error_message)
            self.observer.set_upload_status(f"{utc_timestamp()} | Upload status: FAILED (validation)")
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

        upload_response = self._retry(
            lambda: self.client.upload_result(
                worker_id=self.worker_id or "",
                claim=claim,
                optimized_file=outcome.output_path,
                report_file=outcome.report_path,
                summary=summary,
                progress_callback=self._make_transfer_progress_callback("Upload", output_path.name),
            ),
            operation_name="upload_result",
            attempts=max(1, int(self.config.upload_retries)),
        )
        if isinstance(upload_response, dict) and upload_response.get("status") == "already-completed":
            self.logger.warning(
                "Server already finalized result for job_id=%s after transient upload disruption.",
                claim.job_id,
            )

        upload_msg = f"{utc_timestamp()} | Upload status: SUCCESS"
        self.observer.set_upload_status(upload_msg)
        self.logger.info("Job completed job_id=%s", claim.job_id)
        self.current_job_id = None
