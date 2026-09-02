from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from functools import lru_cache

from echo.archive.archiver import Archiver
from echo.archive.scheduler import Scheduler
from echo.core.config import EchoConfig, get_config
from echo.core.logging import get_logger
from echo.integrations.rclone import RcloneClient, RcloneStatus
from echo.storage.database import Database
from echo.storage.runs import RunRepository


class EchoApplication:
    """Composes ECHO's runtime services and owns their lifecycle."""

    def __init__(self, config: EchoConfig) -> None:
        self.config = config
        self._started = False
        self._started_at: datetime | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._rclone_status: RcloneStatus | None = None

        self.database = Database(config.storage)
        self.runs = RunRepository(config.storage, self.database)
        self.rclone = RcloneClient(config.rclone)
        self.archiver = Archiver(config.archive, self.rclone, self.runs)
        self.scheduler = Scheduler(self.archiver)

    @property
    def started(self) -> bool:
        return self._started

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def rclone_status(self) -> RcloneStatus | None:
        return self._rclone_status

    @property
    def ready(self) -> bool:
        integration_ready = not self.config.archive.enabled or self._rclone_status is not None
        return self._started and self.database.initialized and integration_ready

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started: return

            logger = get_logger(service="app")
            await logger.ainfo("Starting ECHO")

            try:
                await self.database.initialize()
                recovered = await self.runs.recover_incomplete()

                if recovered: await logger.awarning("Recovered interrupted archive runs", runs=recovered)

                if self.config.archive.enabled:
                    self._rclone_status = await self.rclone.validate()

                    if self._rclone_status is None: raise ValueError("rclone integration is not configured")

                    await logger.ainfo(
                        "Integration enabled: rclone",
                        version=self._rclone_status.version,
                        remote=self._rclone_status.remote,
                        bucket=self._rclone_status.bucket,
                    )

                await self.archiver.start()

                if self.config.archive.enabled: await self.scheduler.start()

                self._started_at = datetime.now(UTC)
                self._started = True

            except Exception:
                await self.scheduler.stop()
                await self.archiver.stop()
                await self.database.close()

                self._rclone_status = None

                await logger.acritical("ECHO startup failed", exc_info=True)
                raise

            await logger.ainfo(
                "ECHO started",
                archive_enabled=self.config.archive.enabled,
                plans=len(self.config.archive.plans),
            )

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._started and not self.database.initialized: return

            logger = get_logger(service="app")
            await logger.ainfo("Stopping ECHO")

            await self.scheduler.stop()
            await self.archiver.stop()
            await self.database.close()

            self._started = False
            self._started_at = None
            self._rclone_status = None

            await logger.ainfo("ECHO stopped")


@lru_cache(maxsize=1)
def get_application() -> EchoApplication:
    return EchoApplication(get_config())


def clear_application_cache() -> None:
    get_application.cache_clear()
