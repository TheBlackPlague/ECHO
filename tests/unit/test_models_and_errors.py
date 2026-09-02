from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, UTC
from pathlib import Path

import pytest

from echo.archive.errors import (
    ArchivePlanRunningError,
    ArchiverError,
    SchedulerConfigurationError,
    SchedulerError,
)
from echo.archive.models import ArchiveRun, RunOperation, RunState, RunTrigger
from echo.integrations.rclone.errors import (
    RcloneCommandError,
    RcloneError,
    RcloneOutputError,
    RcloneTimeoutError,
)
from echo.integrations.rclone.models import (
    RcloneAbout,
    RcloneCommandResult,
    RcloneItem,
    RcloneProgress,
    RcloneSize,
)
from echo.storage.errors import RunNotFoundError, StorageError


def _archive_run(**overrides: object) -> ArchiveRun:
    values: dict[str, object] = {
        "id": "run-1",
        "plan_name": "daily",
        "operation": RunOperation.ARCHIVE,
        "trigger": RunTrigger.MANUAL,
        "state": RunState.QUEUED,
        "dry_run": False,
        "source": Path("/source"),
        "destination": "bucket/daily",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return ArchiveRun(**values)


def test_enum_values_and_terminal_states() -> None:
    assert str(RunOperation.ARCHIVE) == "archive"
    assert str(RunTrigger.SCHEDULED) == "scheduled"
    assert {state for state in RunState if state.terminal} == {
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.INTERRUPTED,
    }
    assert RunState.QUEUED.terminal is False
    assert RunState.RUNNING.terminal is False


def test_archive_run_duration_for_not_started_finished_and_clock_skew() -> None:
    assert _archive_run().duration_seconds is None

    started = datetime(2026, 1, 1, 12, tzinfo=UTC)
    completed = _archive_run(
        started_at=started,
        finished_at=started + timedelta(seconds=2.5),
    )
    skewed = _archive_run(
        started_at=started,
        finished_at=started - timedelta(seconds=1),
    )
    assert completed.duration_seconds == 2.5
    assert skewed.duration_seconds == 0.0


def test_archive_run_is_frozen() -> None:
    run = _archive_run()
    with pytest.raises(FrozenInstanceError):
        run.state = RunState.RUNNING  # type: ignore[misc]


@pytest.mark.parametrize("return_code", [0, 1, -1])
def test_rclone_command_result_successful(return_code: int) -> None:
    result = RcloneCommandResult(("rclone", "version"), return_code, "out", "err", 0.5)
    assert result.successful is (return_code == 0)


def test_rclone_progress_prefers_bytes_and_clamps_percent() -> None:
    progress = RcloneProgress.from_payload(
        {
            "transfers": "2",
            "checks": 3,
            "totalTransfers": 4,
            "totalChecks": 6,
            "bytes": 250,
            "totalBytes": 100,
        }
    )
    assert progress.percent == 100.0
    assert progress.transfer_percent == 100.0
    assert progress.files_transferred == 2
    assert progress.files_checked == 3
    assert progress.files_to_transfer == 4
    assert progress.files_to_check == 6


def test_rclone_progress_falls_back_to_file_counts() -> None:
    progress = RcloneProgress.from_payload(
        {"transfers": 2, "checks": 1, "totalTransfers": 3, "totalChecks": 3}
    )
    assert progress.percent == 50.0
    assert progress.transfer_percent == 0.0


def test_rclone_progress_sanitizes_missing_negative_and_invalid_numbers() -> None:
    progress = RcloneProgress.from_payload(
        {
            "transfers": -10,
            "checks": "invalid",
            "totalTransfers": None,
            "totalChecks": object(),
            "bytes": -1,
            "totalBytes": 0,
        }
    )
    assert progress == RcloneProgress(percent=0.0, files_transferred=0, files_checked=0)


@pytest.mark.parametrize(
    ("bytes_transferred", "total_bytes", "expected"),
    [(50, 100, 50.0), (-1, 100, 0.0), (200, 100, 100.0), (1, 0, 0.0)],
)
def test_rclone_progress_transfer_percent(
    bytes_transferred: int,
    total_bytes: int,
    expected: float,
) -> None:
    progress = RcloneProgress(0, 0, 0, bytes_transferred=bytes_transferred, total_bytes=total_bytes)
    assert progress.transfer_percent == expected


def test_rclone_about_parses_optional_integer_fields() -> None:
    about = RcloneAbout.from_payload(
        {"total": "100", "used": 25.9, "free": None, "trashed": 2, "other": "3"}
    )
    assert about == RcloneAbout(total=100, used=25, free=None, trashed=2, other=3)


def test_rclone_about_propagates_invalid_integer() -> None:
    with pytest.raises(ValueError):
        RcloneAbout.from_payload({"total": "invalid"})


def test_rclone_size_parses_values_and_wraps_malformed_payloads() -> None:
    assert RcloneSize.from_payload({"count": "2", "bytes": 10.8}) == RcloneSize(count=2, bytes=10)

    for payload in ({}, {"count": None, "bytes": 1}, {"count": 1, "bytes": "bad"}):
        with pytest.raises(RcloneOutputError, match="invalid size data") as caught:
            RcloneSize.from_payload(payload)
        assert caught.value.__cause__ is not None


def test_rclone_item_parses_and_stringifies_payload() -> None:
    marker = object()
    item = RcloneItem.from_payload(
        {
            "Path": 123,
            "Name": marker,
            "Size": "42",
            "IsDir": 1,
            "ModTime": 456,
            "MimeType": None,
            "Tier": "DEEP_ARCHIVE",
            "Hashes": {"md5": 1234},
        }
    )
    assert item.path == "123"
    assert item.name == str(marker)
    assert item.size == 42
    assert item.is_dir is True
    assert item.mod_time == "456"
    assert item.mime_type is None
    assert item.tier == "DEEP_ARCHIVE"
    assert item.hashes == {"md5": "1234"}


def test_rclone_item_defaults_and_ignores_non_mapping_hashes() -> None:
    assert RcloneItem.from_payload({"Hashes": []}) == RcloneItem("", "", 0, False)


def test_archive_plan_running_error_keeps_context_and_message() -> None:
    error = ArchivePlanRunningError("daily", "run-1")
    assert isinstance(error, ArchiverError)
    assert error.plan_name == "daily"
    assert error.run_id == "run-1"
    assert str(error) == "Archive plan is already active: daily (run run-1)"


def test_error_inheritance() -> None:
    assert issubclass(SchedulerConfigurationError, SchedulerError)
    assert issubclass(RunNotFoundError, StorageError)
    assert issubclass(RcloneOutputError, RcloneError)


def test_rclone_command_error_prefers_stderr_then_stdout_and_omits_blank_detail() -> None:
    stderr_result = RcloneCommandResult(("rclone",), 7, "stdout", " stderr detail \n", 1)
    error = RcloneCommandError(stderr_result)
    assert error.result is stderr_result
    assert str(error) == "rclone command failed with exit code 7: stderr detail"

    stdout_result = RcloneCommandResult(("rclone",), 8, " output ", "", 1)
    assert str(RcloneCommandError(stdout_result)) == (
        "rclone command failed with exit code 8: output"
    )

    blank_result = RcloneCommandResult(("rclone",), 9, " ", "\n", 1)
    assert str(RcloneCommandError(blank_result)) == "rclone command failed with exit code 9"


def test_rclone_timeout_error_keeps_command_timeout_and_formats_number() -> None:
    error = RcloneTimeoutError(["rclone", "copy", "source", "remote:"], 30.0)
    assert error.command == ("rclone", "copy", "source", "remote:")
    assert error.timeout == 30.0
    assert str(error) == "rclone command timed out after 30 seconds"
