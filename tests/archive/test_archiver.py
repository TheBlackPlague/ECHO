from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import echo.archive.archiver as archiver_module
from echo.archive.archiver import Archiver
from echo.archive.errors import (
    ArchivePlanDisabledError,
    ArchivePlanNotFoundError,
    ArchivePlanRunningError,
    ArchiverDisabledError,
    ArchiverNotRunningError,
    ArchiveRunNotCancellableError,
    ArchiveSourceError,
)
from echo.archive.models import RunOperation, RunState, RunTrigger
from echo.core.config import ArchiveConfig, ArchivePlanConfig
from echo.integrations.rclone import RcloneCommandError, RcloneProgress
from echo.storage.errors import RunNotFoundError
from .conftest import command_result, FakeRunRepository, make_run, verification_result


class FakeRclone:
    def __init__(self, *, optimized: bool = False) -> None:
        self.large_uploads_optimized = optimized
        self.copy_calls: list[dict[str, Any]] = []
        self.check_calls: list[dict[str, Any]] = []
        self.copy_effect: Any = command_result()
        self.check_effect: Any = verification_result()

    async def copy_to_remote(self, source: Path, destination: str, **kwargs: Any):
        self.copy_calls.append({"source": source, "destination": destination, **kwargs})
        effect = self.copy_effect
        if isinstance(effect, BaseException):
            raise effect
        if callable(effect):
            effect = effect(kwargs["progress"])
        if asyncio.iscoroutine(effect):
            effect = await effect
        return effect

    async def check_remote(self, source: Path, destination: str, **kwargs: Any):
        self.check_calls.append({"source": source, "destination": destination, **kwargs})
        effect = self.check_effect
        if isinstance(effect, BaseException):
            raise effect
        if callable(effect):
            effect = effect(kwargs["progress"])
        if asyncio.iscoroutine(effect):
            effect = await effect
        return effect


def make_archiver(config: ArchiveConfig, rclone: FakeRclone | None = None):
    repository = FakeRunRepository()
    client = rclone or FakeRclone()
    return Archiver(config, client, repository), client, repository


def progress(
    percent: float,
    *,
    transferred: int = 0,
    checked: int = 0,
    to_transfer: int = 0,
    to_check: int = 0,
) -> RcloneProgress:
    return RcloneProgress(
        percent=percent,
        files_transferred=transferred,
        files_checked=checked,
        files_to_transfer=to_transfer,
        files_to_check=to_check,
    )


@pytest.mark.asyncio
async def test_lifecycle_plan_lookup_and_statuses(archive_config, plan) -> None:  # type: ignore[no-untyped-def]
    archiver, _, repository = make_archiver(archive_config)
    assert archiver.enabled is True
    assert archiver.running is False
    assert archiver.active_count == 0
    assert archiver.plans == (plan,)
    assert archiver.get_plan("photos") is plan
    with pytest.raises(ArchivePlanNotFoundError):
        archiver.get_plan("missing")

    await archiver.start()
    await archiver.start()
    assert archiver.running is True
    status = await archiver.get_status("photos")
    assert status.name == "photos"
    assert status.running is False
    assert status.latest_run is None
    assert await archiver.get_statuses() == (status,)

    latest = make_run(plan.source, state=RunState.SUCCEEDED)
    repository.items[latest.id] = latest
    assert (await archiver.get_status("photos")).latest_run is latest
    await archiver.stop()
    assert archiver.running is False


@pytest.mark.asyncio
async def test_submit_rejects_disabled_archiver(plan) -> None:  # type: ignore[no-untyped-def]
    archiver, _, _ = make_archiver(ArchiveConfig(enabled=False, plans=[plan]))
    await archiver.start()
    with pytest.raises(ArchiverDisabledError):
        await archiver.submit(plan.name)


@pytest.mark.asyncio
async def test_submit_rejects_disabled_or_missing_plan(plan) -> None:  # type: ignore[no-untyped-def]
    disabled = plan.model_copy(update={"enabled": False})
    archiver, _, _ = make_archiver(ArchiveConfig(enabled=True, plans=[disabled]))
    await archiver.start()
    with pytest.raises(ArchivePlanDisabledError):
        await archiver.submit(disabled.name)
    with pytest.raises(ArchivePlanNotFoundError):
        await archiver.submit("missing")


