from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

from echo.core import logging as echo_logging
from echo.core.config import LoggingConfig


@pytest.fixture(autouse=True)
def _reset_echo_logging() -> None:
    echo_logging.shutdown_logging()
    yield
    echo_logging.shutdown_logging()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (None, "UTC"),
        ("", "UTC"),
        ("UTC", "UTC"),
        ("Central Daylight Time", "CDT"),
        ("America/New_York", "ANY"),
        ("abcdefgh", "ABCD"),
    ],
)
def test_timezone_code(name: str | None, expected: str) -> None:
    assert echo_logging._timezone_code(name) == expected


@pytest.mark.parametrize(
    ("logger_name", "extra", "expected"),
    [
        ("anything", {"producer": "worker", "service": "scan"}, ("worker", "scan")),
        ("uvicorn.access", {}, ("ECHO", "http")),
        ("uvicorn.error", {}, ("ECHO", "uvicorn")),
        ("echo.archive", {}, ("ECHO", "archive")),
        ("echo", {}, ("ECHO", "core")),
        ("foreign.library", {}, ("ECHO", "library")),
    ],
)
def test_identity(
    logger_name: str,
    extra: dict[str, str],
    expected: tuple[str, str],
) -> None:
    record = logging.LogRecord(logger_name, logging.INFO, __file__, 1, "message", (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    assert echo_logging._identity(record) == expected


def test_message_formats_arguments_exception_and_stack() -> None:
    try:
        raise ValueError("broken")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "echo.test",
        logging.ERROR,
        __file__,
        1,
        "failed %s",
        ("operation",),
        exc_info,
    )
    record.stack_info = "Stack (most recent call last):\n  test frame"

    message = echo_logging._message(record)
    assert message.startswith("failed operation | Traceback")
    assert "ValueError: broken" in message
    assert "test frame" in message


def test_message_can_contain_only_diagnostic() -> None:
    record = logging.LogRecord("echo", logging.ERROR, __file__, 1, "", (), None)
    record.stack_info = "stack details"
    assert echo_logging._message(record) == "stack details"


def test_context_lines_are_sorted_aligned_and_filter_internal_fields() -> None:
    record = logging.LogRecord("echo", logging.INFO, __file__, 1, "hello", (), None)
    record.producer = "ECHO"
    record.service = "core"
    record.event = "hello"
    record.zebra = 3
    record.alpha = "value"
    record._private = "hidden"

    lines = echo_logging._context_lines(record)
    assert lines[0].startswith("╭")
    assert "alpha = 'value'" in lines[1]
    assert "zebra = 3" in lines[2]
    assert all("private" not in line for line in lines)
    assert echo_logging._context_lines(
        logging.LogRecord("echo", logging.INFO, __file__, 1, "hello", (), None)
    ) == ()


def test_plain_lines_include_prefix_message_and_context() -> None:
    record = logging.LogRecord("echo.worker", logging.WARNING, __file__, 1, "hello", (), None)
    record.created = 0
    record.job = "daily"

    lines = echo_logging._plain_lines(record)
    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z]{1,4}\]", lines[0])
    assert "[  ECHO  ][    WORKER    ][WARNING ] hello" in lines[0]
    assert any("job = 'daily'" in line for line in lines[1:])


def test_queue_handler_prepare_copies_without_mutating_record() -> None:
    record = logging.LogRecord("echo", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    prepared = echo_logging._QueueHandler(None).prepare(record)
    assert prepared is not record
    assert prepared.msg == "hello %s"
    assert prepared.args == ("world",)


def test_console_handler_emits_main_line_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[object] = []

    class FakeConsole:
        def print(self, value: object, **_: object) -> None:
            printed.append(value)

    handler = echo_logging._ConsoleHandler()
    monkeypatch.setattr(handler, "console", FakeConsole())
    record = logging.LogRecord("echo.api", logging.INFO, __file__, 1, "ready", (), None)
    record.request_id = "abc"
    handler.emit(record)

    rendered = [getattr(item, "plain", str(item)) for item in printed]
    assert "ready" in rendered[0]
    assert any("request_id = 'abc'" in line for line in rendered[1:])


def test_console_handler_delegates_internal_errors_to_handle_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = echo_logging._ConsoleHandler()
    handled: list[logging.LogRecord] = []
    monkeypatch.setattr(
        handler.console,
        "print",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )
    monkeypatch.setattr(handler, "handleError", handled.append)
    record = logging.LogRecord("echo", logging.INFO, __file__, 1, "ready", (), None)
    handler.emit(record)
    assert handled == [record]


def test_file_handler_writes_utf8_plain_log_and_closes(tmp_path: Path) -> None:
    handler = echo_logging._FileHandler(tmp_path / "nested")
    record = logging.LogRecord("echo.storage", logging.ERROR, __file__, 1, "café", (), None)
    record.run_id = "run-1"
    handler.emit(record)
    path = handler.path
    handler.close()
    handler.close()

    assert path.parent == tmp_path / "nested"
    assert re.fullmatch(r"echo-\d{4}-\d{2}-\d{2}-[0-9a-f]{8}\.log", path.name)
    content = path.read_text(encoding="utf-8")
    assert "café" in content
    assert "run_id = 'run-1'" in content
    assert handler._stream.closed


def test_file_handler_handles_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handler = echo_logging._FileHandler(tmp_path)
    handled: list[logging.LogRecord] = []
    handler.close()
    monkeypatch.setattr(handler, "handleError", handled.append)
    record = logging.LogRecord("echo", logging.INFO, __file__, 1, "message", (), None)
    handler.emit(record)
    assert handled == [record]


def test_route_uvicorn_clears_handlers_and_enables_propagation() -> None:
    affected = [logging.getLogger(name) for name in ("uvicorn", "uvicorn.error", "uvicorn.access")]
    for logger in affected:
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        logger.setLevel(logging.CRITICAL)

    echo_logging._route_uvicorn_to_root()

    for logger in affected:
        assert logger.handlers == []
        assert logger.propagate is True
        assert logger.level == logging.NOTSET


def test_configure_logging_is_idempotent_routes_records_and_shutdowns(tmp_path: Path) -> None:
    path = echo_logging.configure_logging(LoggingConfig(level="DEBUG", directory=tmp_path))
    same_path = echo_logging.configure_logging(
        LoggingConfig(level="ERROR", directory=tmp_path / "other")
    )
    assert same_path == path

    logger = echo_logging.get_logger(service="tests")
    logger.info("configured", answer=42)
    echo_logging.shutdown_logging()

    content = path.read_text(encoding="utf-8")
    assert "configured" in content
    assert "answer = 42" in content
    assert logging.getLogger().handlers == []
    assert echo_logging._state is None


def test_shutdown_logging_without_state_is_safe() -> None:
    echo_logging.shutdown_logging()
    assert echo_logging._state is None
