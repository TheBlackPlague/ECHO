from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from echo.integrations.rclone.errors import RcloneOutputError


@dataclass(frozen=True, slots=True)
class RcloneCommandResult:
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def successful(self) -> bool:
        return self.return_code == 0


@dataclass(frozen=True, slots=True)
class RcloneProgress:
    percent: float
    files_transferred: int
    files_checked: int
    files_to_transfer: int = 0
    files_to_check: int = 0
    bytes_transferred: int = 0
    total_bytes: int = 0

    @property
    def transfer_percent(self) -> float:
        if self.total_bytes <= 0: return 0.0
        percent = self.bytes_transferred / self.total_bytes * 100
        return min(max(percent, 0.0), 100.0)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RcloneProgress:
        transferred = _nonnegative_int(payload.get("transfers"))
        checked = _nonnegative_int(payload.get("checks"))
        completed = transferred + checked
        total = _nonnegative_int(payload.get("totalTransfers")) + _nonnegative_int(
            payload.get("totalChecks")
        )
        total_bytes = _nonnegative_int(payload.get("totalBytes"))
        bytes_transferred = _nonnegative_int(payload.get("bytes"))

        if total_bytes > 0:
            percent = bytes_transferred / total_bytes * 100
        elif total > 0:
            percent = completed / total * 100
        else:
            percent = 0.0

        return cls(
            percent=min(max(percent, 0.0), 100.0),
            files_transferred=transferred,
            files_checked=checked,
            files_to_transfer=_nonnegative_int(payload.get("totalTransfers")),
            files_to_check=_nonnegative_int(payload.get("totalChecks")),
            bytes_transferred=bytes_transferred,
            total_bytes=total_bytes,
        )


@dataclass(frozen=True, slots=True)
class RcloneStatus:
    version: str
    remote: str | None
    bucket: str | None
    remotes: tuple[str, ...]
    large_uploads_optimized: bool = False


@dataclass(frozen=True, slots=True)
class RcloneVerificationResult:
    result: RcloneCommandResult
    files_verified: int


@dataclass(frozen=True, slots=True)
class RcloneAbout:
    total: int | None = None
    used: int | None = None
    free: int | None = None
    trashed: int | None = None
    other: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RcloneAbout:
        def optional_int(key: str) -> int | None:
            value = payload.get(key)

            # noinspection bad-argument-type
            return int(value) if value is not None else None

        return cls(
            total=optional_int("total"),
            used=optional_int("used"),
            free=optional_int("free"),
            trashed=optional_int("trashed"),
            other=optional_int("other"),
        )


@dataclass(frozen=True, slots=True)
class RcloneSize:
    count: int
    bytes: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RcloneSize:
        try:
            return cls(count=int(payload["count"]), bytes=int(payload["bytes"]))

        except (KeyError, TypeError, ValueError) as exc:
            raise RcloneOutputError("rclone size returned invalid size data") from exc


@dataclass(frozen=True, slots=True)
class RcloneItem:
    path: str
    name: str
    size: int
    is_dir: bool
    mod_time: str | None = None
    mime_type: str | None = None
    tier: str | None = None
    hashes: dict[str, str] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RcloneItem:
        hashes = payload.get("Hashes")
        return cls(
            path=str(payload.get("Path", "")),
            name=str(payload.get("Name", "")),
            size=int(payload.get("Size", 0)),
            is_dir=bool(payload.get("IsDir", False)),
            mod_time=_optional_string(payload.get("ModTime")),
            mime_type=_optional_string(payload.get("MimeType")),
            tier=_optional_string(payload.get("Tier")),
            hashes=(
                {str(key): str(value) for key, value in hashes.items()}
                if isinstance(hashes, dict)
                else None
            ),
        )


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _nonnegative_int(value: Any) -> int:
    try:
        return max(int(value), 0)

    except (TypeError, ValueError):
        return 0
