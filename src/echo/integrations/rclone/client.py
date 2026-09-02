from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from time import monotonic
from typing import Any

from echo.core.config import normalize_remote_path, RcloneConfig
from echo.core.logging import get_logger
from echo.integrations.rclone.errors import (
    RcloneCommandError,
    RcloneConfigurationError,
    RcloneOutputError,
    RcloneTimeoutError,
    RcloneUnavailableError,
    RcloneVerificationError,
)
from echo.integrations.rclone.models import (
    RcloneAbout,
    RcloneCommandResult,
    RcloneItem,
    RcloneProgress,
    RcloneSize,
    RcloneStatus,
    RcloneVerificationResult,
)


_PROGRESS_ARGS = ("--stats", "1s", "--use-json-log", "--stats-log-level", "NOTICE")
_AWS_CHECKSUM_ENVIRONMENT = {
    "AWS_REQUEST_CHECKSUM_CALCULATION": "WHEN_SUPPORTED",
    "AWS_RESPONSE_CHECKSUM_VALIDATION": "WHEN_SUPPORTED",
}
_MINIMUM_INTEGRITY_VERSION = (1, 72, 0)
_MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
ProgressCallback = Callable[[RcloneProgress], Awaitable[None]]


class RcloneClient:
    def __init__(self, config: RcloneConfig) -> None:
        self.config = config
        self._logger = get_logger(service="rclone")
        self._large_uploads_optimized = False

    @property
    def remote(self) -> str | None:
        return self.config.remote.rstrip(":") if self.config.remote else None

    @property
    def bucket(self) -> str | None:
        return self.config.bucket

    @property
    def large_uploads_optimized(self) -> bool:
        """Whether uploads use ECHO's AWS S3 streaming-integrity profile."""
        return self._large_uploads_optimized

    def remote_path(self, path: str | Path | None = None) -> str:
        remote = self.remote
        bucket = self.bucket

        if not remote: raise RcloneConfigurationError("No rclone remote is configured")
        if not bucket: raise RcloneConfigurationError("No archive bucket is configured")

        root = f"{remote}:{bucket}"

        if path is None or not str(path).strip().strip("/\\"): return root

        return f"{root}/{normalize_remote_path(str(path))}"

    async def validate(self) -> RcloneStatus:
        config_file = self.config.config_file
        if config_file and not config_file.expanduser().is_file():
            raise RcloneConfigurationError(f"rclone config file not found: {config_file}")

        timeout = self.config.validation_timeout_seconds
        version, remotes = await asyncio.gather(
            self.version(timeout=timeout),
            self.list_remotes(timeout=timeout),
        )

        remote = self.remote
        if remote and remote not in remotes:
            raise RcloneConfigurationError(f"Configured rclone remote '{remote}' was not found")

        self._large_uploads_optimized = False
        if remote and self.config.s3_upload.enabled:
            details = await self.remote_config(remote, timeout=timeout)
            if _is_aws_s3(details):
                if _version_tuple(version) < _MINIMUM_INTEGRITY_VERSION:
                    raise RcloneConfigurationError(
                        "AWS S3 large-upload optimization requires rclone 1.72 or newer; "
                        "rclone 1.75.0 is recommended"
                    )
                self._large_uploads_optimized = True

        return RcloneStatus(
            version=version,
            remote=remote,
            bucket=self.bucket,
            remotes=remotes,
            large_uploads_optimized=self._large_uploads_optimized,
        )

    async def version(self, *, timeout: float | None = None) -> str:
        result = await self.run("version", timeout=timeout)
        first_line = result.stdout.partition("\n")[0].strip()

        if not first_line: raise RcloneOutputError("rclone returned an empty version response")

        return first_line.removeprefix("rclone ")

    async def list_remotes(self, *, timeout: float | None = None) -> tuple[str, ...]:
        result = await self.run("listremotes", timeout=timeout)
        return tuple(
            remote
            for line in result.stdout.splitlines()
            if (remote := line.strip().removesuffix(":"))
        )

    async def remote_config(
        self,
        remote: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, str]:
        result = await self.run("config", "redacted", remote, timeout=timeout)
        return _parse_redacted_config(result.stdout)

    async def about(self) -> RcloneAbout:
        payload = await self.run_json("about", self.remote_path(), "--json")
        if not isinstance(payload, dict): raise RcloneOutputError("rclone about returned a non-object response")

        return RcloneAbout.from_payload(payload)

    async def size(self) -> RcloneSize:
        payload = await self.run_json("size", self.remote_path(), "--json")
        if not isinstance(payload, dict): raise RcloneOutputError("rclone size returned a non-object response")

        return RcloneSize.from_payload(payload)

    async def list_remote(
        self,
        path: str | Path | None = None,
        *,
        recursive: bool = False,
        include_hashes: bool = False,
    ) -> tuple[RcloneItem, ...]:
        return await self._list_items(
            self.remote_path(path),
            recursive=recursive,
            include_hashes=include_hashes,
        )

    async def copy_to_remote(
        self,
        source: str | Path,
        destination: str | Path | None = None,
        *,
        exclude: Sequence[str] = (),
        dry_run: bool = False,
        progress: ProgressCallback | None = None,
    ) -> RcloneCommandResult:
        return await self._transfer(
            "copy",
            str(Path(source).expanduser()),
            self.remote_path(destination),
            exclude=exclude,
            dry_run=dry_run,
            progress=progress,
            check_first=progress is not None,
            optimize_large_uploads=True,
        )

    async def copy_from_remote(
        self,
        source: str | Path | None,
        destination: str | Path,
        *,
        dry_run: bool = False,
    ) -> RcloneCommandResult:
        return await self._transfer(
            "copy",
            self.remote_path(source),
            str(Path(destination).expanduser()),
            dry_run=dry_run,
        )

    async def check_remote(
        self,
        source: str | Path,
        destination: str | Path | None = None,
        *,
        exclude: Sequence[str] = (),
        one_way: bool = True,
        progress: ProgressCallback | None = None,
    ) -> RcloneVerificationResult:
        if not one_way:
            raise ValueError("Strict verification only supports one-way source checks")

        source_path = str(Path(source).expanduser())
        remote_path = self.remote_path(destination)
        started_at = monotonic()

        # Establish the expected local paths without reading file contents. Remote
        # hashes are validated before ECHO performs any expensive local hashing.
        expected_items = await self._list_items(
            source_path,
            recursive=True,
            files_only=True,
            exclude=exclude,
        )
        remote_items = await self._list_items(
            remote_path,
            recursive=True,
            files_only=True,
            include_hashes=True,
            exclude=exclude,
        )
        expected = _manifest(expected_items, "local source")
        remote = _manifest(remote_items, "remote destination")

        remote_hashes: dict[str, str] = {}
        for path, item in expected.items():
            destination_item = remote.get(path)
            if destination_item is None:
                raise RcloneVerificationError(f"Remote file is missing: {path}")
            if destination_item.size != item.size:
                raise RcloneVerificationError(f"Remote file size differs: {path}")

            remote_hashes[path] = _required_md5(destination_item, "remote destination")

        local_items = await self._list_items(
            source_path,
            recursive=True,
            files_only=True,
            include_hashes=True,
            exclude=exclude,
        )
        local = _manifest(local_items, "local source")
        if local.keys() != expected.keys():
            raise RcloneVerificationError("Local source changed while it was being verified")

        for path, item in local.items():
            if item.size != expected[path].size:
                raise RcloneVerificationError(f"Local file changed while it was being verified: {path}")
            if _required_md5(item, "local source") != remote_hashes[path]:
                raise RcloneVerificationError(f"File content differs: {path}")

        files_verified = len(local)
        if progress is not None:
            await progress(
                RcloneProgress(
                    percent=100.0,
                    files_transferred=0,
                    files_checked=files_verified,
                    files_to_check=files_verified,
                )
            )

        result = RcloneCommandResult(
            command=(self.config.binary, "strict-md5-check", source_path, remote_path),
            return_code=0,
            stdout=f"Strict MD5 verification succeeded for {files_verified} file(s).\n",
            stderr="",
            duration_seconds=monotonic() - started_at,
        )
        return RcloneVerificationResult(result=result, files_verified=files_verified)

    async def run_json(self, *args: str, timeout: float | None = None) -> Any:
        result = await self.run(*args, timeout=timeout)
        try:
            return json.loads(result.stdout)

        except json.JSONDecodeError as exc:
            raise RcloneOutputError("rclone returned invalid JSON") from exc

    async def run(
        self,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
        progress: ProgressCallback | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> RcloneCommandResult:
        if not args: raise ValueError("At least one rclone argument is required")

        command_args = list(args)
        if progress is not None: command_args.extend(_PROGRESS_ARGS)

        command = self._command(*command_args)
        started_at = monotonic()
        await self._logger.adebug("Running rclone command", operation=args[0])

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **environment} if environment is not None else None,
            )

        except (FileNotFoundError, PermissionError) as exc:
            raise RcloneUnavailableError(f"Unable to execute rclone binary: {self.config.binary}") from exc

        try:
            communication = _communicate(process, progress)

            if timeout is None:
                stdout_bytes, stderr_bytes = await communication
            else:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(communication, timeout=timeout)

        except TimeoutError as exc:
            await _terminate_process(process)

            # noinspection bad-argument-type
            raise RcloneTimeoutError(command, timeout) from exc

        except asyncio.CancelledError:
            await _terminate_process(process)
            raise

        except Exception:
            await _terminate_process(process)
            raise

        # noinspection bad-argument-type
        result = RcloneCommandResult(
            command=command,
            return_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_seconds=monotonic() - started_at,
        )

        await self._logger.adebug(
            "rclone command finished",
            operation=args[0],
            return_code=result.return_code,
            duration_seconds=round(result.duration_seconds, 3),
        )

        if check and not result.successful: raise RcloneCommandError(result)

        return result

    async def _transfer(
        self,
        operation: str,
        source: str,
        destination: str,
        *,
        exclude: Sequence[str] = (),
        dry_run: bool,
        progress: ProgressCallback | None = None,
        check_first: bool = False,
        optimize_large_uploads: bool = False,
    ) -> RcloneCommandResult:
        args = [operation, source, destination, *_filter_args(exclude)]
        if check_first: args.append("--check-first")
        if dry_run: args.append("--dry-run")

        environment = None
        if self._large_uploads_optimized and optimize_large_uploads and not dry_run:
            upload = self.config.s3_upload
            args.extend(
                (
                    "--multi-thread-streams", "0",
                    "--transfers", str(upload.transfers),
                    "--s3-chunk-size", f"{upload.chunk_size_mib}Mi",
                    "--s3-upload-concurrency", str(upload.upload_concurrency),
                    "--max-buffer-memory", f"{upload.max_buffer_memory_mib}Mi",
                    "--s3-disable-checksum",
                    "--s3-use-data-integrity-protections=true",
                    "--s3-use-multipart-etag=true",
                    "--s3-no-head=false",
                    "--s3-use-presigned-request=false",
                )
            )
            environment = _AWS_CHECKSUM_ENVIRONMENT

        return await self.run(*args, progress=progress, environment=environment)

    async def _list_items(
        self,
        path: str,
        *,
        recursive: bool = False,
        files_only: bool = False,
        include_hashes: bool = False,
        exclude: Sequence[str] = (),
    ) -> tuple[RcloneItem, ...]:
        args = ["lsjson", path]
        if recursive: args.append("--recursive")
        if files_only: args.append("--files-only")
        if include_hashes: args.append("--hash")
        args.extend(_filter_args(exclude))

        payload = await self.run_json(*args)
        if not isinstance(payload, list):
            raise RcloneOutputError("rclone lsjson returned a non-list response")
        if not all(isinstance(item, dict) for item in payload):
            raise RcloneOutputError("rclone lsjson returned an invalid item")

        return tuple(RcloneItem.from_payload(item) for item in payload)

    def _command(self, *args: str) -> tuple[str, ...]:
        command = [self.config.binary]

        if self.config.config_file: command.extend(("--config", str(self.config.config_file.expanduser())))

        command.extend(("--ask-password=false", *args))

        return tuple(command)


