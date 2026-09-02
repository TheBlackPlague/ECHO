from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr

from echo.archive.models import ArchiveRun, RunOperation, RunState, RunTrigger


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | list[Any] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["alive", "ready", "not_ready"]
    version: str


class AuthLoginRequest(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=4_096)


class AuthSessionResponse(BaseModel):
    authenticated: bool
    login_enabled: bool


class RunSummaryResponse(BaseModel):
    id: str
    plan_name: str
    operation: RunOperation
    trigger: RunTrigger
    state: RunState
    dry_run: bool
    source: Path
    destination: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    return_code: int | None = None
    error: str | None = None
    progress: float | None = Field(default=None, ge=0, le=100)
    files_added: int = Field(default=0, ge=0)
    files_verified: int = Field(default=0, ge=0)

    @classmethod
    def from_run(cls, run: ArchiveRun) -> RunSummaryResponse:
        return cls(
            id=run.id,
            plan_name=run.plan_name,
            operation=run.operation,
            trigger=run.trigger,
            state=run.state,
            dry_run=run.dry_run,
            source=run.source,
            destination=run.destination,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_seconds=run.duration_seconds,
            return_code=run.return_code,
            error=run.error,
            progress=run.progress,
            files_added=run.files_added,
            files_verified=run.files_verified,
        )


class RunDetailResponse(RunSummaryResponse):
    stdout: str | None = None
    stderr: str | None = None

    @classmethod
    def from_run(cls, run: ArchiveRun) -> RunDetailResponse:
        summary = RunSummaryResponse.from_run(run)
        return cls(**summary.model_dump(), stdout=run.stdout, stderr=run.stderr)


class RunPageResponse(BaseModel):
    items: list[RunSummaryResponse]
    limit: int
    offset: int
    has_more: bool


class SubmitRunRequest(BaseModel):
    dry_run: bool = False


class ArchivePlanResponse(BaseModel):
    name: str
    source: Path
    destination: str
    cron: str | None = None
    exclude: list[str]
    enabled: bool
    verify_after_archive: bool
    scheduled: bool
    active_run_id: str | None = None
    latest_run: RunSummaryResponse | None = None


class ArchiveItemResponse(BaseModel):
    path: str
    name: str
    size: int
    is_dir: bool
    mod_time: str | None = None
    mime_type: str | None = None
    tier: str | None = None
    hashes: dict[str, str] | None = None


class ArchiveListingResponse(BaseModel):
    bucket: str
    path: str | None = None
    items: list[ArchiveItemResponse]


class ArchiveSizeResponse(BaseModel):
    bytes: int
    objects: int


class RcloneStatusResponse(BaseModel):
    configured: bool
    available: bool
    version: str | None = None
    remote: str | None = None
    bucket: str | None = None
    remotes: list[str] = Field(default_factory=list)
    large_uploads_optimized: bool = False


class RemoteCapacityResponse(BaseModel):
    total: int | None = None
    used: int | None = None
    free: int | None = None
    trashed: int | None = None
    other: int | None = None


class RunCountsResponse(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    interrupted: int = 0


class SystemStatusResponse(BaseModel):
    version: str
    started: bool
    ready: bool
    started_at: datetime | None = None
    uptime_seconds: float | None = None
    archive_enabled: bool
    archiver_running: bool
    scheduler_running: bool
    scheduler_last_tick: datetime | None = None
    configured_plans: int
    enabled_plans: int
    scheduled_plans: int
    active_runs: int
    run_counts: RunCountsResponse
    remote: str | None = None
    bucket: str | None = None
