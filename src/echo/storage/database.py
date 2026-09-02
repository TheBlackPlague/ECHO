from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from echo.core.config import StorageConfig
from echo.storage.errors import (
    DatabaseSchemaError,
    DatabaseUpgradeRequiredError,
    StorageError,
)
from echo.storage.schema import (create_schema, SCHEMA_VERSION, user_tables, validate_schema, version)


class Database:
    """Own SQLite connection lifecycle and current-schema initialization."""

    def __init__(self, config: StorageConfig) -> None:
        self.path = config.database.expanduser()
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def initialize(self) -> None:
        async with self._initialization_lock:
            if self._initialized: return

            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    async def close(self) -> None:
        self._initialized = False

    def connect(self) -> AbstractContextManager[sqlite3.Connection]:
        if not self._initialized:
            raise StorageError("Database is not initialized")

        return self._managed_connection()

    @contextmanager
    def _managed_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()

        try:
            with connection:
                yield connection

        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with self._managed_connection() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                schema_version = version(connection)

                if schema_version == 0:
                    if user_tables(connection):
                        raise DatabaseSchemaError(
                            f"Database at {self.path} contains an unversioned schema"
                        )

                    create_schema(connection)
                    schema_version = SCHEMA_VERSION

                if schema_version < SCHEMA_VERSION:
                    raise DatabaseUpgradeRequiredError(
                        f"Database schema {schema_version} must be upgraded to {SCHEMA_VERSION}; "
                        "stop ECHO and run the database upgrade script"
                    )

                if schema_version > SCHEMA_VERSION:
                    raise DatabaseSchemaError(
                        f"Database schema {schema_version} is newer than supported schema "
                        f"{SCHEMA_VERSION}"
                    )

                validate_schema(connection)

        except StorageError:
            raise

        except sqlite3.Error as exc:
            raise StorageError(
                f"Unable to initialize database at {self.path}: {exc}"
            ) from exc
