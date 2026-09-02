from __future__ import annotations

from datetime import datetime, UTC
from typing import Annotated

from fastapi import APIRouter, Depends

from echo import __version__
from echo.api.dependencies import get_echo
from echo.api.schemas import RcloneStatusResponse, RunCountsResponse, SystemStatusResponse
from echo.application import EchoApplication


router = APIRouter(prefix="/system", tags=["system"])


@router.get("", response_model=SystemStatusResponse, operation_id="getSystemStatus")
async def get_system_status(
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> SystemStatusResponse:
    counts = await echo.runs.summary()
    started_at = echo.started_at
    return SystemStatusResponse(
        version=__version__,
        started=echo.started,
        ready=echo.ready,
        started_at=started_at,
        uptime_seconds=(datetime.now(UTC) - started_at).total_seconds() if started_at else None,
        archive_enabled=echo.archiver.enabled,
        archiver_running=echo.archiver.running,
        scheduler_running=echo.scheduler.running,
        scheduler_last_tick=echo.scheduler.last_tick,
        configured_plans=len(echo.archiver.plans),
        enabled_plans=sum(plan.enabled for plan in echo.archiver.plans),
        scheduled_plans=len(echo.scheduler.plans),
        active_runs=echo.archiver.active_count,
        run_counts=RunCountsResponse(**{state.value: count for state, count in counts.items()}),
        remote=echo.rclone.remote,
        bucket=echo.rclone.bucket,
    )


@router.get("/rclone", response_model=RcloneStatusResponse, operation_id="getRcloneStatus")
async def get_rclone_status(
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> RcloneStatusResponse:
    rclone = echo.rclone_status
    return RcloneStatusResponse(
        configured=echo.rclone.remote is not None and echo.rclone.bucket is not None,
        available=rclone is not None,
        version=rclone.version if rclone else None,
        remote=rclone.remote if rclone else echo.rclone.remote,
        bucket=rclone.bucket if rclone else echo.rclone.bucket,
        remotes=list(rclone.remotes) if rclone else [],
        large_uploads_optimized=rclone.large_uploads_optimized if rclone else False,
    )