@pytest.mark.asyncio
async def test_submit_validates_source_and_running_state(tmp_path: Path) -> None:
    missing = ArchivePlanConfig(name="missing", source=tmp_path / "none", destination="x")
    archiver, _, _ = make_archiver(ArchiveConfig(enabled=True, plans=[missing]))
    await archiver.start()
    with pytest.raises(ArchiveSourceError, match="does not exist"):
        await archiver.submit("missing")

    file_source = tmp_path / "file"
    file_source.write_text("not a directory")
    file_plan = ArchivePlanConfig(name="file", source=file_source, destination="x")
    archiver, _, _ = make_archiver(ArchiveConfig(enabled=True, plans=[file_plan]))
    await archiver.start()
    with pytest.raises(ArchiveSourceError, match="not a directory"):
        await archiver.submit("file")

    source = tmp_path / "valid"
    source.mkdir()
    valid = ArchivePlanConfig(name="valid", source=source, destination="x")
    archiver, _, _ = make_archiver(ArchiveConfig(enabled=True, plans=[valid]))
    with pytest.raises(ArchiverNotRunningError):
        await archiver.submit("valid")


@pytest.mark.asyncio
async def test_archive_success_tracks_monotonic_transfer_progress(
    archive_config, plan
) -> None:  # type: ignore[no-untyped-def]
    client = FakeRclone()

    async def copy(progress_callback):  # type: ignore[no-untyped-def]
        await progress_callback(progress(25, transferred=1, to_transfer=4))
        await progress_callback(progress(10, transferred=0, to_transfer=2))
        return command_result(stdout="ok")

    client.copy_effect = copy
    archiver, _, repository = make_archiver(archive_config, client)
    await archiver.start()

    queued = await archiver.submit(plan.name, trigger=RunTrigger.SCHEDULED)
    assert queued.state is RunState.QUEUED
    assert queued.trigger is RunTrigger.SCHEDULED
    finished = await archiver.wait(queued.id)

    assert finished.state is RunState.SUCCEEDED
    assert finished.progress == 100.0
    assert finished.files_added == 4
    assert finished.files_verified == 0
    assert finished.return_code == 0
    assert finished.stdout == "ok"
    assert finished.started_at is not None and finished.finished_at is not None
    assert archiver.active_count == 0
    assert client.copy_calls[0] == {
        "source": plan.source.resolve(),
        "destination": plan.destination,
        "exclude": plan.exclude,
        "dry_run": False,
        "progress": client.copy_calls[0]["progress"],
    }
    assert client.check_calls == []
    assert (100.0, 4, 0) in repository.progress_updates


@pytest.mark.asyncio
async def test_dry_run_does_not_count_files_or_verify(plan) -> None:  # type: ignore[no-untyped-def]
    plan = plan.model_copy(update={"verify_after_archive": True})
    client = FakeRclone()

    async def copy(progress_callback):  # type: ignore[no-untyped-def]
        await progress_callback(progress(50, transferred=10, checked=9, to_transfer=20))
        return command_result()

    client.copy_effect = copy
    archiver, _, _ = make_archiver(ArchiveConfig(enabled=True, plans=[plan]), client)
    await archiver.start()
    run = await archiver.archive(plan.name, dry_run=True)

    assert run.state is RunState.SUCCEEDED
    assert run.dry_run is True
    assert run.files_added == 0
    assert run.files_verified == 0
    assert client.copy_calls[0]["dry_run"] is True
    assert client.check_calls == []


@pytest.mark.asyncio
async def test_archive_runs_post_transfer_verification_when_not_optimized(plan) -> None:  # type: ignore[no-untyped-def]
    plan = plan.model_copy(update={"verify_after_archive": True})
    client = FakeRclone(optimized=False)

    async def copy(progress_callback):  # type: ignore[no-untyped-def]
        await progress_callback(progress(50, transferred=2, to_transfer=4))
        return command_result()

    async def check(progress_callback):  # type: ignore[no-untyped-def]
        await progress_callback(progress(50, checked=3, to_check=6))
        await progress_callback(progress(25, checked=2, to_check=6))
        return verification_result(6)

    client.copy_effect = copy
    client.check_effect = check
    archiver, _, repository = make_archiver(ArchiveConfig(enabled=True, plans=[plan]), client)
    await archiver.start()
    run = await archiver.archive(plan.name)

    assert run.state is RunState.SUCCEEDED
    assert run.files_added == 4
    assert run.files_verified == 6
    assert [item[0] for item in repository.progress_updates[:4]] == [40.0, 80.0, 90.0, 85.0]
    assert client.check_calls[0]["exclude"] == plan.exclude


