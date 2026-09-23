from __future__ import annotations

import logging
from io import StringIO

from aiks.logging import RedactingFilter, configure_logging


def test_configured_logging_redacts_before_emission() -> None:
    stream = StringIO()
    logger = configure_logging(stream=stream)

    logger.info(
        "authorization=%s context=%s",
        "Bearer abc.def",
        {"token": "token-value", "safe": "value"},
    )

    emitted = stream.getvalue()
    assert "abc.def" not in emitted
    assert "token-value" not in emitted
    assert "<redacted>" in emitted
    assert "value" in emitted
    assert logger.propagate is False


def test_filter_redacts_structured_context_before_handler_formatting() -> None:
    stream = StringIO()
    logger = logging.getLogger("aiks.test.structured")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(message)s %(details)s"))
    logger.addHandler(handler)

    logger.info("completed", extra={"details": {"password": "secret-value"}})

    emitted = stream.getvalue()
    assert "secret-value" not in emitted
    assert "<redacted>" in emitted


def test_filter_redacts_labeled_positional_secret() -> None:
    stream = StringIO()
    logger = configure_logging(stream=stream)

    logger.info("password=%s", "raw-password")

    emitted = stream.getvalue()
    assert "raw-password" not in emitted
    assert "password=<redacted>" in emitted
