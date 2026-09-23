"""Local, secret-safe logging configuration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, TextIO, cast

from aiks.redaction import redact, redact_text

LOGGER_NAME = "aiks"
_STANDARD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__)


class RedactingFilter(logging.Filter):
    """Redact message arguments and structured context before handler emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if isinstance(record.args, Mapping):
            record.args = cast(dict[str, Any], redact(record.args))
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(argument) for argument in record.args)

        for name in record.__dict__.keys() - _STANDARD_ATTRIBUTES:
            setattr(record, name, redact(getattr(record, name)))
        return True


def configure_logging(*, level: int = logging.INFO, stream: TextIO | None = None) -> logging.Logger:
    """Configure idempotent local logging for the ``aiks`` namespace."""

    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
