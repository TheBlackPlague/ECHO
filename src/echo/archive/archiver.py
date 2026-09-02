from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from uuid import uuid4

from echo.archive.errors import (
    ArchivePlanDisabledError,
    ArchivePlanNotFoundError,
    ArchivePlanRunningError,
    ArchiverDisabledError,
    ArchiverNotRunningError,
    ArchiveRunNotCancellableError,
    ArchiveSourceError,
)
from echo.archive.models import ArchiveRun, RunOperation, RunState, RunTrigger
from echo.core.config import ArchiveConfig, ArchivePlanConfig
from echo.core.logging import get_logger
from echo.integrations.rclone import (
    RcloneClient,
    RcloneCommandError,
    RcloneCommandResult,
    RcloneProgress,
)
from echo.storage.errors import InvalidRunTransitionError, RunNotFoundError
from echo.storage.runs import RunRepository


@dataclass(frozen=True, slots=True)
class ArchivePlanStatus:
    name: str
    active_run_id: str | None
    latest_run: ArchiveRun | None

    @property
    def running(self) -> bool:
        return self.active_run_id is not None


class Archiver:
    """Owns every archive execution, regardless of whether HTTP or cron triggered it."""

    def __init__(
        self,
        config: ArchiveConfig,
        rclone: RcloneClient,
        runs: RunRepository,
    ) -> None:
        self.config = config
        self.rclone = rclone
        self.runs = runs

        self._logger = get_logger(service="archiver")
        self._semaphore = asyncio.Semaphore(config.max_concurrent_plans)
        self._state_lock = asyncio.Lock()
        self._accepting = False
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._active_plans: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def running(self) -> bool:
        return self._accepting

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    @property
    def plans(self) -> tuple[ArchivePlanConfig, ...]:
        return tuple(self.config.plans)

    async def start(self) -> None:
        async with self._state_lock:
            self._accepting = True

    async def stop(self) -> None:
        async with self._state_lock:
            self._accepting = False
            tasks = tuple(self._tasks.values())

        for task in tasks:
            task.cancel()

        if tasks:
            try:
                async with asyncio.timeout(self.config.shutdown_timeout_seconds):
                    await asyncio.gather(*tasks, return_exceptions=True)

            except TimeoutError:
                await self._logger.aerror("Timed out while stopping archive runs", active_runs=len(tasks))

        async with self._state_lock:
            self._tasks.clear()
            self._active_plans.clear()

    def get_plan(self, name: str) -> ArchivePlanConfig:
        for plan in self.config.plans:
            if plan.name == name: return plan

        raise ArchivePlanNotFoundError(f"Archive plan not found: {name}")

    async def get_status(self, name: str) -> ArchivePlanStatus:
        plan = self.get_plan(name)

        async with self._state_lock:
            active_run_id = self._active_plans.get(plan.name)

        return ArchivePlanStatus(
            name=plan.name,
            active_run_id=active_run_id,
            latest_run=await self.runs.latest_for_plan(plan.name),
        )

    async def get_statuses(self) -> tuple[ArchivePlanStatus, ...]:
        return tuple(await asyncio.gather(*(self.get_status(plan.name) for plan in self.plans)))

    async def submit(
        self,
        name: str,
        *,
        operation: RunOperation = RunOperation.ARCHIVE,
        trigger: RunTrigger = RunTrigger.MANUAL,
        dry_run: bool = False,
    ) -> ArchiveRun:
        if not self.enabled: raise ArchiverDisabledError("Archival is disabled")

        plan = self.get_plan(name)
        if not plan.enabled: raise ArchivePlanDisabledError(f"Archive plan is disabled: {plan.name}")

        source = self._validate_source(plan)

        async with self._state_lock:
            if not self._accepting: raise ArchiverNotRunningError("Archiver is not running")

            if active_run_id := self._active_plans.get(plan.name):
                raise ArchivePlanRunningError(plan.name, active_run_id)

            run = ArchiveRun(
                id=str(uuid4()),
                plan_name=plan.name,
                operation=operation,
                trigger=trigger,
                state=RunState.QUEUED,
                dry_run=dry_run,
                source=source,
                destination=plan.destination,
                created_at=datetime.now(UTC),
            )

            await self.runs.create(run)
            self._active_plans[plan.name] = run.id

            task = asyncio.create_task(
                self._execute(run, plan),
                name=f"echo-{operation.value}-{plan.name}-{run.id}",
            )
            self._tasks[run.id] = task
            task.add_done_callback(_consume_task_result)

        await self._logger.ainfo(
            "Archive run queued",
            run_id=run.id,
            plan=plan.name,
            operation=operation.value,
            trigger=trigger.value,
            dry_run=dry_run,
        )
        return run

    async def wait(self, run_id: str) -> ArchiveRun:
        async with self._state_lock:
            task = self._tasks.get(run_id)

        if task is not None: await asyncio.shield(task)

        return await self.runs.get(run_id)

    async def cancel(self, run_id: str) -> ArchiveRun:
        async with self._state_lock:
            task = self._tasks.get(run_id)

        if task is None:
            run = await self.runs.get(run_id)

            if run.state.terminal:
                raise ArchiveRunNotCancellableError(f"Archive run {run_id} is already {run.state.value}")

            raise ArchiveRunNotCancellableError(f"Archive run {run_id} is not owned by this process")

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return await self.runs.get(run_id)

    async def archive(self, name: str, *, dry_run: bool = False) -> ArchiveRun:
        run = await self.submit(name, dry_run=dry_run)
        return await self.wait(run.id)

    async def verify(self, name: str) -> ArchiveRun:
        run = await self.submit(name, operation=RunOperation.VERIFY)
        return await self.wait(run.id)

    async def _execute(self, run: ArchiveRun, plan: ArchivePlanConfig) -> None:
        try:
            async with self._semaphore:
                await self.runs.transition(
                    run.id,
                    expected=(RunState.QUEUED,),
                    state=RunState.RUNNING,
                    started_at=datetime.now(UTC),
                )
                await self._logger.ainfo(
                    "Archive run started",
                    run_id=run.id,
                    plan=plan.name,
                    operation=run.operation.value,
                )

                result = await self._run_operation(run, plan)
                current = await self.runs.get(run.id)

                await self.runs.update_progress(
                    run.id,
                    progress=100.0,
                    files_added=current.files_added,
                    files_verified=current.files_verified,
                )

                await self.runs.transition(
                    run.id,
                    expected=(RunState.RUNNING,),
                    state=RunState.SUCCEEDED,
                    finished_at=datetime.now(UTC),
                    return_code=result.return_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )

                await self._logger.ainfo(
                    "Archive run completed",
                    run_id=run.id,
                    plan=plan.name,
                    operation=run.operation.value,
                    duration_seconds=round(result.duration_seconds, 3),
                )

        except asyncio.CancelledError:
            await self._mark_cancelled(run.id)
            await self._logger.awarning("Archive run cancelled", run_id=run.id, plan=plan.name)
            raise

        except Exception as exc:
            result = exc.result if isinstance(exc, RcloneCommandError) else None
            await self._mark_failed(run.id, exc, result)
            await self._logger.aerror(
                "Archive run failed",
                run_id=run.id,
                plan=plan.name,
                operation=run.operation.value,
                error=str(exc) or exc.__class__.__name__,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

        finally:
            async with self._state_lock:
                self._tasks.pop(run.id, None)

                if self._active_plans.get(plan.name) == run.id: self._active_plans.pop(plan.name, None)

    async def _run_operation(
        self,
        run: ArchiveRun,
        plan: ArchivePlanConfig,
    ) -> RcloneCommandResult:
        if run.operation is RunOperation.VERIFY:
            async def verification_progress(progress: RcloneProgress) -> None:
                await self.runs.update_progress(
                    run.id,
                    progress=progress.percent,
                    files_added=0,
                    files_verified=progress.files_checked,
                )

            verification = await self.rclone.check_remote(
                run.source,
                run.destination,
                exclude=plan.exclude,
                progress=verification_progress,
            )
            await self.runs.update_progress(
                run.id,
                progress=100.0,
                files_added=0,
                files_verified=verification.files_verified,
            )
            return verification.result

        counts = {
            "added": 0,
            "planned": 0,
            "checked": 0,
            "planned_checks": 0,
            "verified": 0,
        }
        includes_verification = plan.verify_after_archive and not run.dry_run
        verifies_during_transfer = includes_verification and self.rclone.large_uploads_optimized

        async def transfer_progress(progress: RcloneProgress) -> None:
            if not run.dry_run:
                counts["added"] = max(counts["added"], progress.files_transferred)
                counts["planned"] = max(counts["planned"], progress.files_to_transfer)
                if verifies_during_transfer:
                    counts["checked"] = max(counts["checked"], progress.files_checked)
                    counts["planned_checks"] = max(
                        counts["planned_checks"],
                        progress.files_to_check,
                    )
                    counts["verified"] = counts["checked"]
            await self.runs.update_progress(
                run.id,
                progress=progress.percent * (0.8 if includes_verification else 1.0),
                files_added=counts["added"],
                files_verified=counts["verified"],
            )

        result = await self.rclone.copy_to_remote(
            run.source,
            run.destination,
            exclude=plan.exclude,
            dry_run=run.dry_run,
            progress=transfer_progress,
        )

        # rclone may emit a trailing/reset stats sample. Only after a successful
        # command can its maximum transfer plan be promoted to completed work.
        if not run.dry_run:
            counts["added"] = max(counts["added"], counts["planned"])
            if verifies_during_transfer:
                counts["checked"] = max(counts["checked"], counts["planned_checks"])
                counts["verified"] = counts["checked"]
            await self.runs.update_progress(
                run.id,
                progress=80.0 if includes_verification else 100.0,
                files_added=counts["added"],
                files_verified=counts["verified"],
            )

        if includes_verification:
            if verifies_during_transfer:
                # Unchanged objects were checked before transfer; AWS and rclone
                # validate newly uploaded bytes in flight. Count both without
                # rereading multi-gigabyte local files afterward.
                counts["verified"] = counts["checked"] + counts["added"]
                await self.runs.update_progress(
                    run.id,
                    progress=100.0,
                    files_added=counts["added"],
                    files_verified=counts["verified"],
                )
            else:
                async def post_archive_progress(progress: RcloneProgress) -> None:
                    counts["verified"] = max(counts["verified"], progress.files_checked)
                    await self.runs.update_progress(
                        run.id,
                        progress=80.0 + progress.percent * 0.2,
                        files_added=counts["added"],
                        files_verified=counts["verified"],
                    )

                verification = await self.rclone.check_remote(
                    run.source,
                    run.destination,
                    exclude=plan.exclude,
                    progress=post_archive_progress,
                )
                counts["verified"] = max(
                    counts["verified"],
                    verification.files_verified,
                )
                await self.runs.update_progress(
                    run.id,
                    progress=100.0,
                    files_added=counts["added"],
                    files_verified=counts["verified"],
                )

        return result

    async def _mark_cancelled(self, run_id: str) -> None:
        try:
            await self.runs.transition(
                run_id,
                expected=(RunState.QUEUED, RunState.RUNNING),
                state=RunState.CANCELLED,
                finished_at=datetime.now(UTC),
                error="Run cancelled",
            )

        except (InvalidRunTransitionError, RunNotFoundError):
            return

    async def _mark_failed(
        self,
        run_id: str,
        error: Exception,
        result: RcloneCommandResult | None,
    ) -> None:
        try:
            await self.runs.transition(
                run_id,
                expected=(RunState.QUEUED, RunState.RUNNING),
                state=RunState.FAILED,
                finished_at=datetime.now(UTC),
                return_code=result.return_code if result else None,
                error=str(error) or error.__class__.__name__,
                stdout=result.stdout if result else None,
                stderr=result.stderr if result else None,
            )

        except (InvalidRunTransitionError, RunNotFoundError):
            return

    @staticmethod
    def _validate_source(plan: ArchivePlanConfig) -> Path:
        source = plan.source.expanduser()

        if not source.exists(): raise ArchiveSourceError(f"Archive source does not exist: {source}")
        if not source.is_dir(): raise ArchiveSourceError(f"Archive source is not a directory: {source}")

        return source.resolve()


def _consume_task_result(task: asyncio.Task[None]) -> None:
    if not task.cancelled(): task.exception()
