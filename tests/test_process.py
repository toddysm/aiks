from __future__ import annotations

import subprocess

import pytest

from aiks.process import run_command


def test_run_command_uses_argument_array_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(arguments: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["arguments"] = arguments
        captured.update(kwargs)
        return subprocess.CompletedProcess(arguments, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(["az", "account", "show"])

    assert result.succeeded
    assert captured["arguments"] == ("az", "account", "show")
    assert "shell" not in captured
    assert captured["check"] is False


def test_run_command_rejects_empty_arguments() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        run_command([])


def test_run_command_redacts_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [],
            1,
            stdout="Authorization: Bearer abc.def",
            stderr="AccountKey=value",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(["tool"])

    assert "abc.def" not in result.stdout
    assert "value" not in result.stderr
