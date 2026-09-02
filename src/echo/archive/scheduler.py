from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from echo.archive.archiver import Archiver
from echo.archive.cron import CronExpression, seconds_until_next_minute
from echo.archive.errors import (
    ArchivePlanDisabledError,
    ArchivePlanRunningError,
    ArchiverDisabledError,
    ArchiverNotRunningError,
    ArchiveSourceError,
    SchedulerConfigurationError,
    SchedulerError,
)
from echo.archive.models import RunTrigger
from echo.core.config import ArchivePlanConfig
from echo.core.logging import get_logger


@dataclass(frozen=True, slots=True)
class _Schedule:
    plan: ArchivePlanConfig
    cron: CronExpression


class Scheduler:
    def __init__(self, archiver: Archiver) -> None:
        self.archiver = archiver
        self._logger = get_logger(service="scheduler")
        self._lifecycle_lock = asyncio.Lock()
        self._running = False
        self._runner: asyncio.Task[None] | None = None
        self._schedules: tuple[_Schedule, ...] = ()
        self._last_tick: datetime | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def plans(self) -> tuple[ArchivePlanConfig, ...]:
        return tuple(schedule.plan for schedule in self._schedules)

    @property
    def last_tick(self) -> datetime | None:
        return self._last_tick

    def is_scheduled(self, plan_name: str) -> bool:
        return any(schedule.plan.name == plan_name for schedule in self._schedules)

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running: return

            self._schedules = self._build_schedules()
            self._running = True

            if self._schedules:
                self._runner = asyncio.create_task(self._run(), name="echo-scheduler")

                if self._runner is None: raise SchedulerError("Failed to create scheduler runner")

                self._runner.add_done_callback(_consume_runner_result)

            await self._logger.ainfo("Scheduler started", plans=len(self._schedules))

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._running and self._runner is None: return

            self._running = False
            runner, self._runner = self._runner, None

            if runner is not None:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

            self._schedules = ()
            await self._logger.ainfo("Scheduler stopped")

    def _build_schedules(self) -> tuple[_Schedule, ...]:
        schedules: list[_Schedule] = []

        for plan in self.archiver.plans:
            if not plan.enabled or not plan.cron: continue

            try:
                cron = CronExpression.parse(plan.cron)

            except ValueError as exc:
                raise SchedulerConfigurationError(
                    f"Invalid cron expression for archive plan '{plan.name}': "
                    f"{plan.cron} ({exc})"
                ) from exc

            schedules.append(_Schedule(plan=plan, cron=cron))

        return tuple(schedules)

    async def _run(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(seconds_until_next_minute())

                tick = datetime.now().astimezone().replace(second=0, microsecond=0)
                self._last_tick = tick

                for schedule in self._schedules:
                    if schedule.cron.matches(tick):
                        await self._dispatch(schedule.plan, scheduled_at=tick)

        except asyncio.CancelledError:
            raise

        except Exception:
            self._running = False
            await self._logger.acritical("Scheduler loop failed", exc_info=True)
            raise

    async def _dispatch(self, plan: ArchivePlanConfig, *, scheduled_at: datetime) -> None:
        try:
            run = await self.archiver.submit(plan.name, trigger=RunTrigger.SCHEDULED)

        except ArchivePlanRunningError as exc:
            await self._logger.awarning(
                "Scheduled archive skipped",
                plan=plan.name,
                active_run_id=exc.run_id,
                scheduled_at=scheduled_at.isoformat(),
            )
            return

        except (
                ArchiverDisabledError,
                ArchiverNotRunningError,
                ArchivePlanDisabledError,
                ArchiveSourceError,
        ):
            await self._logger.aerror(
                "Scheduled archive rejected",
                plan=plan.name,
                scheduled_at=scheduled_at.isoformat(),
                exc_info=True,
            )
            return

        await self._logger.ainfo(
            "Archive plan scheduled",
            plan=plan.name,
            run_id=run.id,
            scheduled_at=scheduled_at.isoformat(),
        )


def _consume_runner_result(task: asyncio.Task[None]) -> None:
    if not task.cancelled(): task.exception()
