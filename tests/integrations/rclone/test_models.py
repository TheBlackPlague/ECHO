from __future__ import annotations

import pytest

from echo.integrations.rclone.errors import RcloneOutputError
from echo.integrations.rclone.models import (
    RcloneAbout,
    RcloneCommandResult,
    RcloneItem,
    RcloneProgress,
    RcloneSize,
)


def test_command_result_reports_success_from_return_code() -> None:
    successful = RcloneCommandResult(("rclone", "version"), 0, "ok", "", 0.1)
    failed = RcloneCommandResult(("rclone", "version"), 1, "", "bad", 0.1)

    assert successful.successful is True
    assert failed.successful is False


def test_progress_prefers_byte_progress_and_clamps_percentages() -> None:
    progress = RcloneProgress.from_payload(
        {
            "transfers": "2",
            "checks": 3,
            "totalTransfers": 4,
            "totalChecks": 5,
            "bytes": 150,
            "totalBytes": 100,
        }
    )

    assert progress == RcloneProgress(
        percent=100.0,
        files_transferred=2,
        files_checked=3,
        files_to_transfer=4,
        files_to_check=5,
        bytes_transferred=150,
        total_bytes=100,
    )
    assert progress.transfer_percent == 100.0


def test_progress_uses_file_counts_when_total_bytes_is_unavailable() -> None:
    progress = RcloneProgress.from_payload(
        {
            "transfers": 1,
            "checks": 2,
            "totalTransfers": 2,
            "totalChecks": 4,
        }
    )

    assert progress.percent == 50.0
    assert progress.transfer_percent == 0.0


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, RcloneProgress(0.0, 0, 0)),
        (
                {
                    "transfers": -1,
                    "checks": "invalid",
                    "totalTransfers": None,
                    "totalChecks": -4,
                    "bytes": -10,
                    "totalBytes": "invalid",
                },
                RcloneProgress(0.0, 0, 0),
        ),
    ],
)
def test_progress_treats_missing_invalid_and_negative_values_as_zero(
    payload: dict[str, object], expected: RcloneProgress
) -> None:
    assert RcloneProgress.from_payload(payload) == expected


@pytest.mark.parametrize(
    ("bytes_transferred", "total_bytes", "expected"),
    [(50, 100, 50.0), (-1, 100, 0.0), (200, 100, 100.0), (1, 0, 0.0)],
)
def test_transfer_percent_is_bounded(
    bytes_transferred: int, total_bytes: int, expected: float
) -> None:
    progress = RcloneProgress(0, 0, 0, bytes_transferred=bytes_transferred, total_bytes=total_bytes)
    assert progress.transfer_percent == expected


def test_about_converts_present_values_and_preserves_missing_values() -> None:
    assert RcloneAbout.from_payload(
        {"total": "10", "used": 6.0, "free": 4, "trashed": None}
    ) == RcloneAbout(total=10, used=6, free=4, trashed=None, other=None)


@pytest.mark.parametrize("payload", [{}, {"count": "x", "bytes": 1}, {"count": 1, "bytes": None}])
def test_size_rejects_missing_or_invalid_data(payload: dict[str, object]) -> None:
    with pytest.raises(RcloneOutputError, match="invalid size data"):
        RcloneSize.from_payload(payload)


def test_size_converts_numeric_strings() -> None:
    assert RcloneSize.from_payload({"count": "2", "bytes": "4096"}) == RcloneSize(2, 4096)


def test_item_parses_complete_payload_and_normalizes_hash_values() -> None:
    item = RcloneItem.from_payload(
        {
            "Path": "folder/file.bin",
            "Name": "file.bin",
            "Size": "8",
            "IsDir": 0,
            "ModTime": 123,
            "MimeType": "application/octet-stream",
            "Tier": "DEEP_ARCHIVE",
            "Hashes": {"MD5": 123, 4: "abcd"},
        }
    )

    assert item == RcloneItem(
        path="folder/file.bin",
        name="file.bin",
        size=8,
        is_dir=False,
        mod_time="123",
        mime_type="application/octet-stream",
        tier="DEEP_ARCHIVE",
        hashes={"MD5": "123", "4": "abcd"},
    )


def test_item_uses_safe_defaults_and_ignores_non_mapping_hashes() -> None:
    assert RcloneItem.from_payload({"Hashes": ["not", "a", "mapping"]}) == RcloneItem(
        path="", name="", size=0, is_dir=False
    )
