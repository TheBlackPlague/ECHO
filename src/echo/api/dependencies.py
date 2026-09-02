from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from echo.api.auth import require_allowed_origin, SESSION_COOKIE, web_session_is_valid
from echo.application import EchoApplication


_api_key_header = APIKeyHeader(name="X-ECHO-API-Key", auto_error=False)


def get_echo(request: Request) -> EchoApplication:
    return request.app.state.echo


async def require_api_key(
    request: Request,
    echo: Annotated[EchoApplication, Depends(get_echo)],
    supplied_key: Annotated[str | None, Security(_api_key_header)],
) -> None:
    configured_key = echo.config.api.api_key

    if configured_key is None: return

    expected = configured_key.get_secret_value()

    if supplied_key is not None and secrets.compare_digest(supplied_key, expected): return

    if web_session_is_valid(request.cookies.get(SESSION_COOKIE), echo.config.api):
        require_allowed_origin(request, echo.config.api)
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid API key or web session is required",
        headers={"WWW-Authenticate": "ApiKey"},
    )
