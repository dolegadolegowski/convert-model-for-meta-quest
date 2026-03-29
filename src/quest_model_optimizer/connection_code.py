from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
from typing import Any

CONNECTION_CODE_VERSION = 1
CONNECTION_CODE_SECRET_ENV = "CMQ_CONNECTION_CODE_SECRET"
CONNECTION_CODE_SECRET_ENV_LEGACY = "WORKER_CONNECTION_CODE_SHARED_SECRET"
_NONCE_SIZE = 16
_MAC_SIZE = 32
_REQUIRED_FIELDS = ("server_url", "worker_token", "worker_name")


class ConnectionCodeError(ValueError):
    pass


def _resolve_shared_secret(explicit_secret: str | None) -> str:
    if explicit_secret is not None:
        value = str(explicit_secret).strip()
        if value:
            return value
    env_value = str(os.getenv(CONNECTION_CODE_SECRET_ENV, "")).strip()
    if env_value:
        return env_value
    legacy_env_value = str(os.getenv(CONNECTION_CODE_SECRET_ENV_LEGACY, "")).strip()
    if legacy_env_value:
        return legacy_env_value
    raise ConnectionCodeError(
        f"Missing connection code secret. Set env {CONNECTION_CODE_SECRET_ENV}."
    )


def _derive_key(*, shared_secret: str | None = None) -> bytes:
    secret = _resolve_shared_secret(shared_secret)
    if not secret:
        raise ConnectionCodeError(f"Missing connection code secret. Set env {CONNECTION_CODE_SECRET_ENV}.")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _keystream(*, key: bytes, nonce: bytes, length: int) -> bytes:
    blocks: list[bytes] = []
    produced = 0
    counter = 0
    while produced < length:
        block = hashlib.sha256(key + nonce + struct.pack(">I", counter)).digest()
        blocks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(blocks)[:length]


def _xor_bytes(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


def encode_connection_code(payload: dict[str, Any], *, shared_secret: str | None = None) -> str:
    key = _derive_key(shared_secret=shared_secret)
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode("utf-8")
    nonce = secrets.token_bytes(_NONCE_SIZE)
    stream = _keystream(key=key, nonce=nonce, length=len(plaintext))
    ciphertext = _xor_bytes(plaintext, stream)
    mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    blob = bytes([CONNECTION_CODE_VERSION]) + nonce + mac + ciphertext
    return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def decode_connection_code(code: str, *, shared_secret: str | None = None) -> dict[str, Any]:
    raw = str(code or "").strip()
    if not raw:
        raise ConnectionCodeError("Connection code is empty")
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        blob = base64.urlsafe_b64decode(raw + pad)
    except Exception as exc:  # pragma: no cover
        raise ConnectionCodeError("Connection code is not valid base64url data") from exc

    min_size = 1 + _NONCE_SIZE + _MAC_SIZE
    if len(blob) < min_size:
        raise ConnectionCodeError("Connection code payload is too short")

    version = blob[0]
    if version != CONNECTION_CODE_VERSION:
        raise ConnectionCodeError(f"Unsupported connection code version: {version}")

    nonce = blob[1 : 1 + _NONCE_SIZE]
    mac = blob[1 + _NONCE_SIZE : 1 + _NONCE_SIZE + _MAC_SIZE]
    ciphertext = blob[1 + _NONCE_SIZE + _MAC_SIZE :]
    key = _derive_key(shared_secret=shared_secret)
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ConnectionCodeError("Connection code signature mismatch")

    plaintext = _xor_bytes(ciphertext, _keystream(key=key, nonce=nonce, length=len(ciphertext)))
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:  # pragma: no cover
        raise ConnectionCodeError("Connection code payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ConnectionCodeError("Connection code payload must be an object")

    missing = [field for field in _REQUIRED_FIELDS if not str(payload.get(field, "")).strip()]
    if missing:
        raise ConnectionCodeError(f"Missing required fields in connection code: {', '.join(missing)}")
    return payload


def connect_button_state(*, connected: bool, config_valid: bool) -> tuple[bool, str]:
    if connected:
        return True, "Disconnect"
    return bool(config_valid), "Connect"
