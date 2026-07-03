"""Application-wide logging setup.

Uses one rotating file handler plus a console handler attached to the root
logger, so every module's logger (obtained via :func:`get_logger`) inherits
the same format and destinations. Module name + level already distinguishes
the categories called out in CLAUDE.md (AI requests/responses, market
analysis, trade execution, errors, retries, trade results) without a bespoke
category system.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_configured = False


def configure_logging(
    log_dir: Path,
    level: str = "INFO",
    max_bytes: int = 10_485_760,
    backup_count: int = 5,
) -> None:
    """Attach a rotating file handler and a console handler to the root logger.

    Idempotent: safe to call once at startup; subsequent calls are no-ops.
    """
    global _configured
    if _configured:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "xauusd_bot.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Pass ``__name__`` from the calling module."""
    return logging.getLogger(name)
