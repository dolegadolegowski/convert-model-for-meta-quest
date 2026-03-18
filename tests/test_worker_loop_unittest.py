from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

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

    def register_worker(self) -> WorkerSession:
        return WorkerSession(worker_id="worker-1", heartbeat_interval=60)

    def heartbeat(self, worker_id: str) -> None:
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

    def download_job_file(self, claim: JobClaim, destination: Path, worker_id: str | None = None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("mesh")
        return destination

    def upload_result(self, worker_id: str, claim: JobClaim, optimized_file: Path, report_file: Path, summary: str):
        self.upload_calls += 1
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
