from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone, UTC
from pathlib import Path

import pytest
import pytest_asyncio

from echo.archive.models import RunOperation, RunState, RunTrigger
from echo.core.config import StorageConfig
from echo.storage.database import Database
from echo.storage.errors import RunNotFoundError, StorageError
from echo.storage.runs import RunRepository
from .storage_test_helpers import make_run


@pytest_asyncio.fixture
async def repository(tmp_path) -> RunRepository:
    config = StorageConfig(database=tmp_path / "echo.db")
    database = Database(config)
    await database.initialize()
    return RunRepository(config, database)


@pytest.mark.asyncio
async def test_create_and_get_round_trip_every_field(repository: RunRepository) -> None:
    non_utc = timezone(timedelta(hours=-5))
    run = make_run(
        state=RunState.SUCCEEDED,
        operation=RunOperation.VERIFY,
        trigger=RunTrigger.SCHEDULED,
        dry_run=True,
        source=Path("relative/source"),
        created_at=datetime(2026, 2, 3, 4, 5, tzinfo=non_utc),
        started_at=datetime(2026, 2, 3, 4, 6, tzinfo=non_utc),
        finished_at=datetime(2026, 2, 3, 4, 7, tzinfo=non_utc),
        return_code=0,
        error="informational",
        stdout="output",
        stderr="warning",
        progress=100.0,
        files_added=12,
        files_verified=11,
    )

    returned = await repository.create(run)
    persisted = await repository.get(run.id)

    assert returned is run
    assert persisted == replace(
        run,
        created_at=run.created_at.astimezone(UTC),
        started_at=run.started_at.astimezone(UTC),
        finished_at=run.finished_at.astimezone(UTC),
    )
    assert isinstance(persisted.source, Path)
    assert persisted.dry_run is True


@pytest.mark.asyncio
async def test_get_missing_run_raises(repository: RunRepository) -> None:
    with pytest.raises(RunNotFoundError, match="Archive run not found: absent"):
        await repository.get("absent")


@pytest.mark.asyncio
async def test_duplicate_create_is_wrapped_and_preserves_original(
    repository: RunRepository,
) -> None:
    original = make_run()
    await repository.create(original)

    with pytest.raises(StorageError, match="Unable to create archive run run-1"):
        await repository.create(replace(original, plan_name="replacement"))

    assert await repository.get(original.id) == original


@pytest.mark.asyncio
async def test_list_orders_newest_first_and_paginates(repository: RunRepository) -> None:
    base = make_run()
    for index in range(5):
        await repository.create(
            replace(
                base,
                id=f"run-{index}",
                created_at=base.created_at + timedelta(minutes=index),
            )
        )

    page = await repository.list(limit=2, offset=1)

    assert [run.id for run in page] == ["run-3", "run-2"]


@pytest.mark.asyncio
async def test_list_filters_by_plan_state_and_operation(repository: RunRepository) -> None:
    runs = (
        make_run("match", state=RunState.RUNNING),
        make_run("wrong-plan", plan_name="photos", state=RunState.RUNNING),
        make_run("wrong-state", state=RunState.SUCCEEDED),
        make_run("wrong-operation", operation=RunOperation.VERIFY, state=RunState.RUNNING),
    )
    for run in runs:
        await repository.create(run)

    result = await repository.list(
        plan_name="movies",
        state=RunState.RUNNING,
        operation=RunOperation.ARCHIVE,
    )

    assert result == (runs[0],)


@pytest.mark.asyncio
async def test_list_clamps_limit_and_offset(repository: RunRepository, monkeypatch) -> None:
    received = []

    def capture(plan_name, state, operation, limit, offset):
        received.append((limit, offset))
        return ()

    monkeypatch.setattr(repository, "_list_sync", capture)

    await repository.list(limit=0, offset=-4)
    await repository.list(limit=50_000, offset=7)

    assert received == [(1, 0), (500, 7)]


@pytest.mark.asyncio
async def test_latest_for_plan_returns_latest_or_none(repository: RunRepository) -> None:
    older = make_run("old")
    newer = replace(older, id="new", created_at=older.created_at + timedelta(seconds=1))
    other = replace(older, id="other", plan_name="photos", created_at=newer.created_at)
    for run in (older, newer, other):
        await repository.create(run)

    assert await repository.latest_for_plan("movies") == newer
    assert await repository.latest_for_plan("unknown") is None
