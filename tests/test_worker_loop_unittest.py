from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quest_model_optimizer.remote_client import ApiRequestError
from quest_model_optimizer.worker_loop import LoopConfig, WorkerLoop
from quest_model_optimizer.worker_models import JobClaim, ProcessingOutcome, WorkerSession


class Observer:
    def __init__(self) -> None:
        self.connection = []
        self.download = []
        self.geometry = []
        self.upload = []

    def set_connection_status(self, connected: bool) -> None:
        self.connection.append(connected)

    def set_last_download(self, message: str) -> None:
        self.download.append(message)

    def set_geometry_summary(self, message: str) -> None:
        self.geometry.append(message)

    def set_upload_status(self, message: str) -> None:
        self.upload.append(message)


class FakeClient:
    def __init__(self) -> None:
        self.claim_count = 0
        self.upload_calls = 0
        self.failure_calls = 0
        self.last_upload_claim: JobClaim | None = None

    def register_worker(self) -> WorkerSession:
        return WorkerSession(worker_id="worker-1", heartbeat_interval=60)

    def heartbeat(self, worker_id: str):
        return {}

    def apply_runtime_config(self, runtime_config: dict[str, int]) -> None:
        return None

    def claim_job(self, worker_id: str, wait_seconds: int = 30):
        self.claim_count += 1
        if self.claim_count > 1:
            return None
        return JobClaim(
            job_id="job-1",
            input_filename="HOL.obj",
            download_url=None,
            payload={},
        )

    def download_job_file(
        self,
        claim: JobClaim,
        destination: Path,
        worker_id: str | None = None,
        progress_callback=None,
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("mesh")
        if progress_callback:
            progress_callback(destination.stat().st_size, destination.stat().st_size)
        return destination

    def upload_result(
        self,
        worker_id: str,
        claim: JobClaim,
        optimized_file: Path,
        report_file: Path,
        summary: str,
        progress_callback=None,
    ):
        self.upload_calls += 1
        self.last_upload_claim = claim
        if progress_callback:
            total = optimized_file.stat().st_size + report_file.stat().st_size
            progress_callback(total, total)
        return {"ok": True}

    def report_failure(self, worker_id: str, claim: JobClaim, error_message: str):
        self.failure_calls += 1
        return {"ok": True}


class SuccessProcessor:
    def process(self, input_path: Path, output_path: Path, report_path: Path) -> ProcessingOutcome:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"glb")
        report_path.write_text("{}", encoding="utf-8")
        report = {
            "faces_before": 559697,
            "faces_final": 298151,
            "decimate": {"applied": True},
        }
        return ProcessingOutcome(
            success=True,
            output_path=output_path,
            report_path=report_path,
            report=report,
            returncode=0,
        )


class FailProcessor:
    def process(self, input_path: Path, output_path: Path, report_path: Path) -> ProcessingOutcome:
        return ProcessingOutcome(
            success=False,
            output_path=output_path,
            report_path=report_path,
            report={},
            returncode=1,
            error="processing failed",
        )


