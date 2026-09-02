from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from echo.api.dependencies import get_echo
from echo.api.schemas import ArchivePlanResponse, RunSummaryResponse, SubmitRunRequest
from echo.application import EchoApplication
from echo.archive.models import RunOperation


router = APIRouter(prefix="/plans", tags=["plans"])


async def _plan_response(echo: EchoApplication, name: str) -> ArchivePlanResponse:
    plan = echo.archiver.get_plan(name)
    plan_status = await echo.archiver.get_status(name)
    return ArchivePlanResponse(
        name=plan.name,
        source=plan.source,
        destination=plan.destination,
        cron=plan.cron,
        exclude=list(plan.exclude),
        enabled=plan.enabled,
        verify_after_archive=plan.verify_after_archive,
        scheduled=echo.scheduler.is_scheduled(plan.name),
        active_run_id=plan_status.active_run_id,
        latest_run=(
            RunSummaryResponse.from_run(plan_status.latest_run) if plan_status.latest_run else None
        ),
    )


@router.get("", response_model=list[ArchivePlanResponse], operation_id="listPlans")
async def list_plans(
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> list[ArchivePlanResponse]:
    return [await _plan_response(echo, plan.name) for plan in echo.archiver.plans]


@router.get("/{name}", response_model=ArchivePlanResponse, operation_id="getPlan")
async def get_plan(
    name: str,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> ArchivePlanResponse:
    return await _plan_response(echo, name)


@router.post(
    "/{name}/runs",
    response_model=RunSummaryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="submitArchiveRun",
)
async def submit_archive_run(
    name: str,
    payload: SubmitRunRequest,
    response: Response,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> RunSummaryResponse:
    run = await echo.archiver.submit(name, dry_run=payload.dry_run)
    response.headers["Location"] = f"/api/runs/{run.id}"
    return RunSummaryResponse.from_run(run)


@router.post(
    "/{name}/verifications",
    response_model=RunSummaryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="submitVerificationRun",
)
async def submit_verification_run(
    name: str,
    response: Response,
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> RunSummaryResponse:
    run = await echo.archiver.submit(name, operation=RunOperation.VERIFY)
    response.headers["Location"] = f"/api/runs/{run.id}"
    return RunSummaryResponse.from_run(run)
