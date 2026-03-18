from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_model_optimizer.remote_client import RemoteWorkerClient


class FakeTransport:
    def __init__(self) -> None:
        self.calls = []

    def json_request(self, method, url, headers, payload=None):
        self.calls.append(("json", method, url, payload))
        if url.endswith("/api/v1/workers/register"):
            return {"worker_id": "worker-1", "heartbeat_interval": 12}
        if "/api/v1/jobs/claim" in url:
            return {"job_id": "job-1", "input_filename": "HOL.obj", "download_url": "https://server/file"}
        return {"ok": True}

    def download_file(self, url, headers, destination: Path):
        self.calls.append(("download", url, headers, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("mesh")

    def upload_multipart(self, url, headers, fields, files):
        self.calls.append(("upload", url, fields, files))
        return {"ok": True}


class RemoteClientTests(unittest.TestCase):
    def test_register_claim_and_upload(self) -> None:
        transport = FakeTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            transport=transport,
        )

        session = client.register_worker()
        self.assertEqual(session.worker_id, "worker-1")

        claim = client.claim_job(worker_id=session.worker_id, wait_seconds=10)
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim.job_id, "job-1")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_file = root / "out.glb"
            report_file = root / "report.json"
            output_file.write_bytes(b"binary")
            report_file.write_text("{}", encoding="utf-8")

            response = client.upload_result(
                worker_id=session.worker_id,
                claim=claim,
                optimized_file=output_file,
                report_file=report_file,
                summary="HOL.obj: 10 -> 8 (decimate)",
            )

        self.assertEqual(response, {"ok": True})

    def test_claim_none_on_empty_payload(self) -> None:
        class EmptyClaimTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                if "/api/v1/jobs/claim" in url:
                    return {}
                return super().json_request(method, url, headers, payload)

        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            transport=EmptyClaimTransport(),
        )
        self.assertIsNone(client.claim_job(worker_id="w", wait_seconds=5))

    def test_http_is_rejected_without_override(self) -> None:
        with self.assertRaises(ValueError):
            RemoteWorkerClient(
                server_url="http://example.org",
                worker_token="token",
                worker_name="worker-a",
                transport=FakeTransport(),
            )

    def test_http_is_allowed_with_override(self) -> None:
        client = RemoteWorkerClient(
            server_url="http://localhost:8000",
            worker_token="token",
            worker_name="worker-a",
            transport=FakeTransport(),
            allow_insecure_http=True,
        )
        self.assertEqual(client.server_url, "http://localhost:8000")

    def test_external_download_url_does_not_receive_bearer_token(self) -> None:
        transport = FakeTransport()
        client = RemoteWorkerClient(
            server_url="https://safe.example",
            worker_token="super-secret",
            worker_name="worker-a",
            transport=transport,
        )
        claim = client.claim_job(worker_id="worker-1", wait_seconds=10)
        assert claim is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "mesh.obj"
            client.download_job_file(claim, destination)

        download_calls = [c for c in transport.calls if c[0] == "download"]
        self.assertEqual(len(download_calls), 1)
        _, _, headers, _ = download_calls[0]
        self.assertNotIn("Authorization", headers)

    def test_same_origin_download_url_receives_bearer_token(self) -> None:
        class SameOriginTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                if "/api/v1/jobs/claim" in url:
                    return {
                        "job_id": "job-2",
                        "input_filename": "mesh.obj",
                        "download_url": "https://example.org/api/v1/jobs/job-2/download",
                    }
                return super().json_request(method, url, headers, payload)

        transport = SameOriginTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="super-secret",
            worker_name="worker-a",
            transport=transport,
        )
        claim = client.claim_job(worker_id="worker-1", wait_seconds=10)
        assert claim is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "mesh.obj"
            client.download_job_file(claim, destination)

        download_calls = [c for c in transport.calls if c[0] == "download"]
        self.assertEqual(len(download_calls), 1)
        _, _, headers, _ = download_calls[0]
        self.assertIn("Authorization", headers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
