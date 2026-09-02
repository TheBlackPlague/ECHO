from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from starlette.requests import Request

from echo.api.auth import (
    _canonical_origin, cookie_path, issue_web_session, require_allowed_origin, SESSION_COOKIE, web_session_is_valid
)
from echo.api.dependencies import get_echo, require_api_key
from echo.core.config import APIConfig


def _request(
    method: str = "GET",
    *,
    headers: dict[str, str] | None = None,
    scheme: str = "http",
    app: object | None = None,
) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": scheme,
            "path": "/api/action",
            "root_path": "",
            "query_string": b"",
            "headers": raw_headers,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "app": app,
        }
    )


def _auth_config(**changes: object) -> APIConfig:
    values: dict[str, object] = {
        "api_key": SecretStr("a" * 16),
        "web_password": SecretStr("correct horse"),
        "session_ttl_seconds": 300,
    }
    values.update(changes)
    return APIConfig(**values)


def test_session_round_trip_and_expiration() -> None:
    config = _auth_config()
    token = issue_web_session(config, now=1_000)

    assert web_session_is_valid(token, config, now=1_299)
    assert not web_session_is_valid(token, config, now=1_300)
    assert not web_session_is_valid(token + "tampered", config, now=1_001)


@pytest.mark.parametrize(
    "token",
    [None, "", "v2.9999.nonce.signature", "v1.nope.nonce.signature", "v1.9999..sig", "x"],
)
def test_invalid_session_tokens_are_rejected(token: str | None) -> None:
    assert not web_session_is_valid(token, _auth_config(), now=1)


def test_session_authentication_requires_both_secrets() -> None:
    with pytest.raises(ValueError, match="not configured"):
        issue_web_session(APIConfig(), now=1)

    assert not web_session_is_valid("anything", APIConfig(), now=1)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://Example.COM", "https://example.com"),
        ("http://example.com/", "http://example.com"),
        (None, None),
        ("null", None),
        ("ftp://example.com", None),
        ("https://user@example.com", None),
        ("https://example.com/path", None),
        ("https://example.com?q=1", None),
    ],
)
def test_canonical_origin(value: str | None, expected: str | None) -> None:
    assert _canonical_origin(value) == expected


def test_origin_check_allows_safe_methods_same_host_and_configured_origins() -> None:
    config = _auth_config(cors_origins=["https://ui.example"])
    require_allowed_origin(_request("GET"), config)
    require_allowed_origin(
        _request("POST", headers={"host": "testserver", "origin": "http://testserver"}),
        config,
    )
    require_allowed_origin(
        _request("DELETE", headers={"host": "testserver", "origin": "https://ui.example"}),
        config,
    )


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example"])
def test_origin_check_rejects_missing_or_foreign_origin(origin: str | None) -> None:
    headers = {"host": "testserver"}
    if origin is not None:
        headers["origin"] = origin

    with pytest.raises(HTTPException) as raised:
        require_allowed_origin(_request("POST", headers=headers), _auth_config())

    assert raised.value.status_code == 403


@pytest.mark.parametrize(("root_path", "expected"), [("", "/api"), ("/echo", "/echo/api")])
def test_cookie_path_tracks_root_path(root_path: str, expected: str) -> None:
    assert cookie_path(APIConfig(root_path=root_path)) == expected


def test_get_echo_reads_application_state() -> None:
    echo = object()
    app = SimpleNamespace(state=SimpleNamespace(echo=echo))
    assert get_echo(_request(app=app)) is echo


@pytest.mark.asyncio
async def test_api_key_dependency_allows_unconfigured_or_matching_key() -> None:
    unconfigured = SimpleNamespace(config=SimpleNamespace(api=APIConfig()))
    await require_api_key(_request(), unconfigured, None)

    echo = SimpleNamespace(config=SimpleNamespace(api=_auth_config()))
    await require_api_key(_request(), echo, "a" * 16)


@pytest.mark.asyncio
async def test_api_key_dependency_accepts_valid_session_and_enforces_origin() -> None:
    config = _auth_config()
    token = issue_web_session(config)
    echo = SimpleNamespace(config=SimpleNamespace(api=config))
    request = _request(
        "POST",
        headers={
            "host": "testserver",
            "origin": "http://testserver",
            "cookie": f"{SESSION_COOKIE}={token}",
        },
    )
    await require_api_key(request, echo, None)

    bad_origin = _request(
        "POST",
        headers={
            "host": "testserver",
            "origin": "https://evil.example",
            "cookie": f"{SESSION_COOKIE}={token}",
        },
    )
    with pytest.raises(HTTPException) as raised:
        await require_api_key(bad_origin, echo, None)
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_api_key_dependency_rejects_invalid_credentials() -> None:
    echo = SimpleNamespace(config=SimpleNamespace(api=_auth_config()))
    with pytest.raises(HTTPException) as raised:
        await require_api_key(_request(), echo, "wrong")

    assert raised.value.status_code == 401
    assert raised.value.headers == {"WWW-Authenticate": "ApiKey"}
