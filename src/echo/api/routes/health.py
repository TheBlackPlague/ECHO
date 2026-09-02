from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from echo import __version__
from echo.api.dependencies import get_echo
from echo.api.schemas import HealthResponse
from echo.application import EchoApplication


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse, operation_id="getHealthLiveness")
async def live() -> HealthResponse:
    return HealthResponse(status="alive", version=__version__)


@router.get(
    "/ready",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
    operation_id="getHealthReadiness",
)
async def ready(
    response: Response,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> HealthResponse:
    if not echo.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="not_ready", version=__version__)
    return HealthResponse(status="ready", version=__version__)
