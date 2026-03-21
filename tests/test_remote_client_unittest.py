from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib import parse

from quest_model_optimizer.remote_client import ApiRequestError, RemoteWorkerClient
from quest_model_optimizer.version import read_version


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

    def download_file(self, url, headers, destination: Path, progress_callback=None):
        self.calls.append(("download", url, headers, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("mesh")
        if progress_callback:
            size = destination.stat().st_size
            progress_callback(size, size)

    def upload_multipart(self, url, headers, fields, files, progress_callback=None):
        self.calls.append(("upload", url, fields, files))
        if progress_callback:
            total = sum(path.stat().st_size for path in files.values())
            progress_callback(total, total)
        return {"ok": True}


class RemoteClientTests(unittest.TestCase):
    def test_api_request_error_parses_retry_after(self) -> None:
        exc = ApiRequestError(
            status_code=503,
            method="POST",
            url="https://example.org/api/v1/jobs/claim",
            body='{"detail":"overloaded"}',
            headers={"Retry-After": "9"},
        )
        self.assertEqual(exc.retry_after_seconds, 9)

        exc_invalid = ApiRequestError(
            status_code=503,
            method="POST",
            url="https://example.org/api/v1/jobs/claim",
            body='{"detail":"overloaded"}',
            headers={"Retry-After": "soon"},
        )
        self.assertIsNone(exc_invalid.retry_after_seconds)

    def test_user_agent_uses_project_version(self) -> None:
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=FakeTransport(),
        )
        expected = f"ConvertModelForMetaQuest-Worker/{read_version()}"
        self.assertEqual(client._headers()["User-Agent"], expected)

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

    def test_download_and_upload_emit_progress_callbacks(self) -> None:
        transport = FakeTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=transport,
        )

        session = client.register_worker()
        claim = client.claim_job(worker_id=session.worker_id, wait_seconds=10)
        assert claim is not None

        download_events = []
        upload_events = []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "mesh.obj"
            output_file = root / "out.glb"
            report_file = root / "report.json"
            client.download_job_file(
                claim=claim,
                destination=source_path,
                worker_id=session.worker_id,
                progress_callback=lambda transferred, total: download_events.append((transferred, total)),
            )
            output_file.write_bytes(b"binary")
            report_file.write_text("{}", encoding="utf-8")
            client.upload_result(
                worker_id=session.worker_id,
                claim=claim,
                optimized_file=output_file,
                report_file=report_file,
                summary="HOL.obj: 10 -> 8 (decimate)",
                progress_callback=lambda transferred, total: upload_events.append((transferred, total)),
            )

        self.assertTrue(download_events)
        self.assertTrue(upload_events)
        self.assertGreaterEqual(download_events[-1][0], 1)
        self.assertGreaterEqual(upload_events[-1][0], 1)

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

    def test_register_parses_runtime_config_from_nested_payload(self) -> None:
        class RuntimeNestedTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                self.calls.append(("json", method, url, payload))
                if url.endswith("/api/v1/workers/register"):
                    return {
                        "worker_id": "worker-runtime-1",
                        "runtime_config": {
                            "poll_wait_seconds": 8,
                            "heartbeat_interval": 16,
                            "reconnect_after_failures": 4,
                            "max_backoff_seconds": 12,
                            "http_timeout_seconds": 70,
                            "download_timeout_seconds": 210,
                            "upload_timeout_seconds": 720,
                            "download_retries": 2,
                            "upload_retries": 3,
                        },
                    }
                return super().json_request(method, url, headers, payload)

        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=RuntimeNestedTransport(),
        )
        session = client.register_worker()
        assert session.runtime_config is not None
        self.assertEqual(session.runtime_config.get("poll_wait_seconds"), 8)
        self.assertEqual(session.runtime_config.get("upload_timeout_seconds"), 720)
        self.assertEqual(client.http_timeout_seconds, 70)
        self.assertEqual(client.download_timeout_seconds, 210)
        self.assertEqual(client.upload_timeout_seconds, 720)

    def test_register_parses_runtime_config_from_flat_legacy_fields(self) -> None:
        class RuntimeFlatTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                self.calls.append(("json", method, url, payload))
                if url.endswith("/api/v1/workers/register"):
                    return {
                        "worker_id": "worker-runtime-2",
                        "poll_wait_seconds": 9,
                        "heartbeat_interval": 18,
                        "download_retries": 6,
                    }
                return super().json_request(method, url, headers, payload)

        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=RuntimeFlatTransport(),
        )
        session = client.register_worker()
        assert session.runtime_config is not None
        self.assertEqual(session.runtime_config.get("poll_wait_seconds"), 9)
        self.assertEqual(session.runtime_config.get("download_retries"), 6)
        self.assertEqual(session.heartbeat_interval, 18)

    def test_heartbeat_returns_runtime_config_patch(self) -> None:
        class HeartbeatRuntimeTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                self.calls.append(("json", method, url, payload))
                if url.endswith("/api/v1/workers/heartbeat"):
                    return {"runtime_config": {"poll_wait_seconds": 22, "upload_retries": 7}}
                return super().json_request(method, url, headers, payload)

        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=HeartbeatRuntimeTransport(),
        )
        patch = client.heartbeat("worker-a-id")
        self.assertEqual(patch.get("poll_wait_seconds"), 22)
        self.assertEqual(patch.get("upload_retries"), 7)

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

    def test_upload_result_treats_done_conflict_as_success(self) -> None:
        class DoneConflictTransport(FakeTransport):
            def upload_multipart(self, url, headers, fields, files, progress_callback=None):
                raise ApiRequestError(
                    status_code=409,
                    method="POST",
                    url=url,
                    body='{"code":"JOB_STATUS_CONFLICT","message":"Job is not awaiting result in status DONE"}',
                )

        transport = DoneConflictTransport()
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
            response = client.upload_result(
                worker_id="worker-1",
                claim=claim,
                optimized_file=output_file,
                report_file=report_file,
                summary="mesh.obj: 10 -> 8 (decimate)",
            )

        self.assertEqual(response, {"ok": True, "status": "already-completed"})

    def test_report_failure_sends_lease_token(self) -> None:
        transport = FakeTransport()
        client = RemoteWorkerClient(
            server_url="https://example.org",
            worker_token="token",
            worker_name="worker-a",
            worker_id="worker-a-id",
            transport=transport,
        )
        claim = client.claim_job(worker_id="worker-1", wait_seconds=10)
        assert claim is not None

        response = client.report_failure(worker_id="worker-1", claim=claim, error_message="processing failed")
        self.assertEqual(response, {"ok": True})

        fail_calls = [c for c in transport.calls if c[0] == "json" and c[2].endswith("/fail")]
        self.assertEqual(len(fail_calls), 1)
        payload = fail_calls[0][3]
        self.assertEqual(payload.get("lease_token"), "lease-1")
        self.assertEqual(payload.get("claim_token"), "lease-1")

    def test_report_failure_requires_lease_token(self) -> None:
        class NoLeaseTransport(FakeTransport):
            def json_request(self, method, url, headers, payload=None):
                if "/api/v1/jobs/claim" in url:
                    return {
                        "job_id": "job-20",
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
        with self.assertRaises(RuntimeError):
            client.report_failure(worker_id="worker-1", claim=claim, error_message="processing failed")

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
