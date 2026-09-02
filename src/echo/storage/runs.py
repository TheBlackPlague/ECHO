from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Collection
from datetime import datetime, UTC
from pathlib import Path

from echo.archive.models import ArchiveRun, RunOperation, RunState, RunTrigger
from echo.core.config import StorageConfig
from echo.storage.database import Database
from echo.storage.errors import (
    InvalidRunTransitionError,
    RunNotFoundError,
    StorageError,
)


class RunRepository:
    """Durable archive run ledger backed by SQLite."""

    def __init__(self, config: StorageConfig, database: Database) -> None:
        self.config = config
        self.database = database
        self._write_lock = asyncio.Lock()

    async def create(self, run: ArchiveRun) -> ArchiveRun:
        async with self._write_lock:
            await asyncio.to_thread(self._create_sync, run)

        return run

    async def get(self, run_id: str) -> ArchiveRun:
        run = await asyncio.to_thread(self._get_sync, run_id)

        if run is None: raise RunNotFoundError(f"Archive run not found: {run_id}")

        return run

    async def list(
        self,
        *,
        plan_name: str | None = None,
        state: RunState | None = None,
        operation: RunOperation | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ArchiveRun, ...]:
        return await asyncio.to_thread(
            self._list_sync,
            plan_name,
            state,
            operation,
            min(max(limit, 1), 500),
            max(offset, 0),
        )

    async def latest_for_plan(self, plan_name: str) -> ArchiveRun | None:
        return await asyncio.to_thread(self._latest_for_plan_sync, plan_name)

    async def transition(
        self,
        run_id: str,
        *,
        expected: Collection[RunState],
        state: RunState,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        return_code: int | None = None,
        error: str | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> ArchiveRun:
        async with self._write_lock:
            return await asyncio.to_thread(
                self._transition_sync,
                run_id,
                tuple(expected),
                state,
                started_at,
                finished_at,
                return_code,
                error,
                stdout,
                stderr,
            )

    async def update_progress(
        self,
        run_id: str,
        *,
        progress: float,
        files_added: int,
        files_verified: int,
    ) -> ArchiveRun:
        async with self._write_lock:
            return await asyncio.to_thread(
                self._update_progress_sync,
                run_id,
                min(max(progress, 0.0), 100.0),
                max(files_added, 0),
                max(files_verified, 0),
            )

    async def recover_incomplete(self) -> int:
        """Mark work left by an unclean process exit as interrupted."""
        async with self._write_lock:
            return await asyncio.to_thread(self._recover_incomplete_sync)

    async def summary(self) -> dict[RunState, int]:
        return await asyncio.to_thread(self._summary_sync)

    def _create_sync(self, run: ArchiveRun) -> None:
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO archive_runs (id, plan_name, operation, trigger, state, dry_run, source,
                                              destination, created_at, started_at, finished_at, return_code,
                                              error, stdout, stderr, progress, files_added, files_verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _run_values(run),
                )

        except sqlite3.Error as exc:
            raise StorageError(f"Unable to create archive run {run.id}: {exc}") from exc

    def _get_sync(self, run_id: str) -> ArchiveRun | None:
        try:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM archive_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()

        except sqlite3.Error as exc:
            raise StorageError(f"Unable to read archive run {run_id}: {exc}") from exc

        return _row_to_run(row) if row is not None else None

    def _list_sync(
        self,
        plan_name: str | None,
        state: RunState | None,
        operation: RunOperation | None,
        limit: int,
        offset: int,
    ) -> tuple[ArchiveRun, ...]:
        clauses: list[str] = []
        parameters: list[object] = []

        for column, value in (
                ("plan_name", plan_name),
                ("state", state.value if state else None),
                ("operation", operation.value if operation else None),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((limit, offset))

        try:
            with self.database.connect() as connection:
                rows = connection.execute(
                    f"SELECT * FROM archive_runs {where} "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    parameters,
                ).fetchall()

        except sqlite3.Error as exc:
            raise StorageError(f"Unable to list archive runs: {exc}") from exc

        return tuple(_row_to_run(row) for row in rows)

    def _latest_for_plan_sync(self, plan_name: str) -> ArchiveRun | None:
        try:
            with self.database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM archive_runs
                    WHERE plan_name = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (plan_name,),
                ).fetchone()

        except sqlite3.Error as exc:
            raise StorageError(
                f"Unable to read latest archive run for {plan_name}: {exc}"
            ) from exc

        return _row_to_run(row) if row is not None else None

    def _transition_sync(
        self,
        run_id: str,
        expected: tuple[RunState, ...],
        state: RunState,
        started_at: datetime | None,
        finished_at: datetime | None,
        return_code: int | None,
        error: str | None,
        stdout: str | None,
        stderr: str | None,
    ) -> ArchiveRun:
        if not expected: raise ValueError("At least one expected state is required")

        assignments = ["state = ?"]
        values: list[object] = [state.value]

        for column, value in (
                ("started_at", _serialize_datetime(started_at)),
                ("finished_at", _serialize_datetime(finished_at)),
                ("return_code", return_code),
                ("error", error),
                ("stdout", self._retain_output(stdout)),
                ("stderr", self._retain_output(stderr)),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)

        placeholders = ", ".join("?" for _ in expected)
        values.extend((run_id, *(item.value for item in expected)))

        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE archive_runs
                    SET {", ".join(assignments)}
                    WHERE id = ? AND state IN ({placeholders})
                    """,
                    values,
                )
                if cursor.rowcount != 1:
                    current = connection.execute(
                        "SELECT state FROM archive_runs WHERE id = ?",
                        (run_id,),
                    ).fetchone()

                    if current is None:
                        raise RunNotFoundError(f"Archive run not found: {run_id}")

                    raise InvalidRunTransitionError(
                        f"Cannot transition archive run {run_id} from "
                        f"{current['state']} to {state.value}"
                    )

                row = connection.execute(
                    "SELECT * FROM archive_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()

        except (RunNotFoundError, InvalidRunTransitionError):
            raise

        except sqlite3.Error as exc:
            raise StorageError(f"Unable to transition archive run {run_id}: {exc}") from exc

        return _row_to_run(row)

    def _update_progress_sync(
        self,
        run_id: str,
        progress: float,
        files_added: int,
        files_verified: int,
    ) -> ArchiveRun:
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE archive_runs
                    SET progress       = MAX(COALESCE(progress, 0), ?),
                        files_added    = MAX(files_added, ?),
                        files_verified = MAX(files_verified, ?)
                    WHERE id = ?
                      AND state = ?
                    """,
                    (
                        progress,
                        files_added,
                        files_verified,
                        run_id,
                        RunState.RUNNING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    row = connection.execute(
                        "SELECT * FROM archive_runs WHERE id = ?",
                        (run_id,),
                    ).fetchone()
                    if row is None:
                        raise RunNotFoundError(f"Archive run not found: {run_id}")
                    return _row_to_run(row)

                row = connection.execute(
                    "SELECT * FROM archive_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()

        except RunNotFoundError:
            raise

        except sqlite3.Error as exc:
            raise StorageError(
                f"Unable to update archive run {run_id} progress: {exc}"
            ) from exc

        return _row_to_run(row)

    def _recover_incomplete_sync(self) -> int:
        finished_at = _serialize_datetime(datetime.now(UTC))

        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE archive_runs
                    SET state       = ?,
                        finished_at = ?,
                        error       = ?
                    WHERE state IN (?, ?)
                    """,
                    (
                        RunState.INTERRUPTED.value,
                        finished_at,
                        "ECHO stopped before the run completed",
                        RunState.QUEUED.value,
                        RunState.RUNNING.value,
                    ),
                )

        except sqlite3.Error as exc:
            raise StorageError(f"Unable to recover interrupted archive runs: {exc}") from exc

        return cursor.rowcount

    def _summary_sync(self) -> dict[RunState, int]:
        try:
            with self.database.connect() as connection:
                rows = connection.execute(
                    "SELECT state, COUNT(*) AS count "
                    "FROM archive_runs GROUP BY state"
                ).fetchall()

        except sqlite3.Error as exc:
            raise StorageError(f"Unable to summarize archive runs: {exc}") from exc

        summary = {state: 0 for state in RunState}
        summary.update({RunState(row["state"]): int(row["count"]) for row in rows})
        return summary

    def _retain_output(self, output: str | None) -> str | None:
        if output is None: return None

        limit = self.config.retained_output_bytes
        encoded = output.encode("utf-8", errors="replace")

        if len(encoded) <= limit: return output
        if limit == 0: return ""

        tail = encoded[-limit:].decode("utf-8", errors="replace")
        return f"[output truncated; final {limit} bytes retained]\n{tail}"


def _run_values(run: ArchiveRun) -> tuple[object, ...]:
    return (
        run.id,
        run.plan_name,
        run.operation.value,
        run.trigger.value,
        run.state.value,
        int(run.dry_run),
        str(run.source),
        run.destination,
        _serialize_datetime(run.created_at),
        _serialize_datetime(run.started_at),
        _serialize_datetime(run.finished_at),
        run.return_code,
        run.error,
        run.stdout,
        run.stderr,
        run.progress,
        run.files_added,
        run.files_verified,
    )


def _row_to_run(row: sqlite3.Row) -> ArchiveRun:
    return ArchiveRun(
        id=str(row["id"]),
        plan_name=str(row["plan_name"]),
        operation=RunOperation(row["operation"]),
        trigger=RunTrigger(row["trigger"]),
        state=RunState(row["state"]),
        dry_run=bool(row["dry_run"]),
        source=Path(row["source"]),
        destination=str(row["destination"]),
        created_at=_parse_datetime(row["created_at"]),
        started_at=_parse_datetime(row["started_at"]),
        finished_at=_parse_datetime(row["finished_at"]),
        return_code=row["return_code"],
        error=row["error"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        progress=row["progress"],
        files_added=int(row["files_added"]),
        files_verified=int(row["files_verified"]),
    )


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None
