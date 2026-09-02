from __future__ import annotations

import uvicorn

from echo.core.config import get_config
from echo.core.logging import configure_logging, get_logger, shutdown_logging


def run() -> None:
    config = get_config()
    log_path = configure_logging(config.logging)
    logger = get_logger(service="main")
    logger.info(
        "Launching ECHO API",
        host=config.api.host,
        port=config.api.port,
        log_path=str(log_path),
    )

    try:
        uvicorn.run(
            "echo.api.app:app",
            host=config.api.host,
            port=config.api.port,
            access_log=config.api.access_log,
            server_header=False,
            log_config=None,
            workers=1,
        )

    except KeyboardInterrupt:
        pass

    except Exception:
        logger.critical("ECHO terminated unexpectedly", exc_info=True)
        raise SystemExit(1) from None

    finally:
        shutdown_logging()
