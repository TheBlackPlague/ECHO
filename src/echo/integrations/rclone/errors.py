from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING


if TYPE_CHECKING: from echo.integrations.rclone.models import RcloneCommandResult


class RcloneError(RuntimeError):
    """Base class for rclone integration errors."""


class RcloneUnavailableError(RcloneError):
    pass


class RcloneConfigurationError(RcloneError):
    pass


class RcloneOutputError(RcloneError):
    pass


class RcloneVerificationError(RcloneError):
    pass


class RcloneCommandError(RcloneError):
    def __init__(self, result: RcloneCommandResult) -> None:
        self.result = result
        detail = (result.stderr or result.stdout).strip()
        message = f"rclone command failed with exit code {result.return_code}"

        if detail: message = f"{message}: {detail}"

        super().__init__(message)


class RcloneTimeoutError(RcloneError):
    def __init__(self, command: Sequence[str], timeout: float) -> None:
        self.command = tuple(command)
        self.timeout = timeout

        super().__init__(f"rclone command timed out after {timeout:g} seconds")