def _filter_args(patterns: Sequence[str]) -> list[str]:
    return [argument for pattern in patterns for argument in ("--exclude", pattern)]


def _parse_redacted_config(output: str) -> dict[str, str]:
    details: dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")) or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        details[key.strip().casefold()] = value.strip()
    return details


def _is_aws_s3(details: Mapping[str, str]) -> bool:
    return details.get("type", "").casefold() == "s3" and details.get("provider", "").casefold() == "aws"


def _version_tuple(version: str) -> tuple[int, ...]:
    match = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", version)
    if match is None: raise RcloneOutputError(f"Unable to parse rclone version: {version}")
    return tuple(int(part or 0) for part in match.groups())


def _manifest(items: Sequence[RcloneItem], label: str) -> dict[str, RcloneItem]:
    manifest: dict[str, RcloneItem] = {}
    for item in items:
        if not item.path: raise RcloneOutputError(f"rclone returned an empty path for {label}")
        if item.path in manifest:
            raise RcloneOutputError(f"rclone returned a duplicate path for {label}: {item.path}")
        manifest[item.path] = item
    return manifest


def _required_md5(item: RcloneItem, label: str) -> str:
    hashes = {key.casefold(): value for key, value in (item.hashes or {}).items()}
    checksum = hashes.get("md5", "")
    if not _MD5_PATTERN.fullmatch(checksum):
        raise RcloneVerificationError(
            f"{label.capitalize()} has no usable MD5 checksum for {item.path}; "
            "verification cannot safely fall back to file size"
        )
    return checksum.casefold()


