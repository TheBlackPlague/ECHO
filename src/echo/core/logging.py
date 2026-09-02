from __future__ import annotations

import atexit
import copy
import hashlib
import logging
import logging.handlers
import queue
import re
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

import structlog
from rich.console import Console
from rich.text import Text
from structlog.stdlib import BoundLogger

from echo.core.config import LoggingConfig


LOGGER_NAME = "echo"
PRODUCER = "ECHO"
DEFAULT_SERVICE = "core"

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_LEVEL_STYLES = {
    logging.DEBUG: "dim cyan",
    logging.INFO: "green",
    logging.WARNING: "bold yellow",
    logging.ERROR: "bold red",
    logging.CRITICAL: "bold white on red",
}
_STANDARD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {"asctime", "message"}
_CONTEXT_FIELDS = {"color_message", "event", "level", "logger", "producer", "service"}


def _timezone_code(name: str | None) -> str:
    words = re.findall(r"[A-Za-z]+", name or "")

    if not words: return "UTC"

    if len(words) == 1: return words[0].upper()[:4]

    return "".join(word[0] for word in words).upper()[:4]


def _identity(record: logging.LogRecord) -> tuple[str, str]:
    producer = str(getattr(record, "producer", PRODUCER))
    service = getattr(record, "service", None)

    if service is not None: return producer, str(service)

    if record.name.startswith("uvicorn.access"): return producer, "http"
    if record.name.startswith("uvicorn"): return producer, "uvicorn"

    if record.name.startswith(f"{LOGGER_NAME}."): return producer, record.name.removeprefix(f"{LOGGER_NAME}.")

    return producer, DEFAULT_SERVICE if record.name == LOGGER_NAME else record.name.rsplit(".", 1)[-1]


def _diagnostic(record: logging.LogRecord) -> str:
    parts: list[str] = []

    if record.exc_info:
        parts.extend(line.strip() for line in traceback.format_exception(*record.exc_info) if line.strip())

    if record.stack_info: parts.extend(line.strip() for line in record.stack_info.splitlines() if line.strip())

    return " | ".join(parts)


def _message(record: logging.LogRecord) -> str:
    message = record.getMessage()
    diagnostic = _diagnostic(record)
    return f"{message} | {diagnostic}" if message and diagnostic else message or diagnostic


def _prefix(record: logging.LogRecord) -> tuple[str, str, str, str]:
    local_time = datetime.fromtimestamp(record.created).astimezone()
    producer, service = _identity(record)
    return (
        f"{local_time:%Y-%m-%d %H:%M:%S} {_timezone_code(local_time.tzname())}",
        f"{producer.upper():^8}",
        f"{service.upper():^14}",
        f"{record.levelname:^8}",
    )


def _context_lines(record: logging.LogRecord) -> tuple[str, ...]:
    items = [
        (key, repr(value))
        for key, value in sorted(record.__dict__.items())
        if key not in _STANDARD_FIELDS and key not in _CONTEXT_FIELDS and not key.startswith("_")
    ]

    if not items: return ()

    key_width = max(len(key) for key, _ in items)
    rows = [f"{key:<{key_width}} = {value}" for key, value in items]
    width = max(24, *(len(row) + 2 for row in rows))

    return (
        f"╭{'─' * width}╮",
        *(f"│ {row:<{width - 2}} │" for row in rows),
        f"╰{'─' * width}╯",
    )


def _plain_lines(record: logging.LogRecord) -> tuple[str, ...]:
    timestamp, producer, service, severity = _prefix(record)
    return (
        f"[{timestamp}][{producer}][{service}][{severity}] {_message(record)}",
        *_context_lines(record),
    )


class _QueueHandler(logging.handlers.QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)


class _ConsoleHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.console = Console(file=sys.stdout, highlight=False, soft_wrap=True)

    def emit(self, record: logging.LogRecord) -> None:
        # noinspection broad-exception
        try:
            timestamp, producer, service, severity = _prefix(record)

            line = Text()

            line.append(f"[{timestamp}]", style="dim white")
            line.append(f"[{producer}]", style="bold bright_blue")
            line.append(f"[{service}]", style="bold bright_magenta")
            line.append(f"[{severity}]", style=_LEVEL_STYLES.get(record.levelno, "white"))
            line.append(f" {_message(record)}", style="bright_white")

            self.console.print(line, highlight=False, soft_wrap=True)

            for context_line in _context_lines(record):
                self.console.print(Text(context_line, style="dim cyan"), highlight=False)

        except Exception:
            self.handleError(record)


class _FileHandler(logging.Handler):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        directory.mkdir(parents=True, exist_ok=True)

        seed = time.time_ns().to_bytes(8, "big", signed=False)
        run_hash = hashlib.blake2s(seed, digest_size=4, person=b"ECHOLOG").hexdigest()
        date = datetime.now().astimezone().strftime("%Y-%m-%d")

        self.path = directory / f"echo-{date}-{run_hash}.log"
        self._stream: TextIO = self.path.open("a", encoding="utf-8", buffering=1)

    def emit(self, record: logging.LogRecord) -> None:
        # noinspection broad-exception
        try:
            self._stream.write("\n".join(_plain_lines(record)) + "\n")

        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.flush()
            self._stream.close()

        super().close()


@dataclass(slots=True)
class _LoggingState:
    listener: logging.handlers.QueueListener
    file_handler: _FileHandler


_state: _LoggingState | None = None


def _configure_structlog() -> None:
    # noinspection bad-argument-type
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.render_to_log_kwargs,
        ],
        context_class=dict,
        wrapper_class=BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _route_uvicorn_to_root() -> None:
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)


def configure_logging(config: LoggingConfig) -> Path:
    global _state
    if _state is not None: return _state.file_handler.path

    minimum = _LEVELS[config.level]

    record_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()

    ingress = _QueueHandler(record_queue)
    ingress.setLevel(minimum)

    file_handler = _FileHandler(config.directory)
    listener = logging.handlers.QueueListener(
        record_queue,
        _ConsoleHandler(),
        file_handler,
        respect_handler_level=False,
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(ingress)
    root.setLevel(minimum)

    _route_uvicorn_to_root()

    logging.captureWarnings(True)

    _configure_structlog()

    listener.start()

    _state = _LoggingState(listener=listener, file_handler=file_handler)

    return file_handler.path


def shutdown_logging() -> None:
    global _state
    state = _state
    if state is None: return

    state.listener.stop()
    logging.getLogger().handlers.clear()
    logging.captureWarnings(False)
    state.file_handler.close()
    _state = None


def get_logger(*, service: str = DEFAULT_SERVICE) -> BoundLogger:
    return structlog.stdlib.get_logger(LOGGER_NAME).bind(producer=PRODUCER, service=service)


_configure_structlog()

atexit.register(shutdown_logging)
