"""Structured JSON logging with correlation ids and secret redaction.

Every record is one JSON object per line: UTC timestamp, level, logger,
event, message, correlation id (from a context variable) and any extra
fields passed with ``log_event``. Field names that look like secrets are
redacted before formatting, so a token can never reach a log file.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import logging.handlers
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_LOGGER = "astra"
_correlation: contextvars.ContextVar[str | None] = contextvars.ContextVar("astra_correlation_id", default=None)
_SECRET_KEY = re.compile(r"(secret|token|password|passwd|api[_-]?key|authorization|credential)", re.IGNORECASE)
_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}
REDACTED = "****"

# Library default: silent until the application calls configure_logging.
logging.getLogger(ROOT_LOGGER).addHandler(logging.NullHandler())


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: (REDACTED if _SECRET_KEY.search(str(key)) else redact(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or "log",
            "message": record.getMessage(),
        }
        correlation = getattr(record, "correlation_id", None) or _correlation.get()
        if correlation:
            payload["correlation_id"] = correlation
        skipped = _RESERVED | {"event", "correlation_id"}
        fields = {key: value for key, value in record.__dict__.items() if key not in skipped}
        if fields:
            payload["fields"] = redact(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_logging(
    *, level: str = "INFO", log_dir: str | Path | None = None, stream: Any = None, max_bytes: int = 5_000_000, backups: int = 5
) -> logging.Logger:
    """Configure the ``astra`` logger once; safe to call again (handlers are replaced)."""

    logger = logging.getLogger(ROOT_LOGGER)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level.upper())
    logger.propagate = False
    formatter = JsonFormatter()
    console = logging.StreamHandler(stream)
    console.setFormatter(formatter)
    logger.addHandler(console)
    if log_dir is not None:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            directory / "astra.jsonl", maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger


def reset_logging() -> None:
    """Remove configured handlers and return to the silent library default."""

    logger = logging.getLogger(ROOT_LOGGER)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{name}")


def log_event(logger: logging.Logger, event: str, message: str = "", *, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, message or event, extra={"event": event, **redact(fields)})


@contextlib.contextmanager
def correlation(correlation_id: str | None) -> Iterator[None]:
    token = _correlation.set(correlation_id)
    try:
        yield
    finally:
        _correlation.reset(token)
