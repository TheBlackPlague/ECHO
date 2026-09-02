from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import echo.application as application_module
from echo.application import clear_application_cache, EchoApplication, get_application
from echo.core.config import ArchiveConfig, EchoConfig, RcloneConfig, StorageConfig
from echo.integrations.rclone import RcloneStatus


class FakeDatabase:
    def __init__(self, *, initialized: bool = False) -> None:
        self.initialized = initialized
        self.initialize = AsyncMock(side_effect=self._initialize)
        self.close = AsyncMock(side_effect=self._close)

    async def _initialize(self) -> None:
        self.initialized = True

    async def _close(self) -> None:
        self.initialized = False


def config(tmp_path: Path, *, archive_enabled: bool = False) -> EchoConfig:
    return EchoConfig(
        storage=StorageConfig(database=tmp_path / "echo.db"),
        rclone=(
            RcloneConfig(remote="aws", bucket="bucket")
            if archive_enabled
            else RcloneConfig()
        ),
        archive=ArchiveConfig(enabled=archive_enabled),
    )


def replace_services(app: EchoApplication):  # type: ignore[no-untyped-def]
    database = FakeDatabase()
    runs = SimpleNamespace(recover_incomplete=AsyncMock(return_value=0))
    rclone = SimpleNamespace(validate=AsyncMock())
    archiver = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    scheduler = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    app.database = database  # type: ignore[assignment]
    app.runs = runs  # type: ignore[assignment]
    app.rclone = rclone  # type: ignore[assignment]
    app.archiver = archiver  # type: ignore[assignment]
    app.scheduler = scheduler  # type: ignore[assignment]
    return database, runs, rclone, archiver, scheduler


@pytest.fixture(autouse=True)
def clear_cached_application() -> None:
    clear_application_cache()
    yield
    clear_application_cache()


@pytest.mark.asyncio
async def test_disabled_archive_application_start_and_stop(tmp_path: Path) -> None:
    app = EchoApplication(config(tmp_path))
    database, runs, rclone, archiver, scheduler = replace_services(app)
    assert app.started is False
    assert app.started_at is None
    assert app.rclone_status is None
    assert app.ready is False

    await app.start()
    assert app.started is True
    assert app.started_at is not None
    assert app.ready is True
    database.initialize.assert_awaited_once()
    runs.recover_incomplete.assert_awaited_once()
    rclone.validate.assert_not_awaited()
    archiver.start.assert_awaited_once()
    scheduler.start.assert_not_awaited()

    await app.stop()
    scheduler.stop.assert_awaited_once()
    archiver.stop.assert_awaited_once()
    database.close.assert_awaited_once()
    assert app.started is False
    assert app.started_at is None
    assert app.rclone_status is None
    assert app.ready is False


@pytest.mark.asyncio
async def test_enabled_archive_validates_rclone_and_starts_scheduler(tmp_path: Path) -> None:
    app = EchoApplication(config(tmp_path, archive_enabled=True))
    database, _, rclone, archiver, scheduler = replace_services(app)
    status = RcloneStatus(
        version="1.70.0",
        remote="aws",
        bucket="bucket",
        remotes=("aws",),
        large_uploads_optimized=True,
    )
    rclone.validate.return_value = status

    await app.start()
    assert app.rclone_status is status
    assert app.ready is True
    rclone.validate.assert_awaited_once()
    archiver.start.assert_awaited_once()
    scheduler.start.assert_awaited_once()
    assert database.initialized is True


@pytest.mark.asyncio
async def test_start_is_idempotent_even_when_called_concurrently(tmp_path: Path) -> None:
    app = EchoApplication(config(tmp_path))
    database, runs, _, archiver, _ = replace_services(app)
    await asyncio.gather(app.start(), app.start())
    database.initialize.assert_awaited_once()
    runs.recover_incomplete.assert_awaited_once()
    archiver.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovered_runs_do_not_block_start(tmp_path: Path) -> None:
    app = EchoApplication(config(tmp_path))
    _, runs, _, _, _ = replace_services(app)
    runs.recover_incomplete.return_value = 3
    await app.start()
    assert app.started is True


@pytest.mark.asyncio
async def test_enabled_archive_requires_validation_status(tmp_path: Path) -> None:
    app = EchoApplication(config(tmp_path, archive_enabled=True))
    database, _, rclone, archiver, scheduler = replace_services(app)
    rclone.validate.return_value = None

    with pytest.raises(ValueError, match="not configured"):
        await app.start()

    scheduler.stop.assert_awaited_once()
    archiver.stop.assert_awaited_once()
    database.close.assert_awaited_once()
    assert database.initialized is False
    assert app.started is False
    assert app.started_at is None
    assert app.rclone_status is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["database", "archiver", "scheduler"])
async def test_start_failure_rolls_back_all_services(
    tmp_path: Path,
    failure_point: str,
) -> None:
    enabled = failure_point == "scheduler"
    app = EchoApplication(config(tmp_path, archive_enabled=enabled))
    database, _, rclone, archiver, scheduler = replace_services(app)
    rclone.validate.return_value = RcloneStatus("v", "aws", "bucket", ("aws",))
    error = RuntimeError(f"{failure_point} failed")
    if failure_point == "database":
        database.initialize.side_effect = error
    elif failure_point == "archiver":
        archiver.start.side_effect = error
    else:
        scheduler.start.side_effect = error

    with pytest.raises(RuntimeError, match=failure_point):
        await app.start()
    scheduler.stop.assert_awaited_once()
    archiver.stop.assert_awaited_once()
    database.close.assert_awaited_once()
    assert app.started is False
    assert app.rclone_status is None


@pytest.mark.asyncio
async def test_stop_is_noop_when_never_initialized(tmp_path: Path) -> None:
    app = EchoApplication(config(tmp_path))
    database, _, _, archiver, scheduler = replace_services(app)
    await app.stop()
    scheduler.stop.assert_not_awaited()
    archiver.stop.assert_not_awaited()
    database.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_cleans_up_partially_initialized_application(tmp_path: Path) -> None:
    app = EchoApplication(config(tmp_path))
    database, _, _, archiver, scheduler = replace_services(app)
    database.initialized = True
    await app.stop()
    scheduler.stop.assert_awaited_once()
    archiver.stop.assert_awaited_once()
    database.close.assert_awaited_once()


def test_ready_requires_rclone_status_when_archive_enabled(tmp_path: Path) -> None:
    app = EchoApplication(config(tmp_path, archive_enabled=True))
    database, _, _, _, _ = replace_services(app)
    database.initialized = True
    app._started = True
    assert app.ready is False
    app._rclone_status = RcloneStatus("v", "aws", "bucket", ("aws",))
    assert app.ready is True


def test_application_factory_is_cached_and_clearable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = config(tmp_path)
    calls = 0

    def load() -> EchoConfig:
        nonlocal calls
        calls += 1
        return configured

    monkeypatch.setattr(application_module, "get_config", load)
    first = get_application()
    assert get_application() is first
    assert calls == 1
    clear_application_cache()
    assert get_application() is not first
    assert calls == 2
