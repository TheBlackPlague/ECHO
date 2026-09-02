from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from echo.api.dependencies import get_echo
from echo.api.schemas import (
    ArchiveItemResponse,
    ArchiveListingResponse,
    ArchiveSizeResponse,
    RemoteCapacityResponse,
)
from echo.application import EchoApplication
from echo.core.config import normalize_remote_path
from echo.integrations.rclone import RcloneConfigurationError


router = APIRouter(prefix="/archive", tags=["archive"])


@router.get("", response_model=ArchiveListingResponse, operation_id="listArchive")
async def list_archive(
    echo: Annotated[EchoApplication, Depends(get_echo)],
    path: str | None = Query(default=None, max_length=2048),
    recursive: bool = Query(default=False),
    include_hashes: bool = Query(default=False),
) -> ArchiveListingResponse:
    try:
        normalized_path = normalize_remote_path(path) if path and path.strip("/\\") else None
        bucket = echo.rclone.bucket
        if not bucket: raise RcloneConfigurationError("No archive bucket is configured")

        items = await echo.rclone.list_remote(
            normalized_path,
            recursive=recursive,
            include_hashes=include_hashes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ArchiveListingResponse(
        bucket=bucket,
        path=normalized_path,
        items=[
            ArchiveItemResponse(
                path=_archive_item_path(normalized_path, item.path),
                name=item.name,
                size=item.size,
                is_dir=item.is_dir,
                mod_time=item.mod_time,
                mime_type=item.mime_type,
                tier=item.tier,
                hashes=item.hashes,
            )
            for item in items
        ],
    )


@router.get("/size", response_model=ArchiveSizeResponse, operation_id="getArchiveSize")
async def get_archive_size(
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> ArchiveSizeResponse:
    size = await echo.rclone.size()
    return ArchiveSizeResponse(bytes=size.bytes, objects=size.count)


@router.get("/capacity", response_model=RemoteCapacityResponse, operation_id="getArchiveCapacity")
async def get_archive_capacity(
    echo: Annotated[EchoApplication, Depends(get_echo)],
) -> RemoteCapacityResponse:
    capacity = await echo.rclone.about()
    return RemoteCapacityResponse(
        total=capacity.total,
        used=capacity.used,
        free=capacity.free,
        trashed=capacity.trashed,
        other=capacity.other,
    )


def _archive_item_path(parent: str | None, child: str) -> str:
    child = child.strip("/")
    if not parent: return child
    return f"{parent}/{child}" if child else parent
