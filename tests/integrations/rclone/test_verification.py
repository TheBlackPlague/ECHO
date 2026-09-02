from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, call

import pytest

from echo.core.config import RcloneConfig
from echo.integrations.rclone import (
    RcloneClient,
    RcloneItem,
    RcloneOutputError,
    RcloneProgress,
    RcloneVerificationError,
)


MD5_A = "a" * 32
MD5_B = "b" * 32


def make_client() -> RcloneClient:
    return RcloneClient(RcloneConfig(remote="archive", bucket="bucket"))


def item(path: str, size: int = 10, md5: str | None = MD5_A) -> RcloneItem:
    return RcloneItem(
        path=path,
        name=Path(path).name,
        size=size,
        is_dir=False,
        hashes=None if md5 is None else {"MD5": md5},
    )


@pytest.mark.asyncio
async def test_check_remote_successfully_performs_metadata_then_hash_passes() -> None:
    client = make_client()
    expected = (item("a.bin", md5=None), item("nested/b.bin", size=20, md5=None))
    remote = (item("a.bin"), item("nested/b.bin", size=20, md5=MD5_B))
    local = (item("a.bin"), item("nested/b.bin", size=20, md5=MD5_B))
    client._list_items = AsyncMock(side_effect=[expected, remote, local])  # type: ignore[method-assign]
    progress = AsyncMock()

    verification = await client.check_remote(
        "source", "destination", exclude=("*.tmp",), progress=progress
    )

    assert verification.files_verified == 2
    assert verification.result.successful
    assert verification.result.command == (
        "rclone",
        "strict-md5-check",
        "source",
        "archive:bucket/destination",
    )
    assert "2 file(s)" in verification.result.stdout
    assert verification.result.duration_seconds >= 0
    assert client._list_items.await_args_list == [
        call(
            "source",
            recursive=True,
            files_only=True,
            exclude=("*.tmp",),
        ),
        call(
            "archive:bucket/destination",
            recursive=True,
            files_only=True,
            include_hashes=True,
            exclude=("*.tmp",),
        ),
        call(
            "source",
            recursive=True,
            files_only=True,
            include_hashes=True,
            exclude=("*.tmp",),
        ),
    ]
    progress.assert_awaited_once_with(
        RcloneProgress(
            percent=100.0,
            files_transferred=0,
            files_checked=2,
            files_to_check=2,
        )
    )


@pytest.mark.asyncio
async def test_check_remote_accepts_empty_manifests_without_progress_callback() -> None:
    client = make_client()
    client._list_items = AsyncMock(side_effect=[(), (), ()])  # type: ignore[method-assign]

    result = await client.check_remote("source")

    assert result.files_verified == 0


@pytest.mark.asyncio
async def test_check_remote_rejects_two_way_verification_before_listing() -> None:
    client = make_client()
    client._list_items = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="one-way"):
        await client.check_remote("source", one_way=False)
    client._list_items.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected", "remote", "message"),
    [
        ((item("file"),), (), "Remote file is missing: file"),
        ((item("file", 10),), (item("file", 11),), "Remote file size differs: file"),
        ((item("file"),), (item("file", md5=None),), "no usable MD5 checksum"),
        ((item("file"),), (item("file", md5="not-an-md5"),), "no usable MD5 checksum"),
    ],
)
async def test_check_remote_rejects_invalid_remote_manifest(
    expected: tuple[RcloneItem, ...], remote: tuple[RcloneItem, ...], message: str
) -> None:
    client = make_client()
    client._list_items = AsyncMock(side_effect=[expected, remote])  # type: ignore[method-assign]

    with pytest.raises(RcloneVerificationError, match=message):
        await client.check_remote("source")
    assert client._list_items.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local", "message"),
    [
        ((), "Local source changed while it was being verified"),
        ((item("file", 11),), "Local file changed while it was being verified: file"),
        ((item("file", md5=None),), "no usable MD5 checksum"),
        ((item("file", md5=MD5_B),), "File content differs: file"),
    ],
)
async def test_check_remote_rejects_local_changes_and_checksum_mismatches(
    local: tuple[RcloneItem, ...], message: str
) -> None:
    client = make_client()
    client._list_items = AsyncMock(  # type: ignore[method-assign]
        side_effect=[(item("file", md5=None),), (item("file"),), local]
    )

    with pytest.raises(RcloneVerificationError, match=message):
        await client.check_remote("source")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("listing", "message"),
    [
        ((item(""),), "empty path for local source"),
        ((item("duplicate"), item("duplicate")), "duplicate path for local source"),
    ],
)
async def test_check_remote_rejects_paths_that_cannot_form_a_manifest(
    listing: tuple[RcloneItem, ...], message: str
) -> None:
    client = make_client()
    client._list_items = AsyncMock(return_value=listing)  # type: ignore[method-assign]

    with pytest.raises(RcloneOutputError, match=message):
        await client.check_remote("source")
