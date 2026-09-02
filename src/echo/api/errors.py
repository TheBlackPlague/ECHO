from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from echo.archive.errors import (
    ArchivePlanDisabledError,
    ArchivePlanNotFoundError,
    ArchivePlanRunningError,
    ArchiverDisabledError,
    ArchiverError,
    ArchiverNotRunningError,
    ArchiveRunNotCancellableError,
    ArchiveSourceError,
)
from echo.core.logging import get_logger
from echo.integrations.rclone import (
    RcloneCommandError,
    RcloneConfigurationError,
    RcloneError,
    RcloneOutputError,
    RcloneTimeoutError,
    RcloneUnavailableError,
)
from echo.storage.errors import RunNotFoundError, StorageError


def _response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content=jsonable_encoder(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "details": details,
                    "request_id": getattr(request.state, "request_id", None),
                }
            }
        ),
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
        details = None if isinstance(exc.detail, str) else exc.detail

        # noinspection bad-argument-type
        return _response(
            request,
            exc.status_code,
            f"http_{exc.status_code}",
            message,
            details=details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _response(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation_error",
            "The request was not valid",
            details=exc.errors(),
        )

    @app.exception_handler(ArchiverError)
    async def archiver_exception_handler(request: Request, exc: ArchiverError) -> JSONResponse:
        status_code, code, details = _archiver_error(exc)
        return _response(request, status_code, code, str(exc), details=details)

    @app.exception_handler(RunNotFoundError)
    async def run_not_found_handler(request: Request, exc: RunNotFoundError) -> JSONResponse:
        return _response(request, status.HTTP_404_NOT_FOUND, "run_not_found", str(exc))

    @app.exception_handler(StorageError)
    async def storage_exception_handler(request: Request, exc: StorageError) -> JSONResponse:
        return _response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "storage_unavailable",
            str(exc),
        )

    @app.exception_handler(RcloneError)
    async def rclone_exception_handler(request: Request, exc: RcloneError) -> JSONResponse:
        if isinstance(exc, RcloneTimeoutError):
            status_code, code = status.HTTP_504_GATEWAY_TIMEOUT, "rclone_timeout"
        elif isinstance(exc, (RcloneUnavailableError, RcloneConfigurationError)):
            status_code, code = status.HTTP_503_SERVICE_UNAVAILABLE, "rclone_unavailable"
        elif isinstance(exc, RcloneOutputError):
            status_code, code = status.HTTP_502_BAD_GATEWAY, "rclone_invalid_response"
        elif isinstance(exc, RcloneCommandError):
            status_code, code = status.HTTP_502_BAD_GATEWAY, "rclone_command_failed"
        else:
            status_code, code = status.HTTP_502_BAD_GATEWAY, "rclone_error"

        return _response(request, status_code, code, str(exc))

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger = get_logger(service="api")
        await logger.aerror(
            "Unhandled API error",
            request_id=getattr(request.state, "request_id", None),
            path=request.url.path,
            exc_info=exc,
        )

        return _response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "An unexpected internal error occurred",
        )


def _archiver_error(exc: ArchiverError) -> tuple[int, str, dict[str, Any] | None]:
    if isinstance(exc, ArchivePlanNotFoundError):
        return status.HTTP_404_NOT_FOUND, "plan_not_found", None
    if isinstance(exc, ArchivePlanRunningError):
        return status.HTTP_409_CONFLICT, "plan_already_running", {"run_id": exc.run_id}
    if isinstance(exc, ArchiveRunNotCancellableError):
        return status.HTTP_409_CONFLICT, "run_not_cancellable", None
    if isinstance(exc, ArchivePlanDisabledError):
        return status.HTTP_409_CONFLICT, "plan_disabled", None
    if isinstance(exc, ArchiverDisabledError):
        return status.HTTP_409_CONFLICT, "archiver_disabled", None
    if isinstance(exc, ArchiverNotRunningError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "archiver_unavailable", None
    if isinstance(exc, ArchiveSourceError):
        return status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_archive_source", None

    return status.HTTP_500_INTERNAL_SERVER_ERROR, "archiver_error", None
