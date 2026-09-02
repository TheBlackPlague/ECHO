from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from echo.api.errors import _archiver_error, _response, install_exception_handlers
from echo.archive.errors import (
    ArchivePlanDisabledError, ArchivePlanNotFoundError, ArchivePlanRunningError, ArchiverDisabledError, ArchiverError,
    ArchiverNotRunningError, ArchiveRunNotCancellableError, ArchiveSourceError
)
from echo.integrations.rclone import (
    RcloneCommandError,
    RcloneCommandResult,
    RcloneConfigurationError,
    RcloneError,
    RcloneOutputError,
    RcloneTimeoutError,
    RcloneUnavailableError,
)
from echo.storage.errors import RunNotFoundError, StorageError


def _exception_client(exc_factory: Callable[[], Exception]) -> TestClient:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/fail")
    async def fail() -> None:
        raise exc_factory()

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("exc", "status_code", "code", "details"),
    [
        (ArchivePlanNotFoundError("missing"), 404, "plan_not_found", None),
        (
                ArchivePlanRunningError("media", "run-9"),
                409,
                "plan_already_running",
                {"run_id": "run-9"},
        ),
        (ArchiveRunNotCancellableError("done"), 409, "run_not_cancellable", None),
        (ArchivePlanDisabledError("disabled"), 409, "plan_disabled", None),
        (ArchiverDisabledError("off"), 409, "archiver_disabled", None),
        (ArchiverNotRunningError("stopped"), 503, "archiver_unavailable", None),
        (ArchiveSourceError("bad source"), 422, "invalid_archive_source", None),
        (ArchiverError("unknown"), 500, "archiver_error", None),
    ],
)
def test_archiver_error_mapping(
    exc: ArchiverError,
    status_code: int,
    code: str,
    details: dict[str, str] | None,
) -> None:
    assert _archiver_error(exc) == (status_code, code, details)
    with _exception_client(lambda: exc) as client:
        response = client.get("/fail")
    assert response.status_code == status_code
    assert response.json()["error"] == {
        "code": code,
        "message": str(exc),
        "details": details,
        "request_id": None,
    }


@pytest.mark.parametrize(
    ("factory", "status_code", "code"),
    [
        (lambda: RcloneTimeoutError(["rclone", "lsf"], 2), 504, "rclone_timeout"),
        (lambda: RcloneUnavailableError("missing"), 503, "rclone_unavailable"),
        (lambda: RcloneConfigurationError("bad config"), 503, "rclone_unavailable"),
        (lambda: RcloneOutputError("bad json"), 502, "rclone_invalid_response"),
        (
                lambda: RcloneCommandError(
                    RcloneCommandResult(("rclone",), 1, "", "failed", 0.1)
                ),
                502,
                "rclone_command_failed",
        ),
        (lambda: RcloneError("generic"), 502, "rclone_error"),
    ],
)
def test_rclone_error_mapping(
    factory: Callable[[], RcloneError], status_code: int, code: str
) -> None:
    with _exception_client(factory) as client:
        response = client.get("/fail")
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


def test_http_exception_string_detail_and_headers() -> None:
    with _exception_client(
            lambda: HTTPException(418, "short and stout", headers={"X-Test": "yes"})
    ) as client:
        response = client.get("/fail")
    assert response.status_code == 418
    assert response.headers["X-Test"] == "yes"
    assert response.json()["error"]["message"] == "short and stout"
    assert response.json()["error"]["details"] is None


def test_http_exception_structured_detail_uses_status_phrase() -> None:
    with _exception_client(lambda: HTTPException(400, {"reason": "invalid"})) as client:
        response = client.get("/fail")
    assert response.json()["error"]["message"] == "Bad Request"
    assert response.json()["error"]["details"] == {"reason": "invalid"}


@pytest.mark.parametrize(
    ("factory", "status_code", "code"),
    [
        (lambda: RunNotFoundError("no run"), 404, "run_not_found"),
        (lambda: StorageError("database down"), 503, "storage_unavailable"),
    ],
)
def test_storage_error_mapping(
    factory: Callable[[], StorageError], status_code: int, code: str
) -> None:
    with _exception_client(factory) as client:
        response = client.get("/fail")
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


def test_request_validation_error_envelope() -> None:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> int:
        return item_id

    with TestClient(app) as client:
        response = client.get("/items/not-an-int")
    body = response.json()["error"]
    assert response.status_code == 422
    assert body["code"] == "validation_error"
    assert body["message"] == "The request was not valid"
    assert body["details"][0]["loc"] == ["path", "item_id"]


def test_unexpected_exception_is_sanitized() -> None:
    with _exception_client(lambda: RuntimeError("sensitive detail")) as client:
        response = client.get("/fail")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "sensitive detail" not in response.text


def test_response_includes_request_id_and_encodes_details() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request._state = SimpleNamespace(request_id="req-1")
    response = _response(
        request,
        409,
        "conflict",
        "Conflict",
        details={"at": datetime(2026, 1, 1, tzinfo=UTC)},
    )
    assert response.status_code == 409
    assert b'"request_id":"req-1"' in response.body
    assert b'"2026-01-01T00:00:00+00:00"' in response.body