class WorkerLoopTests(unittest.TestCase):
    def test_upload_success_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            observer = Observer()
            client = FakeClient()
            loop = WorkerLoop(
                client=client,
                processor=SuccessProcessor(),
                work_root=Path(temp_dir),
                logger=logging.getLogger("worker-loop-success"),
                observer=observer,
                config=LoopConfig(once=True),
            )
            rc = loop.run_forever()

        self.assertEqual(rc, 0)
        self.assertEqual(client.upload_calls, 1)
        assert client.last_upload_claim is not None
        self.assertIn("source_checksum", client.last_upload_claim.payload)
        self.assertTrue(any("Upload status: SUCCESS" in msg for msg in observer.upload))
        self.assertTrue(any("HOL.obj" in msg for msg in observer.geometry))

    def test_upload_failed_status_when_processing_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            observer = Observer()
            client = FakeClient()
            loop = WorkerLoop(
                client=client,
                processor=FailProcessor(),
                work_root=Path(temp_dir),
                logger=logging.getLogger("worker-loop-fail"),
                observer=observer,
                config=LoopConfig(once=True),
            )
            rc = loop.run_forever()

        self.assertEqual(rc, 0)
        self.assertEqual(client.failure_calls, 1)
        self.assertTrue(any("Upload status: FAILED" in msg for msg in observer.upload))

    def test_validation_failure_reports_job_failure(self) -> None:
        class ValidationFailClient(FakeClient):
            def claim_job(self, worker_id: str, wait_seconds: int = 30):
                self.claim_count += 1
                if self.claim_count > 1:
                    return None
                return JobClaim(
                    job_id="job-2",
                    input_filename="evil.obj",
                    download_url=None,
                    payload={"sha256": "deadbeef"},
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            observer = Observer()
            client = ValidationFailClient()
            loop = WorkerLoop(
                client=client,
                processor=SuccessProcessor(),
                work_root=Path(temp_dir),
                logger=logging.getLogger("worker-loop-validation"),
                observer=observer,
                config=LoopConfig(once=True),
            )
            rc = loop.run_forever()

        self.assertEqual(rc, 0)
        self.assertEqual(client.failure_calls, 1)
        self.assertEqual(client.upload_calls, 0)
        self.assertTrue(any("FAILED (validation)" in msg for msg in observer.upload))

    def test_reconnects_by_reregistering_after_claim_session_error(self) -> None:
        class ReconnectClient(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.register_calls = 0
                self.loop_ref = None

            def register_worker(self) -> WorkerSession:
                self.register_calls += 1
                return WorkerSession(worker_id=f"worker-{self.register_calls}", heartbeat_interval=60)

            def claim_job(self, worker_id: str, wait_seconds: int = 30):
                self.claim_count += 1
                if self.claim_count == 1:
                    raise ApiRequestError(
                        status_code=404,
                        method="POST",
                        url="https://example.org/api/v1/jobs/claim",
                        body='{"detail":"worker not found"}',
                    )
                if self.claim_count == 2:
                    return JobClaim(
                        job_id="job-reconnect-1",
                        input_filename="HOL.obj",
                        download_url=None,
                        payload={},
                    )
                return None

            def upload_result(
                self,
                worker_id: str,
                claim: JobClaim,
                optimized_file: Path,
                report_file: Path,
                summary: str,
                progress_callback=None,
            ):
                self.upload_calls += 1
                if self.loop_ref is not None:
                    self.loop_ref.stop_event.set()
                return {"ok": True}

        with tempfile.TemporaryDirectory() as temp_dir:
            observer = Observer()
            client = ReconnectClient()
            loop = WorkerLoop(
                client=client,
                processor=SuccessProcessor(),
                work_root=Path(temp_dir),
                logger=logging.getLogger("worker-loop-reconnect"),
                observer=observer,
                config=LoopConfig(once=False, reconnect_after_failures=1, poll_wait_seconds=1),
            )
            client.loop_ref = loop
            with mock.patch("quest_model_optimizer.worker_loop.time.sleep", return_value=None):
                rc = loop.run_forever()

        self.assertEqual(rc, 0)
        self.assertGreaterEqual(client.register_calls, 2)
        self.assertEqual(client.upload_calls, 1)

    def test_register_runtime_config_overrides_loop_defaults(self) -> None:
        class RuntimeConfigClient(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.wait_seconds_seen = []
                self.timeout_config_seen = {}

            def register_worker(self) -> WorkerSession:
                return WorkerSession(
                    worker_id="worker-runtime",
                    heartbeat_interval=20,
                    runtime_config={
                        "poll_wait_seconds": 7,
                        "reconnect_after_failures": 6,
                        "max_backoff_seconds": 11,
                        "download_retries": 2,
                        "upload_retries": 4,
                        "heartbeat_interval": 12,
                        "http_timeout_seconds": 70,
                        "download_timeout_seconds": 240,
                        "upload_timeout_seconds": 900,
                    },
                )

            def apply_runtime_config(self, runtime_config: dict[str, int]) -> None:
                self.timeout_config_seen.update(runtime_config)

            def claim_job(self, worker_id: str, wait_seconds: int = 30):
                self.wait_seconds_seen.append(wait_seconds)
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            observer = Observer()
            client = RuntimeConfigClient()
            loop = WorkerLoop(
                client=client,
                processor=SuccessProcessor(),
                work_root=Path(temp_dir),
                logger=logging.getLogger("worker-loop-runtime-config"),
                observer=observer,
                config=LoopConfig(once=True),
            )
            rc = loop.run_forever()

        self.assertEqual(rc, 0)
        self.assertEqual(client.wait_seconds_seen, [7])
        self.assertEqual(loop.config.reconnect_after_failures, 6)
        self.assertEqual(loop.config.max_backoff_seconds, 11)
        self.assertEqual(loop.config.download_retries, 2)
        self.assertEqual(loop.config.upload_retries, 4)
        self.assertEqual(loop._heartbeat_interval, 12)
        self.assertEqual(client.timeout_config_seen.get("upload_timeout_seconds"), 900)

    def test_register_without_runtime_config_keeps_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            observer = Observer()
            client = FakeClient()
            loop = WorkerLoop(
                client=client,
                processor=SuccessProcessor(),
                work_root=Path(temp_dir),
                logger=logging.getLogger("worker-loop-runtime-defaults"),
                observer=observer,
                config=LoopConfig(once=True, poll_wait_seconds=33),
            )
            rc = loop.run_forever()

        self.assertEqual(rc, 0)
        self.assertEqual(client.claim_count, 1)

    def test_heartbeat_runtime_update_changes_active_config(self) -> None:
        class HeartbeatRuntimeClient(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.loop_ref = None
                self.runtime_updates = []

            def apply_runtime_config(self, runtime_config: dict[str, int]) -> None:
                self.runtime_updates.append(dict(runtime_config))

            def heartbeat(self, worker_id: str):
                if self.loop_ref is not None:
                    self.loop_ref.stop_event.set()
                return {
                    "poll_wait_seconds": 9,
                    "heartbeat_interval": 13,
                    "download_retries": 6,
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            observer = Observer()
            client = HeartbeatRuntimeClient()
            loop = WorkerLoop(
                client=client,
                processor=SuccessProcessor(),
                work_root=Path(temp_dir),
                logger=logging.getLogger("worker-loop-heartbeat-runtime"),
                observer=observer,
                config=LoopConfig(once=False, heartbeat_interval_fallback=1),
            )
            client.loop_ref = loop
            loop.worker_id = "worker-heartbeat-1"
            with mock.patch("quest_model_optimizer.worker_loop.time.sleep", return_value=None):
                loop._heartbeat_loop()

        self.assertEqual(loop.config.poll_wait_seconds, 9)
        self.assertEqual(loop._heartbeat_interval, 13)
        self.assertEqual(loop.config.download_retries, 6)
        self.assertTrue(client.runtime_updates)

    def test_retry_extends_attempts_for_transient_network_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            loop = WorkerLoop(
                client=FakeClient(),
                processor=SuccessProcessor(),
                work_root=Path(temp_dir),
                logger=logging.getLogger("worker-loop-transient-retry"),
                observer=Observer(),
                config=LoopConfig(once=True),
            )

            attempts = {"count": 0}

            def flaky_operation():
                attempts["count"] += 1
                if attempts["count"] < 4:
                    raise OSError(54, "Connection reset by peer")
                return "ok"

            with mock.patch("quest_model_optimizer.worker_loop.time.sleep", return_value=None):
                result = loop._retry(flaky_operation, operation_name="upload_result", attempts=2)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
