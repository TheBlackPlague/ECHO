from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import structlog.contextvars
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from echo import __version__
from echo.api.dependencies import require_api_key
from echo.api.errors import install_exception_handlers
from echo.api.routes import archive, auth, health, plans, runs, system
from echo.application import EchoApplication, get_application
from echo.core.config import get_config


_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DEFAULT_FRONTEND_DIR = Path("/app/frontend")


def create_api_app(application: EchoApplication | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_api: FastAPI) -> AsyncIterator[None]:
        echo = application or get_application()
        _api.state.echo = echo

        await echo.start()

        try:
            yield

        finally:
            await echo.stop()

    config = application.config if application is not None else get_config()
    docs_enabled = config.api.docs_enabled

    api = FastAPI(
        title="ECHO API",
        description="Emergency Copy Held Offsite - RESTful HTTP API",
        version=__version__,
        lifespan=lifespan,
        root_path=config.api.root_path,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url="/api/redoc" if docs_enabled else None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
    )

    if config.api.cors_origins:
        # noinspection bad-argument-type
        api.add_middleware(
            CORSMiddleware,
            allow_origins=config.api.cors_origins,
            allow_credentials="*" not in config.api.cors_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-ECHO-API-Key", "X-Request-ID"],
            expose_headers=["Location", "X-Request-ID"],
        )

    @api.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid4())

        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

        finally:
            structlog.contextvars.clear_contextvars()

    install_exception_handlers(api)

    api.include_router(health.router, prefix="/api")
    api.include_router(auth.router, prefix="/api")

    control_plane = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])
    frontend_dir = _frontend_directory()

    if frontend_dir is None:
        @control_plane.get("/", tags=["system"], operation_id="getApiIndex")
        async def api_index() -> dict[str, str]:
            return {"name": "ECHO API", "version": __version__}

    control_plane.include_router(system.router)
    control_plane.include_router(plans.router)
    control_plane.include_router(runs.router)
    control_plane.include_router(archive.router)

    api.include_router(control_plane)

    if frontend_dir is not None:
        api.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return api


def _frontend_directory() -> Path | None:
    frontend_dir = Path(os.getenv("ECHO_FRONTEND_DIR", _DEFAULT_FRONTEND_DIR))
    return frontend_dir if (frontend_dir / "index.html").is_file() else None


app = create_api_app()
