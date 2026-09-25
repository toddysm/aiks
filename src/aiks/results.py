"""Human-readable and machine-readable operation results."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console

from aiks.engines.terraform import write_json
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
        write_json(path, self.as_dict())

    def log(self, logger: logging.Logger | None = None) -> None:
        """Log the operation through the local, redacted result boundary."""

        target = logger or logging.getLogger("aiks")
        level = logging.INFO if self.succeeded else logging.ERROR
        payload = self.as_dict()
        target.log(
            level,
            "operation=%s phase=%s succeeded=%s duration=%.3f correlation=%s context=%s",
            payload["operation"],
            payload["phase"],
            payload["succeeded"],
            payload["durationSeconds"],
            payload["correlationId"],
            payload["context"],
        )

    def render(self, console: Console | None = None) -> None:
        target = console if console is not None else Console()
        status = "[green]succeeded[/green]" if self.succeeded else "[red]failed[/red]"
        context = redact(self.context)
        target.print(
            f"{self.operation} {status} during {self.phase} "
            f"({self.duration_seconds:.3f}s, correlation {self.correlation_id}, "
            f"context {context})"
        )
