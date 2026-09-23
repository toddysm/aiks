"""Human-readable and machine-readable operation results."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console

from aiks.redaction import redact


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Secret-redacted summary of one CLI operation."""

    operation: str
    phase: str
    succeeded: bool
    duration_seconds: float
    context: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: str(uuid4()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "phase": self.phase,
            "succeeded": self.succeeded,
            "durationSeconds": round(self.duration_seconds, 3),
            "correlationId": self.correlation_id,
            "context": redact(self.context),
        }

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"
        path.write_text(content, encoding="utf-8")

    def render(self, console: Console | None = None) -> None:
        target = console or Console()
        status = "[green]succeeded[/green]" if self.succeeded else "[red]failed[/red]"
        target.print(
            f"{self.operation} {status} during {self.phase} "
            f"({self.duration_seconds:.3f}s, correlation {self.correlation_id})"
        )
