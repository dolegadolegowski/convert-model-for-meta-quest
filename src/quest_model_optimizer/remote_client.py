"""HTTPS client for remote worker-server communication."""

from __future__ import annotations

import json
import mimetypes
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from urllib import error, parse, request

from .worker_models import JobClaim, WorkerSession

RUNTIME_KEYS = (
    "poll_wait_seconds",
    "heartbeat_interval",
    "reconnect_after_failures",
    "max_backoff_seconds",
    "http_timeout_seconds",
    "download_timeout_seconds",
    "upload_timeout_seconds",
    "download_retries",
    "upload_retries",
)


class ApiRequestError(RuntimeError):
    """Raised when server responds with non-2xx HTTP status."""

    def __init__(self, status_code: int, method: str, url: str, body: str) -> None:
        self.status_code = int(status_code)
        self.method = method
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status_code} for {method} {url}: {body}")


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

    def __init__(
        self,
        timeout: int = 60,
        download_timeout: int | None = None,
        upload_timeout: int | None = None,
    ) -> None:
        self.timeout = max(1, int(timeout))
        self.download_timeout = max(1, int(download_timeout or self.timeout))
        self.upload_timeout = max(1, int(upload_timeout or max(self.timeout, 300)))

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
            raise ApiRequestError(status_code=exc.code, method=method, url=url, body=body) from exc

    def download_file(self, url: str, headers: dict[str, str], destination: Path) -> None:
        req = request.Request(url=url, headers=headers, method="GET")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with request.urlopen(req, timeout=self.download_timeout) as resp, destination.open("wb") as handle:
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    handle.write(chunk)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ApiRequestError(status_code=exc.code, method="GET", url=url, body=body) from exc

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
            with request.urlopen(req, timeout=self.upload_timeout) as resp:
                payload = resp.read()
                if not payload:
                    return None
                return json.loads(payload.decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ApiRequestError(status_code=exc.code, method="POST", url=url, body=body) from exc


class RemoteWorkerClient:
    """High-level client implementing worker API contract."""

    def __init__(
        self,
        server_url: str,
        worker_token: str,
        worker_name: str,
        worker_id: str,
        timeout: int = 60,
        download_timeout: int | None = None,
        upload_timeout: int | None = None,
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
        self.http_timeout_seconds = max(1, int(timeout))
        self.download_timeout_seconds = max(1, int(download_timeout or self.http_timeout_seconds))
        self.upload_timeout_seconds = max(1, int(upload_timeout or max(self.http_timeout_seconds, 300)))
        self.allow_insecure_http = allow_insecure_http
        self.heartbeat_interval_hint = heartbeat_interval_hint
        self.lease_timeout_hint = lease_timeout_hint
        self.transport: TransportProtocol = transport or UrllibTransport(
            timeout=self.http_timeout_seconds,
            download_timeout=self.download_timeout_seconds,
            upload_timeout=self.upload_timeout_seconds,
        )

    @staticmethod
    def _positive_int_or_none(value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    def _extract_runtime_config(self, response: dict[str, Any] | None) -> dict[str, int]:
        if not isinstance(response, dict):
            return {}

        runtime_payload = response.get("runtime_config")
        runtime_map = runtime_payload if isinstance(runtime_payload, dict) else {}
        updates: dict[str, int] = {}
        for key in RUNTIME_KEYS:
            value = runtime_map.get(key, response.get(key))
            normalized = self._positive_int_or_none(value)
            if normalized is not None:
                updates[key] = normalized
        return updates

    def apply_runtime_config(self, runtime_config: dict[str, int]) -> None:
        if not runtime_config:
            return

        http_timeout = runtime_config.get("http_timeout_seconds")
        download_timeout = runtime_config.get("download_timeout_seconds")
        upload_timeout = runtime_config.get("upload_timeout_seconds")

        if http_timeout is not None:
            self.http_timeout_seconds = max(1, int(http_timeout))
        if download_timeout is not None:
            self.download_timeout_seconds = max(1, int(download_timeout))
        if upload_timeout is not None:
            self.upload_timeout_seconds = max(1, int(upload_timeout))

        if isinstance(self.transport, UrllibTransport):
            self.transport.timeout = self.http_timeout_seconds
            self.transport.download_timeout = self.download_timeout_seconds
            self.transport.upload_timeout = self.upload_timeout_seconds

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

    def _normalize_url(self, url_or_path: str) -> str:
        parsed = parse.urlparse(url_or_path)
        if parsed.scheme:
            return url_or_path
        return self._url(url_or_path)

    @staticmethod
    def _coerce_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _extract_lease_token(self, *payloads: dict[str, Any] | None) -> str | None:
        direct_keys = ("lease_token", "leaseToken", "claim_token", "claimToken")
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for key in direct_keys:
                token = self._coerce_optional_string(payload.get(key))
                if token:
                    return token

            lease = payload.get("lease")
            if isinstance(lease, dict):
                for key in ("token", "lease_token", "leaseToken", "claim_token", "claimToken"):
                    token = self._coerce_optional_string(lease.get(key))
                    if token:
                        return token

            claim = payload.get("claim")
            if isinstance(claim, dict):
                for key in direct_keys:
                    token = self._coerce_optional_string(claim.get(key))
                    if token:
                        return token
                lease = claim.get("lease")
                if isinstance(lease, dict):
                    for key in ("token", "lease_token", "leaseToken", "claim_token", "claimToken"):
                        token = self._coerce_optional_string(lease.get(key))
                        if token:
                            return token
        return None

    def _append_query_params(self, url: str, params: dict[str, str | None]) -> str:
        parsed = parse.urlparse(url)
        query = dict(parse.parse_qsl(parsed.query, keep_blank_values=True))
        for key, value in params.items():
            normalized_value = self._coerce_optional_string(value)
            if normalized_value and key not in query:
                query[key] = normalized_value
        updated_query = parse.urlencode(query)
        return parse.urlunparse(parsed._replace(query=updated_query))

    @staticmethod
    def _compute_file_sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 64)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest().lower()

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
        runtime_config = self._extract_runtime_config(response)
        self.apply_runtime_config(runtime_config)
        heartbeat_interval = int(runtime_config.get("heartbeat_interval") or response.get("heartbeat_interval") or 15)
        return WorkerSession(
            worker_id=worker_id,
            heartbeat_interval=heartbeat_interval,
            runtime_config=runtime_config or None,
        )

    def heartbeat(self, worker_id: str) -> dict[str, int]:
        payload = {"worker_id": worker_id}
        response = self.transport.json_request(
            method="POST",
            url=self._url("/api/v1/workers/heartbeat"),
            headers=self._headers(),
            payload=payload,
        )
        runtime_config = self._extract_runtime_config(response if isinstance(response, dict) else None)
        self.apply_runtime_config(runtime_config)
        return runtime_config

    def claim_job(self, worker_id: str, wait_seconds: int = 30) -> JobClaim | None:
        url = self._append_query_params(
            self._url("/api/v1/jobs/claim"),
            {
                "wait": str(max(1, int(wait_seconds))),
                "worker_id": worker_id,
            },
        )
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
        lease_token = self._extract_lease_token(
            job_payload if isinstance(job_payload, dict) else None,
            response if isinstance(response, dict) else None,
        )
        return JobClaim(
            job_id=job_id,
            input_filename=input_filename,
            download_url=str(download_url) if download_url else None,
            payload=job_payload,
            lease_token=lease_token,
        )

    def download_job_file(self, claim: JobClaim, destination: Path, worker_id: str | None = None) -> Path:
        raw_download_url = claim.download_url or f"/api/v1/jobs/{claim.job_id}/download"
        download_url = self._normalize_url(raw_download_url)
        if self._url_origin(download_url) == self._base_origin():
            claim_worker_id = None
            if isinstance(claim.payload, dict):
                claim_worker_id = self._coerce_optional_string(claim.payload.get("worker_id"))
            download_url = self._append_query_params(
                download_url,
                {
                    "worker_id": worker_id or claim_worker_id or self.worker_id,
                    "lease_token": claim.lease_token or self._extract_lease_token(claim.payload),
                },
            )
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
        lease_token = claim.lease_token or self._extract_lease_token(claim.payload)
        if not lease_token:
            raise RuntimeError(f"missing lease_token for upload_result job_id={claim.job_id}")

        source_checksum = (
            self._coerce_optional_string(claim.payload.get("source_checksum"))
            or self._coerce_optional_string(claim.payload.get("source_sha256"))
            or self._coerce_optional_string(claim.payload.get("input_sha256"))
            or self._coerce_optional_string(claim.payload.get("sha256"))
        )
        if not source_checksum:
            raise RuntimeError(f"missing source checksum for upload_result job_id={claim.job_id}")
        result_checksum = self._compute_file_sha256(optimized_file)
        checksum_bundle: dict[str, Any] = {
            "source_checksum": source_checksum,
            "source_sha256": source_checksum,
            "input_sha256": source_checksum,
            "result_checksum": result_checksum,
            "result_sha256": result_checksum,
            "output_sha256": result_checksum,
            "source": {"sha256": source_checksum},
            "result": {"sha256": result_checksum},
        }

        metadata_payload: dict[str, Any] = {
            "job_id": claim.job_id,
            "input_filename": claim.input_filename,
            "summary": summary,
            "metadata_version": 1,
            **checksum_bundle,
            "checksums": checksum_bundle,
            "worker_metadata": checksum_bundle,
        }
        try:
            metadata_payload["report"] = json.loads(report_file.read_text(encoding="utf-8"))
        except Exception:
            # Keep upload resilient even when local report file is malformed.
            metadata_payload["report_parse_error"] = "failed to parse local report JSON"

        fields = {
            "worker_id": worker_id,
            "job_id": claim.job_id,
            "summary": summary,
            "input_filename": claim.input_filename,
            "lease_token": lease_token,
            "source_checksum": source_checksum,
            "source_sha256": source_checksum,
            "input_sha256": source_checksum,
            "result_checksum": result_checksum,
            "result_sha256": result_checksum,
            "output_sha256": result_checksum,
            "metadata_json": json.dumps(metadata_payload, ensure_ascii=False),
            "worker_metadata_json": json.dumps(checksum_bundle, ensure_ascii=False),
        }
        files = {
            "result_file": optimized_file,
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
