from __future__ import annotations

import sqlite3

from echo.storage.errors import DatabaseSchemaError


SCHEMA_VERSION = 1

CREATE_SCHEMA = f"""
CREATE TABLE archive_runs
(
    id             TEXT PRIMARY KEY,
    plan_name      TEXT    NOT NULL,
    operation      TEXT    NOT NULL,
    trigger        TEXT    NOT NULL,
    state          TEXT    NOT NULL,
    dry_run        INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
    source         TEXT    NOT NULL,
    destination    TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    started_at     TEXT,
    finished_at    TEXT,
    return_code    INTEGER,
    error          TEXT,
    stdout         TEXT,
    stderr         TEXT,
    progress       REAL,
    files_added    INTEGER NOT NULL DEFAULT 0,
    files_verified INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX archive_runs_plan_created
    ON archive_runs (plan_name, created_at DESC);

CREATE INDEX archive_runs_state_created
    ON archive_runs (state, created_at DESC);

PRAGMA user_version = {SCHEMA_VERSION};
"""

_REQUIRED_RUN_COLUMNS = frozenset(
    {
        "id",
        "plan_name",
        "operation",
        "trigger",
        "state",
        "dry_run",
        "source",
        "destination",
        "created_at",
        "started_at",
        "finished_at",
        "return_code",
        "error",
        "stdout",
        "stderr",
        "progress",
        "files_added",
        "files_verified",
    }
)


def version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def user_tables(connection: sqlite3.Connection) -> frozenset[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()

    return frozenset(str(row["name"]) for row in rows)


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(CREATE_SCHEMA)


def validate_schema(connection: sqlite3.Connection) -> None:
    schema_version = version(connection)
    if schema_version != SCHEMA_VERSION:
        raise DatabaseSchemaError(f"Database schema {schema_version} does not match supported schema {SCHEMA_VERSION}")

    if "archive_runs" not in user_tables(connection):
        raise DatabaseSchemaError("Current database schema is missing archive_runs")

    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(archive_runs)").fetchall()
    }
    missing = _REQUIRED_RUN_COLUMNS.difference(columns)

    if missing: raise DatabaseSchemaError("Current database schema is missing columns: " + ", ".join(sorted(missing)))
