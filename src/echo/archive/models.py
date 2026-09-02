from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class RunOperation(StrEnum):
    ARCHIVE = "archive"
    VERIFY = "verify"


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            RunState.SUCCEEDED,
            RunState.FAILED,
            RunState.CANCELLED,
            RunState.INTERRUPTED,
        }


@dataclass(frozen=True, slots=True)
class ArchiveRun:
    id: str
    plan_name: str
    operation: RunOperation
    trigger: RunTrigger
    state: RunState
    dry_run: bool
    source: Path
    destination: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    return_code: int | None = None
    error: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    progress: float | None = None
    files_added: int = 0
    files_verified: int = 0

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None: return None

        end = self.finished_at or datetime.now(self.started_at.tzinfo)
        return max((end - self.started_at).total_seconds(), 0.0)
