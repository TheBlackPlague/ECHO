from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio

from echo.archive.models import RunState
from echo.core.config import StorageConfig
from echo.storage.database import Database
from echo.storage.errors import StorageError
from echo.storage.runs import RunRepository
from .storage_test_helpers import make_run


@pytest_asyncio.fixture
async def broken_repository(tmp_path) -> RunRepository:
    config = StorageConfig(database=tmp_path / "echo.db")
    database = Database(config)
    await database.initialize()
    with database.connect() as connection:
        connection.execute("DROP TABLE archive_runs")
    return RunRepository(config, database)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda repository: repository.create(make_run()), "Unable to create archive run"),
        (lambda repository: repository.get("run-1"), "Unable to read archive run"),
        (lambda repository: repository.list(), "Unable to list archive runs"),
        (
                lambda repository: repository.latest_for_plan("movies"),
                "Unable to read latest archive run",
        ),
        (
                lambda repository: repository.transition(
                    "run-1", expected=(RunState.QUEUED,), state=RunState.RUNNING
                ),
                "Unable to transition archive run",
        ),
        (
                lambda repository: repository.update_progress(
                    "run-1", progress=1, files_added=1, files_verified=1
                ),
                "Unable to update archive run",
        ),
        (lambda repository: repository.recover_incomplete(), "Unable to recover"),
        (lambda repository: repository.summary(), "Unable to summarize"),
    ],
)
async def test_repository_wraps_sqlite_errors(broken_repository, operation, message) -> None:
    with pytest.raises(StorageError, match=message) as raised:
        await operation(broken_repository)

    assert isinstance(raised.value.__cause__, sqlite3.Error)
