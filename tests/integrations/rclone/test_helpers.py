from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import echo.integrations.rclone.client as client_module
from echo.integrations.rclone import (
    RcloneItem,
    RcloneOutputError,
    RcloneProgress,
    RcloneVerificationError,
)


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        ((), []),
        (("*.tmp", "cache/**"), ["--exclude", "*.tmp", "--exclude", "cache/**"]),
    ],
)
def test_filter_args_expands_each_pattern(patterns: tuple[str, ...], expected: list[str]) -> None:
    assert client_module._filter_args(patterns) == expected


def test_parse_redacted_config_ignores_comments_sections_and_malformed_lines() -> None:
    output = """
        # comment
        ; another comment
        [archive]
        TYPE = s3
        provider = AWS = retained
        malformed
    """
    assert client_module._parse_redacted_config(output) == {
        "type": "s3",
        "provider": "AWS = retained",
    }


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({"type": "S3", "provider": "Aws"}, True),
        ({"type": "s3", "provider": "Other"}, False),
        ({"provider": "AWS"}, False),
    ],
)
def test_is_aws_s3_is_case_insensitive(details: dict[str, str], expected: bool) -> None:
    assert client_module._is_aws_s3(details) is expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.72", (1, 72, 0)),
        ("v1.75.3-beta", (1, 75, 3)),
        ("1.100.0", (1, 100, 0)),
    ],
)
def test_version_tuple_parses_supported_forms(version: str, expected: tuple[int, ...]) -> None:
    assert client_module._version_tuple(version) == expected


def test_version_tuple_rejects_unrecognized_output() -> None:
    with pytest.raises(RcloneOutputError, match="Unable to parse"):
        client_module._version_tuple("development build")


def test_manifest_builds_path_index() -> None:
    first = RcloneItem("one", "one", 1, False)
    second = RcloneItem("two", "two", 2, False)
    assert client_module._manifest((first, second), "listing") == {"one": first, "two": second}


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (RcloneItem("file", "file", 1, False, hashes={"MD5": "A" * 32}), "a" * 32),
        (
                RcloneItem("file", "file", 1, False, hashes={"md5": "0123456789abcdef" * 2}),
                "0123456789abcdef" * 2,
        ),
    ],
)
def test_required_md5_accepts_case_insensitive_keys_and_values(
    item: RcloneItem, expected: str
) -> None:
    assert client_module._required_md5(item, "remote") == expected


def test_required_md5_explains_that_size_fallback_is_unsafe() -> None:
    item = RcloneItem("file", "file", 1, False, hashes={"SHA-1": "a" * 40})
    with pytest.raises(RcloneVerificationError, match="cannot safely fall back to file size"):
        client_module._required_md5(item, "remote destination")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, None),
        ([], None),
        ({}, None),
        ({"stats": []}, None),
        ({"stats": {"transfers": 1}}, None),
        ({"stats": {"totalTransfers": 0}}, {"totalTransfers": 0}),
        ({"stats": {"totalChecks": 1}}, {"totalChecks": 1}),
        ({"stats": {"totalBytes": 10}}, {"totalBytes": 10}),
    ],
)
def test_stats_payload_only_accepts_recognizable_stats(
    payload: object, expected: dict[str, object] | None
) -> None:
    assert client_module._stats_payload(payload) == expected


@pytest.mark.asyncio
async def test_read_progress_preserves_all_stderr_and_emits_only_stats() -> None:
    stream = asyncio.StreamReader()
    lines = [
        b'not json\n',
        b'\xff\n',
        b'{"level":"notice","msg":"ordinary"}\n',
        (
            b'{"stats":{"transfers":1,"checks":2,"totalTransfers":2,'
            b'"totalChecks":2,"bytes":5,"totalBytes":10}}\n'
        ),
    ]
    for line in lines:
        stream.feed_data(line)
    stream.feed_eof()
    callback = AsyncMock()

    output = await client_module._read_progress(stream, callback)

    assert output == b"".join(lines)
    callback.assert_awaited_once_with(
        RcloneProgress(
            percent=50.0,
            files_transferred=1,
            files_checked=2,
            files_to_transfer=2,
            files_to_check=2,
            bytes_transferred=5,
            total_bytes=10,
        )
    )


@pytest.mark.asyncio
async def test_communicate_without_progress_uses_process_communicate() -> None:
    process = SimpleNamespace(communicate=AsyncMock(return_value=(b"stdout", b"stderr")))
    assert await client_module._communicate(process, None) == (b"stdout", b"stderr")
    process.communicate.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_communicate_with_progress_reads_streams_and_waits_for_process() -> None:
    stdout = asyncio.StreamReader()
    stderr = asyncio.StreamReader()
    stdout.feed_data(b"stdout")
    stdout.feed_eof()
    stderr.feed_data(b'{"stats":{"totalTransfers":1,"transfers":1}}\n')
    stderr.feed_eof()
    process = SimpleNamespace(stdout=stdout, stderr=stderr, wait=AsyncMock(return_value=0))
    callback = AsyncMock()

    assert await client_module._communicate(process, callback) == (
        b"stdout",
        b'{"stats":{"totalTransfers":1,"transfers":1}}\n',
    )
    process.wait.assert_awaited_once_with()
    callback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("stdout", "stderr"), [(None, object()), (object(), None)])
async def test_communicate_requires_both_progress_streams(stdout: object, stderr: object) -> None:
    process = SimpleNamespace(stdout=stdout, stderr=stderr)
    with pytest.raises(RcloneOutputError, match="streams are unavailable"):
        await client_module._communicate(process, AsyncMock())


@pytest.mark.asyncio
async def test_communicate_cancels_sibling_reader_when_progress_callback_fails() -> None:
    stdout = asyncio.StreamReader()
    stderr = asyncio.StreamReader()
    stderr.feed_data(b'{"stats":{"totalTransfers":1}}\n')
    stderr.feed_eof()
    process = SimpleNamespace(stdout=stdout, stderr=stderr, wait=AsyncMock(return_value=0))
    callback = AsyncMock(side_effect=RuntimeError("callback failed"))

    with pytest.raises(RuntimeError, match="callback failed"):
        await client_module._communicate(process, callback)


@pytest.mark.asyncio
async def test_terminate_process_is_noop_for_finished_process() -> None:
    process = SimpleNamespace(returncode=0, terminate=Mock(), kill=Mock(), wait=AsyncMock())
    await client_module._terminate_process(process)
    process.terminate.assert_not_called()
    process.wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminate_process_requests_graceful_termination() -> None:
    process = SimpleNamespace(
        returncode=None,
        terminate=Mock(),
        kill=Mock(),
        wait=AsyncMock(return_value=0),
    )
    await client_module._terminate_process(process)
    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_terminate_process_kills_after_grace_period(monkeypatch: pytest.MonkeyPatch) -> None:
    process = SimpleNamespace(
        returncode=None,
        terminate=Mock(),
        kill=Mock(),
        wait=AsyncMock(return_value=0),
    )

    async def wait_for(awaitable: object, *, timeout: float) -> None:
        assert timeout == 5
        awaitable.close()  # type: ignore[attr-defined]
        raise TimeoutError

    monkeypatch.setattr(client_module.asyncio, "wait_for", wait_for)

    await client_module._terminate_process(process)

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2
    assert process.wait.await_count == 1
