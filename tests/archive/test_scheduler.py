from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import echo.archive.scheduler as scheduler_module
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
from echo.archive.scheduler import _consume_runner_result, _Schedule, Scheduler
from echo.core.config import ArchivePlanConfig


class FakeArchiver:
    def __init__(self, plans: list[ArchivePlanConfig]) -> None:
        self.plans = tuple(plans)
        self.submit = AsyncMock(return_value=SimpleNamespace(id="run-1"))


def make_plan(
    tmp_path: Path,
    name: str = "photos",
    *,
    cron: str | None = "* * * * *",
    enabled: bool = True,
) -> ArchivePlanConfig:
    return ArchivePlanConfig(
        name=name,
        source=tmp_path,
        destination=name,
        cron=cron,
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_start_builds_only_enabled_schedules_and_stop_cleans_up(tmp_path: Path) -> None:
    scheduled = make_plan(tmp_path)
    no_cron = make_plan(tmp_path, "manual", cron=None)
    disabled = make_plan(tmp_path, "disabled", enabled=False)
    scheduler = Scheduler(FakeArchiver([scheduled, no_cron, disabled]))  # type: ignore[arg-type]

    await scheduler.start()
    runner = scheduler._runner
    assert scheduler.running is True
    assert scheduler.plans == (scheduled,)
    assert scheduler.is_scheduled("photos") is True
    assert scheduler.is_scheduled("manual") is False
    assert runner is not None and runner.get_name() == "echo-scheduler"

    await scheduler.start()
    assert scheduler._runner is runner
    await scheduler.stop()
    assert scheduler.running is False
    assert scheduler.plans == ()
    assert scheduler._runner is None
    await scheduler.stop()


@pytest.mark.asyncio
async def test_start_without_schedules_does_not_create_runner(tmp_path: Path) -> None:
    scheduler = Scheduler(FakeArchiver([make_plan(tmp_path, cron=None)]))  # type: ignore[arg-type]
    await scheduler.start()
    assert scheduler.running is True
    assert scheduler._runner is None
    await scheduler.stop()


@pytest.mark.asyncio
async def test_invalid_cron_fails_start_without_changing_lifecycle(tmp_path: Path) -> None:
    scheduler = Scheduler(FakeArchiver([make_plan(tmp_path, cron="bad")]))  # type: ignore[arg-type]
    with pytest.raises(SchedulerConfigurationError, match="photos"):
        await scheduler.start()
    assert scheduler.running is False
    assert scheduler._runner is None


@pytest.mark.asyncio
async def test_start_rejects_failed_runner_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scheduler = Scheduler(FakeArchiver([make_plan(tmp_path)]))  # type: ignore[arg-type]
    created = []

    def fail_creation(coroutine, **_kwargs):  # type: ignore[no-untyped-def]
        created.append(coroutine)
        return None

    monkeypatch.setattr(scheduler_module.asyncio, "create_task", fail_creation)
    with pytest.raises(SchedulerError, match="runner"):
        await scheduler.start()
    created[0].close()
    scheduler._running = False


@pytest.mark.asyncio
async def test_dispatch_submits_scheduled_trigger(tmp_path: Path) -> None:
    archiver = FakeArchiver([make_plan(tmp_path)])
    scheduler = Scheduler(archiver)  # type: ignore[arg-type]
    tick = datetime(2026, 9, 2, 12, 0)
    await scheduler._dispatch(archiver.plans[0], scheduled_at=tick)
    archiver.submit.assert_awaited_once_with("photos", trigger=RunTrigger.SCHEDULED)


@pytest.mark.asyncio
async def test_dispatch_skips_already_running_plan(tmp_path: Path) -> None:
    archiver = FakeArchiver([make_plan(tmp_path)])
    archiver.submit.side_effect = ArchivePlanRunningError("photos", "active")
    scheduler = Scheduler(archiver)  # type: ignore[arg-type]
    await scheduler._dispatch(archiver.plans[0], scheduled_at=datetime.now())
    archiver.submit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ArchiverDisabledError("disabled"),
        ArchiverNotRunningError("stopped"),
        ArchivePlanDisabledError("plan disabled"),
        ArchiveSourceError("missing"),
    ],
)
async def test_dispatch_handles_expected_rejections(tmp_path: Path, error: Exception) -> None:
    archiver = FakeArchiver([make_plan(tmp_path)])
    archiver.submit.side_effect = error
    scheduler = Scheduler(archiver)  # type: ignore[arg-type]
    await scheduler._dispatch(archiver.plans[0], scheduled_at=datetime.now())


@pytest.mark.asyncio
async def test_dispatch_propagates_unexpected_failure(tmp_path: Path) -> None:
    archiver = FakeArchiver([make_plan(tmp_path)])
    archiver.submit.side_effect = RuntimeError("unexpected")
    scheduler = Scheduler(archiver)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="unexpected"):
        await scheduler._dispatch(archiver.plans[0], scheduled_at=datetime.now())


@pytest.mark.asyncio
async def test_run_ticks_and_dispatches_only_matching_schedule(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    matching = make_plan(tmp_path, "matching", cron="34 12 * * *")
    other = make_plan(tmp_path, "other", cron="35 12 * * *")
    archiver = FakeArchiver([matching, other])
    scheduler = Scheduler(archiver)  # type: ignore[arg-type]
    scheduler._schedules = (
        _Schedule(matching, scheduler_module.CronExpression.parse(matching.cron or "")),
        _Schedule(other, scheduler_module.CronExpression.parse(other.cron or "")),
    )
    scheduler._running = True

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            value = cls(2026, 9, 2, 12, 34, 48)
            return value.astimezone() if tz is None else value.replace(tzinfo=tz)

    async def one_tick(_delay: float) -> None:
        scheduler._running = False

    monkeypatch.setattr(scheduler_module, "datetime", FixedDateTime)
    monkeypatch.setattr(scheduler_module, "seconds_until_next_minute", lambda: 12.0)
    monkeypatch.setattr(scheduler_module.asyncio, "sleep", one_tick)
    await scheduler._run()

    assert scheduler.last_tick == FixedDateTime.now().replace(second=0, microsecond=0)
    archiver.submit.assert_awaited_once_with("matching", trigger=RunTrigger.SCHEDULED)


@pytest.mark.asyncio
async def test_run_marks_scheduler_stopped_on_loop_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scheduler = Scheduler(FakeArchiver([make_plan(tmp_path)]))  # type: ignore[arg-type]
    scheduler._running = True

    async def fail(_delay: float) -> None:
        raise RuntimeError("clock failed")

    monkeypatch.setattr(scheduler_module.asyncio, "sleep", fail)
    with pytest.raises(RuntimeError, match="clock failed"):
        await scheduler._run()
    assert scheduler.running is False


@pytest.mark.asyncio
async def test_run_propagates_cancellation(tmp_path: Path) -> None:
    scheduler = Scheduler(FakeArchiver([make_plan(tmp_path)]))  # type: ignore[arg-type]
    scheduler._running = True
    task = asyncio.create_task(scheduler._run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_runner_result_callback_observes_exception() -> None:
    async def fail() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(fail())
    await asyncio.gather(task, return_exceptions=True)
    _consume_runner_result(task)


@pytest.mark.asyncio
async def test_runner_result_callback_ignores_cancellation() -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    _consume_runner_result(task)
