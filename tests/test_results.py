from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

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
