from __future__ import annotations

import sqlite3

import pytest

from echo.storage.errors import DatabaseSchemaError
from echo.storage.schema import (create_schema, SCHEMA_VERSION, user_tables, validate_schema, version)


@pytest.fixture
def connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def test_create_schema_builds_current_schema(connection: sqlite3.Connection) -> None:
    create_schema(connection)

    assert version(connection) == SCHEMA_VERSION
    assert user_tables(connection) == frozenset({"archive_runs"})
    validate_schema(connection)

    indexes = {
        row["name"]
        for row in connection.execute("PRAGMA index_list(archive_runs)").fetchall()
    }
    assert {"archive_runs_plan_created", "archive_runs_state_created"} <= indexes


def test_archive_run_schema_applies_counter_defaults(connection: sqlite3.Connection) -> None:
    create_schema(connection)
    connection.execute(
        """
        INSERT INTO archive_runs (id, plan_name, operation, trigger, state, dry_run,
                                  source, destination, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run-1",
            "movies",
            "archive",
            "manual",
            "queued",
            0,
            "/media/movies",
            "aws:bucket/movies",
            "2026-01-02T03:04:05+00:00",
        ),
    )

    row = connection.execute(
        "SELECT files_added, files_verified FROM archive_runs WHERE id = 'run-1'"
    ).fetchone()
    assert (row["files_added"], row["files_verified"]) == (0, 0)


def test_archive_run_schema_rejects_invalid_dry_run_and_rolls_back_statement(
    connection: sqlite3.Connection,
) -> None:
    create_schema(connection)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        connection.execute(
            """
            INSERT INTO archive_runs (id, plan_name, operation, trigger, state, dry_run,
                                      source, destination, created_at)
            VALUES ('invalid', 'movies', 'archive', 'manual', 'queued', 2,
                    '/media/movies', 'aws:bucket/movies', '2026-01-02T03:04:05+00:00')
            """
        )

    assert connection.execute("SELECT COUNT(*) FROM archive_runs").fetchone()[0] == 0


def test_user_tables_excludes_sqlite_internal_tables(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE ordinary (id INTEGER PRIMARY KEY AUTOINCREMENT)")
    connection.execute("INSERT INTO ordinary DEFAULT VALUES")

    assert user_tables(connection) == frozenset({"ordinary"})


def test_validate_schema_rejects_wrong_version(connection: sqlite3.Connection) -> None:
    create_schema(connection)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

    with pytest.raises(DatabaseSchemaError, match="does not match supported schema"):
        validate_schema(connection)


def test_validate_schema_rejects_missing_table(connection: sqlite3.Connection) -> None:
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    with pytest.raises(DatabaseSchemaError, match="missing archive_runs"):
        validate_schema(connection)


def test_validate_schema_reports_all_missing_columns_sorted(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("CREATE TABLE archive_runs (id TEXT PRIMARY KEY)")
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    with pytest.raises(DatabaseSchemaError) as raised:
        validate_schema(connection)

    message = str(raised.value)
    assert "missing columns:" in message
    assert "created_at" in message
    assert "files_verified" in message
    assert "id" not in message.removeprefix("Current database schema is missing columns: ")
    columns = message.split(": ", 1)[1].split(", ")
    assert columns == sorted(columns)
