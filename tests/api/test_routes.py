from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path

from echo import __version__
from echo.archive.archiver import ArchivePlanStatus
from echo.archive.models import RunOperation, RunState
from echo.core.config import ArchivePlanConfig
from echo.integrations.rclone import RcloneAbout, RcloneItem, RcloneSize, RcloneStatus


def test_liveness_readiness_and_lifecycle(client_factory) -> None:
    client, application = client_factory(ready=True)
    assert client.get("/api/health/live").json() == {"status": "alive", "version": __version__}
    assert client.get("/api/health/ready").json() == {"status": "ready", "version": __version__}
    application.start.assert_awaited_once()


def test_not_ready_returns_service_unavailable(client_factory) -> None:
    client, _ = client_factory(ready=False)
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_request_id_is_preserved_or_generated(client_factory) -> None:
    client, _ = client_factory()
    supplied = client.get("/api/health/live", headers={"X-Request-ID": "trace:one.2"})
    assert supplied.headers["X-Request-ID"] == "trace:one.2"

    invalid = client.get("/api/health/live", headers={"X-Request-ID": "bad id"})
    assert invalid.headers["X-Request-ID"] != "bad id"
    assert len(invalid.headers["X-Request-ID"]) == 36


def test_api_index_docs_and_disabled_docs(client_factory) -> None:
    client, _ = client_factory()
    assert client.get("/api/").json() == {"name": "ECHO API", "version": __version__}
    assert client.get("/api/openapi.json").status_code == 200

    disabled, _ = client_factory(docs_enabled=False)
    assert disabled.get("/api/openapi.json").status_code == 404
    assert disabled.get("/api/docs").status_code == 404


