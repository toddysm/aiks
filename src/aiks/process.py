"""Safe external tool invocation."""

from __future__ import annotations

import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from aiks.redaction import redact_text


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Redacted process result."""

    arguments: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0


def _captured_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def run_command(
    arguments: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
    environment: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a command without a shell and redact captured output."""

    if not arguments:
        raise ValueError("command arguments must not be empty")
    normalized = tuple(str(argument) for argument in arguments)
    try:
        completed = subprocess.run(  # nosec B603
            normalized,
            capture_output=True,
            check=False,
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            arguments=normalized,
            return_code=124,
            stdout=redact_text(_captured_text(error.stdout)),
            stderr=redact_text(_captured_text(error.stderr) or str(error)),
        )
    except OSError as error:
        return CommandResult(
            arguments=normalized,
            return_code=127,
            stdout="",
            stderr=redact_text(str(error)),
        )
    return CommandResult(
        arguments=normalized,
        return_code=completed.returncode,
        stdout=redact_text(completed.stdout),
        stderr=redact_text(completed.stderr),
    )
