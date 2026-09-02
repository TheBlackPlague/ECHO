from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from echo.core.config import RcloneConfig, S3UploadConfig
from echo.integrations.rclone import (
    RcloneAbout,
    RcloneClient,
    RcloneCommandResult,
    RcloneConfigurationError,
    RcloneItem,
    RcloneOutputError,
    RcloneSize,
)


def make_client(**overrides: object) -> RcloneClient:
    values: dict[str, object] = {"remote": "archive", "bucket": "bucket"}
    values.update(overrides)
    return RcloneClient(RcloneConfig(**values))


def command_result(
    stdout: str = "", stderr: str = "", return_code: int = 0
) -> RcloneCommandResult:
    return RcloneCommandResult(("rclone",), return_code, stdout, stderr, 0.01)


def item(path: str, size: int = 10, md5: str | None = "a" * 32) -> RcloneItem:
    return RcloneItem(
        path=path,
        name=Path(path).name,
        size=size,
        is_dir=False,
        hashes=None if md5 is None else {"MD5": md5},
    )


def test_properties_and_remote_path_normalization() -> None:
    client = make_client(remote="archive:")

    assert client.remote == "archive"
    assert client.bucket == "bucket"
    assert client.large_uploads_optimized is False
    assert client.remote_path() == "archive:bucket"
    assert client.remote_path(" /folder\\child/ ") == "archive:bucket/folder/child"
    assert client.remote_path(" / ") == "archive:bucket"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (RcloneConfig(), "No rclone remote"),
        (
                SimpleNamespace(
                    binary="rclone",
                    config_file=None,
                    remote="archive",
                    bucket=None,
                ),
                "No archive bucket",
        ),
    ],
)
def test_remote_path_requires_complete_configuration(config: object, message: str) -> None:
    client = RcloneClient(config)  # type: ignore[arg-type]
    with pytest.raises(RcloneConfigurationError, match=message):
        client.remote_path()


@pytest.mark.asyncio
async def test_validate_rejects_missing_config_file(tmp_path: Path) -> None:
    client = make_client(config_file=tmp_path / "missing.conf")

    with pytest.raises(RcloneConfigurationError, match="config file not found"):
        await client.validate()


@pytest.mark.asyncio
async def test_validate_reports_status_without_s3_probe_when_optimization_disabled() -> None:
    client = make_client(s3_upload=S3UploadConfig(enabled=False))
    client.version = AsyncMock(return_value="1.75.0")  # type: ignore[method-assign]
    client.list_remotes = AsyncMock(return_value=("archive", "other"))  # type: ignore[method-assign]
    client.remote_config = AsyncMock()  # type: ignore[method-assign]

    status = await client.validate()

    assert status.version == "1.75.0"
    assert status.remote == "archive"
    assert status.bucket == "bucket"
    assert status.remotes == ("archive", "other")
    assert status.large_uploads_optimized is False
    client.version.assert_awaited_once_with(timeout=30.0)
    client.list_remotes.assert_awaited_once_with(timeout=30.0)
    client.remote_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_rejects_unknown_remote() -> None:
    client = make_client()
    client.version = AsyncMock(return_value="1.75.0")  # type: ignore[method-assign]
    client.list_remotes = AsyncMock(return_value=("other",))  # type: ignore[method-assign]

    with pytest.raises(RcloneConfigurationError, match="'archive' was not found"):
        await client.validate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "details", "optimized", "error"),
    [
        ("1.75.0", {"type": "s3", "provider": "AWS"}, True, None),
        ("1.75.0", {"type": "sftp", "provider": "AWS"}, False, None),
        ("1.71.2", {"type": "S3", "provider": "aws"}, False, "1.72 or newer"),
    ],
)
async def test_validate_detects_supported_aws_s3_profile(
    version: str, details: dict[str, str], optimized: bool, error: str | None
) -> None:
    client = make_client()
    client.version = AsyncMock(return_value=version)  # type: ignore[method-assign]
    client.list_remotes = AsyncMock(return_value=("archive",))  # type: ignore[method-assign]
    client.remote_config = AsyncMock(return_value=details)  # type: ignore[method-assign]

    if error:
        with pytest.raises(RcloneConfigurationError, match=error):
            await client.validate()
    else:
        status = await client.validate()
        assert status.large_uploads_optimized is optimized
        assert client.large_uploads_optimized is optimized

    client.remote_config.assert_awaited_once_with("archive", timeout=30.0)