def _stats_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict): return None

    stats = payload.get("stats")
    if not isinstance(stats, dict): return None
    if not any(key in stats for key in ("totalTransfers", "totalChecks", "totalBytes")): return None

    return stats


async def _communicate(
    process: asyncio.subprocess.Process,
    progress: ProgressCallback | None,
) -> tuple[bytes, bytes]:
    if progress is None: return await process.communicate()
    if process.stdout is None or process.stderr is None:
        raise RcloneOutputError("rclone progress streams are unavailable")

    stdout_task = asyncio.create_task(process.stdout.read())
    stderr_task = asyncio.create_task(_read_progress(process.stderr, progress))

    try:
        stdout, stderr, _ = await asyncio.gather(stdout_task, stderr_task, process.wait())
        return stdout, stderr

    except BaseException:
        stdout_task.cancel()
        stderr_task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise


async def _read_progress(stream: asyncio.StreamReader, callback: ProgressCallback) -> bytes:
    output = bytearray()
    while line := await stream.readline():
        output.extend(line)

        try:
            payload = json.loads(line)

        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        if stats := _stats_payload(payload): await callback(RcloneProgress.from_payload(stats))

    return bytes(output)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None: return

    process.terminate()

    try:
        await asyncio.wait_for(process.wait(), timeout=5)

    except TimeoutError:
        process.kill()
        await process.wait()
