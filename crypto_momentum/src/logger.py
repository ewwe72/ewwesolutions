"""Structured JSON logging.

Identical contract to the stocks bot — JSON-line events with timestamp,
level, event_type, symbol, payload. Rotating file under ``logs/`` plus
stdout for interactive watching.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

from pythonjsonlogger import jsonlogger


_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(
    *,
    log_dir: Path,
    rotate_bytes: int = 10 * 1024 * 1024,
    rotate_backups: int = 7,
    level: int = logging.INFO,
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = jsonlogger.JsonFormatter(  # type: ignore[no-untyped-call]
        _DEFAULT_FORMAT, rename_fields={"asctime": "timestamp"}
    )

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    rotating = logging.handlers.RotatingFileHandler(
        log_dir / "crypto-bot.log",
        maxBytes=rotate_bytes,
        backupCount=rotate_backups,
        encoding="utf-8",
    )
    rotating.setFormatter(fmt)
    root.addHandler(rotating)

    return root


def log_event(
    logger: logging.Logger,
    *,
    event_type: str,
    symbol: str | None = None,
    payload: dict[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    extra = {"event_type": event_type, "symbol": symbol, "payload": payload or {}}
    logger.log(level, event_type, extra=extra)
