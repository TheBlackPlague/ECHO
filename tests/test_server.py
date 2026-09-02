from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import echo.server as server


def server_config():  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        api=SimpleNamespace(host="127.0.0.1", port=8123, access_log=False),
        logging=SimpleNamespace(),
    )


def patch_server(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    config = server_config()
    logger = Mock()
    configure = Mock(return_value="/logs/echo.log")
    shutdown = Mock()
    run = Mock()
    monkeypatch.setattr(server, "get_config", Mock(return_value=config))
    monkeypatch.setattr(server, "configure_logging", configure)
    monkeypatch.setattr(server, "get_logger", Mock(return_value=logger))
    monkeypatch.setattr(server, "shutdown_logging", shutdown)
    monkeypatch.setattr(server.uvicorn, "run", run)
    return config, logger, configure, shutdown, run


def test_run_launches_single_worker_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    config, logger, configure, shutdown, uvicorn_run = patch_server(monkeypatch)
    server.run()

    configure.assert_called_once_with(config.logging)
    logger.info.assert_called_once()
    uvicorn_run.assert_called_once_with(
        "echo.api.app:app",
        host="127.0.0.1",
        port=8123,
        access_log=False,
        server_header=False,
        log_config=None,
        workers=1,
    )
    shutdown.assert_called_once()


def test_keyboard_interrupt_is_clean_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    _, logger, _, shutdown, uvicorn_run = patch_server(monkeypatch)
    uvicorn_run.side_effect = KeyboardInterrupt
    server.run()
    logger.critical.assert_not_called()
    shutdown.assert_called_once()


def test_unexpected_server_error_exits_and_shuts_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, logger, _, shutdown, uvicorn_run = patch_server(monkeypatch)
    uvicorn_run.side_effect = RuntimeError("bind failed")
    with pytest.raises(SystemExit) as raised:
        server.run()
    assert raised.value.code == 1
    logger.critical.assert_called_once_with("ECHO terminated unexpectedly", exc_info=True)
    shutdown.assert_called_once()
