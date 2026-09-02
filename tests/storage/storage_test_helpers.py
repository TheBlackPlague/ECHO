from __future__ import annotations

from dataclasses import replace
from datetime import datetime, UTC
from pathlib import Path

from echo.archive.models import ArchiveRun, RunOperation, RunState, RunTrigger


def make_run(identifier: str = "run-1", **overrides) -> ArchiveRun:
    run = ArchiveRun(
        id=identifier,
        plan_name="movies",
        operation=RunOperation.ARCHIVE,
        trigger=RunTrigger.MANUAL,
        state=RunState.QUEUED,
        dry_run=False,
        source=Path("/media/movies"),
        destination="aws:bucket/movies",
        created_at=datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC),
    )
    return replace(run, **overrides)
