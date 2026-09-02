from __future__ import annotations

from echo.integrations.rclone.errors import RcloneCommandError, RcloneTimeoutError
from echo.integrations.rclone.models import RcloneCommandResult


def test_command_error_prefers_stderr_detail() -> None:
    result = RcloneCommandResult(("rclone", "copy"), 7, "stdout detail", "stderr detail\n", 1)
    error = RcloneCommandError(result)

    assert error.result is result
    assert str(error) == "rclone command failed with exit code 7: stderr detail"


def test_command_error_falls_back_to_stdout_or_has_no_detail() -> None:
    with_stdout = RcloneCommandError(RcloneCommandResult(("rclone",), 2, "reason", "", 0))
    without_output = RcloneCommandError(RcloneCommandResult(("rclone",), -1, "", "", 0))

    assert str(with_stdout).endswith(": reason")
    assert str(without_output) == "rclone command failed with exit code -1"


def test_timeout_error_retains_command_and_timeout() -> None:
    error = RcloneTimeoutError(["rclone", "about"], 2.5)

    assert error.command == ("rclone", "about")
    assert error.timeout == 2.5
    assert str(error) == "rclone command timed out after 2.5 seconds"
