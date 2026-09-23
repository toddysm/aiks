from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path

from rich.console import Console

from aiks.logging import configure_logging
from aiks.results import OperationResult


def test_result_redacts_context_and_writes_json(tmp_path: Path) -> None:
    result = OperationResult(
        operation="test",
        phase="unit",
        succeeded=True,
        duration_seconds=1.23456,
        context={"token": "sensitive", "safe": "value"},
        correlation_id="correlation",
    )
    path = tmp_path / "result.json"

    result.write_json(path)
    payload = json.loads(path.read_text())

    assert payload["durationSeconds"] == 1.235
    assert payload["context"] == {"safe": "value", "token": "<redacted>"}


def test_result_renders_human_summary() -> None:
    result = OperationResult(
        operation="test",
        phase="unit",
        succeeded=True,
        duration_seconds=0.1,
        correlation_id="correlation",
    )
    console = Console(record=True, force_terminal=False, width=120)

    result.render(console)

    rendered = console.export_text()
    assert "test succeeded during unit" in rendered
    assert "correlation" in rendered


def test_failed_result_is_redacted_in_json_text_and_logs(tmp_path: Path) -> None:
    result = OperationResult(
        operation="test.failure",
        phase="unit",
        succeeded=False,
        duration_seconds=0.2,
        context={"token": "secret-value", "safe": "value"},
        correlation_id="failure-correlation",
    )
    path = tmp_path / "failure.json"
    console = Console(record=True, force_terminal=False, width=120)
    stream = StringIO()
    logger = configure_logging(level=logging.INFO, stream=stream)

    result.write_json(path)
    result.render(console)
    result.log(logger)

    payload = json.loads(path.read_text())
    rendered = console.export_text()
    assert payload["succeeded"] is False
    assert payload["context"]["token"] == "<redacted>"
    assert "test.failure failed during unit" in rendered
    assert "failure-correlation" in rendered
    assert "secret-value" not in rendered
    assert "secret-value" not in path.read_text()
    assert "ERROR" in stream.getvalue()
    assert "succeeded=False" in stream.getvalue()
    assert "secret-value" not in stream.getvalue()
    assert "<redacted>" in stream.getvalue()