@pytest.mark.asyncio
async def test_optimized_archive_counts_in_flight_checks_and_uploads(plan) -> None:  # type: ignore[no-untyped-def]
    plan = plan.model_copy(update={"verify_after_archive": True})
    client = FakeRclone(optimized=True)

    async def copy(progress_callback):  # type: ignore[no-untyped-def]
        await progress_callback(
            progress(75, transferred=2, checked=5, to_transfer=3, to_check=7)
        )
        return command_result()

    client.copy_effect = copy
    archiver, _, repository = make_archiver(ArchiveConfig(enabled=True, plans=[plan]), client)
    await archiver.start()
    run = await archiver.archive(plan.name)

    assert run.files_added == 3
    assert run.files_verified == 10
    assert client.check_calls == []
    assert (60.0, 2, 5) in repository.progress_updates
    assert (100.0, 3, 10) in repository.progress_updates


@pytest.mark.asyncio
async def test_explicit_verify_reports_check_progress(archive_config, plan) -> None:  # type: ignore[no-untyped-def]
    client = FakeRclone()

    async def check(progress_callback):  # type: ignore[no-untyped-def]
        await progress_callback(progress(40, checked=4, to_check=10))
        return verification_result(10)

    client.check_effect = check
    archiver, _, repository = make_archiver(archive_config, client)
    await archiver.start()
    run = await archiver.verify(plan.name)

    assert run.operation is RunOperation.VERIFY
    assert run.state is RunState.SUCCEEDED
    assert run.files_added == 0
    assert run.files_verified == 10
    assert client.copy_calls == []
    assert (40, 0, 4) in repository.progress_updates


@pytest.mark.asyncio
async def test_rclone_command_failure_preserves_diagnostics(
    archive_config, plan
) -> None:  # type: ignore[no-untyped-def]
    client = FakeRclone()
    result = command_result(return_code=9, stdout="partial", stderr="network down")
    client.copy_effect = RcloneCommandError(result)
    archiver, _, _ = make_archiver(archive_config, client)
    await archiver.start()
    run = await archiver.archive(plan.name)

    assert run.state is RunState.FAILED
    assert run.return_code == 9
    assert run.stdout == "partial"
    assert run.stderr == "network down"
    assert "exit code 9" in (run.error or "")


@pytest.mark.asyncio
async def test_generic_failure_uses_exception_class_when_message_empty(
    archive_config, plan
) -> None:  # type: ignore[no-untyped-def]
    client = FakeRclone()
    client.copy_effect = RuntimeError()
    archiver, _, _ = make_archiver(archive_config, client)
    await archiver.start()
    run = await archiver.archive(plan.name)

    assert run.state is RunState.FAILED
    assert run.error == "RuntimeError"
    assert run.return_code is None


@pytest.mark.asyncio
async def test_duplicate_plan_is_rejected_and_status_is_active(
    archive_config, plan
) -> None:  # type: ignore[no-untyped-def]
    client = FakeRclone()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(_progress_callback):  # type: ignore[no-untyped-def]
        entered.set()
        await release.wait()
        return command_result()

    client.copy_effect = blocked
    archiver, _, _ = make_archiver(archive_config, client)
    await archiver.start()
    first = await archiver.submit(plan.name)
    await entered.wait()
    status = await archiver.get_status(plan.name)
    assert status.running is True and status.active_run_id == first.id
    with pytest.raises(ArchivePlanRunningError) as raised:
        await archiver.submit(plan.name)
    assert raised.value.run_id == first.id
    release.set()
    await archiver.wait(first.id)


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_plans(tmp_path: Path) -> None:
    plans = []
    for name in ("one", "two"):
        source = tmp_path / name
        source.mkdir()
        plans.append(ArchivePlanConfig(name=name, source=source, destination=name))
    config = ArchiveConfig(enabled=True, max_concurrent_plans=1, plans=plans)
    client = FakeRclone()
    release = asyncio.Event()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    active = 0
    maximum = 0
    calls = 0

    async def blocked(_progress_callback):  # type: ignore[no-untyped-def]
        nonlocal active, maximum, calls
        calls += 1
        active += 1
        maximum = max(maximum, active)
        (first_entered if calls == 1 else second_entered).set()
        await release.wait()
        active -= 1
        return command_result()

    client.copy_effect = blocked
    archiver, _, _ = make_archiver(config, client)
    await archiver.start()
    one = await archiver.submit("one")
    two = await archiver.submit("two")
    await first_entered.wait()
    await asyncio.sleep(0)
    assert not second_entered.is_set()
    release.set()
    await asyncio.gather(archiver.wait(one.id), archiver.wait(two.id))
    assert second_entered.is_set()
    assert maximum == 1


