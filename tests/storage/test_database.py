from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing

import pytest

from echo.core.config import StorageConfig
from echo.storage import database as database_module
from echo.storage.database import Database
from echo.storage.errors import (
    DatabaseSchemaError,
    DatabaseUpgradeRequiredError,
    StorageError,
)
from echo.storage.schema import SCHEMA_VERSION


def config_for(path, *, retained_output_bytes: int = 65_536) -> StorageConfig:
    return StorageConfig(database=path, retained_output_bytes=retained_output_bytes)


@pytest.mark.asyncio
async def test_initialize_creates_parent_database_and_is_idempotent(tmp_path) -> None:
    path = tmp_path / "nested" / "state" / "echo.db"
    database = Database(config_for(path))

    assert database.initialized is False
    await database.initialize()
    await database.initialize()

    assert database.initialized is True
    assert path.is_file()
    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


@pytest.mark.asyncio
async def test_initialize_serializes_concurrent_callers(tmp_path, monkeypatch) -> None:
    database = Database(config_for(tmp_path / "echo.db"))
    original = database._initialize_sync
    calls = 0

    def slow_initialize() -> None:
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        original()

    monkeypatch.setattr(database, "_initialize_sync", slow_initialize)

    await asyncio.gather(*(database.initialize() for _ in range(5)))

    assert calls == 1


@pytest.mark.asyncio
async def test_close_disables_connections_and_allows_reinitialize(tmp_path) -> None:
    database = Database(config_for(tmp_path / "echo.db"))
    await database.initialize()
    await database.close()

    assert database.initialized is False
    with pytest.raises(StorageError, match="Database is not initialized"):
        database.connect()

    await database.initialize()
    assert database.initialized is True


@pytest.mark.asyncio
async def test_connect_configures_rows_foreign_keys_and_busy_timeout(tmp_path) -> None:
    database = Database(config_for(tmp_path / "echo.db"))
    await database.initialize()

    with database.connect() as connection:
        row = connection.execute("SELECT 42 AS answer").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["answer"] == 42
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


@pytest.mark.asyncio
async def test_connect_rolls_back_and_closes_after_error(tmp_path) -> None:
    database = Database(config_for(tmp_path / "echo.db"))
    await database.initialize()

    with pytest.raises(RuntimeError, match="abort transaction"):
        with database.connect() as connection:
            connection.execute("CREATE TABLE transaction_probe (value TEXT)")
            connection.execute("INSERT INTO transaction_probe VALUES ('uncommitted')")
            raise RuntimeError("abort transaction")

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")

    with database.connect() as reopened:
        count = reopened.execute("SELECT COUNT(*) FROM transaction_probe").fetchone()[0]

    assert count == 0


@pytest.mark.asyncio
async def test_initialize_rejects_unversioned_user_schema(tmp_path) -> None:
    path = tmp_path / "echo.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE legacy (value TEXT)")

    database = Database(config_for(path))
    with pytest.raises(DatabaseSchemaError, match="contains an unversioned schema"):
        await database.initialize()

    assert database.initialized is False


@pytest.mark.asyncio
async def test_initialize_requires_upgrade_for_older_schema(tmp_path, monkeypatch) -> None:
    path = tmp_path / "echo.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA user_version = 1")

    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 2)
    database = Database(config_for(path))

    with pytest.raises(DatabaseUpgradeRequiredError, match="schema 1 must be upgraded to 2"):
        await database.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_newer_schema(tmp_path) -> None:
    path = tmp_path / "echo.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

    database = Database(config_for(path))
    with pytest.raises(DatabaseSchemaError, match="newer than supported schema"):
        await database.initialize()


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_sql", [None, "CREATE TABLE archive_runs (id TEXT)"])
async def test_initialize_validates_current_schema(tmp_path, schema_sql) -> None:
    path = tmp_path / "echo.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        if schema_sql is not None:
            connection.execute(schema_sql)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    database = Database(config_for(path))
    with pytest.raises(DatabaseSchemaError, match="missing"):
        await database.initialize()


@pytest.mark.asyncio
async def test_initialize_wraps_sqlite_failures_with_path_and_cause(tmp_path) -> None:
    database = Database(config_for(tmp_path))

    with pytest.raises(StorageError, match="Unable to initialize database") as raised:
        await database.initialize()

    assert str(tmp_path) in str(raised.value)
    assert isinstance(raised.value.__cause__, sqlite3.Error)
