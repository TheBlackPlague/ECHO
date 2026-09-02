from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from echo.core.config import APIConfig


SESSION_COOKIE = "echo_session"
_SESSION_VERSION = "v1"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def issue_web_session(config: APIConfig, *, now: int | None = None) -> str:
    key = _session_key(config)
    if key is None: raise ValueError("Web session authentication is not configured")

    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + config.session_ttl_seconds
    nonce = secrets.token_urlsafe(18)
    message = f"{_SESSION_VERSION}.{expires_at}.{nonce}"
    signature = _encode(hmac.digest(key, message.encode("ascii"), "sha256"))
    return f"{message}.{signature}"


def web_session_is_valid(
    token: str | None,
    config: APIConfig,
    *,
    now: int | None = None,
) -> bool:
    key = _session_key(config)
    if key is None or token is None: return False

    try:
        version, expiration, nonce, supplied_signature = token.split(".", 3)
        expires_at = int(expiration)
        if version != _SESSION_VERSION or not nonce or expires_at <= (int(time.time()) if now is None else now):
            return False
        message = f"{version}.{expires_at}.{nonce}"
        expected_signature = _encode(hmac.digest(key, message.encode("ascii"), "sha256"))
        return secrets.compare_digest(supplied_signature, expected_signature)
    except (UnicodeEncodeError, ValueError):
        return False


def require_allowed_origin(request: Request, config: APIConfig) -> None:
    if request.method.upper() not in _UNSAFE_METHODS: return

    supplied = _canonical_origin(request.headers.get("origin"))
    host = request.headers.get("host")
    expected = _canonical_origin(f"{request.url.scheme}://{host}") if host else None
    configured = {
        origin
        for value in config.cors_origins
        if value != "*" and (origin := _canonical_origin(value)) is not None
    }

    if supplied is None or (supplied != expected and supplied not in configured):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The request Origin is not allowed for cookie authentication",
        )


def cookie_path(config: APIConfig) -> str:
    return f"{config.root_path}/api" or "/api"


def _session_key(config: APIConfig) -> bytes | None:
    if config.api_key is None or config.web_password is None: return None

    api_key = config.api_key.get_secret_value().encode("utf-8")
    password = config.web_password.get_secret_value().encode("utf-8")
    return hashlib.sha256(b"ECHO web session v1\0" + api_key + b"\0" + password).digest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical_origin(value: str | None) -> str | None:
    if not value or value == "null": return None

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}"
