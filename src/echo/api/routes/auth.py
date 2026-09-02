from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from echo.api.auth import (cookie_path, issue_web_session, require_allowed_origin, SESSION_COOKIE, web_session_is_valid)
from echo.api.dependencies import get_echo
from echo.api.schemas import AuthLoginRequest, AuthSessionResponse
from echo.application import EchoApplication


router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/session", response_model=AuthSessionResponse, operation_id="getAuthSession")
async def get_auth_session(
    request: Request,
    response: Response,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> AuthSessionResponse:
    response.headers["Cache-Control"] = "no-store"
    config = echo.config.api
    authentication_required = config.api_key is not None

    return AuthSessionResponse(
        authenticated=not authentication_required or web_session_is_valid(request.cookies.get(SESSION_COOKIE), config),
        login_enabled=authentication_required and config.web_password is not None,
    )


@router.post("/login", response_model=AuthSessionResponse, operation_id="login")
async def login(
    payload: AuthLoginRequest,
    request: Request,
    response: Response,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> AuthSessionResponse:
    config = echo.config.api
    require_allowed_origin(request, config)
    response.headers["Cache-Control"] = "no-store"

    if config.api_key is None:
        return AuthSessionResponse(authenticated=True, login_enabled=False)
    if config.web_password is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web login is not configured",
        )

    supplied = payload.password.get_secret_value().encode("utf-8")
    expected = config.web_password.get_secret_value().encode("utf-8")
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The password was not accepted",
        )

    response.set_cookie(
        key=SESSION_COOKIE,
        value=issue_web_session(config),
        max_age=config.session_ttl_seconds,
        path=cookie_path(config),
        secure=config.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return AuthSessionResponse(authenticated=True, login_enabled=True)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="logout")
async def logout(
    request: Request,
    response: Response,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> None:
    config = echo.config.api
    require_allowed_origin(request, config)
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        SESSION_COOKIE,
        path=cookie_path(config),
        secure=config.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
