from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from aiks.cli import cli

ROOT = Path(__file__).parents[1]
DEV = ROOT / "infrastructure" / "aks-automatic" / "config" / "dev.example.yaml"
PROD = ROOT / "infrastructure" / "aks-automatic" / "config" / "production.example.yaml"


def test_help_exposes_accepted_command_groups() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in ("config", "infra", "state", "local", "workload"):
        assert command in result.output


def test_validate_writes_redacted_machine_result(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    result = CliRunner().invoke(
        cli,
        [
            "infra",
            "validate",
            "--config",
            str(DEV),
            "--engine",
            "bicep",
            "--json-output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(output.read_text())
    assert payload["succeeded"] is True
    assert payload["context"] == {"engine": "bicep", "environment": "dev", "location": "westus3"}
    assert "correlationId" in payload


def test_pending_command_fails_clearly() -> None:
    result = CliRunner().invoke(
        cli, ["infra", "preflight", "--config", str(DEV), "--engine", "terraform"]
    )

    assert result.exit_code == 1
    assert "tracked by GitHub issue #11" in result.output


def test_production_destroy_requires_override() -> None:
    result = CliRunner().invoke(
        cli, ["infra", "destroy", "--config", str(PROD), "--engine", "bicep"]
    )

    assert result.exit_code == 2
    assert "--allow-production-destroy" in result.output


def test_destroy_requires_typed_environment() -> None:
    result = CliRunner().invoke(
        cli,
        ["infra", "destroy", "--config", str(DEV), "--engine", "terraform"],
        input="wrong\n",
    )

    assert result.exit_code == 1
    assert "did not match" in result.output


def test_confirmed_dev_destroy_reaches_tracked_placeholder() -> None:
    result = CliRunner().invoke(
        cli,
        ["infra", "destroy", "--config", str(DEV), "--engine", "terraform"],
        input="dev\n",
    )

    assert result.exit_code == 1
    assert "tracked by GitHub issue #11" in result.output


def test_confirmed_production_state_destroy_reaches_tracked_placeholder() -> None:
    result = CliRunner().invoke(
        cli,
        ["state", "destroy", "--config", str(PROD), "--allow-production-destroy"],
        input="production\n",
    )

    assert result.exit_code == 1
    assert "tracked by GitHub issue #8" in result.output


def test_all_later_issue_commands_are_visible_and_explicitly_pending() -> None:
    invocations = [
        (["infra", "plan", "--config", str(DEV), "--engine", "bicep"], "#11"),
        (["infra", "deploy", "--config", str(DEV), "--engine", "terraform"], "#11"),
        (["infra", "verify", "--config", str(DEV)], "#11"),
        (["state", "bootstrap", "--config", str(DEV)], "#8"),
        (["state", "status", "--config", str(DEV)], "#8"),
        (["local", "create", "--config", str(DEV)], "#9"),
        (["local", "delete", "--config", str(DEV)], "#9"),
    ]
    for operation in ("install", "verify", "upgrade", "rollback", "uninstall"):
        invocations.append(
            (["workload", operation, "--config", str(DEV), "--target", "kind"], "#9")
        )

    for arguments, issue in invocations:
        result = CliRunner().invoke(cli, arguments)
        assert result.exit_code == 1
        assert f"tracked by GitHub issue {issue}" in result.output


def test_schema_command_writes_schema(tmp_path: Path) -> None:
    output = tmp_path / "schema.json"
    result = CliRunner().invoke(cli, ["config", "schema", "--output", str(output)])

    assert result.exit_code == 0
    assert json.loads(output.read_text())["title"] == "EnvironmentConfig"
