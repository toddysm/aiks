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
    pass_fds: tuple[int, ...] = (),
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
            pass_fds=pass_fds,
        )
    except subprocess.TimeoutExpired as error:
        timeout_message = f"command timed out after {error.timeout} seconds"
        return CommandResult(
            arguments=normalized,
            return_code=124,
            stdout=redact_text(_captured_text(error.stdout)),
            stderr=redact_text(_captured_text(error.stderr) or timeout_message),
        )
    except OSError as error:
        error_message = error.strerror or "unable to start command"
        return CommandResult(
            arguments=normalized,
            return_code=127,
            stdout="",
            stderr=redact_text(f"unable to start command: {error_message}"),
        )
    return CommandResult(
        arguments=normalized,
        return_code=completed.returncode,
        stdout=redact_text(completed.stdout),
        stderr=redact_text(completed.stderr),
    )