@pytest.mark.asyncio
async def test_version_list_remotes_and_remote_config_parse_text_responses() -> None:
    client = make_client()
    client.run = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            command_result("rclone v1.75.0\n- os/version: linux"),
            command_result("archive:\n\n second: \n"),
            command_result("[archive]\nType = S3\nprovider= AWS\n# hidden\ninvalid\n"),
        ]
    )

    assert await client.version(timeout=1) == "v1.75.0"
    assert await client.list_remotes(timeout=2) == ("archive", "second")
    assert await client.remote_config("archive", timeout=3) == {
        "type": "S3",
        "provider": "AWS",
    }
    assert client.run.await_args_list == [
        call("version", timeout=1),
        call("listremotes", timeout=2),
        call("config", "redacted", "archive", timeout=3),
    ]


@pytest.mark.asyncio
async def test_version_rejects_empty_output() -> None:
    client = make_client()
    client.run = AsyncMock(return_value=command_result("\n"))  # type: ignore[method-assign]

    with pytest.raises(RcloneOutputError, match="empty version"):
        await client.version()


@pytest.mark.asyncio
async def test_about_and_size_parse_objects() -> None:
    client = make_client()
    client.run_json = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"total": 100, "used": 20}, {"count": 2, "bytes": 50}]
    )

    assert await client.about() == RcloneAbout(total=100, used=20)
    assert await client.size() == RcloneSize(count=2, bytes=50)
    assert client.run_json.await_args_list == [
        call("about", "archive:bucket", "--json"),
        call("size", "archive:bucket", "--json"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "message"), [("about", "about"), ("size", "size")])
async def test_about_and_size_require_object_payloads(method: str, message: str) -> None:
    client = make_client()
    client.run_json = AsyncMock(return_value=[])  # type: ignore[method-assign]

    with pytest.raises(RcloneOutputError, match=f"rclone {message} returned a non-object"):
        await getattr(client, method)()


@pytest.mark.asyncio
async def test_list_remote_delegates_with_normalized_path_and_options() -> None:
    client = make_client()
    client._list_items = AsyncMock(return_value=(item("file"),))  # type: ignore[method-assign]

    result = await client.list_remote("folder", recursive=True, include_hashes=True)

    assert result == (item("file"),)
    client._list_items.assert_awaited_once_with(
        "archive:bucket/folder", recursive=True, include_hashes=True
    )


@pytest.mark.asyncio
async def test_copy_methods_construct_local_and_remote_transfer_arguments() -> None:
    client = make_client()
    client._transfer = AsyncMock(return_value=command_result())  # type: ignore[method-assign]
    callback = AsyncMock()
    expanded_source = str(Path("~/source").expanduser())
    expanded_destination = str(Path("~/restore").expanduser())

    await client.copy_to_remote(
        "~/source", "dest", exclude=("*.tmp",), dry_run=True, progress=callback
    )
    await client.copy_from_remote("source", "~/restore", dry_run=True)

    assert client._transfer.await_args_list == [
        call(
            "copy",
            expanded_source,
            "archive:bucket/dest",
            exclude=("*.tmp",),
            dry_run=True,
            progress=callback,
            check_first=True,
            optimize_large_uploads=True,
        ),
        call(
            "copy",
            "archive:bucket/source",
            expanded_destination,
            dry_run=True,
        ),
    ]


@pytest.mark.asyncio
async def test_run_json_parses_json_and_wraps_decode_errors() -> None:
    client = make_client()
    client.run = AsyncMock(  # type: ignore[method-assign]
        side_effect=[command_result('{"ok": true}'), command_result("not json")]
    )

    assert await client.run_json("about") == {"ok": True}
    with pytest.raises(RcloneOutputError, match="invalid JSON") as caught:
        await client.run_json("about")
    assert caught.value.__cause__ is not None