@pytest.mark.asyncio
async def test_cancel_active_run(archive_config, plan) -> None:  # type: ignore[no-untyped-def]
    client = FakeRclone()
    entered = asyncio.Event()

    async def blocked(_progress_callback):  # type: ignore[no-untyped-def]
        entered.set()
        await asyncio.Event().wait()

    client.copy_effect = blocked
    archiver, _, _ = make_archiver(archive_config, client)
    await archiver.start()
    queued = await archiver.submit(plan.name)
    await entered.wait()
    cancelled = await archiver.cancel(queued.id)
    assert cancelled.state is RunState.CANCELLED
    assert cancelled.error == "Run cancelled"
    assert archiver.active_count == 0


@pytest.mark.asyncio
async def test_cancel_run_queued_behind_concurrency_limit(tmp_path: Path) -> None:
    plans = []
    for name in ("running", "queued"):
        source = tmp_path / name
        source.mkdir()
        plans.append(ArchivePlanConfig(name=name, source=source, destination=name))
    client = FakeRclone()
    entered = asyncio.Event()

    async def blocked(_progress_callback):  # type: ignore[no-untyped-def]
        entered.set()
        await asyncio.Event().wait()

    client.copy_effect = blocked
    archiver, _, repository = make_archiver(
        ArchiveConfig(enabled=True, max_concurrent_plans=1, plans=plans),
        client,
    )
    await archiver.start()
    running = await archiver.submit("running")
    await entered.wait()
    queued = await archiver.submit("queued")
    assert (await repository.get(queued.id)).state is RunState.QUEUED

    cancelled = await archiver.cancel(queued.id)
    assert cancelled.state is RunState.CANCELLED
    await archiver.cancel(running.id)


@pytest.mark.asyncio
async def test_stop_cancels_runs_and_clears_ownership(archive_config, plan) -> None:  # type: ignore[no-untyped-def]
    client = FakeRclone()
    entered = asyncio.Event()

    async def blocked(_progress_callback):  # type: ignore[no-untyped-def]
        entered.set()
        await asyncio.Event().wait()

    client.copy_effect = blocked
    archiver, _, repository = make_archiver(archive_config, client)
    await archiver.start()
    queued = await archiver.submit(plan.name)
    await entered.wait()
    await archiver.stop()
    assert archiver.running is False
    assert archiver.active_count == 0
    assert (await repository.get(queued.id)).state is RunState.CANCELLED


@pytest.mark.asyncio
async def test_stop_handles_shutdown_timeout(
    monkeypatch: pytest.MonkeyPatch,
    archive_config,
    plan,
) -> None:  # type: ignore[no-untyped-def]
    archiver, _, _ = make_archiver(archive_config)
    task = asyncio.create_task(asyncio.Event().wait())
    archiver._tasks["run"] = task

    class ImmediateTimeout:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            raise TimeoutError

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return False

    monkeypatch.setattr(archiver_module.asyncio, "timeout", lambda _seconds: ImmediateTimeout())
    await archiver.stop()
    await asyncio.gather(task, return_exceptions=True)
    assert archiver.active_count == 0


@pytest.mark.asyncio
async def test_cancel_rejects_terminal_unowned_and_missing_runs(
    archive_config, plan
) -> None:  # type: ignore[no-untyped-def]
    archiver, _, repository = make_archiver(archive_config)
    terminal = make_run(plan.source, state=RunState.SUCCEEDED)
    unowned = make_run(plan.source, run_id="unowned", state=RunState.RUNNING)
    repository.items[terminal.id] = terminal
    repository.items[unowned.id] = unowned
    with pytest.raises(ArchiveRunNotCancellableError, match="already succeeded"):
        await archiver.cancel(terminal.id)
    with pytest.raises(ArchiveRunNotCancellableError, match="not owned"):
        await archiver.cancel(unowned.id)
    with pytest.raises(RunNotFoundError):
        await archiver.cancel("missing")


@pytest.mark.asyncio
async def test_wait_reads_repository_when_task_is_not_local(
    archive_config, plan
) -> None:  # type: ignore[no-untyped-def]
    archiver, _, repository = make_archiver(archive_config)
    run = make_run(plan.source, state=RunState.INTERRUPTED)
    repository.items[run.id] = run
    assert await archiver.wait(run.id) is run


@pytest.mark.asyncio
async def test_mark_helpers_ignore_stale_or_missing_runs(archive_config, plan) -> None:  # type: ignore[no-untyped-def]
    archiver, _, repository = make_archiver(archive_config)
    succeeded = make_run(plan.source, state=RunState.SUCCEEDED)
    repository.items[succeeded.id] = succeeded
    await archiver._mark_cancelled(succeeded.id)
    await archiver._mark_cancelled("missing")
    await archiver._mark_failed(succeeded.id, RuntimeError("no"), None)
    await archiver._mark_failed("missing", RuntimeError("no"), None)
    assert repository.items[succeeded.id].state is RunState.SUCCEEDED
