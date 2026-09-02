from __future__ import annotations

import runpy
from unittest.mock import Mock

import echo.server


def test_module_entrypoint_runs_server(monkeypatch) -> None:
    run = Mock()
    monkeypatch.setattr(echo.server, "run", run)

    runpy.run_module("echo.__main__", run_name="__main__")

    run.assert_called_once_with()


def test_module_entrypoint_does_not_run_when_imported(monkeypatch) -> None:
    run = Mock()
    monkeypatch.setattr(echo.server, "run", run)

    runpy.run_module("echo.__main__", run_name="echo.__main__")

    run.assert_not_called()
