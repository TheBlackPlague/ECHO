from __future__ import annotations

from datetime import datetime, UTC

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from echo.api.app import _frontend_directory, create_api_app
from echo.api.schemas import (
    ArchiveItemResponse,
    AuthLoginRequest,
    ErrorBody,
    ErrorResponse,
    HealthResponse,
    RunDetailResponse,
    RunPageResponse,
    RunSummaryResponse,
    SubmitRunRequest,
)


def test_run_response_factories_copy_all_run_fields(run_factory) -> None:
    run = run_factory()
    summary = RunSummaryResponse.from_run(run)
    detail = RunDetailResponse.from_run(run)

    assert summary.id == run.id
    assert summary.duration_seconds == 8.0
    assert summary.source == run.source
    assert summary.progress == 100.0
    assert summary.files_added == 4
    assert summary.files_verified == 3
    assert detail.model_dump(exclude={"stdout", "stderr"}) == summary.model_dump()
    assert detail.stdout == "copied"
    assert detail.stderr == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"progress": -0.1},
        {"progress": 100.1},
        {"files_added": -1},
        {"files_verified": -1},
    ],
)
def test_run_summary_rejects_invalid_counters(changes: dict[str, object], run_factory) -> None:
    run = run_factory(**changes)
    with pytest.raises(ValidationError):
        RunSummaryResponse.from_run(run)


def test_schema_defaults_and_serialization(run_factory) -> None:
    assert SubmitRunRequest().dry_run is False
    assert RunPageResponse(items=[], limit=100, offset=0, has_more=False).items == []
    assert ArchiveItemResponse(path="p", name="p", size=0, is_dir=False).hashes is None
    assert ErrorResponse(error=ErrorBody(code="x", message="y")).error.details is None
    assert AuthLoginRequest(password="secret").password.get_secret_value() == "secret"
    assert HealthResponse(status="alive", version="1").model_dump(mode="json")["status"] == "alive"


@pytest.mark.parametrize("password", ["", "x" * 4097])
def test_login_payload_length_constraints(password: str) -> None:
    with pytest.raises(ValidationError):
        AuthLoginRequest(password=password)


def test_frontend_directory_detection(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ECHO_FRONTEND_DIR", str(tmp_path))
    assert _frontend_directory() is None

    (tmp_path / "index.html").write_text("<h1>ECHO</h1>", encoding="utf-8")
    assert _frontend_directory() == tmp_path


def test_frontend_mount_serves_assets_and_replaces_api_index(
    monkeypatch, tmp_path, application_factory
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<h1>ECHO UI</h1>", encoding="utf-8")
    (frontend / "app.js").write_text("console.log('echo')", encoding="utf-8")
    monkeypatch.setenv("ECHO_FRONTEND_DIR", str(frontend))
    application = application_factory()

    with TestClient(create_api_app(application)) as client:
        root = client.get("/")
        asset = client.get("/app.js")
        api_root = client.get("/api/")

    assert root.status_code == 200 and "ECHO UI" in root.text
    assert asset.status_code == 200 and "console.log" in asset.text
    assert api_root.status_code == 404
    application.stop.assert_awaited_once()


def test_running_run_duration_is_nonnegative(run_factory) -> None:
    run = run_factory(started_at=datetime.now(UTC), finished_at=None)
    response = RunSummaryResponse.from_run(run)
    assert response.duration_seconds is not None
    assert response.duration_seconds >= 0
