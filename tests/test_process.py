from __future__ import annotations

import subprocess
import sys
import tempfile

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


def test_anonymous_download_descriptor_is_inherited() -> None:
    with tempfile.TemporaryFile() as handle:
        result = run_command(
            [
                sys.executable,
                "-c",
                "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('{}')",
                f"/dev/fd/{handle.fileno()}",
            ],
            pass_fds=(handle.fileno(),),
        )
        assert result.succeeded
        handle.seek(0)
        assert handle.read() == b"{}"


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


def test_run_command_normalizes_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("Password=missing-tool-secret")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(["missing-tool"])

    assert result.return_code == 127
    assert not result.succeeded
    assert "missing-tool-secret" not in result.stderr
    assert "unable to start command" in result.stderr


def test_run_command_normalizes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            ["tool"], 1, output="Bearer timeout.secret", stderr="ClientSecret=hidden"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(["tool"], timeout_seconds=1)

    assert result.return_code == 124
    assert "timeout.secret" not in result.stdout
    assert "hidden" not in result.stderr


def test_timeout_without_stderr_does_not_render_command_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["tool", "--password", "argument-secret"], 2)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(["tool", "--password", "argument-secret"], timeout_seconds=2)

    assert result.return_code == 124
    assert result.stderr == "command timed out after 2 seconds"
    assert "argument-secret" not in result.stderr
