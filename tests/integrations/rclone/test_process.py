from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import echo.integrations.rclone.client as client_module
from echo.core.config import RcloneConfig, S3UploadConfig
from echo.integrations.rclone import (
    RcloneClient,
    RcloneCommandError,
    RcloneCommandResult,
    RcloneItem,
    RcloneOutputError,
    RcloneTimeoutError,
    RcloneUnavailableError,
)


def make_client(**overrides: object) -> RcloneClient:
    values: dict[str, object] = {"remote": "archive", "bucket": "bucket"}
    values.update(overrides)
    return RcloneClient(RcloneConfig(**values))


def result(return_code: int = 0) -> RcloneCommandResult:
    return RcloneCommandResult(("rclone",), return_code, "", "", 0)


@pytest.mark.asyncio
async def test_run_constructs_command_decodes_output_and_merges_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "rclone.conf"
    client = make_client(binary="custom-rclone", config_file=config_file)
    process = SimpleNamespace(returncode=0)
    create = AsyncMock(return_value=process)
    communicate = AsyncMock(return_value=(b"hello\xff", b"warning\xfe"))
    monkeypatch.setattr(client_module.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(client_module, "_communicate", communicate)

    command = await client.run("about", "archive:bucket", environment={"SPECIAL": "yes"})

    assert command.command == (
        "custom-rclone",
        "--config",
        str(config_file),
        "--ask-password=false",
        "about",
        "archive:bucket",
    )
    assert command.stdout == "hello�"
    assert command.stderr == "warning�"
    assert command.duration_seconds >= 0
    create.assert_awaited_once()
    positional = create.await_args.args
    assert positional == command.command
    assert create.await_args.kwargs["stdin"] is asyncio.subprocess.DEVNULL
    assert create.await_args.kwargs["stdout"] is asyncio.subprocess.PIPE
    assert create.await_args.kwargs["stderr"] is asyncio.subprocess.PIPE
    assert create.await_args.kwargs["env"] == {**os.environ, "SPECIAL": "yes"}
    communicate.assert_awaited_once_with(process, None)


@pytest.mark.asyncio
async def test_run_adds_progress_arguments_and_returns_unchecked_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    process = SimpleNamespace(returncode=9)
    create = AsyncMock(return_value=process)
    communicate = AsyncMock(return_value=(b"out", b"err"))
    callback = AsyncMock()
    monkeypatch.setattr(client_module.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(client_module, "_communicate", communicate)

    command = await client.run("copy", "a", "b", check=False, progress=callback)

    assert command.return_code == 9
    assert command.command[-5:] == (
        "--stats",
        "1s",
        "--use-json-log",
        "--stats-log-level",
        "NOTICE",
    )
    assert create.await_args.kwargs["env"] is None
    communicate.assert_awaited_once_with(process, callback)


@pytest.mark.asyncio
async def test_run_uses_negative_one_if_process_has_no_return_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    process = SimpleNamespace(returncode=None)
    monkeypatch.setattr(
        client_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    monkeypatch.setattr(client_module, "_communicate", AsyncMock(return_value=(b"", b"")))

    command = await client.run("version", check=False)
    assert command.return_code == -1


@pytest.mark.asyncio
async def test_run_raises_command_error_for_checked_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    process = SimpleNamespace(returncode=4)
    monkeypatch.setattr(
        client_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    monkeypatch.setattr(
        client_module, "_communicate", AsyncMock(return_value=(b"", b"permission denied"))
    )

    with pytest.raises(RcloneCommandError) as caught:
        await client.run("copy", "a", "b")
    assert caught.value.result.return_code == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [FileNotFoundError(), PermissionError()])
async def test_run_wraps_binary_execution_failures(
    monkeypatch: pytest.MonkeyPatch, exception: Exception
) -> None:
    client = make_client(binary="missing-rclone")
    monkeypatch.setattr(
        client_module.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=exception),
    )

    with pytest.raises(RcloneUnavailableError, match="missing-rclone") as caught:
        await client.run("version")
    assert caught.value.__cause__ is exception


@pytest.mark.asyncio
async def test_run_requires_an_operation() -> None:
    with pytest.raises(ValueError, match="At least one"):
        await make_client().run()


@pytest.mark.asyncio
async def test_run_times_out_and_terminates_process(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client()
    process = SimpleNamespace(returncode=None)
    monkeypatch.setattr(
        client_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )

    async def never_finishes(*_args: object) -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    terminate = AsyncMock()
    monkeypatch.setattr(client_module, "_communicate", never_finishes)
    monkeypatch.setattr(client_module, "_terminate_process", terminate)

    with pytest.raises(RcloneTimeoutError) as caught:
        await client.run("about", timeout=0.001)
    assert caught.value.timeout == 0.001
    terminate.assert_awaited_once_with(process)


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [asyncio.CancelledError(), RuntimeError("reader failed")])
async def test_run_terminates_on_cancellation_and_unexpected_communication_errors(
    monkeypatch: pytest.MonkeyPatch, exception: BaseException
) -> None:
    client = make_client()
    process = SimpleNamespace(returncode=None)
    monkeypatch.setattr(
        client_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    monkeypatch.setattr(client_module, "_communicate", AsyncMock(side_effect=exception))
    terminate = AsyncMock()
    monkeypatch.setattr(client_module, "_terminate_process", terminate)

    with pytest.raises(type(exception), match=str(exception) or None):
        await client.run("copy", "a", "b")
    terminate.assert_awaited_once_with(process)


@pytest.mark.asyncio
async def test_transfer_builds_filters_ordering_and_dry_run() -> None:
    client = make_client()
    client.run = AsyncMock(return_value=result())  # type: ignore[method-assign]
    callback = AsyncMock()

    await client._transfer(
        "copy",
        "source",
        "destination",
        exclude=("*.tmp", "cache/**"),
        dry_run=True,
        progress=callback,
        check_first=True,
        optimize_large_uploads=True,
    )

    client.run.assert_awaited_once_with(
        "copy",
        "source",
        "destination",
        "--exclude",
        "*.tmp",
        "--exclude",
        "cache/**",
        "--check-first",
        "--dry-run",
        progress=callback,
        environment=None,
    )


@pytest.mark.asyncio
async def test_transfer_applies_validated_aws_integrity_profile() -> None:
    upload = S3UploadConfig(
        transfers=3,
        chunk_size_mib=8,
        upload_concurrency=4,
        max_buffer_memory_mib=64,
    )
    client = make_client(s3_upload=upload)
    client._large_uploads_optimized = True
    client.run = AsyncMock(return_value=result())  # type: ignore[method-assign]

    await client._transfer(
        "copy", "source", "destination", dry_run=False, optimize_large_uploads=True
    )

    args = client.run.await_args.args
    assert args == (
        "copy",
        "source",
        "destination",
        "--multi-thread-streams",
        "0",
        "--transfers",
        "3",
        "--s3-chunk-size",
        "8Mi",
        "--s3-upload-concurrency",
        "4",
        "--max-buffer-memory",
        "64Mi",
        "--s3-disable-checksum",
        "--s3-use-data-integrity-protections=true",
        "--s3-use-multipart-etag=true",
        "--s3-no-head=false",
        "--s3-use-presigned-request=false",
    )
    assert client.run.await_args.kwargs["environment"] == {
        "AWS_REQUEST_CHECKSUM_CALCULATION": "WHEN_SUPPORTED",
        "AWS_RESPONSE_CHECKSUM_VALIDATION": "WHEN_SUPPORTED",
    }


@pytest.mark.asyncio
async def test_list_items_builds_all_flags_and_parses_items() -> None:
    client = make_client()
    client.run_json = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"Path": "file", "Name": "file", "Size": 2, "IsDir": False}]
    )

    items = await client._list_items(
        "archive:bucket",
        recursive=True,
        files_only=True,
        include_hashes=True,
        exclude=("*.tmp",),
    )

    assert items == (RcloneItem("file", "file", 2, False),)
    client.run_json.assert_awaited_once_with(
        "lsjson",
        "archive:bucket",
        "--recursive",
        "--files-only",
        "--hash",
        "--exclude",
        "*.tmp",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [({}, "non-list response"), ([{"Path": "ok"}, "bad"], "invalid item")],
)
async def test_list_items_rejects_malformed_payloads(payload: object, message: str) -> None:
    client = make_client()
    client.run_json = AsyncMock(return_value=payload)  # type: ignore[method-assign]

    with pytest.raises(RcloneOutputError, match=message):
        await client._list_items("archive:bucket")


def test_command_omits_config_flag_when_no_config_file_is_set() -> None:
    assert make_client()._command("version") == (
        "rclone",
        "--ask-password=false",
        "version",
    )
