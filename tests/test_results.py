from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path

import pytest
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
    path = tmp_path / "nested" / "results" / "result.json"

    result.write_json(path)
    payload = json.loads(path.read_text())

    assert payload["durationSeconds"] == 1.235
    assert payload["context"] == {"safe": "value", "token": "<redacted>"}
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_result_parent_creation_refuses_symlinks(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    parent = tmp_path / "linked"
    parent.symlink_to(external, target_is_directory=True)
    result = OperationResult("test", "unit", True, 0.1)
    with pytest.raises(OSError):
        result.write_json(parent / "new" / "result.json")
    assert not list(external.iterdir())


def test_result_output_does_not_follow_symlink(tmp_path: Path) -> None:
    external = tmp_path / "external.txt"
    external.write_text("preserve")
    path = tmp_path / "result.json"
    path.symlink_to(external)
    result = OperationResult("test", "unit", True, 0.1, context={"token": "synthetic-secret"})
    result.write_json(path)
    assert external.read_text() == "preserve"
    assert not path.is_symlink()
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["context"]["token"] == "<redacted>"


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
        context={
            "token": "secret-value",
            "safe": "value",
            "terraform": {"sensitive": True, "value": "terraform-secret"},
        },
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
    assert payload["context"]["terraform"]["value"] == "<redacted>"
    assert "test.failure failed during unit" in rendered
    assert "failure-correlation" in rendered
    assert "'safe'" in rendered
    assert "'value'" in rendered
    assert "'token': '<redacted>'" in rendered
    assert "secret-value" not in rendered
    assert "terraform-secret" not in rendered
    assert "secret-value" not in path.read_text()
    assert "ERROR" in stream.getvalue()
    assert "succeeded=False" in stream.getvalue()
    assert "secret-value" not in stream.getvalue()
    assert "<redacted>" in stream.getvalue()
