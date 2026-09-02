from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, UTC
from pathlib import Path

from echo.core.config import get_config
from echo.storage.errors import DatabaseSchemaError
from echo.storage.schema import SCHEMA_VERSION, validate_schema, version


# Add one migration here whenever SCHEMA_VERSION is incremented.
# The key is the source schema version and the SQL must advance it by exactly one.
MIGRATIONS: dict[int, str] = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upgrade an existing ECHO SQLite database to the current schema"
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="Database path. Defaults to storage.database from ECHO configuration.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a SQLite backup before applying migrations.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = (args.database or get_config().storage.database).expanduser()

    if not path.is_file():
        raise SystemExit(f"Database does not exist: {path}")

    with closing(sqlite3.connect(path, timeout=10)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")

        current = version(connection)

        if current == SCHEMA_VERSION:
            _validate_current(connection, path)
            print(f"{path}: schema {current} is already current")
            return

        if current == 0:
            raise SystemExit(
                f"{path}: database schema is unversioned; automatic upgrade is not safe"
            )

        if current > SCHEMA_VERSION:
            raise SystemExit(
                f"{path}: schema {current} is newer than supported schema {SCHEMA_VERSION}"
            )

        missing = [
            candidate
            for candidate in range(current, SCHEMA_VERSION)
            if candidate not in MIGRATIONS
        ]
        if missing:
            raise SystemExit(
                f"{path}: no migration path from schema {current} to {SCHEMA_VERSION}"
            )

        if not args.no_backup:
            backup = _backup(connection, path, current)
            print(f"Backup: {backup}")

        original = current
        while current < SCHEMA_VERSION:
            target = current + 1
            _apply_migration(connection, path, current, target)
            current = version(connection)

            if current != target:
                raise SystemExit(
                    f"{path}: migration expected schema {target}, found {current}"
                )

        _validate_current(connection, path)

    print(f"{path}: upgraded schema {original} -> {SCHEMA_VERSION}")


def _apply_migration(
    connection: sqlite3.Connection,
    path: Path,
    source: int,
    target: int,
) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + MIGRATIONS[source]
            + f"\nPRAGMA user_version = {target};\nCOMMIT;"
        )
    except sqlite3.Error as exc:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

        raise SystemExit(
            f"{path}: migration {source} -> {target} failed: {exc}"
        ) from exc


def _validate_current(connection: sqlite3.Connection, path: Path) -> None:
    try:
        validate_schema(connection)
    except DatabaseSchemaError as exc:
        raise SystemExit(
            f"{path}: schema version matches {SCHEMA_VERSION}, but its layout does not match "
            "the current schema baseline. Legacy schema-1 databases are not supported; "
            "recreate the database."
        ) from exc


def _backup(connection: sqlite3.Connection, path: Path, schema_version: int) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.schema-{schema_version}.{timestamp}.bak")

    with closing(sqlite3.connect(backup)) as target, target:
        connection.backup(target)

    return backup


if __name__ == "__main__":
    main()
