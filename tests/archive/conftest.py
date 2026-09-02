from __future__ import annotations

from dataclasses import replace
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import pytest

from echo.archive.models import ArchiveRun, RunOperation, RunState, RunTrigger
from echo.core.config import ArchiveConfig, ArchivePlanConfig
from echo.integrations.rclone import RcloneCommandResult, RcloneVerificationResult
from echo.storage.errors import InvalidRunTransitionError, RunNotFoundError


class FakeRunRepository:
    def __init__(self) -> None:
        self.items: dict[str, ArchiveRun] = {}
        self.progress_updates: list[tuple[float, int, int]] = []
        self.transitions: list[tuple[str, RunState]] = []

    async def create(self, run: ArchiveRun) -> ArchiveRun:
        self.items[run.id] = run
        return run

    async def get(self, run_id: str) -> ArchiveRun:
        try:
            return self.items[run_id]
        except KeyError as exc:
            raise RunNotFoundError(run_id) from exc

    async def latest_for_plan(self, plan_name: str) -> ArchiveRun | None:
        matches = [run for run in self.items.values() if run.plan_name == plan_name]
        return max(matches, key=lambda run: run.created_at) if matches else None

    async def transition(
        self,
        run_id: str,
        *,
        expected: tuple[RunState, ...],
        state: RunState,
        **changes: Any,
    ) -> ArchiveRun:
        run = await self.get(run_id)
        if run.state not in expected:
            raise InvalidRunTransitionError(f"{run.state} -> {state}")
        run = replace(run, state=state, **changes)
        self.items[run_id] = run
        self.transitions.append((run_id, state))
        return run

    async def update_progress(
        self,
        run_id: str,
        *,
        progress: float,
        files_added: int,
        files_verified: int,
    ) -> ArchiveRun:
        run = await self.get(run_id)
        if run.state is RunState.RUNNING:
            run = replace(
                run,
                progress=max(run.progress or 0.0, progress),
                files_added=max(run.files_added, files_added),
                files_verified=max(run.files_verified, files_verified),
            )
            self.items[run_id] = run
        self.progress_updates.append((progress, files_added, files_verified))
        return run


def command_result(
    *,
    return_code: int = 0,
    stdout: str = "copied",
    stderr: str = "",
) -> RcloneCommandResult:
    return RcloneCommandResult(
        command=("rclone", "copy"),
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.25,
    )


def verification_result(files_verified: int = 0) -> RcloneVerificationResult:
    return RcloneVerificationResult(
        result=command_result(stdout="verified"),
        files_verified=files_verified,
    )


def make_run(
    source: Path,
    *,
    run_id: str = "run-1",
    plan_name: str = "photos",
    operation: RunOperation = RunOperation.ARCHIVE,
    state: RunState = RunState.QUEUED,
    dry_run: bool = False,
) -> ArchiveRun:
    return ArchiveRun(
        id=run_id,
        plan_name=plan_name,
        operation=operation,
        trigger=RunTrigger.MANUAL,
        state=state,
        dry_run=dry_run,
        source=source,
        destination="backups/photos",
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def plan(tmp_path: Path) -> ArchivePlanConfig:
    source = tmp_path / "source"
    source.mkdir()
    return ArchivePlanConfig(
        name="photos",
        source=source,
        destination="backups/photos",
        exclude=["*.tmp"],
    )


@pytest.fixture
def archive_config(plan: ArchivePlanConfig) -> ArchiveConfig:
    return ArchiveConfig(enabled=True, plans=[plan])
