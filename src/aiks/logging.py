"""Local, secret-safe logging configuration."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Mapping
from typing import TextIO

from aiks.redaction import redact, redact_text

LOGGER_NAME = "aiks"
_STANDARD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__)


class RedactingFilter(logging.Filter):
    """Redact message arguments and structured context before handler emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, Mapping):
            record.args = redact(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(argument) for argument in record.args)
        record.msg = redact_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            exception_text = "".join(traceback.format_exception(*record.exc_info))
            record.msg = f"{record.msg}\n{redact_text(exception_text)}"
            record.exc_info = None
            record.exc_text = None
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)

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
