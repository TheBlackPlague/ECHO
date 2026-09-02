from __future__ import annotations

from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from echo.api.app import create_api_app
from echo.archive.models import ArchiveRun, RunOperation, RunState, RunTrigger
from echo.core.config import APIConfig, ArchiveConfig, EchoConfig, RcloneConfig


@pytest.fixture
def run_factory():
    def factory(**changes: object) -> ArchiveRun:
        now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        values: dict[str, object] = {
            "id": "run-123",
            "plan_name": "media",
            "operation": RunOperation.ARCHIVE,
            "trigger": RunTrigger.MANUAL,
            "state": RunState.SUCCEEDED,
            "dry_run": False,
            "source": Path("/data/media"),
            "destination": "backups/media",
            "created_at": now - timedelta(seconds=10),
            "started_at": now - timedelta(seconds=8),
            "finished_at": now,
            "return_code": 0,
            "stdout": "copied",
            "stderr": "",
            "progress": 100.0,
            "files_added": 4,
            "files_verified": 3,
        }
        values.update(changes)
        return ArchiveRun(**values)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def application_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHO_FRONTEND_DIR", str(tmp_path / "missing-frontend"))

    def factory(
        *,
        api_key: str | None = None,
        web_password: str | None = None,
        cors_origins: list[str] | None = None,
        ready: bool = True,
        docs_enabled: bool = True,
    ) -> SimpleNamespace:
        config = EchoConfig(
            api=APIConfig(
                root_path="",
                api_key=api_key,
                web_password=web_password,
                cors_origins=cors_origins or [],
                docs_enabled=docs_enabled,
                session_cookie_secure=False,
            ),
            rclone=RcloneConfig(remote="cloud", bucket="echo-bucket"),
            archive=ArchiveConfig(enabled=True),
        )
        rclone = SimpleNamespace(
            remote="cloud",
            bucket="echo-bucket",
            list_remote=AsyncMock(return_value=[]),
            size=AsyncMock(),
            about=AsyncMock(),
        )
        runs = SimpleNamespace(
            list=AsyncMock(return_value=[]),
            get=AsyncMock(),
            summary=AsyncMock(return_value={}),
        )
        archiver = SimpleNamespace(
            enabled=True,
            running=True,
            active_count=0,
            plans=(),
            get_plan=Mock(),
            get_status=AsyncMock(),
            submit=AsyncMock(),
            cancel=AsyncMock(),
        )
        scheduler = SimpleNamespace(
            running=True,
            last_tick=None,
            plans=(),
            is_scheduled=Mock(return_value=False),
        )
        return SimpleNamespace(
            config=config,
            ready=ready,
            started=True,
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            rclone_status=None,
            rclone=rclone,
            runs=runs,
            archiver=archiver,
            scheduler=scheduler,
            start=AsyncMock(),
            stop=AsyncMock(),
        )

    return factory


@pytest.fixture
def client_factory(application_factory):
    clients: list[TestClient] = []

    def factory(*, raise_server_exceptions: bool = True, **application_options):
        application = application_factory(**application_options)
        client = TestClient(
            create_api_app(application),
            raise_server_exceptions=raise_server_exceptions,
        )
        client.__enter__()
        clients.append(client)
        return client, application

    yield factory

    for client in clients:
        client.__exit__(None, None, None)
