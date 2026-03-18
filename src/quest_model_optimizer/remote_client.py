"""HTTPS client for remote worker-server communication."""

from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Protocol
from urllib import error, parse, request

from .worker_models import JobClaim, WorkerSession


class TransportProtocol(Protocol):
    def json_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ...

    def download_file(self, url: str, headers: dict[str, str], destination: Path) -> None:
        ...

    def upload_multipart(
        self,
        url: str,
        headers: dict[str, str],
        fields: dict[str, str],
        files: dict[str, Path],
    ) -> dict[str, Any] | None:
        ...


class UrllibTransport:
    """Default network transport using urllib from stdlib."""

    def __init__(self, timeout: int = 60) -> None:
        self.timeout = timeout

    def json_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        data = None
        req_headers = dict(headers)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")

        req = request.Request(url=url, data=data, headers=req_headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for {method} {url}: {body}") from exc

    def download_file(self, url: str, headers: dict[str, str], destination: Path) -> None:
        req = request.Request(url=url, headers=headers, method="GET")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp, destination.open("wb") as handle:
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    handle.write(chunk)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for GET {url}: {body}") from exc

    def upload_multipart(
        self,
        url: str,
        headers: dict[str, str],
        fields: dict[str, str],
        files: dict[str, Path],
    ) -> dict[str, Any] | None:
        boundary = f"----cmq-{uuid.uuid4().hex}"
        body = bytearray()

        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        for field_name, path in files.items():
            guessed_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{path.name}"\r\n'
                ).encode("utf-8")
            )
            body.extend(f"Content-Type: {guessed_type}\r\n\r\n".encode("utf-8"))
            body.extend(path.read_bytes())
            body.extend(b"\r\n")

        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req_headers = dict(headers)
        req_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = request.Request(url=url, data=bytes(body), headers=req_headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
                if not payload:
                    return None
                return json.loads(payload.decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for multipart POST {url}: {body}") from exc


class RemoteWorkerClient:
    """High-level client implementing worker API contract."""

    def __init__(
        self,
        server_url: str,
        worker_token: str,
        worker_name: str,
        worker_id: str,
        timeout: int = 60,
        transport: TransportProtocol | None = None,
        allow_insecure_http: bool = False,
        heartbeat_interval_hint: int | None = None,
        lease_timeout_hint: int | None = None,
    ) -> None:
        normalized_url = server_url.rstrip("/")
        parsed = parse.urlparse(normalized_url)
        if parsed.scheme not in {"https", "http"}:
            raise ValueError("server_url must use https:// (or http:// with explicit override)")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ValueError("insecure http server_url blocked; use https:// or allow_insecure_http=True")

        self.server_url = normalized_url
        self.worker_token = worker_token
        self.worker_name = worker_name
        self.worker_id = worker_id
        self.allow_insecure_http = allow_insecure_http
        self.heartbeat_interval_hint = heartbeat_interval_hint
        self.lease_timeout_hint = lease_timeout_hint
        self.transport: TransportProtocol = transport or UrllibTransport(timeout=timeout)

    def _url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return parse.urljoin(f"{self.server_url}/", normalized.lstrip("/"))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.worker_token}",
            "Accept": "application/json",
            "User-Agent": "ConvertModelForMetaQuest-Worker/0.11",
        }

    def _base_origin(self) -> str:
        parsed = parse.urlparse(self.server_url)
        return f"{parsed.scheme}://{parsed.netloc}".lower()

    @staticmethod
    def _url_origin(url: str) -> str:
        parsed = parse.urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}".lower()

    def _download_headers_for_url(self, download_url: str) -> dict[str, str]:
        # Do not leak bearer token to external signed-storage URLs.
        same_origin = self._url_origin(download_url) == self._base_origin()
        headers = {
            "Accept": "application/octet-stream",
            "User-Agent": "ConvertModelForMetaQuest-Worker/0.11",
        }
        if same_origin:
            headers["Authorization"] = f"Bearer {self.worker_token}"
        return headers

    def register_worker(self) -> WorkerSession:
        payload = {
            "worker_id": self.worker_id,
            "worker_name": self.worker_name,
            "capabilities": {"pipeline": "ConvertModelForMetaQuest", "format": "GLB"},
        }
        if self.heartbeat_interval_hint is not None:
            payload["heartbeat_interval"] = int(self.heartbeat_interval_hint)
        if self.lease_timeout_hint is not None:
            payload["lease_timeout"] = int(self.lease_timeout_hint)
        response = self.transport.json_request(
            method="POST",
            url=self._url("/api/v1/workers/register"),
            headers=self._headers(),
            payload=payload,
        ) or {}

        worker_id = str(response.get("worker_id") or response.get("id") or self.worker_id or "")
        if not worker_id:
            raise RuntimeError("register response missing worker_id")
        heartbeat_interval = int(response.get("heartbeat_interval") or 15)
        return WorkerSession(worker_id=worker_id, heartbeat_interval=heartbeat_interval)

    def heartbeat(self, worker_id: str) -> None:
        payload = {"worker_id": worker_id}
        self.transport.json_request(
            method="POST",
            url=self._url("/api/v1/workers/heartbeat"),
            headers=self._headers(),
            payload=payload,
        )

    def claim_job(self, worker_id: str, wait_seconds: int = 30) -> JobClaim | None:
        url = self._url(f"/api/v1/jobs/claim?wait={max(1, int(wait_seconds))}")
        payload = {"worker_id": worker_id}
        response = self.transport.json_request(
            method="POST",
            url=url,
            headers=self._headers(),
            payload=payload,
        )

        if not response:
            return None

        job_payload = response.get("job") if isinstance(response.get("job"), dict) else response
        if not job_payload:
            return None

        job_id = str(job_payload.get("job_id") or job_payload.get("id") or "")
        if not job_id:
            return None

        input_filename = str(job_payload.get("input_filename") or job_payload.get("filename") or f"{job_id}.obj")
        download_url = job_payload.get("download_url")
        return JobClaim(
            job_id=job_id,
            input_filename=input_filename,
            download_url=str(download_url) if download_url else None,
            payload=job_payload,
        )

    def download_job_file(self, claim: JobClaim, destination: Path) -> Path:
        download_url = claim.download_url or self._url(f"/api/v1/jobs/{claim.job_id}/download")
        self.transport.download_file(
            download_url,
            headers=self._download_headers_for_url(download_url),
            destination=destination,
        )
        return destination

    def upload_result(
        self,
        worker_id: str,
        claim: JobClaim,
        optimized_file: Path,
        report_file: Path,
        summary: str,
    ) -> dict[str, Any] | None:
        fields = {
            "worker_id": worker_id,
            "job_id": claim.job_id,
            "summary": summary,
            "input_filename": claim.input_filename,
        }
        files = {
            "optimized_file": optimized_file,
            "report_file": report_file,
        }
        return self.transport.upload_multipart(
            url=self._url(f"/api/v1/jobs/{claim.job_id}/result"),
            headers=self._headers(),
            fields=fields,
            files=files,
        )

    def report_failure(self, worker_id: str, claim: JobClaim, error_message: str) -> dict[str, Any] | None:
        payload = {
            "worker_id": worker_id,
            "job_id": claim.job_id,
            "error": error_message,
            "input_filename": claim.input_filename,
        }
        return self.transport.json_request(
            method="POST",
            url=self._url(f"/api/v1/jobs/{claim.job_id}/fail"),
            headers=self._headers(),
            payload=payload,
        )
