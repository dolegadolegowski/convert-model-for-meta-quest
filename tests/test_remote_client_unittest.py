from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib import parse

from quest_model_optimizer.remote_client import RemoteWorkerClient


class FakeTransport:
    def __init__(self) -> None:
        self.calls = []

    def json_request(self, method, url, headers, payload=None):
        self.calls.append(("json", method, url, payload))
        if url.endswith("/api/v1/workers/register"):
            return {"worker_id": "worker-1", "heartbeat_interval": 12}
        if "/api/v1/jobs/claim" in url:
            return {
                "job_id": "job-1",
                "input_filename": "HOL.obj",
                "download_url": "https://server/file",
                "lease_token": "lease-1",
                "sha256": "source-checksum-1",
            }
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
            worker_id="worker-a-id",
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
        upload_calls = [c for c in transport.calls if c[0] == "upload"]
        self.assertEqual(len(upload_calls), 1)
        _, _, fields, files = upload_calls[0]
        self.assertEqual(fields.get("lease_token"), "lease-1")
        self.assertIn("metadata_json", fields)
        self.assertIn("worker_metadata_json", fields)
        self.assertIn("result_file", files)
        metadata = json.loads(fields["metadata_json"])
        self.assertEqual(metadata.get("source_checksum"), "source-checksum-1")
        expected_result_checksum = hashlib.sha256(b"binary").hexdigest()
        self.assertEqual(metadata.get("result_checksum"), expected_result_checksum)
        self.assertEqual(fields.get("result_checksum"), expected_result_checksum)
        checksums = metadata.get("checksums") or {}
        self.assertEqual(checksums.get("result_sha256"), expected_result_checksum)
        worker_metadata = metadata.get("worker_metadata") or {}
        self.assertEqual(worker_metadata.get("result_checksum"), expected_result_checksum)

    def test_register_falls_back_to_local_worker_id_when_response_missing_id(self) -> None:
        class NoIdTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                self.calls.append(("json", method, url, payload))
                if url.endswith("/api/v1/workers/register"):
                    return {"heartbeat_interval": 10}
                return super().json_request(method, url, headers, payload)

        transport = NoIdTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-local-123",
            transport=transport,
        )
        session = client.register_worker()
        self.assertEqual(session.worker_id, "worker-local-123")
        register_calls = [c for c in transport.calls if c[0] == "json" and c[2].endswith("/api/v1/workers/register")]
        self.assertEqual(len(register_calls), 1)
        payload = register_calls[0][3]
        self.assertEqual(payload["worker_id"], "worker-local-123")

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
            worker_id="worker-a-id",
            transport=EmptyClaimTransport(),
        )
        self.assertIsNone(client.claim_job(worker_id="w", wait_seconds=5))

    def test_claim_sends_worker_id_in_query_for_compatibility(self) -> None:
        transport = FakeTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=transport,
        )

        claim = client.claim_job(worker_id="worker-query-1", wait_seconds=17)
        self.assertIsNotNone(claim)

        claim_calls = [c for c in transport.calls if c[0] == "json" and "/api/v1/jobs/claim" in c[2]]
        self.assertEqual(len(claim_calls), 1)
        _, _, url, payload = claim_calls[0]
        parsed = parse.urlparse(url)
        query = parse.parse_qs(parsed.query)
        self.assertEqual(query.get("wait"), ["17"])
        self.assertEqual(query.get("worker_id"), ["worker-query-1"])
        self.assertEqual(payload.get("worker_id"), "worker-query-1")

    def test_upload_result_requires_lease_token(self) -> None:
        class NoLeaseTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                if "/api/v1/jobs/claim" in url:
                    return {
                        "job_id": "job-9",
                        "input_filename": "mesh.obj",
                        "download_url": "https://example.org/file",
                    }
                return super().json_request(method, url, headers, payload)

        transport = NoLeaseTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=transport,
        )
        claim = client.claim_job(worker_id="worker-1", wait_seconds=10)
        assert claim is not None

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_file = root / "out.glb"
            report_file = root / "report.json"
            output_file.write_bytes(b"binary")
            report_file.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                client.upload_result(
                    worker_id="worker-1",
                    claim=claim,
                    optimized_file=output_file,
                    report_file=report_file,
                    summary="mesh.obj: 10 -> 8 (decimate)",
                )

    def test_upload_result_requires_source_checksum(self) -> None:
        class NoChecksumTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                if "/api/v1/jobs/claim" in url:
                    return {
                        "job_id": "job-10",
                        "input_filename": "mesh.obj",
                        "download_url": "https://example.org/file",
                        "lease_token": "lease-10",
                    }
                return super().json_request(method, url, headers, payload)

        transport = NoChecksumTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=transport,
        )
        claim = client.claim_job(worker_id="worker-1", wait_seconds=10)
        assert claim is not None

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_file = root / "out.glb"
            report_file = root / "report.json"
            output_file.write_bytes(b"binary")
            report_file.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                client.upload_result(
                    worker_id="worker-1",
                    claim=claim,
                    optimized_file=output_file,
                    report_file=report_file,
                    summary="mesh.obj: 10 -> 8 (decimate)",
                )

    def test_http_is_rejected_without_override(self) -> None:
        with self.assertRaises(ValueError):
            RemoteWorkerClient(
                server_url="http://example.org",
                worker_token="token",
                worker_name="worker-a",
                worker_id="worker-a-id",
                transport=FakeTransport(),
            )

    def test_http_is_allowed_with_override(self) -> None:
        client = RemoteWorkerClient(
            server_url="http://localhost:8000",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
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
            worker_id="worker-a-id",
            transport=transport,
        )
        claim = client.claim_job(worker_id="worker-1", wait_seconds=10)
        assert claim is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "mesh.obj"
            client.download_job_file(claim, destination)

        download_calls = [c for c in transport.calls if c[0] == "download"]
        self.assertEqual(len(download_calls), 1)
        _, url, headers, _ = download_calls[0]
        self.assertEqual(url, "https://server/file")
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
            worker_id="worker-a-id",
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

    def test_relative_download_path_is_resolved_against_server_url(self) -> None:
        class RelativePathTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                if "/api/v1/jobs/claim" in url:
                    return {
                        "job_id": "job-3",
                        "input_filename": "mesh.obj",
                        "download_url": "/api/v1/jobs/job-3/download",
                    }
                return super().json_request(method, url, headers, payload)

        transport = RelativePathTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="super-secret",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=transport,
        )
        claim = client.claim_job(worker_id="worker-1", wait_seconds=10)
        assert claim is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "mesh.obj"
            client.download_job_file(claim, destination)

        download_calls = [c for c in transport.calls if c[0] == "download"]
        self.assertEqual(len(download_calls), 1)
        _, url, headers, _ = download_calls[0]
        parsed = parse.urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "example.org")
        self.assertEqual(parsed.path, "/api/v1/jobs/job-3/download")
        query = parse.parse_qs(parsed.query)
        self.assertEqual(query.get("worker_id"), ["worker-a-id"])
        self.assertIn("Authorization", headers)

    def test_download_includes_lease_query_params_for_same_origin(self) -> None:
        class LeaseTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                if "/api/v1/jobs/claim" in url:
                    return {
                        "job": {
                            "job_id": "job-4",
                            "input_filename": "mesh.obj",
                            "download_url": "/api/v1/jobs/job-4/download",
                        },
                        "lease_token": "lease-abc",
                    }
                return super().json_request(method, url, headers, payload)

        transport = LeaseTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="super-secret",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=transport,
        )
        claim = client.claim_job(worker_id="worker-1", wait_seconds=10)
        assert claim is not None
        self.assertEqual(claim.lease_token, "lease-abc")
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "mesh.obj"
            client.download_job_file(claim, destination, worker_id="worker-1")

        download_calls = [c for c in transport.calls if c[0] == "download"]
        self.assertEqual(len(download_calls), 1)
        _, url, headers, _ = download_calls[0]
        parsed = parse.urlparse(url)
        query = parse.parse_qs(parsed.query)
        self.assertEqual(query.get("worker_id"), ["worker-1"])
        self.assertEqual(query.get("lease_token"), ["lease-abc"])
        self.assertIn("Authorization", headers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
