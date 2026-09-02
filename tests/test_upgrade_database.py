from __future__ import annotations

import argparse
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path
from types import ModuleType

import pytest

from echo.storage.schema import create_schema


def _load_upgrade_script() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "upgrade_database.py"
    spec = importlib.util.spec_from_file_location("upgrade_database", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


upgrade_database = _load_upgrade_script()


def _track_connections(monkeypatch: pytest.MonkeyPatch) -> list[sqlite3.Connection]:
    original_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def connect(*args, **kwargs) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(upgrade_database.sqlite3, "connect", connect)
    return opened


def _assert_closed(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_current_database_check_closes_connection(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "echo.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        create_schema(connection)

    opened = _track_connections(monkeypatch)
    monkeypatch.setattr(
        upgrade_database,
        "parse_args",
        lambda: argparse.Namespace(database=path, no_backup=False),
    )

    upgrade_database.main()

    assert capsys.readouterr().out == f"{path}: schema 1 is already current\n"
    assert len(opened) == 1
    _assert_closed(opened[0])


def test_backup_closes_target_connection(tmp_path, monkeypatch) -> None:
    path = tmp_path / "echo.db"
    opened = _track_connections(monkeypatch)

    with closing(sqlite3.connect(path)) as source, source:
        source.execute("CREATE TABLE probe (value TEXT)")
        source.execute("INSERT INTO probe VALUES ('preserved')")
        source.commit()
        backup = upgrade_database._backup(source, path, schema_version=1)

    assert len(opened) == 2
    _assert_closed(opened[1])

    with closing(sqlite3.connect(backup)) as restored:
        assert restored.execute("SELECT value FROM probe").fetchone()[0] == "preserved"
