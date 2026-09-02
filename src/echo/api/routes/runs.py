from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from echo.api.dependencies import get_echo
from echo.api.schemas import RunDetailResponse, RunPageResponse, RunSummaryResponse
from echo.application import EchoApplication
from echo.archive.models import RunOperation, RunState


router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=RunPageResponse, operation_id="listRuns")
async def list_runs(
    echo: Annotated[EchoApplication, Depends(get_echo)],
    plan_name: Annotated[str | None, Query()] = None,
    state: Annotated[RunState | None, Query()] = None,
    operation: Annotated[RunOperation | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunPageResponse:
    runs = await echo.runs.list(
        plan_name=plan_name,
        state=state,
        operation=operation,
        limit=limit + 1,
        offset=offset,
    )
    return RunPageResponse(
        items=[RunSummaryResponse.from_run(run) for run in runs[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(runs) > limit,
    )


@router.get("/{run_id}", response_model=RunDetailResponse, operation_id="getRun")
async def get_run(
    run_id: str,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> RunDetailResponse:
    return RunDetailResponse.from_run(await echo.runs.get(run_id))


@router.post("/{run_id}/cancel", response_model=RunDetailResponse, operation_id="cancelRun")
async def cancel_run(
    run_id: str,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> RunDetailResponse:
    return RunDetailResponse.from_run(await echo.archiver.cancel(run_id))