def test_cors_preflight_uses_configured_policy(client_factory) -> None:
    client, _ = client_factory(cors_origins=["https://ui.example"])
    response = client.options(
        "/api/plans",
        headers={
            "Origin": "https://ui.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-ECHO-API-Key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://ui.example"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_control_plane_api_key_authentication(client_factory) -> None:
    client, _ = client_factory(api_key="a" * 16)
    unauthorized = client.get("/api/system")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["WWW-Authenticate"] == "ApiKey"

    authorized = client.get("/api/system", headers={"X-ECHO-API-Key": "a" * 16})
    assert authorized.status_code == 200


def test_auth_session_when_authentication_is_disabled(client_factory) -> None:
    client, _ = client_factory()
    session = client.get("/api/auth/session")
    assert session.json() == {"authenticated": True, "login_enabled": False}
    assert session.headers["Cache-Control"] == "no-store"

    login = client.post(
        "/api/auth/login",
        json={"password": "anything"},
        headers={"Origin": "http://testserver"},
    )
    assert login.json() == {"authenticated": True, "login_enabled": False}


def test_login_disabled_wrong_password_and_success(client_factory) -> None:
    disabled, _ = client_factory(api_key="a" * 16)
    response = disabled.post(
        "/api/auth/login",
        json={"password": "anything"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Web login is not configured"

    client, _ = client_factory(api_key="a" * 16, web_password="correct horse")
    wrong = client.post(
        "/api/auth/login",
        json={"password": "incorrect"},
        headers={"Origin": "http://testserver"},
    )
    assert wrong.status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"password": "correct horse"},
        headers={"Origin": "http://testserver"},
    )
    assert login.status_code == 200
    assert login.json() == {"authenticated": True, "login_enabled": True}
    cookie = login.headers["set-cookie"]
    assert "echo_session=" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie

    session = client.get("/api/auth/session")
    assert session.json() == {"authenticated": True, "login_enabled": True}


def test_cookie_auth_origin_and_logout(client_factory) -> None:
    client, _ = client_factory(api_key="a" * 16, web_password="correct horse")
    client.post(
        "/api/auth/login",
        json={"password": "correct horse"},
        headers={"Origin": "http://testserver"},
    )
    assert client.get("/api/system").status_code == 200
    assert client.post("/api/runs/id/cancel", headers={"Origin": "https://evil.example"}).status_code == 403

    logout = client.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    assert logout.status_code == 204
    assert "echo_session=" in logout.headers["set-cookie"]
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert client.get("/api/system").status_code == 401


def test_login_validation_error(client_factory) -> None:
    client, _ = client_factory(api_key="a" * 16, web_password="correct horse")
    response = client.post(
        "/api/auth/login",
        json={"password": ""},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_archive_listing_normalizes_paths_and_maps_items(client_factory) -> None:
    client, application = client_factory()
    application.rclone.list_remote.return_value = [
        RcloneItem(
            path="movie.mkv",
            name="movie.mkv",
            size=42,
            is_dir=False,
            mod_time="2026-01-01T00:00:00Z",
            mime_type="video/x-matroska",
            tier="DEEP_ARCHIVE",
            hashes={"md5": "abc"},
        )
    ]
    response = client.get(
        "/api/archive",
        params={"path": r"/media\films/", "recursive": "true", "include_hashes": "true"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "bucket": "echo-bucket",
        "path": "media/films",
        "items": [
            {
                "path": "media/films/movie.mkv",
                "name": "movie.mkv",
                "size": 42,
                "is_dir": False,
                "mod_time": "2026-01-01T00:00:00Z",
                "mime_type": "video/x-matroska",
                "tier": "DEEP_ARCHIVE",
                "hashes": {"md5": "abc"},
            }
        ],
    }
    application.rclone.list_remote.assert_awaited_once_with(
        "media/films", recursive=True, include_hashes=True
    )


def test_archive_listing_empty_and_invalid_paths(client_factory) -> None:
    client, application = client_factory()
    assert client.get("/api/archive", params={"path": "///"}).json()["path"] is None
    application.rclone.list_remote.assert_awaited_with(
        None, recursive=False, include_hashes=False
    )

    invalid = client.get("/api/archive", params={"path": "../secret"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "http_422"

    too_long = client.get("/api/archive", params={"path": "x" * 2049})
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "validation_error"


def test_archive_size_and_capacity(client_factory) -> None:
    client, application = client_factory()
    application.rclone.size.return_value = RcloneSize(count=7, bytes=999)
    application.rclone.about.return_value = RcloneAbout(
        total=1000, used=600, free=400, trashed=3, other=2
    )
    assert client.get("/api/archive/size").json() == {"bytes": 999, "objects": 7}
    assert client.get("/api/archive/capacity").json() == {
        "total": 1000,
        "used": 600,
        "free": 400,
        "trashed": 3,
        "other": 2,
    }


def test_plan_routes_and_submissions(client_factory, run_factory) -> None:
    client, application = client_factory()
    plan = ArchivePlanConfig(
        name="media",
        source=Path("/data/media"),
        destination="backups/media",
        cron="0 3 * * *",
        exclude=["*.tmp"],
        verify_after_archive=True,
    )
    latest = run_factory()
    application.archiver.plans = (plan,)
    application.archiver.get_plan.return_value = plan
    application.archiver.get_status.return_value = ArchivePlanStatus("media", "active-1", latest)
    application.scheduler.is_scheduled.return_value = True

    listed = client.get("/api/plans")
    assert listed.status_code == 200
    assert listed.json()[0]["active_run_id"] == "active-1"
    assert listed.json()[0]["latest_run"]["id"] == "run-123"
    assert client.get("/api/plans/media").json()["scheduled"] is True

    queued = run_factory(state=RunState.QUEUED, finished_at=None, return_code=None)
    application.archiver.submit.return_value = queued
    archive = client.post("/api/plans/media/runs", json={"dry_run": True})
    assert archive.status_code == 202
    assert archive.headers["Location"] == "/api/runs/run-123"
    application.archiver.submit.assert_awaited_with("media", dry_run=True)

    verify = client.post("/api/plans/media/verifications")
    assert verify.status_code == 202
    application.archiver.submit.assert_awaited_with("media", operation=RunOperation.VERIFY)


def test_run_routes_filter_paginate_detail_and_cancel(client_factory, run_factory) -> None:
    client, application = client_factory()
    runs = [run_factory(id="one"), run_factory(id="two")]
    application.runs.list.return_value = runs
    page = client.get(
        "/api/runs",
        params={
            "plan_name": "media",
            "state": "succeeded",
            "operation": "archive",
            "limit": 1,
            "offset": 2,
        },
    )
    assert page.status_code == 200
    assert page.json()["items"][0]["id"] == "one"
    assert page.json()["has_more"] is True
    application.runs.list.assert_awaited_once_with(
        plan_name="media",
        state=RunState.SUCCEEDED,
        operation=RunOperation.ARCHIVE,
        limit=2,
        offset=2,
    )

    application.runs.get.return_value = runs[0]
    detail = client.get("/api/runs/one")
    assert detail.json()["stdout"] == "copied"

    application.archiver.cancel.return_value = runs[1]
    cancelled = client.post("/api/runs/two/cancel")
    assert cancelled.json()["id"] == "two"
    application.archiver.cancel.assert_awaited_once_with("two")


def test_run_query_validation(client_factory) -> None:
    client, _ = client_factory()
    for params in ({"limit": 0}, {"limit": 501}, {"offset": -1}, {"state": "unknown"}):
        response = client.get("/api/runs", params=params)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_system_status_and_rclone_status(client_factory) -> None:
    client, application = client_factory()
    enabled = ArchivePlanConfig(name="enabled", source="/a", destination="a", enabled=True)
    disabled = ArchivePlanConfig(name="disabled", source="/b", destination="b", enabled=False)
    application.archiver.plans = (enabled, disabled)
    application.archiver.active_count = 2
    application.scheduler.plans = (enabled,)
    application.scheduler.last_tick = datetime(2026, 1, 1, tzinfo=UTC)
    application.runs.summary.return_value = {RunState.QUEUED: 2, RunState.SUCCEEDED: 5}
    application.rclone_status = RcloneStatus(
        version="v1.2.3",
        remote="cloud",
        bucket="echo-bucket",
        remotes=("cloud", "local"),
        large_uploads_optimized=True,
    )

    system = client.get("/api/system").json()
    assert system["version"] == __version__
    assert system["configured_plans"] == 2
    assert system["enabled_plans"] == 1
    assert system["scheduled_plans"] == 1
    assert system["active_runs"] == 2
    assert system["run_counts"] == {
        "queued": 2,
        "running": 0,
        "succeeded": 5,
        "failed": 0,
        "cancelled": 0,
        "interrupted": 0,
    }
    assert system["uptime_seconds"] >= 299

    rclone = client.get("/api/system/rclone").json()
    assert rclone == {
        "configured": True,
        "available": True,
        "version": "v1.2.3",
        "remote": "cloud",
        "bucket": "echo-bucket",
        "remotes": ["cloud", "local"],
        "large_uploads_optimized": True,
    }


def test_unavailable_rclone_status_uses_configured_target(client_factory) -> None:
    client, _ = client_factory()
    assert client.get("/api/system/rclone").json() == {
        "configured": True,
        "available": False,
        "version": None,
        "remote": "cloud",
        "bucket": "echo-bucket",
        "remotes": [],
        "large_uploads_optimized": False,
    }
